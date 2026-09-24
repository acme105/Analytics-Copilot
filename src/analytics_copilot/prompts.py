"""Prompt builders for scope checking, SQL generation, repair and summaries.

The three generation modes differ only in the context the SQL model sees:
    raw_schema    staging-table DDL only, no business definitions;
    semantic      mart DDL plus every governed metric definition and dimension;
    semantic_rag  mart DDL plus only the retrieved metrics and similar example queries.
"""

from __future__ import annotations

import json

from analytics_copilot.llm import Message
from analytics_copilot.retrieval import Example
from analytics_copilot.schemas import Mode
from analytics_copilot.semantic import SemanticLayer

SCOPE_SYSTEM = """You decide whether a question can be answered from an e-commerce warehouse.

The warehouse holds the Olist Brazilian marketplace, purchases from Sep 2016 to Oct 2018
(the default analysis window is Jan 2017 to Aug 2018). It contains: orders and their status,
item prices and freight (GMV), payments (type, instalments, value), review scores, delivery
dates and delays, customers by Brazilian state and city, sellers, product categories.

It does NOT contain: costs, profit or margins; marketing spend, traffic or conversion;
inventory or stock; returns or refunds; customer names, emails, phones or other personal
data; any data after Oct 2018, so no forecasts or predictions.

Answer in scope when the question can be answered with a query over this data, even if it
is vague. Refuse when it needs data the warehouse lacks, asks for personal data, asks for a
forecast, or is not a data question.

Reply with only a JSON object: {"in_scope": true or false, "reason": "one short sentence"}"""

SQL_RULES = """Write one DuckDB SELECT query that answers the question.

Rules:
- Use only the tables and columns listed. Never invent columns.
- One statement only, read-only. Always end with LIMIT (at most 200).
- Give every computed column a short snake_case alias.
- For monthly or weekly trends use DATE_TRUNC('month', <date column>) AS period.

Reply with only a JSON object:
{"sql": "...", "metrics_used": ["metric names you used, if any"],
 "assumptions": {"date_field": "the date column you filtered or grouped on",
                 "filters": ["each filter you applied, in words"],
                 "notes": ["anything a reader should know"]},
 "chart_hint": "number" | "line" | "bar" | "table"}"""

SEMANTIC_RULES = """Business rules:
- Unless the question gives dates, use the default window {start} to {end} (end exclusive)
  on the metric's date field.
- Compute metrics exactly as defined below: same view, expression and required filters.
- If the question does not say which date to use (e.g. "delivered orders in 2017"), use the
  metric's date field (purchase date) and say so in assumptions.notes.
- Put each metric you use in metrics_used by its name."""

SUMMARY_SYSTEM = """You summarise query results for a business reader.

Write 2 to 3 plain sentences that answer the question. Use only numbers that appear in the
rows, or that follow directly from them (a total, a difference, a percentage). Round sensibly
and keep units (R$, %, days). Do not speculate about causes. Reply with the summary only."""


def _ddl(tables: dict[str, dict[str, str]]) -> str:
    return "\n".join(
        f"TABLE {table} ({', '.join(f'{col} {typ}' for col, typ in cols.items())})"
        for table, cols in tables.items()
    )


def _metric_block(layer: SemanticLayer, names: list[str]) -> str:
    lines = []
    for name in names:
        m = layer.metrics[name]
        filters = " AND ".join(m.required_filters) or "none"
        lines.append(
            f"- {name} ({m.label}, {m.unit}): {m.description}\n"
            f"  SQL: SELECT {m.expression} FROM {m.view} WHERE {filters}; "
            f"date field: {m.date_field}"
        )
    return "\n".join(lines)


def _dimension_block(layer: SemanticLayer) -> str:
    return "\n".join(f"- {name}: {d.description}" for name, d in layer.dimensions.items())


def scope_messages(question: str) -> list[Message]:
    """Messages for the scope check."""
    return [
        {"role": "system", "content": SCOPE_SYSTEM},
        {"role": "user", "content": question},
    ]


def sql_messages(
    question: str,
    mode: Mode,
    tables: dict[str, dict[str, str]],
    layer: SemanticLayer,
    metric_names: list[str],
    examples: list[Example],
) -> list[Message]:
    """Messages for SQL generation in ``mode``.

    ``tables`` is the schema for the mode. ``metric_names`` and ``examples`` are
    what retrieval returned (all metrics in ``semantic`` mode; unused in ``raw_schema``).
    """
    parts = [SQL_RULES, "Tables:\n" + _ddl(tables)]
    if mode != "raw_schema":
        window = layer.default_window
        parts.append(SEMANTIC_RULES.format(start=window.start, end=window.end))
        parts.append("Governed metrics:\n" + _metric_block(layer, metric_names))
        parts.append("Dimensions:\n" + _dimension_block(layer))
    if mode == "semantic_rag" and examples:
        parts.append(
            "Example questions with correct SQL:\n"
            + "\n\n".join(f"Q: {e.question}\nSQL: {e.sql.strip()}" for e in examples)
        )
    return [
        {"role": "system", "content": "\n\n".join(parts)},
        {"role": "user", "content": question},
    ]


def repair_messages(messages: list[Message], previous_reply: str, error: str) -> list[Message]:
    """Messages asking the model to fix a query that failed validation or execution."""
    return [
        *messages,
        {"role": "assistant", "content": previous_reply},
        {
            "role": "user",
            "content": f"That query failed: {error}\n"
            "Fix it and reply with the full JSON object again.",
        },
    ]


def summary_messages(
    question: str, columns: list[str], rows: list[tuple], row_count: int, truncated: bool
) -> list[Message]:
    """Messages for the summary. Only the first 30 rows are shown to the model."""
    shown = [dict(zip(columns, row, strict=False)) for row in rows[:30]]
    note = f"{row_count} rows" + (" (truncated at the row limit)" if truncated else "")
    if len(rows) > 30:
        note += "; first 30 shown"
    return [
        {"role": "system", "content": SUMMARY_SYSTEM},
        {
            "role": "user",
            "content": f"Question: {question}\nResult ({note}):\n"
            + json.dumps(shown, default=str, indent=0),
        },
    ]


def summary_retry_messages(
    messages: list[Message], previous: str, ungrounded: list[str]
) -> list[Message]:
    """Messages asking for a new summary without the ungrounded numbers."""
    return [
        *messages,
        {"role": "assistant", "content": previous},
        {
            "role": "user",
            "content": "These numbers are not in the rows and cannot be derived from them: "
            + ", ".join(ungrounded)
            + ". Rewrite the summary using only numbers from the rows.",
        },
    ]
