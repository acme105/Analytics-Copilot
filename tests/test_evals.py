"""Eval harness: result matching (D20), failure labels, the golden set and the runner."""

import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

from analytics_copilot.evals.golden import (
    HOLDOUT_PATH,
    SMOKE_QUESTIONS,
    GoldenItem,
    load_golden,
    near_duplicates,
)
from analytics_copilot.evals.report import summarise, to_markdown, wilson_interval
from analytics_copilot.evals.runner import CachingLLM, run_eval, run_gold, write_results
from analytics_copilot.evals.scoring import classify_failure, compare_results
from analytics_copilot.planner import PLAN_EXAMPLES

from .conftest import EXAMPLES, IN_SCOPE, FakeLLM, sql_reply

WAREHOUSE = Path("warehouse/olist.duckdb")

# --- matching rules ---


def test_extra_predicted_columns_are_ignored() -> None:
    assert compare_results([(0.0679,)], [[0.06791, 4210]], ordered=False).correct


def test_columns_match_by_content_in_any_position() -> None:
    gold = [("SP", 100.0), ("RJ", 80.0)]
    assert compare_results(gold, [[80.0, "RJ"], [100.0, "SP"]], ordered=False).correct


def test_ratio_shown_as_percentage_matches_and_is_flagged() -> None:
    result = compare_results([(0.0679,)], [[6.79]], ordered=False)
    assert result.correct and result.scaled_columns == [0]


def test_float_tolerance_is_relative_one_in_a_thousand() -> None:
    assert compare_results([(43428.0,)], [[43426]], ordered=False).correct  # 0.005% apart
    assert not compare_results([(43426,)], [[40930]], ordered=False).correct  # date-field trap


def test_row_order_only_matters_when_ordered() -> None:
    gold = [("SP", 3.0), ("RJ", 2.0)]
    swapped = [["RJ", 2.0], ["SP", 3.0]]
    assert compare_results(gold, swapped, ordered=False).correct
    assert not compare_results(gold, swapped, ordered=True).correct


def test_dates_and_midnight_timestamps_are_equal() -> None:
    gold = [(date(2018, 1, 1), 5.0)]
    assert compare_results(gold, [["2018-01-01T00:00:00", 5.0]], ordered=False).correct
    assert compare_results(gold, [[datetime(2018, 1, 1), 5.0]], ordered=False).correct


def test_wrong_row_count_or_values_fail_with_a_reason() -> None:
    assert "row count" in compare_results([(1,)], [[1], [2]], ordered=False).reason
    result = compare_results([("SP", 1.0), ("RJ", 2.0)], [["SP", 2.0], ["RJ", 1.0]], False)
    assert not result.correct and result.reason


def test_values_must_line_up_by_row_not_just_by_column() -> None:
    # Both columns match as sets, but the pairs are wrong.
    gold = [("a", 1.0), ("b", 2.0)]
    assert not compare_results(gold, [["a", 2.0], ["b", 1.0]], ordered=False).correct


# --- failure labels ---

GOLD = """SELECT AVG(CASE WHEN is_late THEN 1 ELSE 0 END) FROM fct_orders
          WHERE is_delivered
            AND purchased_at >= DATE '2018-01-01' AND purchased_at < DATE '2018-09-01'"""


@pytest.mark.parametrize(
    ("pred", "error", "label"),
    [
        (None, "Unknown column profit.", "hallucinated_column"),
        ("SELECT AVG(price) FROM fct_order_items", None, "wrong_table_or_join"),
        (
            "SELECT AVG(CASE WHEN is_late THEN 1 ELSE 0 END) FROM fct_orders "
            "WHERE purchased_at >= DATE '2018-01-01' AND purchased_at < DATE '2018-09-01'",
            None,
            "wrong_metric_definition",
        ),
        (
            "SELECT AVG(CASE WHEN is_late THEN 1 ELSE 0 END) FROM fct_orders "
            "WHERE is_delivered AND delivered_at >= DATE '2018-01-01'",
            None,
            "wrong_filter",
        ),
    ],
)
def test_failure_labels(pred: str | None, error: str | None, label: str) -> None:
    assert classify_failure(pred, GOLD, error) == label


