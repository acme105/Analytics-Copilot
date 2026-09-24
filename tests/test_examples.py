"""Every few-shot example passes the SQL guard and runs on the real warehouse."""

from pathlib import Path

import pytest

from analytics_copilot.executor import describe_tables, run_query
from analytics_copilot.sql_guard import validate_sql

from .conftest import EXAMPLES

WAREHOUSE = Path("warehouse/olist.duckdb")


@pytest.mark.skipif(not WAREHOUSE.exists(), reason="run `make warehouse` first")
@pytest.mark.parametrize("example", EXAMPLES, ids=lambda e: e.question[:40])
async def test_example_is_valid_and_returns_rows(example) -> None:
    schema = {
        t: {c.lower() for c in cols} for t, cols in describe_tables(WAREHOUSE, ("fct_",)).items()
    }
    validated = validate_sql(example.sql, schema, 200)
    result = await run_query(validated.sql, WAREHOUSE, timeout_s=10)
    assert any(all(v is not None for v in row) for row in result.rows)
