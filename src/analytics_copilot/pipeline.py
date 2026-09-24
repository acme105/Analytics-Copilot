"""The /ask pipeline, usable from a notebook without FastAPI.

scope check -> retrieve context -> generate SQL -> validate -> execute
-> (repair once on failure) -> chart spec -> summarise -> grounding check
(regenerate once, then fall back to a template summary).
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date, datetime
from typing import Any

import openai

from analytics_copilot.charts import build_chart_spec
from analytics_copilot.config import Settings
from analytics_copilot.executor import (
    QueryError,
    QueryResult,
    describe_tables,
    dimension_values,
    run_query,
)
from analytics_copilot.grounding import check_grounding
from analytics_copilot.llm import (
    LLMClient,
    LLMOutputError,
    Message,
    OpenAICompatibleClient,
    Usage,
    complete_json,
)
from analytics_copilot.planner import (
    MetricPlan,
    PlanError,
    normalise_plan,
    plan_messages,
    plan_problems,
    plan_to_sql,
)
from analytics_copilot.prompts import (
    repair_messages,
    scope_messages,
    sql_messages,
    summary_messages,
    summary_retry_messages,
)
from analytics_copilot.retrieval import Example, load_examples, top_examples, top_metrics
from analytics_copilot.schemas import (
    AskResponse,
    Assumptions,
    GeneratedAssumptions,
    Grounding,
    MetricDefinition,
    Mode,
    ScopeDecision,
    SQLGeneration,
    UsageReport,
)
from analytics_copilot.semantic import SemanticLayer, load_semantic_layer
from analytics_copilot.sql_checks import result_problems, rule_violations
from analytics_copilot.sql_guard import SQLValidationError, ValidatedSQL, validate_sql

logger = logging.getLogger("analytics_copilot")

# Which warehouse objects each mode may see and query.
MODE_TABLE_PREFIXES: dict[Mode, tuple[str, ...]] = {
    "raw_schema": ("stg_",),
    "semantic": ("fct_",),
    "semantic_rag": ("fct_",),
    "semantic_plan": ("fct_",),
}
# Sampling temperature for self-consistency draws after the first (greedy) one.
CANDIDATE_TEMPERATURE = 0.7


class StageTimer:
    """Collects wall-clock milliseconds per pipeline stage."""

    def __init__(self) -> None:
        self.ms: dict[str, float] = {}
        self._start = time.perf_counter()

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        """Time a block and add it to ``name`` (repeated stages accumulate)."""
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = (time.perf_counter() - start) * 1000
            self.ms[name] = round(self.ms.get(name, 0.0) + elapsed, 1)

    def finish(self) -> dict[str, float]:
        """Return per-stage times plus the total."""
        return {**self.ms, "total": round((time.perf_counter() - self._start) * 1000, 1)}


def _jsonable(value: Any) -> Any:
    return value.isoformat() if isinstance(value, date | datetime) else value


def _result_signature(result: QueryResult) -> tuple:
    """A comparable fingerprint of a result, for voting: rows sorted, floats rounded to
    6 significant digits, so equivalent queries vote together."""

    def cell(value: Any) -> Any:
        if isinstance(value, float):
            return float(f"{value:.6g}")
        return _jsonable(value)

    return tuple(sorted((tuple(cell(v) for v in row) for row in result.rows), key=repr))


class AskPipeline:
    """Answers a natural-language question with governed, checked SQL."""

    def __init__(
        self,
        settings: Settings,
        llm: LLMClient,
        layer: SemanticLayer,
        examples: list[Example],
        schemas: dict[Mode, dict[str, dict[str, str]]],
        data_coverage: str,
        dimension_values: dict[str, list[str]] | None = None,
    ) -> None:
        self.settings = settings
        self.llm = llm
        self.layer = layer
        self.examples = examples
        self.schemas = schemas
        self.data_coverage = data_coverage
        self.dimension_values = dimension_values or {}

    @classmethod
    def from_settings(cls, settings: Settings, llm: LLMClient | None = None) -> AskPipeline:
        """Build a pipeline from settings, reading table schemas from the warehouse."""
        layer = load_semantic_layer()
        schemas = {
            mode: describe_tables(settings.warehouse_path, prefixes)
            for mode, prefixes in MODE_TABLE_PREFIXES.items()
        }
        window = layer.default_window
        coverage = (
            "Olist purchases Sep 2016 to Oct 2018; default analysis window "
            f"{window.start} to {window.end} (end exclusive), by purchase date."
        )
        return cls(
            settings=settings,
            llm=llm or OpenAICompatibleClient(settings),
            layer=layer,
            examples=load_examples(),
            schemas=schemas,
            data_coverage=coverage,
            dimension_values=dimension_values(
                settings.warehouse_path, [d.column for d in layer.dimensions.values()]
            ),
        )

    async def ask(self, question: str, mode: Mode = "semantic_rag") -> AskResponse:
        """Answer ``question`` in ``mode``. Never raises: failures return status 'error'."""
        request_id = uuid.uuid4().hex[:12]
        timer = StageTimer()
        usage = Usage()
        response = AskResponse(
            request_id=request_id, status="error", mode=mode, question=question, answer_summary=""
        )
        try:
            await self._answer(question, mode, response, timer, usage)
        except (LLMOutputError, SQLValidationError, QueryError) as error:
            response.status = "error"
            response.error = str(error)
            response.answer_summary = "I couldn't answer this reliably. " + str(error)
        except openai.APIError as error:
            # Provider down, rate-limited or misconfigured: an operational error, not a bug.
            response.status = "error"
            response.error = f"LLM provider error ({type(error).__name__}): {error}"
            response.answer_summary = "The language model is unavailable right now."
        except Exception as error:  # the API must always return a response
            logger.exception("unexpected pipeline error", extra={"request_id": request_id})
            response.status = "error"
            response.error = f"Internal error: {type(error).__name__}"
            response.answer_summary = "Something went wrong while answering."
        response.latency_ms = timer.finish()
        response.usage = UsageReport(**asdict(usage))
        logger.info(
            "ask",
            extra={
                "request_id": request_id,
                "mode": mode,
                "status": response.status,
                "repaired": response.repaired,
                "latency_ms": response.latency_ms,
                "usage": asdict(usage),
                "error": response.error,
            },
        )
        return response

    async def _answer(
        self, question: str, mode: Mode, response: AskResponse, timer: StageTimer, usage: Usage
    ) -> None:
        with timer.stage("scope"):
            scope = await complete_json(
                self.llm, "summary", scope_messages(question), ScopeDecision, usage
            )
        if not scope.in_scope:
            response.status = "refused"
            response.answer_summary = scope.reason
            return

        if mode == "semantic_plan":
            validated, result, generation = await self._answer_with_plan(
                question, response, timer, usage
            )
        else:
            with timer.stage("retrieve"):
                messages = self._sql_messages(question, mode)
            with timer.stage("generate_sql"):
                generation = await complete_json(self.llm, "sql", messages, SQLGeneration, usage)
            validated, result, generation = await self._run_with_repair(
                generation, messages, mode, response, timer, usage
            )

        rows = result.rows[: self.settings.max_rows]
        response.sql = validated.sql
        response.tables_used = validated.tables
        response.metrics_used = self._metric_definitions(generation.metrics_used)
        response.assumptions = Assumptions(
            date_field=generation.assumptions.date_field,
            filters=generation.assumptions.filters,
            notes=generation.assumptions.notes,
            data_coverage=self.data_coverage,
        )
        response.columns = result.columns
        response.rows = [[_jsonable(v) for v in row] for row in rows]
        response.row_count = len(rows)
        response.truncated = len(result.rows) > self.settings.max_rows

        with timer.stage("chart"):
            response.chart_spec = build_chart_spec(result.columns, rows, generation.chart_hint)

        summary, grounding = await self._summarise(
            question, result.columns, rows, response, timer, usage
        )
        response.answer_summary = summary
        response.grounding = grounding
        response.status = "ok"

    def _sql_messages(self, question: str, mode: Mode) -> list[Message]:
        if mode == "semantic":
            metric_names, examples = list(self.layer.metrics), []
        elif mode in ("semantic_rag", "semantic_plan"):
            metric_names = top_metrics(self.layer, question)
            examples = top_examples(self.examples, question)
        else:
            metric_names, examples = [], []
        values = self.dimension_values if mode == "semantic_plan" else None
        return sql_messages(
            question, mode, self.schemas[mode], self.layer, metric_names, examples, values
        )

    # --- semantic_plan: plan first, custom SQL only when no single metric fits (D27, D28) ---

    async def _answer_with_plan(
        self, question: str, response: AskResponse, timer: StageTimer, usage: Usage
    ) -> tuple[ValidatedSQL, QueryResult, SQLGeneration]:
        """Try the metric-plan route; fall back to guarded custom SQL."""
        planned = await self._metric_plan_route(question, response, timer, usage)
        if planned is not None:
            response.route = "metric_plan"
            return planned
        response.route = "custom_sql"
        return await self._custom_sql_route(question, response, timer, usage)

    async def _metric_plan_route(
        self, question: str, response: AskResponse, timer: StageTimer, usage: Usage
    ) -> tuple[ValidatedSQL, QueryResult, SQLGeneration] | None:
        """Ask for a plan, check it against the question, compile it. None means: use
        custom SQL instead. Problems found by the checks or the compiler get one repair."""
        messages = plan_messages(question, self.layer, self.dimension_values)
        plan: MetricPlan | None = None
        sql: str | None = None
        for attempt in range(2):
            with timer.stage("plan"):
                plan = await complete_json(self.llm, "sql", messages, MetricPlan, usage)
            if plan.kind != "metric":
                break
            plan = normalise_plan(plan, question)
            # Question checks only on the first attempt: after one repair, trust the model.
            problems = plan_problems(plan, question) if attempt == 0 else []
            try:
                sql = plan_to_sql(self.layer, plan)
            except PlanError as error:
                sql = None
                problems.append(str(error))
            if not problems:
                break
            if attempt == 0:
                feedback = "Fix the plan: " + " ".join(problems)
                messages = repair_messages(messages, plan.model_dump_json(), feedback)
        response.plan = plan.model_dump(mode="json") if plan else None
        if plan is None or plan.kind != "metric" or sql is None:
            return None
        allowed = {
            t: {c.lower() for c in cols} for t, cols in self.schemas["semantic_plan"].items()
        }
        try:
            with timer.stage("validate"):
                validated = validate_sql(sql, allowed, self.settings.max_rows + 1)
            with timer.stage("execute"):
                result = await run_query(
                    validated.sql, self.settings.warehouse_path, self.settings.query_timeout_s
                )
        except (SQLValidationError, QueryError):
            return None
        return validated, result, self._plan_generation(plan, sql)

    def _plan_generation(self, plan: MetricPlan, sql: str) -> SQLGeneration:
        """Describe a compiled plan the way the SQL route describes its query."""
        metric = self.layer.metrics[plan.metric or ""]
        filters = [*metric.required_filters]
        filters += [f"{dim} in {', '.join(values)}" for dim, values in plan.filters.items()]
        ranges = [p.to_range() for p in plan.periods] or (
            [plan.period.to_range()] if plan.period else []
        )
        if ranges:
            filters.append("period: " + ", ".join(f"{s} to {e} (end exclusive)" for s, e in ranges))
        if plan.share_of:
            filters += [f"share of {dim} in {', '.join(v)}" for dim, v in plan.share_of.items()]
        hint = "line" if plan.grain else "bar" if (plan.group_by or plan.periods) else "number"
        return SQLGeneration(
            sql=sql,
            metrics_used=[plan.metric or ""],
            assumptions=GeneratedAssumptions(
                date_field=metric.date_field, filters=filters, notes=plan.notes
            ),
            chart_hint=hint,
        )

    async def _custom_sql_route(
        self, question: str, response: AskResponse, timer: StageTimer, usage: Usage
    ) -> tuple[ValidatedSQL, QueryResult, SQLGeneration]:
        """Self-consistency over up to N candidates, each passed through the rule gate,
        the guard with one repair, and one round of result feedback. The answer is the
        result most candidates agree on (ties go to the greedy first candidate)."""
        with timer.stage("retrieve"):
            messages = self._sql_messages(question, "semantic_plan")
        candidates: list[tuple[ValidatedSQL, QueryResult, SQLGeneration]] = []
        last_error: Exception | None = None
        for sample in range(self.settings.sql_candidates):
            temperature = 0.0 if sample == 0 else CANDIDATE_TEMPERATURE
            try:
                with timer.stage("generate_sql"):
                    generation = await complete_json(
                        self.llm, "sql", messages, SQLGeneration, usage, temperature, sample
                    )
                generation = await self._apply_rule_gate(
                    question, messages, generation, timer, usage, temperature, sample
                )
                candidate = await self._run_with_repair(
                    generation, messages, "semantic_plan", response, timer, usage
                )
                candidate = await self._apply_result_feedback(
                    question, messages, candidate, timer, usage, temperature, sample
                )
                candidates.append(candidate)
            except (LLMOutputError, SQLValidationError, QueryError) as error:
                last_error = error
        response.sql_candidates = len(candidates)
        if not candidates:
            raise last_error or QueryError("No SQL candidate could be produced.")
        votes = Counter(_result_signature(result) for _, result, _ in candidates)
        winner = max(votes.values())
        return next(c for c in candidates if votes[_result_signature(c[1])] == winner)

    async def _apply_rule_gate(
        self,
        question: str,
        messages: list[Message],
        generation: SQLGeneration,
        timer: StageTimer,
        usage: Usage,
        temperature: float,
        sample: int,
    ) -> SQLGeneration:
        """If the SQL breaks business rules, ask once for a corrected query."""
        violations = rule_violations(generation.sql, question, generation.metrics_used, self.layer)
        if not violations:
            return generation
        with timer.stage("rule_gate"):
            feedback = "It breaks these business rules: " + " ".join(violations)
            retry = repair_messages(messages, generation.model_dump_json(), feedback)
            return await complete_json(
                self.llm, "sql", retry, SQLGeneration, usage, temperature, sample
            )

    async def _apply_result_feedback(
        self,
        question: str,
        messages: list[Message],
        candidate: tuple[ValidatedSQL, QueryResult, SQLGeneration],
        timer: StageTimer,
        usage: Usage,
        temperature: float,
        sample: int,
    ) -> tuple[ValidatedSQL, QueryResult, SQLGeneration]:
        """If the result looks wrong (empty, outside the window, ...), allow one fix.
        The fix is kept only if it runs and has fewer problems."""
        validated, result, generation = candidate
        truncated = len(result.rows) > self.settings.max_rows
        problems = result_problems(result.columns, result.rows, self.layer, truncated, question)
        if not problems:
            return candidate
        allowed = {
            t: {c.lower() for c in cols} for t, cols in self.schemas["semantic_plan"].items()
        }
        with timer.stage("result_feedback"):
            feedback = "The query ran, but: " + " ".join(problems)
            retry = repair_messages(messages, generation.model_dump_json(), feedback)
            try:
                fixed = await complete_json(
                    self.llm, "sql", retry, SQLGeneration, usage, temperature, sample
                )
                fixed_sql = validate_sql(fixed.sql, allowed, self.settings.max_rows + 1)
                fixed_result = await run_query(
                    fixed_sql.sql, self.settings.warehouse_path, self.settings.query_timeout_s
                )
            except (LLMOutputError, SQLValidationError, QueryError):
                return candidate
        fixed_truncated = len(fixed_result.rows) > self.settings.max_rows
        remaining = result_problems(
            fixed_result.columns, fixed_result.rows, self.layer, fixed_truncated, question
        )
        return (fixed_sql, fixed_result, fixed) if len(remaining) < len(problems) else candidate

    async def _run_with_repair(
        self,
        generation: SQLGeneration,
        messages: list[Message],
        mode: Mode,
        response: AskResponse,
        timer: StageTimer,
        usage: Usage,
    ) -> tuple[ValidatedSQL, QueryResult, SQLGeneration]:
        """Validate and execute; on failure, give the model one repair attempt."""
        allowed = {t: {c.lower() for c in cols} for t, cols in self.schemas[mode].items()}
        for attempt in range(2):
            try:
                with timer.stage("validate"):
                    # One extra row tells us whether the result was truncated.
                    validated = validate_sql(generation.sql, allowed, self.settings.max_rows + 1)
                with timer.stage("execute"):
                    result = await run_query(
                        validated.sql, self.settings.warehouse_path, self.settings.query_timeout_s
                    )
                return validated, result, generation
            except (SQLValidationError, QueryError) as error:
                if attempt == 1:
                    raise
                response.repaired = True
                with timer.stage("repair"):
                    repair = repair_messages(messages, generation.model_dump_json(), str(error))
                    generation = await complete_json(self.llm, "sql", repair, SQLGeneration, usage)
        raise AssertionError("unreachable")

    async def _summarise(
        self,
        question: str,
        columns: list[str],
        rows: list[tuple],
        response: AskResponse,
        timer: StageTimer,
        usage: Usage,
    ) -> tuple[str, Grounding]:
        """Summarise from the rows only; regenerate once if ungrounded, then use a template."""
        if not rows:
            return "The query returned no rows for this question.", Grounding(passed=True)

        messages = summary_messages(question, columns, rows, response.row_count, response.truncated)
        ungrounded: list[str] = []
        for attempt in range(2):
            with timer.stage("summarise"):
                completion = await self.llm.complete("summary", messages)
                usage.record(completion)
            summary = completion.text.strip()
            with timer.stage("grounding"):
                check = check_grounding(summary, rows, question)
            if check.passed:
                return summary, Grounding(passed=True)
            ungrounded = check.ungrounded
            if attempt == 0:
                messages = summary_retry_messages(messages, summary, check.ungrounded)
        return (
            template_summary(columns, rows, response.row_count),
            Grounding(passed=False, ungrounded=ungrounded, fallback_used=True),
        )

    def _metric_definitions(self, names: list[str]) -> list[MetricDefinition]:
        """Definitions for the metrics the model says it used; unknown names are dropped."""
        return [
            MetricDefinition(
                name=name, **self.layer.metrics[name].model_dump(exclude={"grain", "synonyms"})
            )
            for name in dict.fromkeys(names)
            if name in self.layer.metrics
        ]


def template_summary(columns: list[str], rows: list[tuple], row_count: int) -> str:
    """A summary built only from the rows, used when the model's summary is ungrounded."""
    first = ", ".join(f"{c} = {_format(v)}" for c, v in zip(columns, rows[0], strict=False))
    if row_count == 1:
        return f"Result: {first}."
    return f"The query returned {row_count} rows. First row: {first}."


def _format(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:,.4g}" if abs(value) < 1 else f"{value:,.2f}"
    if isinstance(value, date | datetime):
        return value.isoformat()
    return str(value)
