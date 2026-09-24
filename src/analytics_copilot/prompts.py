"""Prompt builders for scope checking, SQL generation, repair and summaries.

The three generation modes differ only in the context the SQL model sees:
    raw_schema    staging-table DDL only, no business definitions;
    semantic      mart DDL plus every governed metric definition and dimension;
    semantic_rag  mart DDL plus only the retrieved metrics and similar example queries;
    semantic_plan the planner (planner.py) first; when a question needs custom SQL, the
                  semantic_rag context plus every dimension's allowed values.
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

It does NOT contain: costs, profit or margins, including what the company pays shipping
carriers (freight in the data is what customers were charged); marketing spend, traffic
or conversion;
inventory or stock; returns or refunds; customer names, emails, phones or other personal
data; any data after Oct 2018, so no forecasts or predictions.

Answer in scope when the question can be answered with a query over this data, even if it
is vague. Refuse when it needs data the warehouse lacks, asks for personal data, asks for a
forecast, or is not a data question.

Reply with only a JSON object: {"in_scope": true or false, "reason": "one short sentence"}"""

SQL_RULES = """Write one DuckDB SELECT query that answers the question.

Rules:
- Use only the tables and columns listed. Never invent columns.
- Every column must belong to a table in your FROM or JOIN. To use a column from another
  table, join on the shared key (for example order_id).
- Filter dates with ranges: col >= DATE '2018-03-01' AND col < DATE '2018-04-01'. Never
  compare a date with a partial string such as '2018-03'.
- One statement only, read-only. Always end with LIMIT (at most 200).
- Give every computed column a short snake_case alias.
- For monthly or weekly trends use DATE_TRUNC('month', <date column>) AS period.

Reply with only a JSON object:
{"sql": "...", "metrics_used": ["metric names you used, if any"],
 "assumptions": {"date_field": "the date column you filtered or grouped on",
                 "filters": ["each filter you applied, in words"],
                 "notes": ["anything a reader should know"]},
 "chart_hint": "number" | "line" | "bar" | "table"}"""

BUSINESS_RULES_HEADER = """Business rules. Apply every one, even if the question does not ask:
- The default window is purchased_at >= DATE '{start}' AND purchased_at < DATE '{end}'.
- If the question does not say which date to use (e.g. "delivered orders in 2017"), use
  purchased_at and say so in assumptions.notes."""

FINAL_CHECK = """Before you reply, check your SQL:
1. Every metric you use has its required filters in the WHERE clause.
2. purchased_at is limited to the default window (or the part of the asked period inside it).
3. Dates are filtered and grouped on purchased_at."""

SUMMARY_SYSTEM = """You summarise query results for a business reader.

Write 2 to 3 plain sentences that answer the question. Use only numbers that appear in the
rows, or that follow directly from them (a total, a difference, a percentage). Round sensibly
and keep units (R$, %, days). Do not speculate about causes.

Write numbers in English format: a dot for decimals and commas for thousands, e.g.
R$ 2,960,326 or R$ 2.96 million, 7,168 orders, 6.8%. Never use a comma as the decimal
separator. Ratios between 0 and 1 are shown as percentages (0.068 -> 6.8%).
Reply with the summary only."""


def _ddl(tables: dict[str, dict[str, str]], notes: dict[str, dict[str, str]]) -> str:
    """Table definitions, one column per line, with a comment where ``notes`` has one."""
    blocks = []
    for table, cols in tables.items():
        table_notes = notes.get(table, {})
        items = list(cols.items())
        lines = []
        for i, (col, typ) in enumerate(items):
            line = f"  {col} {typ}" + ("," if i < len(items) - 1 else "")
            if col in table_notes:
                line += f"  -- {table_notes[col]}"
            lines.append(line)
        blocks.append(f"TABLE {table} (\n" + "\n".join(lines) + "\n)")
    return "\n".join(blocks)


def _metric_block(layer: SemanticLayer, names: list[str]) -> str:
    lines = []
    for name in names:
        m = layer.metrics[name]
        lines.append(
            f"- {name} ({m.label}, {m.unit}): {m.description}\n"
            f"    expression: {m.expression}   table: {m.view}\n"
            f"    required filters: {' AND '.join(m.required_filters) or 'none'}\n"
            f"    date field: {m.date_field}"
        )
    return "\n".join(lines)


def _business_rules(layer: SemanticLayer) -> str:
    window = layer.default_window
    header = BUSINESS_RULES_HEADER.format(start=window.start, end=window.end)
    return header + "\n" + "\n".join(f"- {rule}" for rule in layer.business_rules)


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
    dimension_values: dict[str, list[str]] | None = None,
) -> list[Message]:
    """Messages for SQL generation in ``mode``.

    ``tables`` is the schema for the mode. ``metric_names`` and ``examples`` are
    what retrieval returned (all metrics in ``semantic`` mode; unused in ``raw_schema``).
    """
    if mode == "raw_schema":
        # Baseline: generic SQL rules and bare table definitions, no business knowledge.
        parts = [SQL_RULES, "Tables:\n" + _ddl(tables, notes={})]
    else:
        notes = {name: view.columns for name, view in layer.views.items()}
        parts = [
            SQL_RULES,
            "Tables:\n" + _ddl(tables, notes),
            "Governed metrics:\n" + _metric_block(layer, metric_names),
            "Dimensions:\n" + _dimension_block(layer),
        ]
        if dimension_values:
            parts.append(
                "Dimension values (use exactly these spellings in filters):\n"
                + "\n".join(f"- {d}: {', '.join(v)}" for d, v in dimension_values.items())
            )
        if mode in ("semantic_rag", "semantic_plan") and examples:
            parts.append(
                "Example questions with correct SQL:\n"
                + "\n\n".join(f"Q: {e.question}\nSQL: {e.sql.strip()}" for e in examples)
            )
        # Rules and checklist last: small models follow the most recent instructions best.
        parts += [_business_rules(layer), FINAL_CHECK]
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
