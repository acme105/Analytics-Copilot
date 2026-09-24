"""semantic_plan mode: compiler extensions, planner, rule gate, result checks, both routes."""

from datetime import date

import pytest

from analytics_copilot.evals.golden import load_golden
from analytics_copilot.planner import MetricPlan, PlanError, plan_messages, plan_to_sql
from analytics_copilot.semantic import MetricQueryError, compile_metric
from analytics_copilot.sql_checks import result_problems, rule_violations

from .conftest import IN_SCOPE, LAYER, sql_reply

WINDOW = "purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2018-09-01'"
GOOD = f"SELECT customer_state, SUM(price) AS gmv FROM fct_order_items WHERE is_valid AND {WINDOW} GROUP BY 1 ORDER BY 1"  # noqa: E501

# --- compiler extensions ---


def test_rankings_sort_on_the_value_and_limit() -> None:
    sql = compile_metric(LAYER, "late_delivery_rate", dimensions=["customer_state"],
                         sort="desc", limit=5)  # fmt: skip
    assert sql.endswith("ORDER BY late_delivery_rate DESC\nLIMIT 5")
    assert "(is_delivered)" in sql


def test_periods_are_compared_side_by_side_and_clipped_to_the_window() -> None:
    sql = compile_metric(
        LAYER, "orders_placed",
        periods=[(date(2017, 1, 1), date(2017, 4, 1)), (date(2018, 7, 1), date(2019, 1, 1))],
    )  # fmt: skip
    assert "THEN DATE '2017-01-01'" in sql and "THEN DATE '2018-07-01'" in sql
    assert "purchased_at < DATE '2018-09-01'" in sql and "2019" not in sql


def test_share_of_a_total_needs_an_additive_metric() -> None:
    sql = compile_metric(LAYER, "gmv", share_of=("customer_state", ["SP"]))
    assert "FILTER (WHERE share_dim IN ('SP')) / SUM(value) AS gmv_share" in sql
    with pytest.raises(MetricQueryError, match="not additive"):
        compile_metric(LAYER, "aov", share_of=("customer_state", ["SP"]))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"grain": "month", "periods": [(date(2017, 1, 1), date(2017, 2, 1))]},
        {"start": date(2019, 1, 1), "end": date(2019, 6, 1)},
        {"sort": "sideways"},
    ],
)
def test_impossible_metric_queries_are_rejected(kwargs: dict) -> None:
    with pytest.raises(MetricQueryError):
        compile_metric(LAYER, "gmv", **kwargs)


# --- planner ---


def test_plans_compile_and_single_values_become_lists() -> None:
    plan = MetricPlan.model_validate(
        {"kind": "metric", "metric": "gmv", "filters": {"customer_state": "SP"}, "notes": "x"}
    )
    assert plan.filters == {"customer_state": ["SP"]} and plan.notes == ["x"]
    assert "customer_state IN ('SP')" in plan_to_sql(LAYER, plan)


def test_invalid_plans_explain_themselves() -> None:
    with pytest.raises(PlanError, match="Unknown metric"):
        plan_to_sql(LAYER, MetricPlan(kind="metric", metric="profit"))
    with pytest.raises(PlanError):
        plan_to_sql(LAYER, MetricPlan(kind="custom"))


def test_planner_prompt_lists_metrics_and_real_dimension_values() -> None:
    prompt = plan_messages("q", LAYER, {"customer_state": ["RJ", "SP"]})[0]["content"]
    assert "- orders_placed: Orders placed" in prompt and "customer_state: RJ, SP" in prompt
    assert "Additive (can use share_of)" in prompt


# --- rule gate and result checks ---


def test_no_gold_query_trips_the_rule_gate() -> None:
    for item in load_golden():
        if item.gold_sql:
            assert rule_violations(item.gold_sql, item.question, [], LAYER) == [], item.id


