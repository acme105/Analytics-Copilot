"""Unit tests for the semantic layer file and compiler. No warehouse needed."""

from datetime import date

import pytest

from analytics_copilot.semantic import compile_metric, load_semantic_layer

LAYER = load_semantic_layer()


def test_layer_has_24_metrics_with_complete_definitions() -> None:
    assert len(LAYER.metrics) == 24
    for name, metric in LAYER.metrics.items():
        assert metric.description and metric.expression and metric.unit, name
        assert metric.synonyms, f"{name} needs synonyms for retrieval"


def test_required_dimensions_are_defined() -> None:
    assert {"customer_state", "product_category", "payment_type", "seller_tier"} <= set(
        LAYER.dimensions
    )
    assert LAYER.time_grains == ["day", "week", "month", "quarter", "year"]


def test_compile_applies_window_filters_grain_and_dimensions() -> None:
    sql = compile_metric(LAYER, "aov", grain="month", dimensions=["customer_state"])
    assert "DATE_TRUNC('month', purchased_at)" in sql
    assert "purchased_at >= DATE '2017-01-01'" in sql
    assert "purchased_at < DATE '2018-09-01'" in sql
    assert "(is_valid)" in sql and "(items > 0)" in sql
    assert "customer_state AS customer_state" in sql
    assert "GROUP BY ALL" in sql


def test_compile_without_grain_or_dimensions_returns_one_row_query() -> None:
    sql = compile_metric(LAYER, "gmv", start=date(2018, 1, 1), end=date(2018, 2, 1))
    assert "GROUP BY" not in sql
    assert "DATE '2018-01-01'" in sql and "DATE '2018-02-01'" in sql


def test_filter_values_are_quoted_safely() -> None:
    sql = compile_metric(LAYER, "gmv", filters={"customer_state": ["SP", "x' OR 1=1 --"]})
    assert "IN ('SP', 'x'' OR 1=1 --')" in sql


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"metric_name": "profit"}, KeyError),
        ({"metric_name": "gmv", "dimensions": ["customer_age"]}, KeyError),
        ({"metric_name": "gmv", "filters": {"city": "Recife"}}, KeyError),
        ({"metric_name": "gmv", "grain": "decade"}, ValueError),
    ],
)
def test_compile_rejects_unknown_inputs(kwargs: dict, error: type[Exception]) -> None:
    with pytest.raises(error):
        compile_metric(LAYER, **kwargs)
