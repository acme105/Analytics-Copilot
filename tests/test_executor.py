"""The executor's own defences, tested without the SQL guard in front of it."""

from pathlib import Path

import pytest

from analytics_copilot.executor import QueryError, run_query


async def test_slow_queries_are_interrupted(tiny_warehouse: Path) -> None:
    with pytest.raises(QueryError, match="timed out"):
        await run_query("SELECT SUM(i) FROM range(20000000000) t(i)", tiny_warehouse, 0.2)


async def test_connection_is_read_only(tiny_warehouse: Path) -> None:
    with pytest.raises(QueryError, match="read-only"):
        await run_query("CREATE TABLE hacked (a INTEGER)", tiny_warehouse, 5)


async def test_file_access_is_disabled(tiny_warehouse: Path, tmp_path: Path) -> None:
    secret = tmp_path / "secret.csv"
    secret.write_text("password\nhunter2\n")
    with pytest.raises(QueryError):
        await run_query(f"SELECT * FROM read_csv_auto('{secret}')", tiny_warehouse, 5)


async def test_intervals_become_days(tiny_warehouse: Path) -> None:
    result = await run_query("SELECT INTERVAL 36 HOUR AS gap", tiny_warehouse, 5)
    assert result.rows == [(1.5,)]
