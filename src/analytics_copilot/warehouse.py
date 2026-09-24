"""Build the Olist DuckDB warehouse: raw tables, staging views and mart tables.

Layers:
    raw_*   the CSVs loaded as text, untouched, so typing is explicit and reviewable;
    stg_*   views that type every column and apply the cleaning rules commented below;
    fct_*, dim_*  mart tables the semantic layer queries. They are materialised once
            at build time because they hold window functions.

Run with ``make warehouse``, or on Kaggle with
``python -m analytics_copilot.warehouse --data-dir /kaggle/input/brazilian-ecommerce``.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import duckdb

from analytics_copilot.profiling import RAW_FILES

DEFAULT_WAREHOUSE = Path("warehouse/olist.duckdb")

STAGING_SQL = """
CREATE OR REPLACE VIEW stg_orders AS
SELECT
    order_id,
    customer_id,
    order_status,
    CAST(order_purchase_timestamp AS TIMESTAMP) AS purchased_at,
    CAST(order_approved_at AS TIMESTAMP) AS approved_at,
    CAST(order_delivered_carrier_date AS TIMESTAMP) AS shipped_at,
    -- Rule: a delivery date only counts on delivered orders (drops it on 6 canceled orders).
    CASE WHEN order_status = 'delivered'
         THEN CAST(order_delivered_customer_date AS TIMESTAMP) END AS delivered_at,
    CAST(order_estimated_delivery_date AS TIMESTAMP) AS estimated_delivery_at,
    -- Rule: canceled and unavailable orders were never fulfilled, so they are not "valid"
    -- orders. They stay in the table so cancellation rate can see them.
    order_status NOT IN ('canceled', 'unavailable') AS is_valid
FROM raw_orders;

CREATE OR REPLACE VIEW stg_order_items AS
SELECT
    order_id,
    CAST(order_item_id AS INTEGER) AS order_item_id,
    product_id,
    seller_id,
    -- Seller shipping deadline. Some run into 2020; never used for time windows.
    CAST(shipping_limit_date AS TIMESTAMP) AS shipping_limit_at,
    CAST(price AS DOUBLE) AS price,
    CAST(freight_value AS DOUBLE) AS freight_value
FROM raw_order_items;

CREATE OR REPLACE VIEW stg_order_payments AS
SELECT
    order_id,
    CAST(payment_sequential AS INTEGER) AS payment_sequential,
    payment_type,
    -- Rule: 0 instalments (2 rows) means paid in full, so treat as 1.
    GREATEST(CAST(payment_installments AS INTEGER), 1) AS installments,
    CAST(payment_value AS DOUBLE) AS payment_value
FROM raw_order_payments;

CREATE OR REPLACE VIEW stg_order_reviews AS
SELECT
    order_id,
    review_id,
    CAST(review_score AS INTEGER) AS review_score,
    CAST(review_creation_date AS TIMESTAMP) AS review_created_at,
    CAST(review_answer_timestamp AS TIMESTAMP) AS review_answered_at,
    review_comment_message IS NOT NULL AS has_comment
FROM raw_order_reviews
-- Rule: 547 orders have several reviews. Keep the latest answered one per order.
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY order_id
    ORDER BY CAST(review_answer_timestamp AS TIMESTAMP) DESC, review_id
) = 1;

CREATE OR REPLACE VIEW stg_customers AS
SELECT
    customer_id,
    -- customer_id is issued per order; customer_unique_id is the person (DECISIONS D4).
    customer_unique_id,
    customer_zip_code_prefix AS zip_prefix,
    customer_city AS city,
    UPPER(customer_state) AS customer_state
FROM raw_customers;

CREATE OR REPLACE VIEW stg_products AS
SELECT
    p.product_id,
    p.product_category_name AS category_pt,
    -- Rule: English name; fall back to the Portuguese name for the 2 untranslated
    -- categories, and 'unknown' for the 610 products with no category.
    COALESCE(t.product_category_name_english, p.product_category_name, 'unknown') AS category,
    -- The source misspells these columns as "lenght".
    CAST(p.product_name_lenght AS INTEGER) AS name_length,
    CAST(p.product_description_lenght AS INTEGER) AS description_length,
    CAST(p.product_photos_qty AS INTEGER) AS photos_qty,
    CAST(p.product_weight_g AS INTEGER) AS weight_g
FROM raw_products p
LEFT JOIN raw_category_translation t USING (product_category_name);

CREATE OR REPLACE VIEW stg_sellers AS
SELECT
    seller_id,
    seller_zip_code_prefix AS zip_prefix,
    seller_city AS city,
    UPPER(seller_state) AS seller_state
