"""Semantic planning: the model chooses a governed metric and its options, and code writes
the SQL (DECISIONS D27).

The model returns a small JSON plan (metric, dimensions, filters, period or periods, grain,
share, sort, limit), or says the question needs custom SQL. ``plan_to_sql`` validates
the plan against the semantic layer and compiles it with ``compile_metric``, so required
filters, the date field, the window and the table can't be got wrong.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, field_validator

from analytics_copilot.llm import Message
from analytics_copilot.semantic import SemanticLayer, compile_metric, is_additive


class PlanError(ValueError):
    """The plan can't be compiled. The message is fed back to the model."""


class Period(BaseModel):
    """A calendar period named the way questions name it; code turns it into exact dates.

    Use year (+ half, quarter or month), or first_day and last_day (both inclusive) for
    anything else. The model never writes exclusive end dates (D38).
    """

    year: int | None = None
    half: Literal[1, 2] | None = None
    quarter: Literal[1, 2, 3, 4] | None = None
    month: int | None = None
    first_day: date | None = None
    last_day: date | None = None

    def to_range(self) -> tuple[date, date]:
        """The period as [start, end) dates.

        Raises:
            PlanError: the period is incomplete or contradictory.
        """
        if self.first_day or self.last_day:
            if not (self.first_day and self.last_day) or self.last_day < self.first_day:
                raise PlanError("A day range needs first_day and last_day, in order.")
            return self.first_day, self.last_day + timedelta(days=1)
        if self.year is None:
            raise PlanError("A period needs a year, or first_day and last_day.")
        if self.month is not None:
            if not 1 <= self.month <= 12:
                raise PlanError(f"Month {self.month} is not 1-12.")
            start, months = date(self.year, self.month, 1), 1
        elif self.quarter is not None:
            start, months = date(self.year, 3 * (self.quarter - 1) + 1, 1), 3
        elif self.half is not None:
            start, months = date(self.year, 1 if self.half == 1 else 7, 1), 6
        else:
            start, months = date(self.year, 1, 1), 12
        end_month = start.month - 1 + months
        return start, date(start.year + end_month // 12, end_month % 12 + 1, 1)


class MetricPlan(BaseModel):
    """What the planner model returns."""

    kind: Literal["metric", "custom"]
    metric: str | None = None
    group_by: list[str] = []
    filters: dict[str, list[str]] = {}
    period: Period | None = None
    periods: list[Period] = []
    grain: str | None = None
    share_of: dict[str, list[str]] | None = None
    change: Literal["absolute", "percent"] | None = None
    compare: Literal["difference", "percent"] | None = None
    min_group_size: int | None = None
    sort: Literal["asc", "desc"] | None = None
    limit: int | None = None
    notes: list[str] = []

    @field_validator("filters", "share_of", mode="before")
    @classmethod
    def _single_values_are_lists(cls, value: object) -> object:
        # {"customer_state": "SP"} is as good as {"customer_state": ["SP"]}.
        if isinstance(value, dict):
            return {k: [v] if isinstance(v, str) else v for k, v in value.items()}
        return value

    @field_validator("notes", mode="before")
    @classmethod
    def _single_note_is_a_list(cls, value: object) -> object:
        return [value] if isinstance(value, str) else value


def plan_to_sql(layer: SemanticLayer, plan: MetricPlan) -> str:
    """Compile a metric plan to SQL.

    Raises:
        PlanError: unknown metric or dimension, or an unsupported combination.
    """
    if plan.kind != "metric" or not plan.metric:
        raise PlanError("Only plans of kind 'metric' with a metric name can be compiled.")
    if plan.share_of and len(plan.share_of) != 1:
        raise PlanError("share_of takes exactly one dimension.")
    start, end = plan.period.to_range() if plan.period else (None, None)
    try:
        return compile_metric(
            layer,
            plan.metric,
            grain=plan.grain,
            dimensions=plan.group_by,
            start=start,
            end=end,
            filters=plan.filters,
            periods=[p.to_range() for p in plan.periods],
            share_of=next(iter(plan.share_of.items())) if plan.share_of else None,
            change=plan.change,
            compare=plan.compare,
            min_group_size=plan.min_group_size,
            sort=plan.sort,
            limit=plan.limit,
        )
    except (KeyError, ValueError) as error:
        raise PlanError(str(error).strip("'\"")) from error


STATE_NAMES = {
    "AC": "Acre", "AL": "Alagoas", "AP": "Amapa", "AM": "Amazonas", "BA": "Bahia",
    "CE": "Ceara", "DF": "Distrito Federal", "ES": "Espirito Santo", "GO": "Goias",
    "MA": "Maranhao", "MT": "Mato Grosso", "MS": "Mato Grosso do Sul", "MG": "Minas Gerais",
    "PA": "Para", "PB": "Paraiba", "PR": "Parana", "PE": "Pernambuco", "PI": "Piaui",
    "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte", "RS": "Rio Grande do Sul",
    "RO": "Rondonia", "RR": "Roraima", "SC": "Santa Catarina", "SP": "Sao Paulo",
    "SE": "Sergipe", "TO": "Tocantins",
}  # fmt: skip
# Words that show a question asks for a breakdown by a dimension.
DIMENSION_WORDS = {
    "customer_state": ("state", "region"),
    "product_category": ("categor", "product type", "department"),
    "payment_type": ("payment", "paid", "pay ", "boleto", "voucher", "credit card", "debit"),
    "seller_tier": ("tier", "seller size", "seller segment", "seller band"),
    "delivery_status": ("late", "on-time", "on time", "delivery status"),
    "customer_type": ("first order", "repeat", "first-time", "returning", "first and"),
}
TIME_UNITS = {"day", "date", "week", "month", "quarter", "year", "period"}
MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august",
          "september", "october", "november", "december")  # fmt: skip