def test_changed_grain_is_a_time_grain_failure() -> None:
    template = (
        "SELECT DATE_TRUNC('{}', purchased_at), COUNT(*) FROM fct_orders WHERE is_valid GROUP BY 1"
    )
    gold, pred = template.format("month"), template.format("week")
    assert classify_failure(pred, gold, None) == "wrong_time_grain"


# --- golden set ---


def test_golden_set_has_the_planned_mix() -> None:
    counts = Counter(item.difficulty for item in load_golden())
    assert counts == {"easy": 40, "medium": 45, "hard": 20, "refuse": 15}


def test_golden_questions_are_not_near_copies_of_examples_or_tuning_questions() -> None:
    others = [e.question for e in EXAMPLES] + SMOKE_QUESTIONS + [q for q, _ in PLAN_EXAMPLES]
    assert near_duplicates(load_golden(), others, threshold=0.6) == []


def test_refusal_items_have_no_gold_sql_and_answer_items_do() -> None:
    with pytest.raises(ValueError):
        GoldenItem(id="x", question="q", difficulty="easy", category="c")
    with pytest.raises(ValueError):
        GoldenItem(
            id="x", question="q", difficulty="refuse", category="c",
            expected_behaviour="refuse", gold_sql="SELECT 1",
        )  # fmt: skip


@pytest.mark.skipif(not WAREHOUSE.exists(), reason="run `make warehouse` first")
async def test_every_gold_query_runs_and_returns_rows() -> None:
    gold = await run_gold(load_golden(), WAREHOUSE)
    assert len(gold) == 105
    assert all(result.rows for result in gold.values())


# --- runner and report ---


async def test_runner_scores_answers_refusals_and_caches_replies(
    make_pipeline, tmp_path: Path
) -> None:
    items = [
        GoldenItem(
            id="t1", question="GMV by state", difficulty="easy", category="gmv",
            gold_sql="SELECT customer_state, SUM(price) FROM fct_order_items "
            "WHERE is_valid GROUP BY 1 ORDER BY 1",
        ),
        GoldenItem(
            id="t2", question="Profit?", difficulty="refuse", category="out_of_scope",
            expected_behaviour="refuse",
        ),
    ]  # fmt: skip
    replies = [
        IN_SCOPE,
        sql_reply(
            "SELECT customer_state, SUM(price) AS gmv FROM fct_order_items "
            "WHERE is_valid GROUP BY 1 ORDER BY 1"
        ),  # fmt: skip
        "RJ had R$ 250 of GMV and SP had R$ 100.",
        {"in_scope": False, "reason": "No profit data."},
    ]
    pipeline, fake = make_pipeline(replies)
    cache_path = tmp_path / "cache.jsonl"
    pipeline.llm = CachingLLM(fake, cache_path, "fake-model")
    gold = await run_gold(items, pipeline.settings.warehouse_path)

    records = await run_eval(pipeline, items, ["semantic"], gold)
    assert [r["outcome"] for r in records] == ["correct", "correct_refusal"]
    assert len(cache_path.read_text().splitlines()) == 4

    # Second run: every reply comes from the cache, so the fake LLM is never called.
    pipeline.llm = CachingLLM(FakeLLM([]), cache_path, "fake-model")
    again = await run_eval(pipeline, items, ["semantic"], gold)
    assert [r["outcome"] for r in again] == ["correct", "correct_refusal"]
    assert [r["cached_calls"] for r in again] == [3, 1]

    run = {"timestamp_utc": "T", "provider": "p", "model": "org/m", "quantisation": "q",
           "hardware": "h", "code_version": "c"}  # fmt: skip
    json_path, md_path, summary = write_results(run, records, tmp_path)
    assert summary["all_items"]["semantic"]["execution_accuracy"] == 1.0
    assert summary["all_items"]["semantic"]["correct_refusal_rate"] == 1.0
    assert summary["verified_only"] is None
    assert "Provisional" in md_path.read_text()
    assert json.loads(json_path.read_text())["records"][0]["id"] == "t1"


def test_wilson_interval_is_wide_for_small_samples() -> None:
    low, high = wilson_interval(8, 10)
    assert 0.44 < low < 0.5 and 0.94 < high < 0.97


