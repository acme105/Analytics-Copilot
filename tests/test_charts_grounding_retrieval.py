"""Chart spec selection, the grounding check, and retrieval."""

from datetime import date

from analytics_copilot.charts import build_chart_spec
from analytics_copilot.grounding import check_grounding, extract_numbers
from analytics_copilot.retrieval import top_examples, top_metrics

from .conftest import EXAMPLES, LAYER

# --- charts ---


def test_single_value_is_a_number() -> None:
    assert build_chart_spec(["gmv"], [(13_449_529.68,)], None).type == "number"


def test_time_series_is_a_line_and_can_be_a_bar_on_request() -> None:
    rows = [(date(2018, 1, 1), 10.0), (date(2018, 2, 1), 12.0)]
    assert build_chart_spec(["period", "orders"], rows, None).model_dump() == {
        "type": "line",
        "x": "period",
        "y": ["orders"],
        "series": None,
    }
    assert build_chart_spec(["period", "orders"], rows, "bar").type == "bar"


def test_category_breakdown_is_a_bar_and_an_impossible_hint_is_ignored() -> None:
    rows = [("SP", 5.0), ("RJ", 3.0)]
    assert build_chart_spec(["customer_state", "gmv"], rows, "line").type == "bar"


def test_time_series_by_category_uses_a_series() -> None:
    rows = [(date(2018, 1, 1), "SP", 1.0), (date(2018, 1, 1), "RJ", 2.0)]
    spec = build_chart_spec(["period", "customer_state", "orders"], rows, None)
    assert spec.type == "line" and spec.series == "customer_state"


def test_empty_or_wide_results_are_tables() -> None:
    assert build_chart_spec(["a"], [], None).type == "table"
    rows = [("x", "y", 1.0)]
    assert build_chart_spec(["a", "b", "c"], rows, None).type == "table"


# --- grounding ---


def test_extract_numbers_reads_units_and_scale() -> None:
    values = {
        m.text: m.value for m in extract_numbers("GMV was R$ 13.4M, up 6.8%, from 1,234 orders")
    }
    assert values == {"R$ 13.4M": 13_400_000.0, "6.8%": 6.8, "1,234": 1234.0}


def test_rounded_values_percentages_and_totals_are_grounded() -> None:
    rows = [("SP", 13_449_529.68, 0.06791), ("RJ", 2_000_000.0, 0.081)]
    summary = (
        "SP had R$ 13.4M of GMV with a late rate of 6.8%. Across both states GMV was 15.45 million."
    )
    assert check_grounding(summary, rows).passed


def test_differences_and_percentage_changes_are_grounded() -> None:
    rows = [(date(2018, 1, 1), 200.0), (date(2018, 2, 1), 150.0)]
    summary = "Orders fell by 50 in February 2018, a 25% drop from January."
    assert check_grounding(summary, rows).passed


def test_invented_numbers_are_flagged() -> None:
    rows = [("SP", 100.0), ("RJ", 80.0)]
    result = check_grounding("SP led with 100 orders, and growth should reach 37% next year.", rows)
    assert not result.passed and result.ungrounded == ["37%"]


def test_numbers_from_the_question_are_allowed() -> None:
    rows = [("SP", 100.0), ("RJ", 80.0), ("MG", 70.0)]
    result = check_grounding("The top 3 states are SP, RJ and MG.", rows, "top 3 states by orders")
    assert result.passed


# --- retrieval ---


def test_synonyms_retrieve_the_right_metric() -> None:
    assert top_metrics(LAYER, "What was our revenue last year?")[0] == "gmv"
    assert top_metrics(LAYER, "How long does shipping take?")[0] == "avg_delivery_days"
    assert "late_delivery_rate" in top_metrics(LAYER, "Are delayed orders getting worse?")


def test_unrelated_question_retrieves_nothing() -> None:
    assert top_metrics(LAYER, "xyzzy plugh") == []


def test_examples_ranked_by_word_overlap() -> None:
    best = top_examples(EXAMPLES, "Which categories had the highest GMV in 2017?", k=1)
    assert "product categories had the highest GMV" in best[0].question