_PERIOD_WORDS = re.compile(
    r"\b(20\d\d|january|february|march|april|may|june|july|august|september|october|"
    r"november|december|q[1-4]|quarter|half|black friday)\b",
    re.IGNORECASE,
)
_CHANGE_WORDS = re.compile(
    r"\b(grew|grow|growth|increased?|decreased?|drop|fall|fell|rose|change|improved?)\b",
    re.IGNORECASE,
)
_PREVIOUS_WORDS = re.compile(
    r"(over-(day|week|month|quarter|year)|previous (day|week|month|quarter|year)|"
    r"(day|week|month|quarter|year) before|compared with the (day|week|month|quarter|year))",
    re.IGNORECASE,
)
_TREND_WORDS = re.compile(
    r"\b((each|every|per|by) (day|week|month|quarter|year)|daily|weekly|monthly|quarterly|"
    r"yearly|annual|trend|over time|(day|week|month|quarter|year) by|"
    r"which (day|week|month|quarter|year))\b",
    re.IGNORECASE,
)
_SHARE_WORDS = re.compile(r"\b(share|percent(age)?|proportion|fraction)\b|%", re.IGNORECASE)
_COMPARISON_WORDS = re.compile(
    r"\b(compare|compared|comparison|each|versus|vs\.?|between|respectively|separately)\b",
    re.IGNORECASE,
)
_WHICH_UNIT = re.compile(r"\bwhich (day|week|month|quarter|year)\b", re.IGNORECASE)