@pytest.mark.parametrize(
    ("sql", "question", "metrics", "expected"),
    [
        ("SELECT SUM(price) FROM fct_order_items WHERE is_valid", "GMV?", [], "Limit purchased_at"),
        (f"SELECT COUNT(*) FROM fct_orders WHERE is_valid AND {WINDOW} AND delivered_at > "
         "DATE '2018-01-01'", "q", [], "not delivered_at"),
        (f"SELECT AVG(delivery_days) FROM fct_orders WHERE {WINDOW}", "q", [], "is_delivered"),
        (f"SELECT SUM(price) FROM fct_order_items WHERE {WINDOW}", "GMV?", [], "is_valid"),
        (f"SELECT AVG(items) FROM fct_orders WHERE is_valid AND {WINDOW}", "q",
         ["items_per_order"], "requires the filter `items > 0`"),
    ],
)  # fmt: skip
def test_rule_gate_names_each_broken_rule(sql, question, metrics, expected) -> None:
    assert any(expected in v for v in rule_violations(sql, question, metrics, LAYER))


def test_placed_questions_may_count_every_status() -> None:
    sql = f"SELECT COUNT(*) FROM fct_orders WHERE {WINDOW}"
    assert rule_violations(sql, "How many orders were placed?", [], LAYER) == []


def test_result_checks_flag_empty_out_of_window_and_null_results() -> None:
    assert "no rows" in result_problems(["n"], [], LAYER, False)[0]
    assert "outside" in result_problems(["d"], [(date(2016, 10, 1),)], LAYER, False)[0]
    assert "empty" in result_problems(["a", "b"], [(1, None)], LAYER, False)[0]
    assert result_problems(["d", "n"], [(date(2018, 1, 1), 3)], LAYER, False) == []


# --- the two routes ---


async def test_metric_plan_route_compiles_the_plan(make_pipeline) -> None:
    plan = {"kind": "metric", "metric": "gmv", "group_by": ["customer_state"], "sort": "desc"}
    pipeline, llm = make_pipeline([IN_SCOPE, plan, "RJ had R$ 250 of GMV, SP R$ 100."])
    response = await pipeline.ask("GMV by state, highest first", "semantic_plan")
    assert response.status == "ok" and response.route == "metric_plan"
    assert response.rows == [["RJ", 250.0], ["SP", 100.0]]
    assert "(is_valid)" in response.sql and response.metrics_used[0].name == "gmv"
    assert "- customer_state: RJ, SP" in llm.calls[1][1][0]["content"]  # real values shown


async def test_custom_route_gates_votes_and_answers(make_pipeline) -> None:
    breaks_rules = "SELECT customer_state, SUM(price) AS gmv FROM fct_order_items GROUP BY 1"
    other = GOOD.replace("WHERE is_valid", "WHERE is_valid AND customer_state = 'SP'")
    pipeline, llm = make_pipeline(
        [
            IN_SCOPE,
            {"kind": "custom", "notes": ["needs custom SQL"]},
            sql_reply(GOOD),  # candidate 1
            sql_reply(breaks_rules),  # candidate 2 ...
            sql_reply(GOOD),  # ... fixed by the rule gate
            sql_reply(other),  # candidate 3, a minority answer
            "RJ had R$ 250 of GMV, SP R$ 100.",
        ]
    )
    response = await pipeline.ask("GMV by state", "semantic_plan")
    assert response.status == "ok" and response.route == "custom_sql"
    assert response.sql_candidates == 3
    assert response.rows == [["RJ", 250.0], ["SP", 100.0]]  # 2 of 3 candidates agree
    gate_prompt = llm.calls[4][1][-1]["content"]
    assert "business rules" in gate_prompt and "is_valid" in gate_prompt


async def test_custom_route_uses_result_feedback_to_fix_an_empty_answer(make_pipeline) -> None:
    empty = GOOD.replace("WHERE is_valid", "WHERE is_valid AND customer_state = 'XX'")
    pipeline, llm = make_pipeline(
        [IN_SCOPE, {"kind": "custom"}, sql_reply(empty), sql_reply(GOOD),
         "RJ had R$ 250 of GMV, SP R$ 100."],
        sql_candidates=1,
    )  # fmt: skip
    response = await pipeline.ask("GMV by state", "semantic_plan")
    assert response.rows == [["RJ", 250.0], ["SP", 100.0]]
    assert "no rows" in llm.calls[3][1][-1]["content"]
