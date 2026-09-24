"""SQL guard: reject writes, multiple statements, unknown tables and columns, and file
access; enforce LIMIT."""

import pytest

from analytics_copilot.sql_guard import SQLValidationError, validate_sql

SCHEMA = {
    "fct_orders": {"order_id", "customer_state", "purchased_at", "gmv", "is_valid"},
    "fct_order_items": {"order_id", "price", "product_category", "is_valid"},
}


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("DELETE FROM fct_orders", "Only SELECT"),
        ("UPDATE fct_orders SET gmv = 0", "Only SELECT"),
        ("INSERT INTO fct_orders VALUES (1)", "Only SELECT"),
        ("DROP TABLE fct_orders", "Only SELECT"),
        ("CREATE TABLE x AS SELECT 1", "Only SELECT"),
        ("COPY fct_orders TO 'out.csv'", "Only SELECT"),
        ("ATTACH 'other.db'", "Only SELECT"),
        ("WITH d AS (DELETE FROM fct_orders RETURNING *) SELECT * FROM d", "not allowed"),
    ],
)
def test_rejects_ddl_dml_and_admin(sql: str, message: str) -> None:
    with pytest.raises(SQLValidationError, match=message):
        validate_sql(sql, SCHEMA, 100)


def test_rejects_multiple_statements() -> None:
    with pytest.raises(SQLValidationError, match="Exactly one statement"):
        validate_sql("SELECT 1 LIMIT 1; SELECT 2 LIMIT 1", SCHEMA, 100)


def test_rejects_unknown_tables() -> None:
    with pytest.raises(SQLValidationError, match="Unknown table raw_orders"):
        validate_sql("SELECT order_id FROM raw_orders LIMIT 5", SCHEMA, 100)


def test_rejects_table_functions_that_read_files() -> None:
    with pytest.raises(SQLValidationError, match="Table functions"):
        validate_sql("SELECT * FROM read_csv_auto('/etc/passwd') LIMIT 5", SCHEMA, 100)


def test_rejects_hallucinated_columns() -> None:
    with pytest.raises(SQLValidationError, match="Unknown column profit"):
        validate_sql("SELECT SUM(profit) AS p FROM fct_orders LIMIT 1", SCHEMA, 100)


def test_rejects_unparseable_sql() -> None:
    with pytest.raises(SQLValidationError):
        validate_sql("SELEC gmv FROM", SCHEMA, 100)


def test_adds_missing_limit() -> None:
    result = validate_sql("SELECT customer_state FROM fct_orders", SCHEMA, 100)
    assert result.sql.endswith("LIMIT 100")


def test_lowers_a_limit_above_the_cap_and_keeps_a_smaller_one() -> None:
    assert validate_sql("SELECT gmv FROM fct_orders LIMIT 5000", SCHEMA, 100).sql.endswith(
        "LIMIT 100"
    )
    assert validate_sql("SELECT gmv FROM fct_orders LIMIT 5", SCHEMA, 100).sql.endswith("LIMIT 5")


def test_allows_ctes_aliases_joins_and_reports_tables() -> None:
    sql = """
        WITH by_state AS (
            SELECT o.customer_state, SUM(i.price) AS state_gmv
            FROM fct_orders o JOIN fct_order_items i ON i.order_id = o.order_id
            WHERE o.is_valid GROUP BY 1
        )
        SELECT customer_state, state_gmv FROM by_state ORDER BY state_gmv DESC LIMIT 5
    """
    result = validate_sql(sql, SCHEMA, 100)
    assert result.tables == ["fct_orders", "fct_order_items"]