def _plain(text: str) -> str:
    """Lower case without accents, so 'São Paulo' matches 'sao paulo'."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def _mentions(question: str, dimension: str, value: str) -> bool:
    """Whether the question names this dimension value."""
    q = _plain(question)
    if dimension == "customer_state":
        name = STATE_NAMES.get(value, "")
        return bool(re.search(rf"\b{re.escape(value)}\b", question)) or (
            bool(name) and _plain(name) in q
        )
    return value.lower().replace("_", " ") in q or value.lower() in q


def normalise_plan(plan: MetricPlan, question: str) -> MetricPlan:
    """Silent, certain corrections (D38, D42):
    - "orders placed" uses orders_placed (D24);
    - a comparison of periods keeps every period (no limit that drops one);
    - a time unit in group_by becomes the grain; date "filters" become the period;
    - a filter on several values of a dimension, in a question that compares them
      ("compare SP and RJ", "for each of ..."), becomes a breakdown by that dimension.
    """
    updates: dict[str, object] = {}
    if plan.metric == "orders" and re.search(r"\bplaced\b", question, re.IGNORECASE):
        updates["metric"] = "orders_placed"
    if plan.periods and plan.limit and plan.limit < len(plan.periods) and not plan.compare:
        updates |= {"limit": None, "sort": None}
    group_by = [d for d in plan.group_by if d not in TIME_UNITS]
    time_in_group = [d for d in plan.group_by if d in TIME_UNITS]
    if time_in_group and not plan.grain and not plan.periods:
        unit = time_in_group[0]
        updates["grain"] = "day" if unit in ("date", "period") else unit
    filters = {d: v for d, v in plan.filters.items() if d not in TIME_UNITS}
    days = sorted(
        v for d, vs in plan.filters.items() if d in ("date", "day") for v in vs
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v)
    )  # fmt: skip
    if days and not plan.periods:
        updates["period"] = Period(first_day=days[0], last_day=days[-1])
    if _COMPARISON_WORDS.search(question):
        for dimension, values in filters.items():
            if len(values) > 1 and dimension not in group_by:
                group_by.append(dimension)
    if group_by != plan.group_by:
        updates["group_by"] = group_by
    if filters != plan.filters:
        updates["filters"] = filters
    return plan.model_copy(update=updates)


def plan_problems(plan: MetricPlan, question: str) -> list[str]:
    """Deterministic checks that a plan matches its question (D38, D42). Each problem is
    a sentence the model can act on."""
    problems = []
    names_period = bool(_PERIOD_WORDS.search(question))
    has_period = plan.period is not None or bool(plan.periods)
    if names_period and not has_period:
        problems.append('The question names a period: set "period" (or "periods") to it.')
    if has_period and not names_period:
        problems.append('The question names no period: remove "period" to use all the data.')
    q = question.lower()
    named_months = [i + 1 for i, m in enumerate(MONTHS) if re.search(rf"\b{m}\b", q)]
    if len(named_months) == 1 and plan.period and not plan.period.first_day:
        if plan.period.month != named_months[0]:
            problems.append(f"The question names month {named_months[0]}: set it in the period.")
    for dimension in plan.group_by:
        words = DIMENSION_WORDS.get(dimension, ())
        if words and not any(w in q for w in words):
            problems.append(f"The question doesn't ask for a breakdown by {dimension}.")
    for dimension, values in {**plan.filters, **(plan.share_of or {})}.items():
        for value in values:
            if not _mentions(question, dimension, value):
                problems.append(f"The question doesn't mention {dimension} = {value}.")
    if plan.share_of and not _SHARE_WORDS.search(question):
        problems.append(
            'The question asks for no share or percentage: use "filters", not share_of.'
        )
    if plan.grain and not plan.change and not _TREND_WORDS.search(question):
        problems.append("The question asks for no breakdown over time: remove grain.")
    if (
        plan.periods
        and len(plan.periods) == 2
        and _CHANGE_WORDS.search(question)
        and not plan.compare
    ):
        problems.append('Growth or change between two periods: set "compare": "difference".')
    if _PREVIOUS_WORDS.search(question) and not plan.change:
        problems.append('Change from the previous period: set a grain and "change".')
    if _WHICH_UNIT.search(question) and not plan.grain and not plan.periods:
        problems.append('"Which month/week/..." needs that grain, sort desc or asc, and limit 1.')
    return problems


PLANNER_SYSTEM = """You turn business questions into a query plan over governed metrics.

If ONE governed metric answers the question exactly, reply with a plan:
{{"kind": "metric", "metric": "<metric name>",
 "group_by": ["<dimension>", ...],              // only if the question asks "by X" / "each X"
 "filters": {{"<dimension>": ["<value>", ...]}},  // only values the question names
 "period": {{"year": 2018, "half": 1}},          // or quarter 1-4, or month 1-12,
                                                // or first_day + last_day (inclusive dates);
                                                // omit if the question names no period
 "periods": [<period>, <period>],               // two or more periods side by side
 "grain": "day" | "week" | "month" | "quarter" | "year",  // for trends over time
 "share_of": {{"<dimension>": ["<value>"]}},      // only for "what share / percentage of"
 "change": "absolute" | "percent",              // with grain: change from the previous period
                                                // (month-over-month, biggest drop/rise)
 "compare": "difference" | "percent",           // with exactly 2 periods: per group, the change
                                                // from the first period to the second (growth)
 "min_group_size": N,                           // "among X with at least N orders/reviews"
 "sort": "desc" | "asc",                        // with change/compare, sorts on the change
 "limit": N,
 "notes": ["how you read the question"]}}
Omit keys you don't need. Never invent a period, filter or breakdown the question doesn't ask for.

