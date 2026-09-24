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
    """A column metrics can be grouped or filtered by."""

    column: str
    description: str
    synonyms: list[str] = []


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
        periods: instead of one date range, several (start, end) ranges compared side by
            side; each becomes a row labelled ``period`` with its start date.
        share_of: (dimension, values): return the share of the metric those values make up,
            e.g. ("customer_state", ["SP"]). Only for additive metrics.
        sort: "desc" or "asc" on the metric value (for rankings).
        limit: keep the first N rows (for top-N).

    Returns:
        SQL with columns ``period`` (if grain or periods), each dimension, and the value,
        named after the metric (or ``<metric>_share``).

    Raises:
        KeyError: unknown metric or dimension.
        MetricQueryError: an unsupported combination, grain, sort or period.
    """
    if metric_name not in layer.metrics:
        raise KeyError(f"Unknown metric: {metric_name}")
    share_dims = [share_of[0]] if share_of else []
    unknown = [d for d in [*dimensions, *(filters or {}), *share_dims] if d not in layer.dimensions]
    if unknown:
        raise KeyError(f"Unknown dimensions: {unknown}")
    if grain is not None and grain not in layer.time_grains:
        raise MetricQueryError(f"Unsupported time grain {grain!r}; use one of {layer.time_grains}")
    if grain and periods:
        raise MetricQueryError("Use either a time grain or a list of periods, not both.")
    if sort not in (None, "asc", "desc"):
        raise MetricQueryError(f"Sort must be 'asc' or 'desc', not {sort!r}.")
    metric = layer.metrics[metric_name]
    if share_of and not is_additive(metric):
        raise MetricQueryError(f"{metric_name} is not additive, so it has no share of a total.")

    date_field = metric.date_field
    ranges = [clip_to_window(layer, s, e) for s, e in periods] or [
        clip_to_window(layer, start, end)
    ]
    group: list[str] = []
    if periods:
        cases = " ".join(
            f"WHEN {date_field} >= DATE {_quote(s.isoformat())} AND {date_field} < DATE "
            f"{_quote(e.isoformat())} THEN DATE {_quote(s.isoformat())}"
            for s, e in ranges
        )
        group.append(f"CASE {cases} END AS period")
    elif grain:
        group.append(f"CAST(DATE_TRUNC('{grain}', {date_field}) AS DATE) AS period")
    group += [f"{layer.dimensions[d].column} AS {d}" for d in dimensions]
    names = [g.rsplit(" AS ", 1)[1] for g in group]

    date_filter = " OR ".join(
        f"({date_field} >= DATE {_quote(s.isoformat())} "
        f"AND {date_field} < DATE {_quote(e.isoformat())})"
        for s, e in ranges
    )
    where = [f"({date_filter})", *(f"({f})" for f in metric.required_filters)]
    for dim, value in (filters or {}).items():
        values = [value] if isinstance(value, str) else list(value)
        where.append(f"{layer.dimensions[dim].column} IN ({', '.join(_quote(v) for v in values)})")
    where_sql = "WHERE " + "\n  AND ".join(where)

    if share_of:
        column = layer.dimensions[share_of[0]].column
        values = ", ".join(_quote(v) for v in share_of[1])
        value_name = f"{metric_name}_share"
        inner = ", ".join([*group, f"{column} AS share_dim", f"{metric.expression} AS value"])
        share = f"SUM(value) FILTER (WHERE share_dim IN ({values})) / SUM(value) AS {value_name}"
        base = f"SELECT {inner}\n  FROM {metric.view}\n  {where_sql}\n  GROUP BY ALL"
        sql = f"WITH base AS (\n  {base}\n)\nSELECT {', '.join([*names, share])}\nFROM base"
    else:
        value_name = metric_name
        columns = ", ".join([*group, f"{metric.expression} AS {metric_name}"])
        sql = f"SELECT {columns}\nFROM {metric.view}\n{where_sql}"
    if names:
        sql += "\nGROUP BY ALL"
    if sort:
        sql += f"\nORDER BY {value_name} {sort.upper()}"
    elif names:
        sql += "\nORDER BY ALL"
    if limit:
        sql += f"\nLIMIT {int(limit)}"
    return sql