FROM raw_sellers;
"""

MART_SQL = """
-- Seller tier per seller and month, from GMV in the 3 full months BEFORE that month, so a
-- tier never uses the month it describes (no look-ahead). Among sellers with trailing GMV:
-- top 10% = 'top', next 40% = 'mid', the rest = 'long_tail'. No trailing GMV = 'new_or_dormant'.
CREATE OR REPLACE TABLE dim_seller_month AS
WITH seller_month_gmv AS (
    SELECT i.seller_id, DATE_TRUNC('month', o.purchased_at) AS month, SUM(i.price) AS gmv
    FROM stg_order_items i
    JOIN stg_orders o USING (order_id)
    WHERE o.is_valid
    GROUP BY 1, 2
),
seller_months AS (
    -- Every month the seller had any order, valid or not, so each order gets a tier.
    SELECT DISTINCT i.seller_id, DATE_TRUNC('month', o.purchased_at) AS month
    FROM stg_order_items i
    JOIN stg_orders o USING (order_id)
),
trailing_gmv AS (
    SELECT sm.seller_id, sm.month, COALESCE(SUM(g.gmv), 0) AS trailing_3m_gmv
    FROM seller_months sm
    LEFT JOIN seller_month_gmv g
        ON g.seller_id = sm.seller_id
       AND g.month >= sm.month - INTERVAL 3 MONTH
       AND g.month < sm.month
    GROUP BY 1, 2
),
ranked AS (
    SELECT seller_id, month,
           PERCENT_RANK() OVER (PARTITION BY month ORDER BY trailing_3m_gmv DESC) AS pct_rank
    FROM trailing_gmv
    WHERE trailing_3m_gmv > 0
)
SELECT
    t.seller_id,
    t.month,
    t.trailing_3m_gmv,
    CASE
        WHEN r.pct_rank IS NULL THEN 'new_or_dormant'
        WHEN r.pct_rank < 0.10 THEN 'top'
        WHEN r.pct_rank < 0.50 THEN 'mid'
        ELSE 'long_tail'
    END AS seller_tier
FROM trailing_gmv t
LEFT JOIN ranked r USING (seller_id, month);

-- Primary item and payment per order: the highest-value one. Used to give order-grain
-- facts a single category, seller tier and payment type (1.3% of orders have several
-- sellers, 0.8% several categories, 2.3% several payment types).
CREATE OR REPLACE TEMP VIEW order_primary_item AS
SELECT i.order_id, i.seller_id, p.category AS product_category
FROM stg_order_items i
JOIN stg_products p USING (product_id)
QUALIFY ROW_NUMBER() OVER (PARTITION BY i.order_id ORDER BY i.price DESC, i.order_item_id) = 1;

CREATE OR REPLACE TEMP VIEW order_primary_payment AS
SELECT order_id, payment_type
FROM stg_order_payments
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY order_id ORDER BY payment_value DESC, payment_sequential
) = 1;

-- One row per order, every status. Dimensions come from the order's primary item/payment.
CREATE OR REPLACE TABLE fct_orders AS
WITH items AS (
    SELECT order_id, COUNT(*) AS items, SUM(price) AS gmv, SUM(freight_value) AS freight
    FROM stg_order_items
    GROUP BY 1
),
customer_sequence AS (
    -- Nth valid order for this person over the full history (so 2017 "new" is truly new),
    -- and when they placed their next valid order.
    SELECT o.order_id,
           o.purchased_at,
           ROW_NUMBER() OVER w AS customer_order_number,
           LEAD(o.purchased_at) OVER w AS next_order_at
    FROM stg_orders o
    JOIN stg_customers c USING (customer_id)
    WHERE o.is_valid
    WINDOW w AS (PARTITION BY c.customer_unique_id ORDER BY o.purchased_at, o.order_id)
),
data_cutoff AS (
    -- The last valid purchase in the data: nothing after it can be observed.
    SELECT MAX(purchased_at) AS last_purchase_at FROM stg_orders WHERE is_valid
)
SELECT
    o.order_id,
    c.customer_unique_id,
    c.customer_state,
    o.order_status,
    o.is_valid,
    -- Rule: 'unavailable' means the order was placed and then could not be fulfilled,
    -- so it counts as a cancellation alongside 'canceled'.
    o.order_status IN ('canceled', 'unavailable') AS is_canceled,
    o.purchased_at,
    o.delivered_at,
    o.estimated_delivery_at,
    COALESCE(it.items, 0) AS items,
    COALESCE(it.gmv, 0) AS gmv,
    COALESCE(it.freight, 0) AS freight,
    COALESCE(pi.product_category, 'unknown') AS product_category,
    COALESCE(sm.seller_tier, 'new_or_dormant') AS seller_tier,
    COALESCE(pp.payment_type, 'not_defined') AS payment_type,
    -- Rule: 8 'delivered' orders have no delivery date; they are not counted as delivered.
    o.delivered_at IS NOT NULL AS is_delivered,
    DATE_DIFF('second', o.purchased_at, o.delivered_at) / 86400.0 AS delivery_days,
    -- Rule: the estimate is a calendar date, so compare dates. Arriving at 15:00 on the
    -- estimated day is on time, not late.
    CASE WHEN o.delivered_at IS NOT NULL
         THEN CAST(o.delivered_at AS DATE) > CAST(o.estimated_delivery_at AS DATE) END AS is_late,
    r.review_score,
    cs.customer_order_number,
    -- 90-day repeat: this order's next valid order came within 90 days. The metric reads it
    -- on first orders only, so it means "second order within 90 days of the first".
    COALESCE(cs.next_order_at <= cs.purchased_at + INTERVAL 90 DAY, FALSE) AS repeat_within_90d,
    -- Rule: only orders placed 90+ days before the data cutoff have a full 90-day window to
    -- repeat in; later ones would bias the repeat rate down.
    o.purchased_at + INTERVAL 90 DAY <= dc.last_purchase_at AS has_90d_followup,
    -- Cohort columns (D43): when the person's next valid order came, and how long after.
    cs.next_order_at,
    DATE_DIFF('second', o.purchased_at, cs.next_order_at) / 86400.0 AS days_to_next_order,
    -- Comparison dimensions (D42): named segments so "late vs on time" and "first vs
    -- repeat orders" are one breakdown, not a SQL trick. NULL where they don't apply.
    CASE WHEN o.delivered_at IS NOT NULL THEN
        CASE WHEN CAST(o.delivered_at AS DATE) > CAST(o.estimated_delivery_at AS DATE)
             THEN 'late' ELSE 'on_time' END
    END AS delivery_status,
    CASE WHEN cs.customer_order_number = 1 THEN 'first_order'
         WHEN cs.customer_order_number > 1 THEN 'repeat_order'
    END AS customer_type
