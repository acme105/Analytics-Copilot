"""Compare a predicted result with the gold result, and label why a failure happened.

Matching rules (DECISIONS D20):
- columns are matched by content, not name; extra predicted columns are ignored;
- a predicted column may equal the gold column times 100 (ratio shown as a percentage);
- row order only matters when the item is ``ordered``;
- floats match within a relative tolerance of 1e-3;
- a year may come back as its 1 January date: 2017 equals '2017-01-01' (D39).
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

REL_TOL = 1e-3
_MIDNIGHT = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ]00:00:00(\.0+)?$")
_NEW_YEAR = re.compile(r"^(\d{4})-01-01$")

# Columns whose presence in WHERE is part of a governed metric definition.
_DEFINITION_FLAGS = {
    "is_valid", "is_delivered", "is_canceled", "review_score", "customer_order_number",
    "has_90d_followup",
}  # fmt: skip


@dataclass
class MatchResult:
    """Whether the prediction matched, and why not if it didn't."""

    correct: bool
    reason: str = ""
    scaled_columns: list[int] = field(default_factory=list)


def normalise(value: object) -> object:
    """Put a cell into a comparable form: numbers as float, dates as ISO strings,
    midnight timestamps as dates, text trimmed and lower-cased."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int | float | Decimal):
        return float(value)
    if isinstance(value, datetime):
        value = value.isoformat()
    elif isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    midnight = _MIDNIGHT.match(text)
    return midnight.group(1) if midnight else text.lower()


def _year_matches(number: object, text: object) -> bool:
    """2017.0 vs '2017-01-01': a year returned as DATE_TRUNC('year', ...) (D39)."""
    if not (isinstance(number, float) and isinstance(text, str)):
        return False
    match = _NEW_YEAR.match(text)
    return bool(match) and number.is_integer() and int(match.group(1)) == int(number)


def _equal(a: object, b: object) -> bool:
    if _year_matches(a, b) or _year_matches(b, a):
        return True
    if isinstance(a, float) and isinstance(b, float):
        if math.isnan(a) or math.isnan(b):
            return math.isnan(a) and math.isnan(b)
        return math.isclose(a, b, rel_tol=REL_TOL, abs_tol=1e-9)
    return a == b


def _sort_key(row: Sequence[object]) -> tuple:
    return tuple(
        (2, "") if v is None else (1, "", v) if isinstance(v, float) else (0, str(v)) for v in row
    )


def _rows_equal(gold: list[tuple], pred: list[tuple], ordered: bool) -> bool:
    if not ordered:
        gold, pred = sorted(gold, key=_sort_key), sorted(pred, key=_sort_key)
    return all(
        all(_equal(g, p) for g, p in zip(grow, prow, strict=True))
        for grow, prow in zip(gold, pred, strict=True)
    )


def _column_candidates(gold_col: list[object], pred_cols: list[list[object]]) -> list[tuple]:
    """(pred column index, scale) pairs whose values match the gold column as a multiset."""
    candidates = []
    for k, column in enumerate(pred_cols):
        scales = [1.0]
        if all(isinstance(v, float) or v is None for v in column + gold_col):
            scales.append(100.0)
        for scale in scales:
            scaled = [v / scale if isinstance(v, float) else v for v in column]
            if _rows_equal([(v,) for v in gold_col], [(v,) for v in scaled], ordered=False):
                candidates.append((k, scale))
    return candidates


def compare_results(
    gold_rows: Sequence[Sequence[object]],
    pred_rows: Sequence[Sequence[object]],
    ordered: bool,
) -> MatchResult:
    """Decide whether ``pred_rows`` answers the question the way ``gold_rows`` does."""
    gold = [tuple(normalise(v) for v in row) for row in gold_rows]
    pred = [tuple(normalise(v) for v in row) for row in pred_rows]
    if len(gold) != len(pred):
        return MatchResult(False, f"row count: gold {len(gold)}, predicted {len(pred)}")
    if not gold:
        return MatchResult(True)
    n_gold, n_pred = len(gold[0]), len(pred[0])
    if n_pred < n_gold:
        return MatchResult(False, f"columns: gold {n_gold}, predicted {n_pred}")

    gold_cols = [[row[j] for row in gold] for j in range(n_gold)]
    pred_cols = [[row[k] for row in pred] for k in range(n_pred)]
    options = [_column_candidates(col, pred_cols) for col in gold_cols]
    missing = [j for j, opts in enumerate(options) if not opts]
    if missing:
        return MatchResult(False, f"no predicted column matches gold column(s) {missing}")

    # Try each way of assigning distinct predicted columns to the gold columns.
    for assignment in _assignments(options):
        projected = [
            tuple(
                row[k] / scale if isinstance(row[k], float) else row[k] for k, scale in assignment
            )
            for row in pred
        ]
        if _rows_equal(gold, projected, ordered):
            scaled = [j for j, (_, scale) in enumerate(assignment) if scale != 1.0]
            return MatchResult(True, scaled_columns=scaled)
    reason = "rows are in a different order" if ordered else "values do not line up by row"
    return MatchResult(False, reason)


def _assignments(options: list[list[tuple]]):
    """Yield combinations of (column, scale) with no predicted column used twice."""

    def extend(j: int, used: set[int], chosen: list[tuple]):
        if j == len(options):
            yield list(chosen)
            return
        for k, scale in options[j]:
            if k not in used:
                yield from extend(j + 1, used | {k}, [*chosen, (k, scale)])

    yield from extend(0, set(), [])


# --- failure taxonomy ---


@dataclass(frozen=True)
class _Features:
    tables: frozenset[str]
    aggregates: frozenset[str]
    where_columns: frozenset[str]
    grains: frozenset[str]
    date_literals: frozenset[str]


def _features(sql: str) -> _Features | None:
    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except ParseError:
        return None
    ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    tables = {t.name.lower() for t in tree.find_all(exp.Table)} - ctes
    aggregates = {
        node.key.upper() + ("_DISTINCT" if node.find(exp.Distinct) else "")
        for node in tree.find_all(exp.AggFunc)
    }
    where_columns = {
        col.name.lower() for where in tree.find_all(exp.Where) for col in where.find_all(exp.Column)
    }
    grains = {
        node.text("unit").lower()
        for node in tree.find_all(exp.DateTrunc, exp.TimestampTrunc)
        if node.text("unit")
    }
    date_literals = {
        lit.this[:10]
        for lit in tree.find_all(exp.Literal)
        if lit.is_string and re.match(r"^\d{4}-\d{2}-\d{2}", lit.this)
    }
    return _Features(
        frozenset(tables),
        frozenset(aggregates),
        frozenset(where_columns),
        frozenset(grains),
        frozenset(date_literals),
    )


def classify_failure(pred_sql: str | None, gold_sql: str, error: str | None) -> str:
    """First-pass failure label from comparing the predicted and gold SQL.

    Checked in order: hallucinated column, wrong table or join, wrong metric definition,
    wrong time grain, wrong filter, other. A reviewer can override it.
    """
    if error:
        lowered = error.lower()
        if "unknown column" in lowered or "referenced column" in lowered:
            return "hallucinated_column"
        if "unknown table" in lowered:
            return "wrong_table_or_join"
        return "other"
    if not pred_sql:
        return "other"
    pred, gold = _features(pred_sql), _features(gold_sql)
    if pred is None or gold is None:
        return "other"
    if pred.tables != gold.tables:
        return "wrong_table_or_join"
    missing_flags = (gold.where_columns & _DEFINITION_FLAGS) - pred.where_columns
    if pred.aggregates != gold.aggregates or missing_flags:
        return "wrong_metric_definition"
    if pred.grains != gold.grains:
        return "wrong_time_grain"
    if pred.where_columns != gold.where_columns or pred.date_literals != gold.date_literals:
        return "wrong_filter"
    return "other"
