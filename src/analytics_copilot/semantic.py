"""Semantic layer: load the governed metric definitions and compile them to SQL.

A metric is one aggregate expression over one mart table plus its required filters.
``compile_metric`` adds the time window, an optional time grain, dimensions and
dimension filters, and returns a single SELECT. The LLM never writes metric logic
itself in ``semantic`` mode: it picks metrics and dimensions, and this module owns
how they are calculated.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, model_validator

DEFAULT_LAYER_PATH = Path(__file__).resolve().parents[2] / "semantic" / "metrics.yaml"


class View(BaseModel):
    """A mart table the semantic layer queries."""

    grain: str
    description: str
    columns: dict[str, str] = {}


class Dimension(BaseModel):
    """A column metrics can be grouped or filtered by. ``required_filters`` apply whenever
    the dimension is used (delivery_status only exists for delivered orders)."""

    column: str
    description: str
    synonyms: list[str] = []
    required_filters: list[str] = []


class GlossaryTerm(BaseModel):
    """A business term and what it means in this data."""

    term: str
    definition: str


class Metric(BaseModel):
    """A governed metric: one aggregate expression over one view."""

    label: str
    description: str
    view: str
    expression: str
    grain: str
    required_filters: list[str]
    date_field: str
    unit: str
    synonyms: list[str] = []


class Window(BaseModel):
    """A half-open date range: start inclusive, end exclusive."""

    start: date
    end: date


class SemanticLayer(BaseModel):
    """The full semantic layer file."""

    version: int
    default_window: Window
    time_grains: list[str]
    business_rules: list[str] = []
    glossary: list[GlossaryTerm] = []
    views: dict[str, View]
    dimensions: dict[str, Dimension]
    metrics: dict[str, Metric]

    @model_validator(mode="after")
    def _metrics_use_known_views(self) -> SemanticLayer:
        unknown = {m.view for m in self.metrics.values()} - set(self.views)
        if unknown:
            raise ValueError(f"Metrics reference undefined views: {sorted(unknown)}")
        return self


def load_semantic_layer(path: Path = DEFAULT_LAYER_PATH) -> SemanticLayer:
    """Load and validate the semantic layer YAML."""
    return SemanticLayer.model_validate(yaml.safe_load(path.read_text()))


def _quote(value: str) -> str:
    """Quote a string as a SQL literal."""
    return "'" + value.replace("'", "''") + "'"


class MetricQueryError(ValueError):
    """A metric query the semantic layer can't compile. The message says why."""


def is_additive(metric: Metric) -> bool:
    """True for a single plain SUM(...) or COUNT(*): values that can be split into parts
    and added back up. Ratios such as SUM(a) / COUNT(*) and distinct counts are not."""
    expression = " ".join(metric.expression.upper().split())
    return expression == "COUNT(*)" or re.fullmatch(r"SUM\([^()]*\)", expression) is not None


def clip_to_window(layer: SemanticLayer, start: date | None, end: date | None) -> tuple[date, date]:
    """Keep only the part of [start, end) inside the default window (business rule).

    Raises:
        MetricQueryError: the period lies entirely outside the window.
    """
    window = layer.default_window
    clipped = (max(start or window.start, window.start), min(end or window.end, window.end))
    if clipped[0] >= clipped[1]:
        raise MetricQueryError(
            f"The period {start} to {end} is outside the data window "
            f"{window.start} to {window.end}."
        )
    return clipped