FROM stg_orders o
CROSS JOIN data_cutoff dc
JOIN stg_customers c USING (customer_id)
LEFT JOIN items it USING (order_id)
LEFT JOIN order_primary_item pi USING (order_id)
LEFT JOIN dim_seller_month sm
    ON sm.seller_id = pi.seller_id AND sm.month = DATE_TRUNC('month', o.purchased_at)
LEFT JOIN order_primary_payment pp USING (order_id)
LEFT JOIN stg_order_reviews r USING (order_id)
LEFT JOIN customer_sequence cs USING (order_id);

-- One row per order item, with the item's own category and seller tier.
CREATE OR REPLACE TABLE fct_order_items AS
SELECT
    i.order_id,
    i.order_item_id,
    i.product_id,
    i.seller_id,
    o.customer_unique_id,
    o.customer_state,
    o.order_status,
    o.is_valid,
    o.purchased_at,
    i.price,
    i.freight_value,
    p.category AS product_category,
    COALESCE(sm.seller_tier, 'new_or_dormant') AS seller_tier,
    o.payment_type,
    o.delivery_status,
    o.customer_type
FROM stg_order_items i
JOIN fct_orders o USING (order_id)
JOIN stg_products p USING (product_id)
LEFT JOIN dim_seller_month sm
    ON sm.seller_id = i.seller_id AND sm.month = DATE_TRUNC('month', o.purchased_at);

-- One row per payment, with the order's dimensions.
CREATE OR REPLACE TABLE fct_payments AS
SELECT
    pay.order_id,
    pay.payment_sequential,
    pay.payment_type,
    pay.installments,
    pay.payment_value,
    o.customer_state,
    o.order_status,
    o.is_valid,
    o.purchased_at,
    o.product_category,
    o.seller_tier,
    o.delivery_status,
    o.customer_type
FROM stg_order_payments pay
JOIN fct_orders o USING (order_id);
"""


def load_raw(con: duckdb.DuckDBPyConnection, data_dir: Path) -> None:
    """Load each Olist CSV into a ``raw_<name>`` table with every column as text."""
    for table, file_name in RAW_FILES.items():
        path = data_dir / file_name
        if not path.exists():
            raise FileNotFoundError(f"Missing {path}. Run `make data` or set OLIST_DATA_DIR.")
        escaped = str(path).replace("'", "''")
        con.execute(
            f"CREATE OR REPLACE TABLE raw_{table} AS "
            f"SELECT * FROM read_csv('{escaped}', header = true, all_varchar = true)"
        )


def build_warehouse(data_dir: Path, out: Path = DEFAULT_WAREHOUSE) -> Path:
    """Build the warehouse file from the raw CSVs in ``data_dir`` and return its path.

    The file is rebuilt from scratch each time, so the build is reproducible.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    with duckdb.connect(str(out)) as con:
        load_raw(con, data_dir)
        con.execute(STAGING_SQL)
        con.execute(MART_SQL)
    return out


def main() -> None:
    """CLI entry point: build the warehouse from ``--data-dir`` (or OLIST_DATA_DIR)."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--data-dir", type=Path, default=Path(os.environ.get("OLIST_DATA_DIR", "data/raw"))
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_WAREHOUSE)
    args = parser.parse_args()

    path = build_warehouse(args.data_dir, args.out)
    with duckdb.connect(str(path), read_only=True) as con:
        for (table,) in con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name LIKE 'fct_%' OR table_name LIKE 'dim_%' ORDER BY 1"
        ).fetchall():
            rows = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"{table:<20} {rows:>9,}")
    print(f"Built {path}")


if __name__ == "__main__":
    main()
