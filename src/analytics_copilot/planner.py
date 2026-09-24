"""Semantic planning: the model chooses a governed metric and its options, and code writes
the SQL (DECISIONS D27).

The model returns a small JSON plan (metric, dimensions, filters, period or periods, grain,
share, sort, limit), or says the question needs custom SQL. ``plan_to_sql`` validates
the plan against the semantic layer and compiles it with ``compile_metric``, so required
filters, the date field, the window and the table can't be got wrong.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Literal

from pydantic import BaseModel, field_validator

from analytics_copilot.llm import Message
from analytics_copilot.semantic import SemanticLayer, compile_metric, is_additive


class Period(BaseModel):
    """A half-open date range: start inclusive, end exclusive."""

    start: date
    end: date


class MetricPlan(BaseModel):
    """What the planner model returns."""

    kind: Literal["metric", "custom"]
    metric: str | None = None
    group_by: list[str] = []
    filters: dict[str, list[str]] = {}
    start: date | None = None
    end: date | None = None
    periods: list[Period] = []
    grain: str | None = None
    share_of: dict[str, list[str]] | None = None
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


class PlanError(ValueError):
    """The plan can't be compiled. The message is fed back to the model."""


def plan_to_sql(layer: SemanticLayer, plan: MetricPlan) -> str:
    """Compile a metric plan to SQL.

    Raises:
        PlanError: unknown metric or dimension, or an unsupported combination.
    """
    if plan.kind != "metric" or not plan.metric:
        raise PlanError("Only plans of kind 'metric' with a metric name can be compiled.")
    if plan.share_of and len(plan.share_of) != 1:
        raise PlanError("share_of takes exactly one dimension.")
    try:
        return compile_metric(
            layer,
            plan.metric,
            grain=plan.grain,
            dimensions=plan.group_by,
            start=plan.start,
            end=plan.end,
            filters=plan.filters,
            periods=[(p.start, p.end) for p in plan.periods],
            share_of=next(iter(plan.share_of.items())) if plan.share_of else None,
            sort=plan.sort,
            limit=plan.limit,
        )
    except (KeyError, ValueError) as error:
        raise PlanError(str(error).strip("'\"")) from error


PLANNER_SYSTEM = """You turn business questions into a query plan over governed metrics.

If ONE governed metric answers the question, reply with a plan:
{{"kind": "metric", "metric": "<metric name>",
 "group_by": ["<dimension>", ...],            // breakdowns, e.g. "by state"
 "filters": {{"<dimension>": ["<value>", ...]}}, // e.g. only SP, only credit_card
 "start": "YYYY-MM-DD", "end": "YYYY-MM-DD",    // end exclusive; omit for the whole window
 "periods": [{{"start": ..., "end": ...}}, ...], // two or more periods compared side by side
 "grain": "day" | "week" | "month" | "quarter" | "year",  // for trends over time
 "share_of": {{"<dimension>": ["<value>"]}},     // "what share of X came from Y"
 "sort": "desc" | "asc",                       // rankings: top/most/highest = desc,
                                               // bottom/least/lowest/worst-for-good-metrics = asc
 "limit": N,                                   // top-N
 "notes": ["how you read the question"]}}
Omit keys you don't need.

Otherwise reply {{"kind": "custom", "notes": ["why no single metric fits"]}}. Use custom for
questions that need two metrics at once, growth between periods, cohorts or thresholds.

Rules:
- Dates: the data covers {start} to {end} (end exclusive). "In 2018" = start 2018-01-01,
  end 2019-01-01 (the compiler keeps the part inside the data).
- Rankings: "highest", "most", "top" sort desc; "lowest", "least", "fewest" sort asc.
  "Worst" and "best" depend on the metric: for rates of bad outcomes (late, canceled,
  1-2 star) worst = highest; for good outcomes (review score, on-time rate) worst = lowest;
  for delivery days, fastest = lowest.
- Filter values must come from the lists below, written exactly.

Metrics:
{metrics}

Dimensions and their values:
{dimensions}

Examples:
{examples}

Reply with only the JSON object."""

PLAN_EXAMPLES = [
    ("Which 3 product categories have the lowest average number of items per order?",
     {"kind": "metric", "metric": "items_per_order", "group_by": ["product_category"],
      "sort": "asc", "limit": 3}),
    ("Compare average delivery days in Q2 2017 and Q2 2018.",
     {"kind": "metric", "metric": "avg_delivery_days",
      "periods": [{"start": "2017-04-01", "end": "2017-07-01"},
                  {"start": "2018-04-01", "end": "2018-07-01"}]}),
    ("What share of 2017 orders came from Minas Gerais (MG)?",
     {"kind": "metric", "metric": "orders", "share_of": {"customer_state": ["MG"]},
      "start": "2017-01-01", "end": "2018-01-01"}),
    ("Show cancellation rate by week in March 2018.",
     {"kind": "metric", "metric": "cancellation_rate", "grain": "week",
      "start": "2018-03-01", "end": "2018-04-01"}),
    ("Which categories grew orders fastest between 2017 and 2018?",
     {"kind": "custom", "notes": ["growth between two periods per category"]}),
]  # fmt: skip


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
        examples=examples,
    )  # fmt: skip
    return [{"role": "system", "content": system}, {"role": "user", "content": question}]
