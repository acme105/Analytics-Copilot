"""Phase 0 data profile for the raw Olist CSVs.

Run with ``make profile``, or on Kaggle with
``python -m analytics_copilot.profiling --data-dir /kaggle/input/brazilian-ecommerce``.
Every number in the report is computed here, so the profile can be regenerated
on any machine (locally or on Kaggle) from the same CSVs.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

import duckdb

# Short table name -> CSV file name, as published by olistbr/brazilian-ecommerce.
RAW_FILES: dict[str, str] = {
    "customers": "olist_customers_dataset.csv",
    "geolocation": "olist_geolocation_dataset.csv",
    "order_items": "olist_order_items_dataset.csv",
    "order_payments": "olist_order_payments_dataset.csv",
    "order_reviews": "olist_order_reviews_dataset.csv",
    "orders": "olist_orders_dataset.csv",
    "products": "olist_products_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
    "category_translation": "product_category_name_translation.csv",
}

ZIP_COLUMNS: dict[str, str] = {
    "customers": "customer_zip_code_prefix",
    "sellers": "seller_zip_code_prefix",
    "geolocation": "geolocation_zip_code_prefix",
}

# (label, SQL returning one number). Duplicates are rows beyond the first per key.
KEY_CHECKS: list[tuple[str, str]] = [
    ("Duplicate orders.order_id", "SELECT COUNT(*) - COUNT(DISTINCT order_id) FROM orders"),
    (
        "Duplicate customers.customer_id",
        "SELECT COUNT(*) - COUNT(DISTINCT customer_id) FROM customers",
    ),
    ("Duplicate products.product_id", "SELECT COUNT(*) - COUNT(DISTINCT product_id) FROM products"),
    ("Duplicate sellers.seller_id", "SELECT COUNT(*) - COUNT(DISTINCT seller_id) FROM sellers"),
    (
        "Duplicate order_items (order_id, order_item_id)",
        "SELECT COUNT(*) - COUNT(DISTINCT (order_id, order_item_id)) FROM order_items",
    ),
    (
        "Duplicate order_reviews.review_id",
        "SELECT COUNT(*) - COUNT(DISTINCT review_id) FROM order_reviews",
    ),
    (
        "Orphan order_items -> orders",
        "SELECT COUNT(*) FROM order_items WHERE order_id NOT IN (SELECT order_id FROM orders)",
    ),
    (
        "Orphan order_items -> products",
        """SELECT COUNT(*) FROM order_items
        WHERE product_id NOT IN (SELECT product_id FROM products)""",
    ),
    (
        "Orphan order_items -> sellers",
        "SELECT COUNT(*) FROM order_items WHERE seller_id NOT IN (SELECT seller_id FROM sellers)",
    ),
    (
        "Orphan orders -> customers",
        "SELECT COUNT(*) FROM orders WHERE customer_id NOT IN (SELECT customer_id FROM customers)",
    ),
    (
        "Orphan order_payments -> orders",
        "SELECT COUNT(*) FROM order_payments WHERE order_id NOT IN (SELECT order_id FROM orders)",
    ),
    (
        "Orphan order_reviews -> orders",
        "SELECT COUNT(*) FROM order_reviews WHERE order_id NOT IN (SELECT order_id FROM orders)",
    ),
    (
        "Orders with no items",
        "SELECT COUNT(*) FROM orders WHERE order_id NOT IN (SELECT order_id FROM order_items)",
    ),
    (
        "Orders with no payment",
        "SELECT COUNT(*) FROM orders WHERE order_id NOT IN (SELECT order_id FROM order_payments)",
    ),
    (
        "Orders with no review",
        "SELECT COUNT(*) FROM orders WHERE order_id NOT IN (SELECT order_id FROM order_reviews)",
    ),
    (
        "Customer zip prefixes missing from geolocation",
        """SELECT COUNT(DISTINCT customer_zip_code_prefix) FROM customers
        WHERE customer_zip_code_prefix NOT IN
              (SELECT geolocation_zip_code_prefix FROM geolocation)""",
    ),
]

QUIRK_CHECKS: list[tuple[str, str]] = [
    ("Distinct customer_id (one per order)", "SELECT COUNT(DISTINCT customer_id) FROM customers"),
    (
        "Distinct customer_unique_id (the real person)",
        "SELECT COUNT(DISTINCT customer_unique_id) FROM customers",
    ),
    (
        "People with more than one order",
        """SELECT COUNT(*) FROM (SELECT customer_unique_id FROM customers
        GROUP BY 1 HAVING COUNT(*) > 1)""",
    ),
    (
        "Orders with more than one review",
        "SELECT COUNT(*) FROM (SELECT order_id FROM order_reviews GROUP BY 1 HAVING COUNT(*) > 1)",
    ),
    (
        "Review ids shared by more than one order",
        """SELECT COUNT(*) FROM (SELECT review_id FROM order_reviews
        GROUP BY 1 HAVING COUNT(DISTINCT order_id) > 1)""",
    ),
    (
        "Products with no category",
        "SELECT COUNT(*) FROM products WHERE product_category_name IS NULL",
    ),
    (
        "Categories with no English translation",
        """SELECT COUNT(DISTINCT product_category_name) FROM products
        WHERE product_category_name IS NOT NULL AND product_category_name NOT IN
              (SELECT product_category_name FROM category_translation)""",
    ),
    (
        "Delivered orders with no delivery date",
        """SELECT COUNT(*) FROM orders
        WHERE order_status = 'delivered' AND order_delivered_customer_date IS NULL""",
    ),
    (
        "Canceled orders with a delivery date",
        """SELECT COUNT(*) FROM orders
        WHERE order_status = 'canceled' AND order_delivered_customer_date IS NOT NULL""",
    ),
    (
        "Orders handed to carrier before purchase",
        "SELECT COUNT(*) FROM orders WHERE order_delivered_carrier_date < order_purchase_timestamp",
    ),
    (
        "Orders where payments differ from items + freight by > 1 BRL",
        """SELECT COUNT(*) FROM
          (SELECT order_id, SUM(price + freight_value) AS billed FROM order_items GROUP BY 1) i
        JOIN (SELECT order_id, SUM(payment_value) AS paid FROM order_payments GROUP BY 1) p
          USING (order_id)
        WHERE ABS(billed - paid) > 1""",
    ),
    (
        "Payments with type 'not_defined'",
        "SELECT COUNT(*) FROM order_payments WHERE payment_type = 'not_defined'",
    ),
    (
        "Payments with 0 instalments",
        "SELECT COUNT(*) FROM order_payments WHERE payment_installments = 0",
    ),
    ("Order items with zero freight", "SELECT COUNT(*) FROM order_items WHERE freight_value = 0"),
    (
        "Geolocation exact duplicate rows",
        """SELECT COUNT(*) - (SELECT COUNT(*) FROM (SELECT DISTINCT * FROM geolocation))
        FROM geolocation""",
    ),
    (
        "Geolocation points outside Brazil's bounding box",
        """SELECT COUNT(*) FROM geolocation WHERE NOT
        (geolocation_lat BETWEEN -34 AND 6 AND geolocation_lng BETWEEN -74 AND -34)""",
    ),
    (
        "Latest order_items.shipping_limit_date year",
        "SELECT CAST(YEAR(MAX(shipping_limit_date)) AS VARCHAR) FROM order_items",
    ),
]


def connect(data_dir: Path) -> duckdb.DuckDBPyConnection:
    """Load every raw CSV into an in-memory DuckDB table named by RAW_FILES.

    Zip code prefixes are read as text so leading zeros survive.
    """
    con = duckdb.connect()
    for table, file_name in RAW_FILES.items():
        path = data_dir / file_name
        if not path.exists():
            raise FileNotFoundError(f"Missing {path}. Run `make data` or set --data-dir.")
        zip_column = ZIP_COLUMNS.get(table)
        types = f", types={{'{zip_column}': 'VARCHAR'}}" if zip_column else ""
        escaped = str(path).replace("'", "''")
        con.execute(f"CREATE TABLE {table} AS SELECT * FROM read_csv_auto('{escaped}'{types})")
    return con


def md_table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    """Render rows as a GitHub-flavoured markdown table."""

    def fmt(value: object) -> str:
        if isinstance(value, float):
            return f"{value:,.2f}"
        if isinstance(value, int):
            return f"{value:,}"
        return str(value)

    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(fmt(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


def scalar(con: duckdb.DuckDBPyConnection, sql: str) -> object:
    """Return the single value produced by ``sql``."""
    return con.execute(sql).fetchone()[0]


def build_report(con: duckdb.DuckDBPyConnection) -> str:
    """Compute the full Phase 0 profile and return it as markdown."""
    sections: list[str] = [
        "# Phase 0 data profile: Olist\n",
        "Generated by `make profile` (`analytics_copilot.profiling`).\n",
    ]

    counts = [
        (t, scalar(con, f"SELECT COUNT(*) FROM {t}"), len(con.execute(f"DESCRIBE {t}").fetchall()))
        for t in RAW_FILES
    ]
    sections += ["## Row counts\n", md_table(["table", "rows", "columns"], counts)]

    first, last = con.execute(
        "SELECT MIN(order_purchase_timestamp), MAX(order_purchase_timestamp) FROM orders"
    ).fetchone()
    sections.append(f"\n## Order date range\n\nPurchases run from {first} to {last}.\n")

    statuses = con.execute(
        "SELECT order_status, COUNT(*) FROM orders GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall()
    sections += ["## Order status\n", md_table(["status", "orders"], statuses)]

    # Month spine so empty months (e.g. Nov 2016) show as 0 instead of disappearing.
    monthly = con.execute("""
        WITH months AS (
            SELECT UNNEST(generate_series(
                DATE_TRUNC('month', MIN(order_purchase_timestamp)),
                DATE_TRUNC('month', MAX(order_purchase_timestamp)),
                INTERVAL 1 MONTH)) AS month
            FROM orders)
        SELECT STRFTIME(m.month, '%Y-%m'),
               COUNT(o.order_id),
               COUNT(o.order_id) FILTER (WHERE o.order_status = 'delivered')
        FROM months m
        LEFT JOIN orders o ON DATE_TRUNC('month', o.order_purchase_timestamp) = m.month
        GROUP BY m.month ORDER BY m.month
    """).fetchall()
    sections += [
        "\n## Monthly order volume (by purchase date)\n",
        md_table(["month", "orders", "delivered"], monthly),
    ]

    null_rows = []
    for table in RAW_FILES:
        summary = con.execute(
            f"SELECT column_name, null_percentage FROM (SUMMARIZE {table})"
        ).fetchall()
        null_rows += [(table, col, f"{float(pct):.2f}%") for col, pct in summary if float(pct) > 0]
    sections += ["\n## Columns with nulls\n", md_table(["table", "column", "null rate"], null_rows)]

    keys = [(label, scalar(con, sql)) for label, sql in KEY_CHECKS]
    sections += ["\n## Keys: duplicates and orphans\n", md_table(["check", "count"], keys)]

    quirks = [(label, scalar(con, sql)) for label, sql in QUIRK_CHECKS]
    sections += ["\n## Quirks\n", md_table(["check", "value"], quirks)]

    untranslated = [
        r[0]
        for r in con.execute("""
        SELECT DISTINCT product_category_name FROM products
        WHERE product_category_name IS NOT NULL AND product_category_name NOT IN
              (SELECT product_category_name FROM category_translation) ORDER BY 1
    """).fetchall()
    ]
    sections.append("\nUntranslated categories: " + ", ".join(f"`{c}`" for c in untranslated))

    by_purchase, by_delivery = con.execute("""
        SELECT COUNT(*) FILTER (WHERE YEAR(order_purchase_timestamp) = 2017),
               COUNT(*) FILTER (WHERE YEAR(order_delivered_customer_date) = 2017)
        FROM orders WHERE order_status = 'delivered'
    """).fetchone()
    sections.append(
        "\n## Date-field ambiguity example\n\n"
        f"Delivered orders in 2017 are {by_purchase:,} by purchase date "
        f'but {by_delivery:,} by delivery date. A question like "delivered orders in 2017" '
        "has two defensible answers, so every metric must name its date field.\n"
    )
    return "\n".join(sections) + "\n"


def main() -> None:
    """CLI entry point: profile ``--data-dir`` and write markdown to ``--out``."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--out", type=Path, default=Path("results/phase0_profile.md"))
    args = parser.parse_args()

    report = build_report(connect(args.data_dir))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