def compile_metric(
    layer: SemanticLayer,
    metric_name: str,
    *,
    grain: str | None = None,
    dimensions: Sequence[str] = (),
    start: date | None = None,
    end: date | None = None,
    filters: Mapping[str, str | Sequence[str]] | None = None,
    periods: Sequence[tuple[date, date]] = (),
    share_of: tuple[str, Sequence[str]] | None = None,
    change: str | None = None,
    compare: str | None = None,
    min_group_size: int | None = None,
    sort: str | None = None,
    limit: int | None = None,
) -> str:
    """Compile a metric into one SELECT statement.

    Args:
        layer: the loaded semantic layer.
        metric_name: key of the metric in the layer.
        grain: optional time grain (see ``layer.time_grains``), output as ``period``.
        dimensions: dimension names to group by.
        start: inclusive start date; defaults to the window start.
        end: exclusive end date; defaults to the window end. Both are clipped to the window.
        filters: dimension name -> value or list of values to keep.
        periods: several (start, end) ranges side by side, each a row labelled ``period``.
        share_of: (dimension, values): the share of the metric those values make up.
            Additive metrics only.
        change: "absolute" or "percent": with a grain, each period's change from the
            previous one. The previous period is fetched even when it lies before ``start``,
            so the first asked period has a change too (D43).
        compare: "difference" or "percent": with exactly two periods, one row per group with
            the value in each period and the change from the first to the second.
        min_group_size: keep groups with at least this many rows of the metric's table
            (in every period, with ``compare``).
        sort: "desc" or "asc" on the value, or on the change when change/compare is set.
        limit: keep the first N rows.

    Returns:
        SQL. Columns: ``period`` (if grain or periods), each dimension, then the value named
        after the metric (``<metric>_share`` with share_of), then ``<metric>_change`` with
        change or compare (compare also returns ``<metric>_first`` and ``<metric>_second``).

    Raises:
        KeyError: unknown metric or dimension.
        MetricQueryError: an unsupported combination, grain, sort or period.
    """
    if metric_name not in layer.metrics:
        raise KeyError(f"Unknown metric: {metric_name}")
    share_dims = [share_of[0]] if share_of else []
    used_dims = [*dimensions, *(filters or {}), *share_dims]
    unknown = [d for d in used_dims if d not in layer.dimensions]
    if unknown:
        raise KeyError(f"Unknown dimensions: {unknown}")
    _check_options(layer, metric_name, grain, periods, share_of, change, compare, sort)
    metric = layer.metrics[metric_name]
    date_field = metric.date_field
    ranges = [clip_to_window(layer, s, e) for s, e in periods] or [
        clip_to_window(layer, start, end)
    ]

    required = [*metric.required_filters]
    for dim in dict.fromkeys(used_dims):
        required += [f for f in layer.dimensions[dim].required_filters if f not in required]
    conditions = [f"({f})" for f in required]
    for dim, value in (filters or {}).items():
        values = [value] if isinstance(value, str) else list(value)
        conditions.append(
            f"{layer.dimensions[dim].column} IN ({', '.join(_quote(v) for v in values)})"
        )
    dims = [f"{layer.dimensions[d].column} AS {d}" for d in dimensions]
    having = f"\nHAVING COUNT(*) >= {int(min_group_size)}" if min_group_size else ""

    def between(s: date, e: date) -> str:
        return (
            f"({date_field} >= DATE {_quote(s.isoformat())} "
            f"AND {date_field} < DATE {_quote(e.isoformat())})"
        )

    def where(date_condition: str) -> str:
        return "WHERE " + "\n  AND ".join([date_condition, *conditions])

    if compare:
        (s1, e1), (s2, e2) = ranges
        names = list(dimensions)
        cols = ", ".join([*dims, f"{metric.expression} AS value"])
        group = "\nGROUP BY ALL" if names else ""
        p1 = f"SELECT {cols}\n  FROM {metric.view}\n  {where(between(s1, e1))}{group}{having}"
        p2 = f"SELECT {cols}\n  FROM {metric.view}\n  {where(between(s2, e2))}{group}{having}"
        join = f"JOIN p2 USING ({', '.join(names)})" if names else "CROSS JOIN p2"
        delta = "p2.value - p1.value" if compare == "difference" else "p2.value / p1.value - 1"
        value_name = f"{metric_name}_change"
        select = ", ".join(
            [*(f"p1.{n}" for n in names), f"p1.value AS {metric_name}_first",
             f"p2.value AS {metric_name}_second", f"{delta} AS {value_name}"]
        )  # fmt: skip
        sql = f"WITH p1 AS (\n  {p1}\n),\np2 AS (\n  {p2}\n)\nSELECT {select}\nFROM p1 {join}"
        return _finish(sql, value_name, sort, limit, grouped=False)

    if change:
        s, e = ranges[0]
        window_start = _quote(layer.default_window.start.isoformat())
        first = f"DATE_TRUNC('{grain}', DATE {_quote(s.isoformat())})"
        # One grain unit earlier, but never before the window (D5).
        fetch_from = f"GREATEST({first} - INTERVAL 1 {grain}, DATE {window_start})"
        date_condition = (
            f"({date_field} >= {fetch_from} AND {date_field} < DATE {_quote(e.isoformat())})"
        )
        period = f"CAST(DATE_TRUNC('{grain}', {date_field}) AS DATE) AS period"
        cols = ", ".join([period, *dims, f"{metric.expression} AS {metric_name}"])
        base = (
            f"SELECT {cols}\n  FROM {metric.view}\n  {where(date_condition)}"
            f"\n  GROUP BY ALL{having}"
        )
        partition = f"PARTITION BY {', '.join(dimensions)} " if dimensions else ""
        previous = f"LAG({metric_name}) OVER ({partition}ORDER BY period)"
        delta = (
            f"{metric_name} - {previous}"
            if change == "absolute"
            else f"{metric_name} / {previous} - 1"
        )
        value_name = f"{metric_name}_change"
        sql = (
            f"WITH base AS (\n  {base}\n),\n"
            f"changes AS (SELECT *, {delta} AS {value_name} FROM base)\n"
            f"SELECT * FROM changes WHERE period >= CAST({first} AS DATE)"
        )
        return _finish(sql, value_name, sort, limit, grouped=True)

    group: list[str] = []
    if periods:
        cases = " ".join(
            f"WHEN {between(s, e)} THEN DATE {_quote(s.isoformat())}" for s, e in ranges
        )
        group.append(f"CASE {cases} END AS period")
    elif grain:
        group.append(f"CAST(DATE_TRUNC('{grain}', {date_field}) AS DATE) AS period")
    group += dims
    names = [g.rsplit(" AS ", 1)[1] for g in group]
    date_condition = "(" + " OR ".join(between(s, e) for s, e in ranges) + ")"

    if share_of:
        column = layer.dimensions[share_of[0]].column
        values = ", ".join(_quote(v) for v in share_of[1])
        value_name = f"{metric_name}_share"
        inner = ", ".join([*group, f"{column} AS share_dim", f"{metric.expression} AS value"])
        share = f"SUM(value) FILTER (WHERE share_dim IN ({values})) / SUM(value) AS {value_name}"
        base = f"SELECT {inner}\n  FROM {metric.view}\n  {where(date_condition)}\n  GROUP BY ALL"
        sql = f"WITH base AS (\n  {base}\n)\nSELECT {', '.join([*names, share])}\nFROM base"
        if names:
            sql += "\nGROUP BY ALL"
    else:
        value_name = metric_name
        columns = ", ".join([*group, f"{metric.expression} AS {metric_name}"])
        sql = f"SELECT {columns}\nFROM {metric.view}\n{where(date_condition)}"
        if names:
            sql += f"\nGROUP BY ALL{having}"
    return _finish(sql, value_name, sort, limit, grouped=bool(names))


