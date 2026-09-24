"""Deterministic checks on model-written SQL, before and after it runs (DECISIONS D28).

``rule_violations`` is the rule gate: it reads the SQL with sqlglot and reports every
business rule the query breaks (required metric filters, purchase date, window). A query
can be valid SQL and still be silently wrong; these checks catch that before it runs.

``result_problems`` is execution feedback: it looks at what came back (empty, dates outside
the data, all nulls) and reports anything suspicious, so the model gets one chance to fix it.
"""

from __future__ import annotations

import re
from datetime import date, datetime

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from analytics_copilot.semantic import SemanticLayer

_OTHER_DATES = ("delivered_at", "estimated_delivery_at", "shipped_at", "approved_at")
_SALES_COLUMNS = {"price", "gmv", "freight", "freight_value", "payment_value", "items"}


def _filter_columns(expression: str) -> set[str]:
    """Column names used in a required-filter expression such as 'items > 0'."""
    try:
        return {c.name.lower() for c in sqlglot.parse_one(expression).find_all(exp.Column)}
    except ParseError:
        return set()


def rule_violations(
    sql: str, question: str, metrics_used: list[str], layer: SemanticLayer
) -> list[str]:
    """Business rules the query breaks, in words the model can act on. Empty if none."""
    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except ParseError:
        return []  # the SQL guard reports parse errors
    where = {c.name.lower() for w in tree.find_all(exp.Where) for c in w.find_all(exp.Column)}
    used = {c.name.lower() for c in tree.find_all(exp.Column)}
    problems: list[str] = []

    if "purchased_at" not in where:
        window = layer.default_window
        problems.append(
            "Limit purchased_at to the analysis window (purchased_at >= DATE "
            f"'{window.start}' AND purchased_at < DATE '{window.end}') or to the asked period."
        )
    for column in _OTHER_DATES:
        if column in where:
            problems.append(f"Filter dates on purchased_at, not {column}.")

    for name in metrics_used:
        metric = layer.metrics.get(name)
        if metric is None:
            continue
        for required in metric.required_filters:
            if not _filter_columns(required) <= where:
                problems.append(f"Metric {name} requires the filter `{required}`.")

    if used & {"is_late", "delivery_days"} and "is_delivered" not in where:
        problems.append("Delivery metrics must filter `is_delivered` (delivered orders only).")
    placed = re.search(r"\bplaced\b", question, re.IGNORECASE)
    if used & _SALES_COLUMNS and not where & {"is_valid", "is_canceled"} and not placed:
        problems.append(
            "Sales, order and payment figures must filter `is_valid` "
            "(canceled and unavailable orders are not sales)."
        )
    return list(dict.fromkeys(problems))


def result_problems(
    columns: list[str], rows: list[tuple], layer: SemanticLayer, truncated: bool
) -> list[str]:
    """Signs the result doesn't answer the question, in words the model can act on."""
    if not rows:
        return ["The query returned no rows. Check the filters and filter values."]
    problems = []
    window = layer.default_window
    dates = [
        v.date() if isinstance(v, datetime) else v
        for row in rows
        for v in row
        if isinstance(v, date | datetime)
    ]
    if any(d < window.start or d >= window.end for d in dates):
        problems.append(
            f"The result has dates outside the analysis window {window.start} to {window.end}."
        )
    for i, column in enumerate(columns):
        if all(row[i] is None for row in rows):
            problems.append(f"Column {column} is empty (all NULL).")
    if truncated:
        problems.append("The result hit the row limit; aggregate or add a LIMIT for top-N.")
    return problems
