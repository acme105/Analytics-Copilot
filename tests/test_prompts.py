"""The signed-off decisions reach the SQL model in semantic modes, and only there."""

from pathlib import Path

import pytest

from analytics_copilot.executor import describe_tables
from analytics_copilot.prompts import sql_messages, summary_messages

from .conftest import EXAMPLES, LAYER

TABLES = {"fct_orders": {"order_id": "VARCHAR", "is_valid": "BOOLEAN", "is_delivered": "BOOLEAN"}}
WAREHOUSE = Path("warehouse/olist.duckdb")


def system_prompt(mode: str) -> str:
    return sql_messages("q", mode, TABLES, LAYER, ["orders"], EXAMPLES[:1])[0]["content"]


@pytest.mark.parametrize("mode", ["semantic", "semantic_rag"])
def test_every_business_rule_and_the_window_reach_semantic_modes(mode: str) -> None:
    prompt = system_prompt(mode)
    for rule in LAYER.business_rules:
        assert rule in prompt
    assert "purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2018-09-01'" in prompt
    assert "is_valid BOOLEAN,  -- FALSE for canceled and unavailable orders" in prompt
    assert "required filters: is_valid" in prompt
    assert prompt.rstrip().endswith("3. Dates are filtered and grouped on purchased_at.")


def test_raw_schema_gets_generic_sql_rules_but_no_business_knowledge() -> None:
    prompt = system_prompt("raw_schema")
    assert "partial string such as" in prompt
    assert "Business rules" not in prompt and "--" not in prompt
    assert not any(rule in prompt for rule in LAYER.business_rules)


def test_summary_prompt_asks_for_english_number_format() -> None:
    prompt = summary_messages("q", ["gmv"], [(1.0,)], 1, False)[0]["content"]
    assert "Never use a comma as the decimal" in prompt


@pytest.mark.skipif(not WAREHOUSE.exists(), reason="run `make warehouse` first")
def test_column_notes_describe_real_columns() -> None:
    schema = describe_tables(WAREHOUSE, ("fct_",))
    for view, spec in LAYER.views.items():
        assert set(spec.columns) <= set(schema[view]), view