def _check_options(
    layer: SemanticLayer,
    metric_name: str,
    grain: str | None,
    periods: Sequence[tuple[date, date]],
    share_of: tuple[str, Sequence[str]] | None,
    change: str | None,
    compare: str | None,
    sort: str | None,
) -> None:
    """Reject combinations the compiler can't express, with a reason the model can act on."""
    if grain is not None and grain not in layer.time_grains:
        raise MetricQueryError(f"Unsupported time grain {grain!r}; use one of {layer.time_grains}")
    if grain and periods:
        raise MetricQueryError("Use either a time grain or a list of periods, not both.")
    if sort not in (None, "asc", "desc"):
        raise MetricQueryError(f"Sort must be 'asc' or 'desc', not {sort!r}.")
    if share_of and not is_additive(layer.metrics[metric_name]):
        raise MetricQueryError(f"{metric_name} is not additive, so it has no share of a total.")
    if change not in (None, "absolute", "percent"):
        raise MetricQueryError("change must be 'absolute' or 'percent'.")
    if change and (not grain or periods or share_of):
        raise MetricQueryError("change needs a grain, and no periods or share_of.")
    if compare not in (None, "difference", "percent"):
        raise MetricQueryError("compare must be 'difference' or 'percent'.")
    if compare and (len(periods) != 2 or grain or share_of):
        raise MetricQueryError("compare needs exactly two periods, and no grain or share_of.")


def _finish(sql: str, value_name: str, sort: str | None, limit: int | None, grouped: bool) -> str:
    """Add ORDER BY and LIMIT."""
    if sort:
        sql += f"\nORDER BY {value_name} {sort.upper()} NULLS LAST"
    elif grouped:
        sql += "\nORDER BY ALL"
    if limit:
        sql += f"\nLIMIT {int(limit)}"
    return sql
