"""Run validated SQL against the warehouse: read-only, no file access, with a timeout.

DuckDB is synchronous, so queries run in a worker thread to keep the API async.
DuckDB has no per-query timeout setting, so a timer calls ``interrupt()`` instead.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import duckdb


class QueryError(RuntimeError):
    """The query failed or timed out. The message is fed back to the model for repair."""


@dataclass(frozen=True)
class QueryResult:
    """Column names and rows returned by a query."""

    columns: list[str]
    rows: list[tuple]


def connect_read_only(path: Path) -> duckdb.DuckDBPyConnection:
    """Open the warehouse read-only, with file and network access disabled."""
    return duckdb.connect(str(path), read_only=True, config={"enable_external_access": False})


def _run(sql: str, path: Path, timeout_s: float) -> QueryResult:
    con = connect_read_only(path)
    timer = threading.Timer(timeout_s, con.interrupt)
    timer.start()
    try:
        cursor = con.execute(sql)
        # DECIMAL arrives as Decimal; downstream code (charts, grounding, JSON) wants floats.
        rows = [
            tuple(float(v) if isinstance(v, Decimal) else v for v in row)
            for row in cursor.fetchall()
        ]
        return QueryResult(columns=[d[0] for d in cursor.description], rows=rows)
    except duckdb.InterruptException as error:
        raise QueryError(f"Query timed out after {timeout_s:g}s.") from error
    except duckdb.Error as error:
        raise QueryError(str(error)) from error
    finally:
        timer.cancel()
        con.close()


async def run_query(sql: str, path: Path, timeout_s: float) -> QueryResult:
    """Run ``sql`` in a worker thread and return its result.

    Raises:
        QueryError: DuckDB rejected the query or it exceeded ``timeout_s``.
    """
    return await asyncio.to_thread(_run, sql, path, timeout_s)


def describe_tables(path: Path, prefixes: tuple[str, ...]) -> dict[str, dict[str, str]]:
    """Return {table: {column: type}} for warehouse tables and views whose names start
    with one of ``prefixes``, in column order."""
    con = connect_read_only(path)
    try:
        rows = con.execute(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "ORDER BY table_name, ordinal_position"
        ).fetchall()
    finally:
        con.close()
    schema: dict[str, dict[str, str]] = {}
    for table, column, data_type in rows:
        if table.startswith(prefixes):
            schema.setdefault(table, {})[column] = data_type
    return schema
