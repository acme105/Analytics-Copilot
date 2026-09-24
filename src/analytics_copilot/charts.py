"""Choose a chart from the shape of the result, deterministically.

The model only gives a hint. The hint is used when the data can support it;
otherwise the shape decides. The same rows always produce the same chart.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

ChartType = Literal["number", "line", "bar", "table"]
_TIME_NAMES = {"period", "date", "day", "week", "month", "year", "quarter"}
_MAX_BARS = 50


class ChartSpec(BaseModel):
    """What the front end should draw: type, x column and y columns."""

    type: ChartType
    x: str | None = None
    y: list[str] = []
    series: str | None = None


def _is_numeric(values: list[object]) -> bool:
    present = [v for v in values if v is not None]
    return bool(present) and all(
        isinstance(v, int | float) and not isinstance(v, bool) for v in present
    )


def _is_temporal(name: str, values: list[object]) -> bool:
    present = [v for v in values if v is not None]
    return bool(present) and (
        all(isinstance(v, date | datetime) for v in present) or name.lower() in _TIME_NAMES
    )


def build_chart_spec(columns: list[str], rows: list[tuple], hint: str | None) -> ChartSpec:
    """Pick the chart for a result set.

    number: one row with one numeric value. line: a time column plus numbers (an
    optional category column becomes the series). bar: one category column plus
    numbers, at most 50 rows. table: anything else.
    """
    if not rows:
        return ChartSpec(type="table")
    values = {c: [row[i] for row in rows] for i, c in enumerate(columns)}
    numeric = [c for c in columns if _is_numeric(values[c])]
    temporal = [c for c in columns if c not in numeric and _is_temporal(c, values[c])]
    categorical = [c for c in columns if c not in numeric and c not in temporal]

    options: dict[ChartType, ChartSpec] = {"table": ChartSpec(type="table")}
    if len(rows) == 1 and len(numeric) == 1 and len(columns) <= 2:
        options["number"] = ChartSpec(type="number", y=numeric)
    if temporal and numeric:
        series = categorical[0] if len(categorical) == 1 else None
        if series or not categorical:
            options["line"] = ChartSpec(type="line", x=temporal[0], y=numeric[:3], series=series)
            options["bar"] = ChartSpec(type="bar", x=temporal[0], y=numeric[:3], series=series)
    elif len(categorical) == 1 and numeric and len(rows) <= _MAX_BARS:
        options["bar"] = ChartSpec(type="bar", x=categorical[0], y=numeric[:3])

    if hint in options:
        return options[hint]  # type: ignore[index]
    for preferred in ("number", "line", "bar"):
        if preferred in options:
            return options[preferred]  # type: ignore[index]
    return options["table"]
