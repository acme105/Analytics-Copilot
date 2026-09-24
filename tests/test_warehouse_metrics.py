"""Integration tests: every metric runs against the built warehouse and returns a
plausible value, and three metrics match an independent pandas calculation.

Skipped when the warehouse or raw data is missing (build with ``make warehouse``).
"""

from pathlib import Path

import duckdb
import pandas as pd
import pytest

from analytics_copilot.semantic import compile_metric, load_semantic_layer

WAREHOUSE = Path("warehouse/olist.duckdb")
RAW = Path("data/raw")
LAYER = load_semantic_layer()
START, END = LAYER.default_window.start, LAYER.default_window.end

# Business sanity bounds for the full default window, set from domain expectations for a
# Brazilian marketplace. They catch broken joins and filters, not small definition changes.
PLAUSIBLE = {
    "gmv": (1e7, 2e7),
    "orders": (90_000, 100_000),
    "aov": (100, 200),
    "items_per_order": (1.0, 1.5),
    "active_customers": (85_000, 97_000),
    "new_customers": (85_000, 97_000),
    "repeat_purchase_rate": (0.01, 0.10),
    "active_sellers": (2_500, 3_100),
    "gmv_per_seller": (2_000, 8_000),
    "freight_ratio": (0.10, 0.30),
    "on_time_delivery_rate": (0.85, 0.97),
    "avg_delivery_days": (8, 20),
    "late_delivery_rate": (0.03, 0.15),
    "cancellation_rate": (0.0, 0.02),
    "avg_review_score": (3.8, 4.4),
    "low_review_share": (0.08, 0.20),
    "avg_installments": (2.5, 4.5),
    "credit_card_payment_share": (0.65, 0.90),
}

pytestmark = pytest.mark.skipif(
    not WAREHOUSE.exists() or not RAW.exists(), reason="run `make warehouse` first"
)


@pytest.fixture(scope="module")
def con() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(str(WAREHOUSE), read_only=True)
    yield connection
    connection.close()


def metric_value(con: duckdb.DuckDBPyConnection, name: str) -> float:
    return con.execute(compile_metric(LAYER, name)).fetchone()[0]


def test_every_metric_has_a_plausibility_bound() -> None:
    assert set(PLAUSIBLE) == set(LAYER.metrics)


@pytest.mark.parametrize("name", sorted(LAYER.metrics))
def test_metric_value_is_plausible(con: duckdb.DuckDBPyConnection, name: str) -> None:
    low, high = PLAUSIBLE[name]
    value = metric_value(con, name)
    assert value is not None and low <= value <= high, f"{name} = {value}"


@pytest.mark.parametrize("name", sorted(LAYER.metrics))
@pytest.mark.parametrize("dimension", sorted(LAYER.dimensions))
def test_metric_compiles_by_month_and_dimension(
    con: duckdb.DuckDBPyConnection, name: str, dimension: str
) -> None:
    rows = con.execute(
        compile_metric(LAYER, name, grain="month", dimensions=[dimension])
    ).fetchall()
    assert rows
    assert len({r[0] for r in rows}) == 20  # 20 months in the default window


def test_orders_on_time_and_late_rates_sum_to_one(con: duckdb.DuckDBPyConnection) -> None:
    total = metric_value(con, "on_time_delivery_rate") + metric_value(con, "late_delivery_rate")
    assert total == pytest.approx(1.0)


# --- Independent cross-checks: raw CSVs + pandas, no warehouse code involved. ---


@pytest.fixture(scope="module")
def raw_orders() -> pd.DataFrame:
    orders = pd.read_csv(
        RAW / "olist_orders_dataset.csv",
        parse_dates=[
            "order_purchase_timestamp",
            "order_delivered_customer_date",
            "order_estimated_delivery_date",
        ],
    )
    in_window = (orders["order_purchase_timestamp"] >= pd.Timestamp(START)) & (
        orders["order_purchase_timestamp"] < pd.Timestamp(END)
    )
    return orders[in_window]


def valid(orders: pd.DataFrame) -> pd.DataFrame:
    return orders[~orders["order_status"].isin(["canceled", "unavailable"])]


def test_gmv_matches_pandas(con: duckdb.DuckDBPyConnection, raw_orders: pd.DataFrame) -> None:
    items = pd.read_csv(RAW / "olist_order_items_dataset.csv")
    expected = items.merge(valid(raw_orders)[["order_id"]], on="order_id")["price"].sum()
    assert metric_value(con, "gmv") == pytest.approx(expected, rel=1e-9)


def test_active_customers_matches_pandas(
    con: duckdb.DuckDBPyConnection, raw_orders: pd.DataFrame
) -> None:
    customers = pd.read_csv(RAW / "olist_customers_dataset.csv")
    expected = valid(raw_orders).merge(customers, on="customer_id")["customer_unique_id"].nunique()
    assert metric_value(con, "active_customers") == expected


def test_late_delivery_rate_matches_pandas(
    con: duckdb.DuckDBPyConnection, raw_orders: pd.DataFrame
) -> None:
    delivered = raw_orders[
        (raw_orders["order_status"] == "delivered")
        & raw_orders["order_delivered_customer_date"].notna()
    ]
    late = (
        delivered["order_delivered_customer_date"].dt.normalize()
        > delivered["order_estimated_delivery_date"].dt.normalize()
    )
    assert metric_value(con, "late_delivery_rate") == pytest.approx(late.mean(), rel=1e-9)
