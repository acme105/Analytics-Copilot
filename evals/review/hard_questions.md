# Hard questions (h001–h020): gold vs agent

Agent answers are from the first eval run (20260924, prompts before D24). Gold is never
changed to match the agent. semantic_plan answers will be added after its run.

## h001: Which 5 categories grew GMV the most, in reais, from the first half of 2017 to the first half of 2018?

**Gold answer:** watches_gifts 405,307; health_beauty 381,136; bed_bath_table 282,858; sports_leisure 278,229; computers_accessories 274,585

```sql
SELECT product_category,
       SUM(price) FILTER (WHERE purchased_at >= DATE '2018-01-01')
       - SUM(price) FILTER (WHERE purchased_at < DATE '2018-01-01') AS gmv_growth
FROM fct_order_items
WHERE is_valid AND (
    (purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2017-07-01')
    OR (purchased_at >= DATE '2018-01-01' AND purchased_at < DATE '2018-07-01'))
GROUP BY 1 ORDER BY gmv_growth DESC LIMIT 5
```

**raw_schema** 💥 (hallucinated_column): ERROR: Unknown column quantity.

**semantic** ❌ (wrong_metric_definition): bed_bath_table 498,440; watches_gifts 492,795; health_beauty 481,756; sports_leisure 452,149; computers_accessories 405,079

```sql
SELECT product_category, SUM(price) AS gmv_growth FROM fct_order_items WHERE purchased_at >= CAST('2017-01-01' AS DATE) AND purchased_at < CAST('2018-01-01' AS DATE) GROUP BY product_category ORDER BY gmv_growth DESC LIMIT 5
```

**semantic_rag** ❌ (wrong_metric_definition): bed_bath_table 89,173 42,975; health_beauty 78,977 37,071; furniture_decor 63,011 32,821; watches_gifts 96,576 31,515; computers 33,700 30,151

```sql
WITH half_yearly AS (SELECT product_category, SUM(price) AS gmv, DATE_TRUNC('MONTH', purchased_at) AS period FROM fct_order_items WHERE is_valid AND purchased_at >= CAST('2017-01-01' AS DATE) AND purchased_at < CAST('2018-01-01' AS DATE) GROUP BY product_category, period) SELECT product_category, gmv, gmv - LAG(gmv) OVER (PARTITION BY product_category ORDER BY period) AS growth FROM half_yearly ORDER BY growth DESC LIMIT 5
```

## h002: What was the month-over-month percentage change in orders for each month of 2018?

_Note: January 2018 is compared with December 2017._

**Gold answer:** 2018-01-01 27.9%; 2018-02-01 -7.8%; 2018-03-01 8.2%; 2018-04-01 -3.5%; 2018-05-01 -1.2% … (8 rows)

```sql
WITH monthly AS (
    SELECT DATE_TRUNC('month', purchased_at) AS period, COUNT(*) AS orders
    FROM fct_orders
    WHERE is_valid
      AND purchased_at >= DATE '2017-12-01' AND purchased_at < DATE '2018-09-01'
    GROUP BY 1
),
changes AS (
    SELECT period, orders / LAG(orders) OVER (ORDER BY period) - 1 AS mom_change
    FROM monthly
)
SELECT CAST(period AS DATE) AS period, mom_change FROM changes
WHERE period >= DATE '2018-01-01'
```

**raw_schema** ❌ (wrong_join): 2018-01-01 7,269 None None; 2018-02-01 6,728 7,269 -7.44; 2018-03-01 7,211 6,728 7.18; 2018-04-01 6,939 7,211 -3.77; 2018-05-01 6,873 6,939 -95.1% … (10 rows)

```sql
SELECT DATE_TRUNC('MONTH', purchased_at) AS period, COUNT(order_id) AS order_count, LAG(COUNT(order_id)) OVER (ORDER BY DATE_TRUNC('MONTH', purchased_at)) AS prev_month_order_count, (COUNT(order_id) - LAG(COUNT(order_id)) OVER (ORDER BY DATE_TRUNC('MONTH', purchased_at))) / NULLIF(LAG(COUNT(order_id)) OVER (ORDER BY DATE_TRUNC('MONTH', purchased_at)), 0) * 100 AS mo_over_mo_change FROM stg_orders WHERE purchased_at >= CAST('2018-01-01' AS DATE) AND purchased_at < CAST('2019-01-01' AS DATE) GROUP BY DATE_TRUNC('MONTH', purchased_at) ORDER BY period LIMIT 200
```