def test_report_shows_verified_numbers_when_any_item_is_verified() -> None:
    base = {"mode": "semantic", "difficulty": "easy", "category": "gmv", "expected": "answer",
            "status": "ok", "outcome": "correct", "correct": True, "failure_label": None,
            "grounding": {"passed": True, "fallback_used": False}, "repaired": False,
            "latency_ms": {"total": 1000.0}, "cached_calls": 0,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "llm_calls": 3,
                      "parse_failures": 0}}  # fmt: skip
    records = [
        {**base, "id": "a", "verified": True},
        {**base, "id": "b", "verified": False, "correct": False, "outcome": "wrong"},
    ]
    summary = summarise(records)
    assert summary["verified_only"]["semantic"]["execution_accuracy"] == 1.0
    assert summary["all_items"]["semantic"]["execution_accuracy"] == 0.5
    run = {"model": "m", "timestamp_utc": "t", "provider": "p", "quantisation": "q",
           "hardware": "h", "code_version": "c"}  # fmt: skip
    assert "Provisional" not in to_markdown(run, summary)


async def test_fresh_runs_record_without_replaying(tmp_path: Path) -> None:
    path = tmp_path / "cache.jsonl"
    first = CachingLLM(FakeLLM(["a"]), path, "m", replay=False)
    await first.complete("sql", [{"role": "user", "content": "q"}])
    second = CachingLLM(FakeLLM(["b"]), path, "m", replay=False)
    assert (await second.complete("sql", [{"role": "user", "content": "q"}])).text == "b"
    replaying = CachingLLM(FakeLLM([]), path, "m", replay=True)  # latest recording wins
    assert (await replaying.complete("sql", [{"role": "user", "content": "q"}])).text == "b"


async def test_concurrent_runs_keep_item_order_and_per_question_cache_counts(
    make_pipeline, tmp_path: Path
) -> None:
    items = [
        GoldenItem(id=f"t{i}", question=f"Profit {i}?", difficulty="refuse",
                   category="out_of_scope", expected_behaviour="refuse")
        for i in range(4)
    ]  # fmt: skip
    replies = [{"in_scope": False, "reason": f"no {i}"} for i in range(4)]
    pipeline, fake = make_pipeline(replies)
    cache_path = tmp_path / "cache.jsonl"
    pipeline.llm = CachingLLM(fake, cache_path, "m", replay=False)
    await run_eval(pipeline, items, ["semantic"], {})  # record, sequentially
    pipeline.llm = CachingLLM(FakeLLM([]), cache_path, "m")
    records = await run_eval(pipeline, items, ["semantic"], {}, concurrency=3)
    assert [r["id"] for r in records] == ["t0", "t1", "t2", "t3"]
    assert [r["cached_calls"] for r in records] == [1, 1, 1, 1]


def test_a_year_may_come_back_as_its_first_of_january() -> None:
    gold = [(2017, 4.28), (2017, 2.32), (2018, 4.30)]
    pred = [
        ["2017-01-01", 4.28, "on_time"],
        ["2017-01-01", 2.32, "late"],
        ["2018-01-01", 4.30, "x"],
    ]
    assert compare_results(gold, pred, ordered=False).correct
    assert not compare_results([(2017, 1.0)], [["2017-02-01", 1.0]], ordered=False).correct


def test_holdout_set_is_separate_and_not_in_any_prompt_material() -> None:
    holdout = load_golden(HOLDOUT_PATH)
    assert len(holdout) == 40 and {i.split for i in holdout} == {"holdout"}
    assert Counter(i.difficulty for i in holdout) == {
        "easy": 12,
        "medium": 12,
        "hard": 10,
        "refuse": 6,
    }
    prompt_material = (
        [e.question for e in EXAMPLES] + SMOKE_QUESTIONS + [q for q, _ in PLAN_EXAMPLES]
    )
    assert near_duplicates(holdout, prompt_material, threshold=0.6) == []
    assert near_duplicates(holdout, [i.question for i in load_golden()], threshold=0.6) == []


@pytest.mark.skipif(not WAREHOUSE.exists(), reason="run `make warehouse` first")
async def test_every_holdout_gold_query_runs_and_returns_rows() -> None:
    gold = await run_gold(load_golden(HOLDOUT_PATH), WAREHOUSE)
    assert len(gold) == 34 and all(result.rows for result in gold.values())
