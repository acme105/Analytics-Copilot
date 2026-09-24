"""Validate model-written SQL before it runs.

Rules: exactly one statement, and it must be a SELECT (or UNION etc. of SELECTs);
no write, admin or file statements anywhere in the tree; only allowed tables
(no table functions such as read_csv); only known column names; LIMIT enforced.
The read-only, no-external-access DuckDB connection (executor.py) is the
second line of defence.
"""

from __future__ import annotations

from collections.abc import Mapping, Set
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

_FORBIDDEN_NAMES = (
    "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "Command", "Copy",
    "Pragma", "Set", "Attach", "Detach", "Use", "Transaction", "Commit", "Rollback",
    "LoadData", "Install",
)  # fmt: skip
FORBIDDEN_NODES = tuple(getattr(exp, name) for name in _FORBIDDEN_NAMES if hasattr(exp, name))


class SQLValidationError(ValueError):
    """The SQL breaks a guard rule. The message is fed back to the model for repair."""


@dataclass(frozen=True)
class ValidatedSQL:
    """SQL that passed the guard, rewritten with the enforced LIMIT."""

    sql: str
    tables: list[str]


def validate_sql(sql: str, schema: Mapping[str, Set[str]], max_rows: int) -> ValidatedSQL:
    """Check ``sql`` against the guard rules and enforce a LIMIT of at most ``max_rows``.

    Args:
        sql: the model's SQL.
        schema: allowed table name -> its column names (lower case).
        max_rows: a missing LIMIT is added, and a larger one is lowered, to this value.

    Raises:
        SQLValidationError: with a message that says which rule was broken.
    """
    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except ParseError as error:
        raise SQLValidationError(f"SQL does not parse: {error}") from error
    if len(statements) != 1:
        raise SQLValidationError(f"Exactly one statement is allowed; got {len(statements)}.")

    tree = statements[0]
    if not isinstance(tree, exp.Query):
        raise SQLValidationError(f"Only SELECT queries are allowed; got {tree.key.upper()}.")
    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise SQLValidationError(f"{node.key.upper()} is not allowed; queries are read-only.")

    tables = _check_tables(tree, schema)
    _check_columns(tree, schema)
    _enforce_limit(tree, max_rows)
    return ValidatedSQL(sql=tree.sql(dialect="duckdb"), tables=tables)


def _check_tables(tree: exp.Query, schema: Mapping[str, Set[str]]) -> list[str]:
    """Reject table functions and unknown tables; return the real tables used."""
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    used: list[str] = []
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            raise SQLValidationError(f"Table functions such as {table.this.sql()} are not allowed.")
        name = table.name.lower()
        if table.db and table.db.lower() != "main":
            raise SQLValidationError(f"Schema-qualified table {table.sql()} is not allowed.")
        if name in cte_names:
            continue
        if name not in schema:
            raise SQLValidationError(
                f"Unknown table {name}. Allowed tables: {', '.join(sorted(schema))}."
            )
        if name not in used:
            used.append(name)
    return used


def _check_columns(tree: exp.Query, schema: Mapping[str, Set[str]]) -> None:
    """Reject column names that exist in no allowed table and are not defined in the query.

    This is a name-level check: it catches hallucinated columns, not a real column
    used on the wrong table, which DuckDB itself rejects at execution.
    """
    known = set().union(*schema.values()) if schema else set()
    known |= {alias.alias.lower() for alias in tree.find_all(exp.Alias)}
    known |= {
        col.name.lower()
        for table_alias in tree.find_all(exp.TableAlias)
        for col in table_alias.columns
    }
    for column in tree.find_all(exp.Column):
        name = column.name.lower()
        if name and name not in known:
            raise SQLValidationError(f"Unknown column {name}.")


def _enforce_limit(tree: exp.Query, max_rows: int) -> None:
    """Add LIMIT ``max_rows`` if missing; lower a literal LIMIT above it."""
    limit = tree.args.get("limit")
    if limit is None:
        tree.limit(max_rows, copy=False)
        return
    value = limit.expression
    if not (isinstance(value, exp.Literal) and value.is_int):
        raise SQLValidationError("LIMIT must be a plain integer.")
    if int(value.this) > max_rows:
        limit.set("expression", exp.Literal.number(max_rows))
