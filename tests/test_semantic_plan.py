"""semantic_plan mode: compiler extensions, planner, rule gate, result checks, both routes."""

from datetime import date

import pytest

from analytics_copilot.evals.golden import load_golden
from analytics_copilot.planner import (
    MetricPlan,
    Period,
    PlanError,
    normalise_plan,
    plan_messages,
    plan_problems,
    plan_to_sql,
)
from analytics_copilot.semantic import MetricQueryError, compile_metric
from analytics_copilot.sql_checks import result_problems, rule_violations

from .conftest import IN_SCOPE, LAYER, sql_reply

WINDOW = "purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2018-09-01'"
GOOD = f"SELECT customer_state, SUM(price) AS gmv FROM fct_order_items WHERE is_valid AND {WINDOW} GROUP BY 1 ORDER BY 1"  # noqa: E501

# --- compiler extensions ---


def test_rankings_sort_on_the_value_and_limit() -> None:
    sql = compile_metric(LAYER, "late_delivery_rate", dimensions=["customer_state"],
                         sort="desc", limit=5)  # fmt: skip
    assert sql.endswith("ORDER BY late_delivery_rate DESC NULLS LAST\nLIMIT 5")
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


def test_fan_out_joins_between_fact_tables_are_flagged() -> None:
    blow_up = (
        f"SELECT o.customer_state, AVG(CASE WHEN l.is_late THEN 1 ELSE 0 END) FROM fct_orders o "
        f"JOIN fct_orders l ON o.customer_state = l.customer_state "
        f"WHERE o.is_delivered AND l.is_delivered AND o.{WINDOW} GROUP BY 1"
    )
    assert any("multiplies rows" in v for v in rule_violations(blow_up, "q", [], LAYER))


def test_joining_an_aggregate_to_a_fact_table_is_fine() -> None:
    top = (
        "WITH top AS (SELECT customer_state FROM fct_orders WHERE is_valid "
        f"AND {WINDOW} GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 2) "
        "SELECT o.customer_state, AVG(delivery_days) FROM top JOIN fct_orders o "
        f"ON top.customer_state = o.customer_state WHERE o.is_delivered AND o.{WINDOW} GROUP BY 1"
    )
    assert not any("multiplies rows" in v for v in rule_violations(top, "q", [], LAYER))


# --- named periods and plan checks (D38) ---


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ({"year": 2018, "half": 1}, (date(2018, 1, 1), date(2018, 7, 1))),
        ({"year": 2017, "quarter": 4}, (date(2017, 10, 1), date(2018, 1, 1))),
        ({"year": 2017, "month": 12}, (date(2017, 12, 1), date(2018, 1, 1))),
        ({"year": 2017}, (date(2017, 1, 1), date(2018, 1, 1))),
        ({"first_day": "2017-11-20", "last_day": "2017-11-26"},
         (date(2017, 11, 20), date(2017, 11, 27))),
    ],
)  # fmt: skip
def test_named_periods_become_exact_half_open_dates(spec: dict, expected: tuple) -> None:
    assert Period.model_validate(spec).to_range() == expected


def test_incomplete_periods_are_rejected() -> None:
    with pytest.raises(PlanError):
        Period(half=1).to_range()


def test_placed_orders_use_orders_placed_and_comparisons_keep_every_period() -> None:
    plan = MetricPlan(kind="metric", metric="orders", group_by=["customer_state"])
    assert normalise_plan(plan, "Which states placed the most orders?").metric == "orders_placed"
    two = MetricPlan(kind="metric", metric="aov", periods=[Period(year=2017), Period(year=2018)],
                     sort="desc", limit=1)  # fmt: skip
    assert normalise_plan(two, "AOV in 2017 vs 2018").limit is None


@pytest.mark.parametrize(
    ("plan", "question", "expected"),
    [
        ({"metric": "late_delivery_rate", "period": {"year": 2018}},
         "What share of delivered orders arrived late overall?", "names no period"),
        ({"metric": "new_customers", "group_by": ["customer_state"]},
         "Which 5 states brought in the most new customers in 2018?", "names a period"),
        ({"metric": "late_delivery_rate", "filters": {"customer_state": ["BA"]},
          "period": {"first_day": "2017-11-20", "last_day": "2017-11-26"}},
         "Late delivery rate in Black Friday week 2017?", "doesn't mention customer_state = BA"),
        ({"metric": "gmv", "group_by": ["customer_state"]}, "What was GMV in 2017?",
         "breakdown by customer_state"),
        ({"metric": "gmv", "periods": [{"year": 2017}, {"year": 2018}]},
         "How much did GMV grow from 2017 to 2018?", "compare"),
        ({"metric": "gmv"}, "Which month had the highest GMV?", "needs that grain"),
    ],
)  # fmt: skip
def test_plan_checks_catch_misreadings(plan: dict, question: str, expected: str) -> None:
    problems = plan_problems(MetricPlan(kind="metric", **plan), question)
    assert any(expected in p for p in problems), problems