Reply {{"kind": "custom", "notes": ["why"]}} when no metric fits EXACTLY: a different
measure, two metrics at once, or a condition on each order (e.g. "orders with more than 2
items") that no metric expresses.

Rules:
- "How many / how much ... from X" is a filter on X, not a share. Use share_of only for
  "share", "percentage" or "proportion".
- "Which month (week, day) had the highest X": grain = that unit, sort desc, limit 1.
- "Orders placed" uses the orders_placed metric.
- "X vs Y" groups that exist as a dimension (late vs on time, first vs repeat orders) are a
  group_by on that dimension.
- Growth, rise or fall between two periods: "periods" + "compare"; biggest drop or rise from
  one period to the next: "grain" + "change" + sort + limit 1.
- Rankings: "highest", "most", "top" sort desc; "lowest", "least", "fewest" sort asc.
  "Worst" and "best" depend on the metric: for rates of bad outcomes (late, canceled,
  1-2 star) worst = highest; for good outcomes (review score, on-time rate) worst = lowest;
  "slowest" delivery = highest days, "fastest" = lowest.
- The data covers {start} to {end} (end exclusive). A named period is clipped to it.
- Filter values must come from the lists below, written exactly.

Metrics:
{metrics}

Dimensions and their values:
{dimensions}

Glossary:
{glossary}

Examples:
{examples}

Reply with only the JSON object."""

PLAN_EXAMPLES = [
    ("Which 3 product categories have the lowest average number of items per order?",
     {"kind": "metric", "metric": "items_per_order", "group_by": ["product_category"],
      "sort": "asc", "limit": 3}),
    ("Compare average delivery days in Q2 2017 and Q2 2018.",
     {"kind": "metric", "metric": "avg_delivery_days",
      "periods": [{"year": 2017, "quarter": 2}, {"year": 2018, "quarter": 2}]}),
    ("What share of 2017 orders came from Minas Gerais (MG)?",
     {"kind": "metric", "metric": "orders", "share_of": {"customer_state": ["MG"]},
      "period": {"year": 2017}}),
    ("Show cancellation rate by week in March 2018.",
     {"kind": "metric", "metric": "cancellation_rate", "grain": "week",
      "period": {"year": 2018, "month": 3}}),
    ("Show how average instalments changed per payment type from Q1 2017 to Q1 2018, "
     "biggest increase first.",
     {"kind": "metric", "metric": "avg_installments", "group_by": ["payment_type"],
      "periods": [{"year": 2017, "quarter": 1}, {"year": 2018, "quarter": 1}],
      "compare": "difference", "sort": "desc", "limit": 1}),
    ("Show the quarter-over-quarter percentage change in active sellers during 2018.",
     {"kind": "metric", "metric": "active_sellers", "grain": "quarter", "change": "percent",
      "period": {"year": 2018}}),
    ("Show the 3 best-rated product categories, counting only categories with 200+ "
     "reviewed orders.",
     {"kind": "metric", "metric": "avg_review_score", "group_by": ["product_category"],
      "min_group_size": 200, "sort": "desc", "limit": 3}),
    ("Show GMV and number of orders side by side for each state in 2017.",
     {"kind": "custom", "notes": ["two metrics at once"]}),
]  # fmt: skip


def glossary_block(layer: SemanticLayer) -> str:
    """The business glossary as prompt lines."""
    return "\n".join(f"- {t.term}: {t.definition}" for t in layer.glossary)


def plan_messages(
    question: str, layer: SemanticLayer, dimension_values: dict[str, list[str]]
) -> list[Message]:
    """Messages for the planner: every metric, every dimension with its values, examples."""
    metrics = "\n".join(
        f"- {name}: {m.label} ({m.unit}). {m.description} Synonyms: {', '.join(m.synonyms)}."
        f"{' Additive (can use share_of).' if is_additive(m) else ''}"
        for name, m in layer.metrics.items()
    )
    dimensions = "\n".join(
        f"- {name}: {', '.join(dimension_values.get(name, []))}" for name in layer.dimensions
    )
    examples = "\n".join(f"Q: {q}\n{json.dumps(p)}" for q, p in PLAN_EXAMPLES)
    window = layer.default_window
    system = PLANNER_SYSTEM.format(
        start=window.start, end=window.end, metrics=metrics, dimensions=dimensions,
        glossary=glossary_block(layer), examples=examples,
    )  # fmt: skip
    return [{"role": "system", "content": system}, {"role": "user", "content": question}]
