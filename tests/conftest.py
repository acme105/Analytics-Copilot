"""Shared fixtures: a tiny warehouse and a scripted fake LLM, so pipeline and API tests
run offline in milliseconds, with no GPU and no real data."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from analytics_copilot.config import Settings
from analytics_copilot.llm import Completion, Message, Role
from analytics_copilot.pipeline import AskPipeline
from analytics_copilot.retrieval import load_examples
from analytics_copilot.semantic import load_semantic_layer

TINY_WAREHOUSE_SQL = """
CREATE TABLE fct_orders AS SELECT * FROM (VALUES
    ('o1', 'c1', 'SP', 'delivered', TRUE, FALSE, TIMESTAMP '2018-01-05 10:00', 1, 100.0, 'toys',
     'top', 'credit_card', TRUE, 5.0, FALSE, 5, 1, FALSE, TRUE),
    ('o2', 'c2', 'RJ', 'delivered', TRUE, FALSE, TIMESTAMP '2018-01-20 10:00', 2, 250.0, 'toys',
     'mid', 'boleto', TRUE, 9.0, TRUE, 2, 1, FALSE, TRUE),
    ('o3', 'c1', 'SP', 'canceled', FALSE, TRUE, TIMESTAMP '2018-02-03 10:00', 1, 80.0, 'garden',
     'top', 'credit_card', FALSE, NULL, NULL, NULL, NULL, FALSE, TRUE)
) AS t(order_id, customer_unique_id, customer_state, order_status, is_valid, is_canceled,
       purchased_at, items, gmv, product_category, seller_tier, payment_type, is_delivered,
       delivery_days, is_late, review_score, customer_order_number, repeat_within_90d,
       has_90d_followup);

CREATE TABLE fct_order_items AS SELECT * FROM (VALUES
    ('o1', 1, 's1', 'SP', TRUE, TIMESTAMP '2018-01-05 10:00', 100.0, 10.0, 'toys', 'top'),
    ('o2', 1, 's2', 'RJ', TRUE, TIMESTAMP '2018-01-20 10:00', 125.0, 20.0, 'toys', 'mid'),
    ('o2', 2, 's2', 'RJ', TRUE, TIMESTAMP '2018-01-20 10:00', 125.0, 20.0, 'toys', 'mid')
) AS t(order_id, order_item_id, seller_id, customer_state, is_valid, purchased_at, price,
       freight_value, product_category, seller_tier);

CREATE TABLE fct_payments AS SELECT * FROM (VALUES
    ('o1', 1, 'credit_card', 3, 110.0, TRUE, TIMESTAMP '2018-01-05 10:00')
) AS t(order_id, payment_sequential, payment_type, installments, payment_value, is_valid,
       purchased_at);

CREATE VIEW stg_orders AS SELECT order_id, order_status, purchased_at FROM fct_orders;
"""


@pytest.fixture
def tiny_warehouse(tmp_path: Path) -> Path:
    """A small warehouse file with the mart columns the tests use."""
    path = tmp_path / "tiny.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute(TINY_WAREHOUSE_SQL)
    return path


class FakeLLM:
    """Returns scripted replies in order and records every call."""

    def __init__(self, replies: list[str | dict]) -> None:
        self.replies = [r if isinstance(r, str) else json.dumps(r) for r in replies]
        self.calls: list[tuple[Role, list[Message]]] = []

    async def complete(self, role: Role, messages: list[Message]) -> Completion:
        self.calls.append((role, messages))
        if not self.replies:
            raise AssertionError(f"FakeLLM ran out of replies on call {len(self.calls)}")
        return Completion(self.replies.pop(0), prompt_tokens=100, completion_tokens=20)


@pytest.fixture
def make_pipeline(tiny_warehouse: Path):
    """Factory: a pipeline over the tiny warehouse driven by the given scripted replies."""

    def build(replies: list[str | dict]) -> tuple[AskPipeline, FakeLLM]:
        settings = Settings(warehouse_path=tiny_warehouse, max_rows=50)
        llm = FakeLLM(replies)
        pipeline = AskPipeline.from_settings(settings, llm=llm)
        return pipeline, llm

    return build


IN_SCOPE = {"in_scope": True, "reason": "Answerable from orders."}


def sql_reply(sql: str, metrics: list[str] | None = None, hint: str = "bar") -> dict:
    """A well-formed SQL-generation reply."""
    return {
        "sql": sql,
        "metrics_used": metrics or [],
        "assumptions": {"date_field": "purchased_at", "filters": ["valid orders"], "notes": []},
        "chart_hint": hint,
    }


# Make the layer and examples importable once for tests that only need them.
LAYER = load_semantic_layer()
EXAMPLES = load_examples()