def test_plan_checks_accept_state_names_and_named_values() -> None:
    plan = MetricPlan(kind="metric", metric="orders", filters={"customer_state": ["SP"]},
                      period=Period(year=2018))  # fmt: skip
    assert plan_problems(plan, "How many orders came from São Paulo state in 2018?") == []


# --- D42-D45: new plan options, normalisation, shape checks ---


def test_time_units_in_group_by_and_filters_are_moved() -> None:
    plan = MetricPlan(kind="metric", metric="new_customers", group_by=["month"],
                      period=Period(year=2017))  # fmt: skip
    assert normalise_plan(plan, "New customers in each month of 2017").grain == "month"
    day = MetricPlan(kind="metric", metric="orders_placed", filters={"date": ["2017-11-24"]})
    fixed = normalise_plan(day, "Orders placed on 24 November 2017")
    assert fixed.filters == {} and fixed.period.to_range()[0] == date(2017, 11, 24)


def test_several_values_in_a_comparison_become_a_breakdown() -> None:
    plan = MetricPlan(kind="metric", metric="gmv", filters={"customer_state": ["SP", "RJ"]})
    assert normalise_plan(plan, "Compare GMV between SP and RJ").group_by == ["customer_state"]
    assert normalise_plan(plan, "Total GMV of SP and RJ together").group_by == []


NEW_CHECKS = [
    ({"metric": "avg_delivery_days", "grain": "day"},
     "On average, how many days does delivery take?", "remove grain"),
    ({"metric": "gmv", "share_of": {"customer_state": ["SP"]}},
     "Top categories by GMV in SP", "not share_of"),
    ({"metric": "active_sellers", "period": {"year": 2018}},
     "How many sellers sold in August 2018?", "month 8"),
    ({"metric": "orders", "grain": "month", "period": {"year": 2018}},
     "Orders month-over-month in 2018", '"change"'),
]  # fmt: skip


@pytest.mark.parametrize(("plan", "question", "expected"), NEW_CHECKS)
def test_new_plan_checks(plan: dict, question: str, expected: str) -> None:
    problems = plan_problems(MetricPlan(kind="metric", **plan), question)
    assert any(expected in p for p in problems), problems


def test_new_query_shapes_compile_and_run_on_the_tiny_warehouse(tiny_warehouse) -> None:
    import duckdb

    con = duckdb.connect(str(tiny_warehouse), read_only=True)
    change = compile_metric(
        LAYER, "orders_placed", grain="month", start=date(2018, 2, 1), change="absolute"
    )
    # February (1 order) vs January (2): January is fetched though the range starts in Feb.
    assert con.execute(change).fetchall() == [(date(2018, 2, 1), 1, -1)]
    compare = compile_metric(
        LAYER, "gmv", dimensions=["customer_state"], compare="difference",
        periods=[(date(2017, 1, 1), date(2018, 1, 1)), (date(2018, 1, 1), date(2018, 9, 1))],
    )  # fmt: skip
    assert "JOIN p2 USING (customer_state)" in compare
    grouped = compile_metric(LAYER, "gmv", dimensions=["customer_state"], min_group_size=2)
    assert con.execute(grouped).fetchall() == [("RJ", 250.0)]


def test_dimension_filters_apply_whenever_the_dimension_is_used() -> None:
    sql = compile_metric(LAYER, "avg_review_score", dimensions=["delivery_status"])
    assert "(delivery_status IS NOT NULL)" in sql


def test_shape_checks_catch_identical_columns_missing_first_change_and_single_values() -> None:
    same = result_problems(["yr", "a", "b"], [(date(2017, 1, 1), 0.5, 0.5)], LAYER, False)
    assert any("identical" in p for p in same)
    lag = result_problems(
        ["m", "chg"], [(date(2018, 1, 1), None), (date(2018, 2, 1), 0.1)], LAYER, False
    )
    assert any("previous period" in p for p in lag)
    one = result_problems(["aov"], [(137.4,)], LAYER, False, "First vs repeat orders: AOV?")
    assert any("one value" in p for p in one)
