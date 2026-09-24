"""Semantic layer: load the governed metric definitions and compile them to SQL.

A metric is one aggregate expression over one mart table plus its required filters.
``compile_metric`` adds the time window, an optional time grain, dimensions and
dimension filters, and returns a single SELECT. The LLM never writes metric logic
itself in ``semantic`` mode: it picks metrics and dimensions, and this module owns
how they are calculated.
"""

from __future__ import annotations

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


def compile_metric(
    layer: SemanticLayer,
    metric_name: str,
    *,
    grain: str | None = None,
    dimensions: Sequence[str] = (),
    start: date | None = None,
    end: date | None = None,
    filters: Mapping[str, str | Sequence[str]] | None = None,
) -> str:
    """Compile a metric into one SELECT statement.

    Args:
        layer: the loaded semantic layer.
        metric_name: key of the metric in the layer.
        grain: optional time grain ('day', 'week' or 'month'), output as ``period``.
        dimensions: dimension names to group by.
        start: inclusive start date; defaults to the layer's default window.
        end: exclusive end date; defaults to the layer's default window.
        filters: dimension name -> value or list of values to keep.

    Returns:
        SQL with columns ``period`` (if grain), each dimension, and the metric value
        named after the metric.

    Raises:
        KeyError: unknown metric or dimension.
        ValueError: unsupported time grain.
    """
    if metric_name not in layer.metrics:
        raise KeyError(f"Unknown metric: {metric_name}")
    if grain is not None and grain not in layer.time_grains:
        raise ValueError(f"Unsupported time grain {grain!r}; use one of {layer.time_grains}")
    unknown = [d for d in [*dimensions, *(filters or {})] if d not in layer.dimensions]
    if unknown:
        raise KeyError(f"Unknown dimensions: {unknown}")

    metric = layer.metrics[metric_name]
    start = start or layer.default_window.start
    end = end or layer.default_window.end

    select: list[str] = []
    if grain:
        select.append(f"CAST(DATE_TRUNC('{grain}', {metric.date_field}) AS DATE) AS period")
    select += [f"{layer.dimensions[d].column} AS {d}" for d in dimensions]
    select.append(f"{metric.expression} AS {metric_name}")

    where = [
        f"{metric.date_field} >= DATE {_quote(start.isoformat())}",
        f"{metric.date_field} < DATE {_quote(end.isoformat())}",
        *(f"({f})" for f in metric.required_filters),
    ]
    for dim, value in (filters or {}).items():
        values = [value] if isinstance(value, str) else list(value)
        where.append(f"{layer.dimensions[dim].column} IN ({', '.join(_quote(v) for v in values)})")

    sql = f"SELECT {', '.join(select)}\nFROM {metric.view}\nWHERE " + "\n  AND ".join(where)
    if grain or dimensions:
        sql += "\nGROUP BY ALL\nORDER BY ALL"
    return sql