**semantic** 💥 (other): ERROR: Binder Error: No function matches the given name and argument types '/(INTERVAL, TIMESTAMP)'. You might need to add explicit type casts. 	Candidate functions: 	/(FLOAT, FLOAT) -> FLOAT 	/(DOUBLE, DOUB

**semantic_rag** ❌ (wrong_filter): 2018-01-01 7,187 None; 2018-02-01 6,625 -7.8%; 2018-03-01 7,168 8.2%; 2018-04-01 6,919 -3.5%; 2018-05-01 6,833 -1.2% … (8 rows)

```sql
WITH monthly_orders AS (SELECT CAST(DATE_TRUNC('MONTH', purchased_at) AS DATE) AS period, COUNT(*) AS orders FROM fct_orders WHERE is_valid AND purchased_at >= CAST('2018-01-01' AS DATE) AND purchased_at < CAST('2018-09-01' AS DATE) GROUP BY 1) SELECT period, orders, orders / LAG(orders) OVER (ORDER BY period) - 1 AS mom_change FROM monthly_orders ORDER BY period LIMIT 200
```

## h003: Among states with at least 500 delivered orders in both 2017 and 2018, which state improved its late delivery rate the most?

**Gold answer:** PE

```sql
WITH by_year AS (
    SELECT customer_state, YEAR(purchased_at) AS yr, COUNT(*) AS delivered,
           AVG(CASE WHEN is_late THEN 1 ELSE 0 END) AS late_rate
    FROM fct_orders
    WHERE is_delivered
      AND purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2018-09-01'
    GROUP BY 1, 2
)
SELECT a.customer_state
FROM by_year a JOIN by_year b ON a.customer_state = b.customer_state
WHERE a.yr = 2017 AND b.yr = 2018 AND a.delivered >= 500 AND b.delivered >= 500
ORDER BY b.late_rate - a.late_rate ASC LIMIT 1
```

**raw_schema:** not replayed

**semantic** ❌ (wrong_metric_definition): MA 16.8% 713

```sql
SELECT customer_state, SUM(CASE WHEN is_late THEN 1 ELSE 0 END) / COUNT(*) AS late_delivery_rate, SUM(CASE WHEN is_delivered THEN 1 ELSE 0 END) AS delivered_orders FROM fct_orders WHERE purchased_at >= CAST('2017-01-01' AS DATE) AND purchased_at < CAST('2018-09-01' AS DATE) GROUP BY customer_state HAVING delivered_orders >= 500 ORDER BY late_delivery_rate DESC LIMIT 1
```

**semantic_rag** 💥 (other): ERROR: Query timed out after 10s.

## h004: What share of 2018 GMV came from top-tier sellers?

**Gold answer:** 42.3%

```sql
SELECT SUM(price) FILTER (WHERE seller_tier = 'top') / SUM(price) AS top_tier_gmv_share
FROM fct_order_items
WHERE is_valid AND purchased_at >= DATE '2018-01-01' AND purchased_at < DATE '2018-09-01'
```

**raw_schema** ❌ (wrong_join): 78.66

```sql
SELECT SUM(CASE WHEN s.seller_state IN ('SP', 'RJ', 'MG', 'ES') THEN oi.price ELSE 0 END) / SUM(oi.price) * 100 AS top_tier_gmv_share FROM stg_order_items AS oi JOIN stg_orders AS o ON oi.order_id = o.order_id JOIN stg_sellers AS s ON oi.seller_id = s.seller_id WHERE o.purchased_at >= CAST('2018-01-01' AS DATE) AND o.purchased_at < CAST('2019-01-01' AS DATE) LIMIT 201
```

**semantic** ❌ (wrong_metric_definition): 2.38

```sql
SELECT SUM(price) / SUM(price) FILTER(WHERE seller_tier = 'top') AS top_tier_gmv_share FROM fct_order_items WHERE purchased_at >= CAST('2018-01-01' AS DATE) AND purchased_at < CAST('2018-09-01' AS DATE) LIMIT 201
```

**semantic_rag** 💥 (hallucinated_column): ERROR: Binder Error: Referenced column "gmv" not found in FROM clause! Candidate bindings: "is_valid"  LINE 1: SELECT SUM(price) / SUM(gmv) AS top_tier_gmv_share FROM fct_order_items WHERE is_valid...       

## h005: Split 2017's first-time customers by how they paid for their first order. What was the 90-day repeat purchase rate for each payment type?

_Note: Payment type is the first order's highest-value payment (D8)._

**Gold answer:** credit_card 2.5%; voucher 2.8%; debit_card 2.0%; boleto 2.4%

```sql
SELECT payment_type,
       AVG(CASE WHEN repeat_within_90d THEN 1 ELSE 0 END) AS repeat_purchase_rate
FROM fct_orders
WHERE is_valid AND customer_order_number = 1 AND has_90d_followup
  AND purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2018-01-01'
GROUP BY 1
```

**raw_schema** ❌ (wrong_join): voucher 33 33 100; debit_card 9 9 100; credit_card 582 582 100; boleto 197 197 100

```sql
SELECT p.payment_type, COUNT(DISTINCT o.order_id) AS repeat_purchase_count, COUNT(DISTINCT c.customer_id) AS first_time_customer_count, (COUNT(DISTINCT o.order_id) / COUNT(DISTINCT c.customer_id)) * 100 AS repeat_purchase_rate FROM stg_order_payments AS p JOIN stg_orders AS o ON p.order_id = o.order_id JOIN stg_customers AS c ON o.customer_id = c.customer_id WHERE o.purchased_at >= CAST('2017-01-01' AS DATE) AND o.purchased_at < CAST('2017-02-01' AS DATE) GROUP BY p.payment_type ORDER BY repeat_purchase_rate DESC LIMIT 200
```

**semantic** ❌ (wrong_filter): voucher 2.6%; debit_card 2.1%; credit_card 2.3%; boleto 2.3%

```sql
SELECT payment_type, AVG(CASE WHEN repeat_within_90d THEN 1 ELSE 0 END) AS repeat_purchase_rate FROM fct_orders WHERE is_valid AND customer_order_number = 1 AND has_90d_followup AND purchased_at >= CAST('2017-01-01' AS DATE) AND purchased_at < CAST('2018-09-01' AS DATE) GROUP BY payment_type LIMIT 200
```

**semantic_rag** ✅: voucher 2.8%; credit_card 2.5%; boleto 2.4%; debit_card 2.0%

```sql
SELECT payment_type, AVG(CASE WHEN repeat_within_90d THEN 1 ELSE 0 END) AS repeat_purchase_rate FROM fct_orders WHERE is_valid AND customer_order_number = 1 AND has_90d_followup AND purchased_at >= CAST('2017-01-01' AS DATE) AND purchased_at < CAST('2018-01-01' AS DATE) GROUP BY 1 ORDER BY repeat_purchase_rate DESC LIMIT 10
```

## h006: Among categories with at least 1,000 reviewed orders, which 3 have the highest share of 1 and 2 star reviews?

_Note: The top result is 'unknown' (products with no category). Gold keeps it because it is a category value in the data; a reader might expect it excluded. Decide at review._

**Gold answer:** unknown 39.5%; office_furniture 22.4%; bed_bath_table 16.6%

```sql
SELECT product_category, AVG(CASE WHEN review_score <= 2 THEN 1 ELSE 0 END) AS low_review_share
FROM fct_orders
WHERE review_score IS NOT NULL
  AND purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2018-09-01'
GROUP BY 1 HAVING COUNT(*) >= 1000
ORDER BY low_review_share DESC LIMIT 3
```

**raw_schema** 💥 (other): ERROR: Binder Error: Values list "o" does not have a column named "product_id"  LINE 1: ... AS o ON r.order_id = o.order_id JOIN stg_products AS p ON o.product_id = p.product_id GROUP BY p.category HAVING CO

**semantic** ❌ (wrong_metric_definition): office_furniture 1,259 285 22.64; unknown 1,396 263 18.84; bed_bath_table 9,207 1,516 16.47

```sql
SELECT product_category, COUNT(*) AS review_count, SUM(CASE WHEN review_score <= 2 THEN 1 ELSE 0 END) AS two_star_reviews, CAST(SUM(CASE WHEN review_score <= 2 THEN 1 ELSE 0 END) AS REAL) * 100 / COUNT(*) AS two_star_review_percentage FROM fct_orders WHERE NOT review_score IS NULL AND is_valid GROUP BY product_category HAVING COUNT(*) >= 1000 ORDER BY two_star_review_percentage DESC LIMIT 3
```

**semantic_rag** ❌ (wrong_filter): office_furniture 22.4%; unknown 18.7%; bed_bath_table 16.5%

```sql
SELECT product_category, AVG(CASE WHEN review_score <= 2 THEN 1 ELSE 0 END) AS low_review_share FROM fct_orders WHERE NOT review_score IS NULL AND is_valid AND purchased_at >= CAST('2017-01-01' AS DATE) AND purchased_at < CAST('2018-09-01' AS DATE) GROUP BY 1 HAVING COUNT(*) >= 1000 ORDER BY low_review_share DESC LIMIT 3
```

## h007: Do late deliveries get worse reviews? Give the average review score for late and on-time orders in each year.

_Note: Gold keeps year and the score; the late/on-time label wording varies._

**Gold answer:** 2,018 4.30; 2,017 4.28; 2,017 2.32; 2,018 2.24

```sql
SELECT YEAR(purchased_at) AS yr, AVG(review_score) AS avg_review_score FROM fct_orders
WHERE is_delivered AND review_score IS NOT NULL
  AND purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2018-09-01'
GROUP BY 1, is_late
```

**raw_schema** ❌ (wrong_join): 2016-01-01 3.50 late; 2016-01-01 3.53 on_time; 2017-01-01 2.63 late; 2017-01-01 4.09 on_time; 2018-01-01 4.09 on_time

```sql
SELECT DATE_TRUNC('YEAR', o.purchased_at) AS year, AVG(r.review_score) AS avg_review_score, CASE WHEN o.shipped_at > o.delivered_at THEN 'late' ELSE 'on_time' END AS delivery_status FROM stg_orders AS o LEFT JOIN stg_order_reviews AS r ON o.order_id = r.order_id GROUP BY year, delivery_status ORDER BY year, delivery_status LIMIT 200
```

**semantic** ❌ (wrong_metric_definition): 2018-01-01 2.24 4.30; 2017-01-01 2.32 4.28; 2016-01-01 2.00 4.03

```sql
SELECT DATE_TRUNC('YEAR', purchased_at) AS year, AVG(CASE WHEN is_late THEN review_score ELSE NULL END) AS late_review_score, AVG(CASE WHEN is_late = FALSE THEN review_score ELSE NULL END) AS on_time_review_score FROM fct_orders WHERE is_valid AND is_delivered GROUP BY DATE_TRUNC('YEAR', purchased_at) LIMIT 200
```

**semantic_rag** ✅: 2,017 late 2.32; 2,017 on_time 4.28; 2,018 late 2.24; 2,018 on_time 4.30

```sql
SELECT EXTRACT(YEAR FROM purchased_at) AS year, CASE WHEN is_late THEN 'late' ELSE 'on_time' END AS delivery_status, AVG(review_score) AS avg_review_score FROM fct_orders WHERE is_valid AND is_delivered AND purchased_at >= CAST('2017-01-01' AS DATE) AND purchased_at < CAST('2018-09-01' AS DATE) GROUP BY 1, 2 ORDER BY 1, 2 LIMIT 200
```

## h008: Which month saw the biggest drop in GMV compared with the month before?

**Gold answer:** 2017-12-01

```sql
WITH monthly AS (
    SELECT DATE_TRUNC('month', purchased_at) AS period, SUM(price) AS gmv
    FROM fct_order_items
    WHERE is_valid
      AND purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2018-09-01'
    GROUP BY 1
),
changes AS (
    SELECT period, gmv - LAG(gmv) OVER (ORDER BY period) AS change FROM monthly
)
SELECT CAST(period AS DATE) AS period FROM changes
WHERE change IS NOT NULL ORDER BY change ASC LIMIT 1
```

**raw_schema** ❌ (wrong_join): 2018-03-01 28,529,943

```sql
SELECT DATE_TRUNC('MONTH', purchased_at) AS period, SUM(price * freight_value) AS gmv FROM stg_orders JOIN stg_order_items ON stg_orders.order_id = stg_order_items.order_id WHERE purchased_at >= CAST('2018-03-01' AS DATE) AND purchased_at < CAST('2018-04-01' AS DATE) GROUP BY period ORDER BY gmv DESC LIMIT 1
```

**semantic** ❌ (wrong_metric_definition): 2017-11-01 1,003,862

```sql
SELECT DATE_TRUNC('MONTH', purchased_at) AS period, SUM(price) AS gmv FROM fct_order_items WHERE is_valid GROUP BY period ORDER BY gmv DESC LIMIT 1
```

**semantic_rag** ❌ (wrong_filter): 2017-01-01 120,098 11,017

```sql
WITH monthly_gmv AS (SELECT CAST(DATE_TRUNC('MONTH', purchased_at) AS DATE) AS period, SUM(price) AS gmv FROM fct_order_items WHERE is_valid GROUP BY 1) SELECT period, gmv, gmv / LAG(gmv) OVER (ORDER BY period) - 1 AS mom_change FROM monthly_gmv ORDER BY mom_change DESC LIMIT 1
```

## h009: What percentage of 2018 GMV came from São Paulo state?

**Gold answer:** 40.3%

```sql
SELECT SUM(price) FILTER (WHERE customer_state = 'SP') / SUM(price) AS sp_gmv_share
FROM fct_order_items
WHERE is_valid AND purchased_at >= DATE '2018-01-01' AND purchased_at < DATE '2018-09-01'
```

**raw_schema** 💥 (other): ERROR: Binder Error: Values list "o" does not have a column named "customer_state"  LINE 1: SELECT CAST(SUM(CASE WHEN o.customer_state = 'SP' THEN oi.price ELSE 0 END) AS REAL...                             

**semantic** ✅: 40.29

```sql
SELECT CAST(SUM(CASE WHEN customer_state = 'SP' THEN gmv ELSE 0 END) AS REAL) * 100 / SUM(gmv) AS sp_gmv_percentage FROM fct_orders WHERE purchased_at >= CAST('2018-01-01' AS DATE) AND purchased_at < CAST('2018-09-01' AS DATE) LIMIT 201
```

**semantic_rag** ✅: 40.33

```sql
SELECT CAST(SUM(CASE WHEN customer_state = 'SP' THEN price ELSE 0 END) AS REAL) * 100 / SUM(price) AS sp_percentage FROM fct_order_items WHERE is_valid AND purchased_at >= CAST('2018-01-01' AS DATE) AND purchased_at < CAST('2018-09-01' AS DATE) LIMIT 201
```

## h010: How does the average order value of first orders compare with repeat orders?

_Note: Gold keeps only the numeric column; the first/repeat label wording varies._

**Gold answer:** 123; 138

```sql
SELECT SUM(gmv) / COUNT(*) AS aov FROM fct_orders
WHERE is_valid AND items > 0
  AND purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2018-09-01'
GROUP BY customer_order_number = 1
```

**raw_schema** ❌ (wrong_join): repeat_order 141

```sql
SELECT CASE WHEN o.order_status = 'first_order' THEN 'first_order' ELSE 'repeat_order' END AS order_type, AVG(oi.price + oi.freight_value) AS avg_order_value FROM stg_orders AS o JOIN stg_order_items AS oi ON o.order_id = oi.order_id WHERE o.purchased_at >= CAST('2018-03-01' AS DATE) AND o.purchased_at < CAST('2018-04-01' AS DATE) GROUP BY order_type LIMIT 200
```

**semantic** ❌ (wrong_metric_definition): 138 123

```sql
SELECT AVG(CASE WHEN customer_order_number = 1 THEN gmv ELSE NULL END) AS avg_gmv_first_order, AVG(CASE WHEN customer_order_number > 1 THEN gmv ELSE NULL END) AS avg_gmv_repeat_order FROM fct_orders WHERE is_valid LIMIT 201
```

**semantic_rag** ❌ (wrong_metric_definition): 4 163; 1 138; 2 123; 3 120; 5 101 … (16 rows)

```sql
SELECT customer_order_number, AVG(gmv) AS aov FROM fct_orders WHERE is_valid AND items > 0 AND purchased_at >= CAST('2017-01-01' AS DATE) AND purchased_at < CAST('2018-09-01' AS DATE) GROUP BY 1 ORDER BY aov DESC LIMIT 200
```

## h011: How many customers placed 3 or more orders during the analysis window?

_Note: "Placed" counts every order whatever its status (D24)._

**Gold answer:** 247

```sql
SELECT COUNT(*) AS customers FROM (
    SELECT customer_unique_id FROM fct_orders
    WHERE purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2018-09-01'
    GROUP BY 1 HAVING COUNT(*) >= 3
)
```

**raw_schema** ❌ (wrong_join): 

```sql
SELECT COUNT(DISTINCT customer_id) AS num_customers FROM stg_orders WHERE purchased_at >= CAST('2018-03-01' AS DATE) AND purchased_at < CAST('2018-04-01' AS DATE) GROUP BY customer_id HAVING COUNT(order_id) >= 3 LIMIT 200
```

**semantic** ❌ (wrong_metric_definition): 1; 1; 1; 1; 1 … (20 rows)

```sql
SELECT COUNT(DISTINCT customer_unique_id) AS customers_with_three_or_more_orders FROM fct_orders WHERE is_valid AND purchased_at >= CAST('2017-01-01' AS DATE) AND purchased_at < CAST('2018-09-01' AS DATE) GROUP BY customer_unique_id HAVING COUNT(order_id) >= 3 LIMIT 200
```

**semantic_rag** ❌ (wrong_metric_definition): 236

```sql
SELECT COUNT(DISTINCT customer_unique_id) AS customers_with_three_or_more_orders FROM fct_orders WHERE is_valid AND customer_order_number >= 3 AND purchased_at >= CAST('2017-01-01' AS DATE) AND purchased_at < CAST('2018-09-01' AS DATE) LIMIT 201
```

## h012: Which seller tier had the highest cancellation rate in 2018?

**Gold answer:** new_or_dormant

```sql
SELECT seller_tier FROM fct_orders
WHERE purchased_at >= DATE '2018-01-01' AND purchased_at < DATE '2018-09-01'
GROUP BY 1 ORDER BY AVG(CASE WHEN is_canceled THEN 1 ELSE 0 END) DESC LIMIT 1
```

**raw_schema** 💥 (other): ERROR: Binder Error: Values list "o" does not have a column named "seller_id"  LINE 1: ...) AS period FROM stg_orders AS o JOIN stg_sellers AS s ON o.seller_id = s.seller_id WHERE o.purchased_at >= CAST(... 

**semantic** ✅: new_or_dormant 7.5%

```sql
SELECT seller_tier, AVG(CASE WHEN is_canceled THEN 1 ELSE 0 END) AS cancellation_rate FROM fct_orders WHERE purchased_at >= CAST('2018-01-01' AS DATE) AND purchased_at < CAST('2018-09-01' AS DATE) GROUP BY seller_tier ORDER BY cancellation_rate DESC LIMIT 1
```

**semantic_rag** ✅: new_or_dormant 7.5%

```sql
SELECT seller_tier, AVG(CASE WHEN is_canceled THEN 1 ELSE 0 END) AS cancellation_rate FROM fct_orders WHERE purchased_at >= CAST('2018-01-01' AS DATE) AND purchased_at < CAST('2018-09-01' AS DATE) GROUP BY 1 ORDER BY cancellation_rate DESC LIMIT 1
```

## h013: In 2018, what share of orders paid more than half their item value in freight?

**Gold answer:** 16.1%

```sql
SELECT AVG(CASE WHEN freight > 0.5 * gmv THEN 1 ELSE 0 END) AS high_freight_share
FROM fct_orders
WHERE is_valid AND items > 0
  AND purchased_at >= DATE '2018-01-01' AND purchased_at < DATE '2018-09-01'
```

**raw_schema** ❌ (wrong_join): 17.12

```sql
SELECT CAST(SUM(CASE WHEN t2.freight_value > t2.price / 2 THEN 1 ELSE 0 END) AS REAL) * 100 / COUNT(t1.order_id) AS freight_share FROM stg_orders AS t1 INNER JOIN stg_order_items AS t2 ON t1.order_id = t2.order_id WHERE t1.purchased_at >= CAST('2018-01-01' AS DATE) AND t1.purchased_at < CAST('2019-01-01' AS DATE) LIMIT 201
```

**semantic** ❌ (wrong_join): 17.13

```sql
SELECT CAST(SUM(CASE WHEN freight_value > (price * 0.5) THEN 1 ELSE 0 END) AS REAL) * 100 / COUNT(*) AS freight_share FROM fct_order_items WHERE is_valid AND purchased_at >= CAST('2018-01-01' AS DATE) AND purchased_at < CAST('2018-09-01' AS DATE) LIMIT 201
```

**semantic_rag** ❌ (wrong_join): 17.1%

```sql
SELECT SUM(CASE WHEN freight_value > (price * 0.5) THEN 1 ELSE 0 END) / COUNT(*) AS freight_share_above_half_value FROM fct_order_items WHERE is_valid AND purchased_at >= CAST('2018-01-01' AS DATE) AND purchased_at < CAST('2018-09-01' AS DATE) LIMIT 201
```

## h014: In which week of 2017 did GMV peak, and what was it?

**Gold answer:** 2017-11-20 405,241

```sql
SELECT CAST(DATE_TRUNC('week', purchased_at) AS DATE) AS period, SUM(price) AS gmv
FROM fct_order_items
WHERE is_valid AND purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2018-01-01'
GROUP BY 1 ORDER BY gmv DESC LIMIT 1
```

**raw_schema** ❌ (wrong_join): 2017-11-20 483,416

```sql
SELECT DATE_TRUNC('WEEK', purchased_at) AS week, SUM(price * order_item_id) AS gmv FROM stg_order_items JOIN stg_orders ON stg_order_items.order_id = stg_orders.order_id WHERE YEAR(purchased_at) = 2017 GROUP BY week ORDER BY gmv DESC LIMIT 1
```

**semantic** ✅: 2017-11-20 405,241

```sql
SELECT DATE_TRUNC('WEEK', purchased_at) AS week, SUM(price) AS gmv_peak FROM fct_order_items WHERE is_valid AND purchased_at >= CAST('2017-01-01' AS DATE) AND purchased_at < CAST('2018-09-01' AS DATE) GROUP BY week ORDER BY gmv_peak DESC LIMIT 1
```

**semantic_rag** ✅: 2017-11-20 405,241

```sql
WITH weekly AS (SELECT DATE_TRUNC('WEEK', purchased_at) AS week, SUM(price) AS gmv FROM fct_order_items WHERE is_valid AND purchased_at >= CAST('2017-01-01' AS DATE) AND purchased_at < CAST('2018-01-01' AS DATE) GROUP BY 1) SELECT week, gmv FROM weekly ORDER BY gmv DESC LIMIT 1
```

## h015: Which 5 states have the slowest median delivery time?

**Gold answer:** AM 25.88; RR 25.13; AP 24.35; AL 22.33; PA 21.07

```sql
SELECT customer_state, MEDIAN(delivery_days) AS median_delivery_days FROM fct_orders
WHERE is_delivered
  AND purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2018-09-01'
GROUP BY 1 ORDER BY median_delivery_days DESC LIMIT 5
```

**raw_schema** 💥 (hallucinated_column): ERROR: Binder Error: Referenced column "customer_state" not found in FROM clause! Candidate bindings: "customer_id", "order_status", "delivered_at", "purchased_at", "estimated_delivery_at"  LINE 1: ... FROM 

**semantic** ❌ (wrong_metric_definition): SP 8.74; PR 11.97; MG 11.98; DF 12.94; SC 14.95

```sql
SELECT customer_state, AVG(delivery_days) AS avg_delivery_days FROM fct_orders WHERE is_delivered AND purchased_at >= CAST('2017-01-01' AS DATE) AND purchased_at < CAST('2018-09-01' AS DATE) GROUP BY customer_state ORDER BY avg_delivery_days ASC LIMIT 5
```

**semantic_rag** ❌ (wrong_metric_definition): SP 7.20; MG 10.31; PR 10.42; DF 11.36; RJ 12.04

```sql
WITH slowest_delivery_times AS (SELECT customer_state, QUANTILE_CONT(delivery_days, 0.5 ORDER BY delivery_days) AS median_delivery_days FROM fct_orders WHERE is_delivered AND purchased_at >= CAST('2017-01-01' AS DATE) AND purchased_at < CAST('2018-09-01' AS DATE) GROUP BY 1) SELECT customer_state, median_delivery_days FROM slowest_delivery_times ORDER BY median_delivery_days ASC LIMIT 5
```

## h016: Of the customers whose first order was in Q1 2018, how many placed a second order by the end of August 2018?

**Gold answer:** 617

```sql
SELECT COUNT(DISTINCT f.customer_unique_id) AS repeat_customers
FROM fct_orders f
JOIN fct_orders s
  ON s.customer_unique_id = f.customer_unique_id AND s.customer_order_number = 2
WHERE f.is_valid AND f.customer_order_number = 1
  AND f.purchased_at >= DATE '2018-01-01' AND f.purchased_at < DATE '2018-04-01'
  AND s.is_valid AND s.purchased_at < DATE '2018-09-01'
```

**raw_schema** ❌ (wrong_join): 0

```sql
SELECT COUNT(DISTINCT o.order_id) AS num_customers FROM stg_orders AS o JOIN stg_order_items AS oi ON o.order_id = oi.order_id WHERE o.purchased_at >= CAST('2018-01-01' AS DATE) AND o.purchased_at < CAST('2018-04-01' AS DATE) AND o.customer_id IN (SELECT DISTINCT customer_id FROM stg_orders WHERE purchased_at >= CAST('2018-01-01' AS DATE) AND purchased_at < CAST('2018-04-01' AS DATE)) AND o.order_id IN (SELECT order_id FROM stg_orders WHERE purchased_at >= CAST('2018-09-01' AS DATE) AND purchased_at <= CAST('2018-08-31' AS DATE)) LIMIT 201
```

**semantic** ❌ (wrong_metric_definition): 51,668

```sql
SELECT COUNT(DISTINCT customer_unique_id) AS new_customers_with_second_order FROM fct_orders WHERE customer_order_number = 1 AND purchased_at >= CAST('2018-01-01' AS DATE) AND purchased_at < CAST('2018-09-01' AS DATE) LIMIT 201
```

**semantic_rag** ❌ (wrong_filter): 51,668

```sql
SELECT COUNT(DISTINCT customer_unique_id) AS new_customers FROM fct_orders WHERE is_valid AND customer_order_number = 1 AND purchased_at >= CAST('2018-01-01' AS DATE) AND purchased_at <= CAST('2018-08-31' AS DATE) LIMIT 201
```

## h017: What share of 2018 GMV came from new or dormant sellers?

**Gold answer:** 8.4%

```sql
SELECT SUM(price) FILTER (WHERE seller_tier = 'new_or_dormant') / SUM(price)
       AS new_or_dormant_gmv_share
FROM fct_order_items
WHERE is_valid AND purchased_at >= DATE '2018-01-01' AND purchased_at < DATE '2018-09-01'
```

**raw_schema** ❌ (wrong_join): 0.0%

```sql
SELECT SUM(CASE WHEN s.seller_state = 'GMV' THEN oi.price ELSE 0 END) / SUM(oi.price) * 100 AS gmv_share FROM stg_order_items AS oi INNER JOIN stg_orders AS o ON oi.order_id = o.order_id INNER JOIN stg_sellers AS s ON oi.seller_id = s.seller_id WHERE o.purchased_at >= CAST('2018-01-01' AS DATE) AND o.purchased_at < CAST('2019-01-01' AS DATE) LIMIT 201
```

**semantic** ❌ (wrong_metric_definition): 8.5%

```sql
SELECT SUM(CASE WHEN seller_tier = 'new_or_dormant' THEN price ELSE 0 END) / SUM(price) AS new_or_dormant_gmv_share FROM fct_order_items WHERE purchased_at >= CAST('2018-01-01' AS DATE) AND purchased_at < CAST('2018-09-01' AS DATE) LIMIT 201
```

**semantic_rag** ❌ (other): 616,217

```sql
SELECT SUM(price) AS gmv FROM fct_order_items WHERE is_valid AND purchased_at >= CAST('2018-01-01' AS DATE) AND purchased_at < CAST('2018-09-01' AS DATE) AND seller_tier = 'new_or_dormant' LIMIT 201
```

## h018: Which 5 states pay the most freight per order on average in 2018?

**Gold answer:** RR 60.60; PB 53.75; AC 50.67; TO 47.79; PI 47.49

```sql
SELECT customer_state, SUM(freight) / COUNT(*) AS freight_per_order FROM fct_orders
WHERE is_valid AND items > 0
  AND purchased_at >= DATE '2018-01-01' AND purchased_at < DATE '2018-09-01'
GROUP BY 1 ORDER BY freight_per_order DESC LIMIT 5
```

**raw_schema** ❌ (wrong_join): 

```sql
SELECT s.customer_state, AVG(si.freight_value) AS avg_freight_per_order FROM stg_customers AS s JOIN stg_order_items AS si ON s.customer_id = si.order_id WHERE si.shipping_limit_at >= CAST('2018-03-01' AS DATE) AND si.shipping_limit_at < CAST('2018-04-01' AS DATE) GROUP BY s.customer_state ORDER BY avg_freight_per_order DESC LIMIT 5
```

**semantic** ❌ (wrong_join): RR 52.24; PB 47.51; RO 43.14; PI 42.94; AC 42.75

```sql
SELECT customer_state, AVG(freight_value) AS avg_freight_per_order FROM fct_order_items WHERE purchased_at >= CAST('2018-01-01' AS DATE) AND purchased_at < CAST('2018-09-01' AS DATE) AND is_valid GROUP BY customer_state ORDER BY avg_freight_per_order DESC LIMIT 5
```

**semantic_rag** ❌ (wrong_join): RR 52.24; PB 47.51; RO 43.14; PI 42.94; AC 42.75

```sql
SELECT customer_state, AVG(freight_value) AS avg_freight_per_order FROM fct_order_items WHERE is_valid AND purchased_at >= CAST('2018-01-01' AS DATE) AND purchased_at < CAST('2018-09-01' AS DATE) GROUP BY 1 ORDER BY avg_freight_per_order DESC LIMIT 5
```

## h019: How did the average review score in the first half of 2017 compare with the second half?

_Note: Gold keeps only the numeric column; the half-year label wording varies._

**Gold answer:** 4.08; 4.09

```sql
SELECT AVG(review_score) AS avg_review_score FROM fct_orders
WHERE review_score IS NOT NULL
  AND purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2018-01-01'
GROUP BY purchased_at < DATE '2017-07-01'
```

**raw_schema** ❌ (wrong_join): 2017-01-01 4.33; 2017-02-01 4.28; 2017-03-01 4.03; 2017-04-01 4.04; 2017-05-01 4.10 … (6 rows)

```sql
SELECT DATE_TRUNC('MONTH', review_created_at) AS period, AVG(review_score) AS avg_review_score FROM stg_order_reviews WHERE review_created_at >= CAST('2017-01-01' AS DATE) AND review_created_at < CAST('2017-07-01' AS DATE) GROUP BY period ORDER BY period LIMIT 200
```

**semantic** ❌ (wrong_metric_definition): 4.06 4.06; 4.05 4.05

```sql
SELECT AVG(CASE WHEN NOT review_score IS NULL THEN review_score ELSE 0 END) AS avg_review_score_first_half, AVG(CASE WHEN NOT review_score IS NULL THEN review_score ELSE 0 END) AS avg_review_score_second_half FROM fct_orders WHERE purchased_at >= CAST('2017-01-01' AS DATE) AND purchased_at < CAST('2017-07-01' AS DATE) UNION ALL SELECT AVG(CASE WHEN NOT review_score IS NULL THEN review_score ELSE 0 END) AS avg_review_score_first_half, AVG(CASE WHEN NOT review_score IS NULL THEN review_score ELSE 0 END) AS avg_review_score_second_half FROM fct_orders WHERE purchased_at >= CAST('2017-07-01' AS DATE) AND purchased_at < CAST('2018-01-01' AS DATE) LIMIT 201
```

**semantic_rag** ❌ (wrong_time_grain): 2017-01-01 4.06; 2017-02-01 4.02

```sql
SELECT period, AVG(avg_review_score) AS avg_review_score FROM (SELECT DATE_TRUNC('MONTH', purchased_at) AS period, AVG(review_score) AS avg_review_score FROM fct_orders WHERE NOT review_score IS NULL AND purchased_at >= CAST('2017-01-01' AS DATE) AND purchased_at < CAST('2018-01-01' AS DATE) GROUP BY 1) AS subquery GROUP BY 1 ORDER BY 1 LIMIT 2
```

## h020: For customers who ordered again, how many days on average passed between their first and second order?

_Note: First orders in the analysis window; second order at any time after._

**Gold answer:** 79.82

```sql
SELECT AVG(DATE_DIFF('second', f.purchased_at, s.purchased_at) / 86400.0) AS avg_days
FROM fct_orders f
JOIN fct_orders s
  ON s.customer_unique_id = f.customer_unique_id AND s.customer_order_number = 2
WHERE f.is_valid AND f.customer_order_number = 1 AND s.is_valid
  AND f.purchased_at >= DATE '2017-01-01' AND f.purchased_at < DATE '2018-09-01'
```

**raw_schema** ❌ (wrong_join): None

```sql
SELECT AVG(T2.purchased_at - T1.purchased_at) AS avg_days_between_first_and_second_order FROM stg_orders AS T1 JOIN stg_orders AS T2 ON T1.customer_id = T2.customer_id WHERE T1.order_id <> T2.order_id AND T1.purchased_at < T2.purchased_at AND T1.purchased_at >= CAST('2018-03-01' AS DATE) AND T2.purchased_at < CAST('2018-04-01' AS DATE) LIMIT 200
```

**semantic** ❌ (other): 79 days, 19:47:37.216189

```sql
SELECT AVG(T2.purchased_at - T1.purchased_at) AS avg_days_between_first_and_second_order FROM fct_orders AS T1 JOIN fct_orders AS T2 ON T1.customer_unique_id = T2.customer_unique_id AND T1.customer_order_number = 1 AND T2.customer_order_number = 2 WHERE T1.is_valid AND T2.is_valid AND T1.customer_order_number = 1 AND T2.customer_order_number = 2 AND T1.purchased_at >= CAST('2017-01-01' AS DATE) AND T1.purchased_at < CAST('2018-09-01' AS DATE) LIMIT 201
```

**semantic_rag** ❌ (wrong_metric_definition): 0:00:00; 506 days, 6:23:57; 0:00:00; 137 days, 16:19:52; 298 days, 12:36:26 … (20 rows)

```sql
SELECT AVG(T2.purchased_at - T1.purchased_at) AS avg_days_between_first_and_second_order FROM fct_orders AS T1 JOIN fct_orders AS T2 ON T1.customer_unique_id = T2.customer_unique_id AND T1.order_id <> T2.order_id AND T1.is_valid = TRUE AND T2.is_valid = TRUE AND T1.customer_order_number = 1 AND T2.customer_order_number = 2 AND T1.purchased_at >= CAST('2017-01-01' AS DATE) AND T1.purchased_at < CAST('2018-09-01' AS DATE) AND T2.purchased_at >= CAST('2017-01-01' AS DATE) AND T2.purchased_at < CAST('2018-09-01' AS DATE) GROUP BY T1.customer_unique_id LIMIT 200
```
