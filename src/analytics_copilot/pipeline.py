"""The /ask pipeline, usable from a notebook without FastAPI.

scope check -> retrieve context -> generate SQL -> validate -> execute
-> (repair once on failure) -> chart spec -> summarise -> grounding check
(regenerate once, then fall back to a template summary).
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date, datetime
from typing import Any

import openai

from analytics_copilot.charts import build_chart_spec
from analytics_copilot.config import Settings
from analytics_copilot.executor import QueryError, QueryResult, describe_tables, run_query
from analytics_copilot.grounding import check_grounding
from analytics_copilot.llm import (
    LLMClient,
    LLMOutputError,
    Message,
    OpenAICompatibleClient,
    Usage,
    complete_json,
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
    Grounding,
    MetricDefinition,
    Mode,
    ScopeDecision,
    SQLGeneration,
    UsageReport,
)
from analytics_copilot.semantic import SemanticLayer, load_semantic_layer
from analytics_copilot.sql_guard import SQLValidationError, ValidatedSQL, validate_sql

logger = logging.getLogger("analytics_copilot")

# Which warehouse objects each mode may see and query.
MODE_TABLE_PREFIXES: dict[Mode, tuple[str, ...]] = {
    "raw_schema": ("stg_",),
    "semantic": ("fct_",),
    "semantic_rag": ("fct_",),
}


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
    ) -> None:
        self.settings = settings
        self.llm = llm
        self.layer = layer
        self.examples = examples
        self.schemas = schemas
        self.data_coverage = data_coverage

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
        elif mode == "semantic_rag":
            metric_names = top_metrics(self.layer, question)
            examples = top_examples(self.examples, question)
        else:
            metric_names, examples = [], []
        return sql_messages(question, mode, self.schemas[mode], self.layer, metric_names, examples)

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
