# Gold review: batch 2, 20 random medium items (seed 20260924)

Check that each gold answer is what the question asks for under the signed-off definitions.
Reply with the ids to approve and any to change (and how).

## m001: Show GMV for each month of 2017.

Ordered: no. Rows: 12

| period | gmv |
|---|---|
| 2017-01-01 | 120,098 |
| 2017-02-01 | 244,959 |
| 2017-03-01 | 368,341 |
| 2017-04-01 | 353,843 |
| 2017-05-01 | 503,159 |
| 2017-06-01 | 429,917 |
| 2017-07-01 | 492,287 |
| 2017-08-01 | 568,246 |
| 2017-09-01 | 621,416 |
| 2017-10-01 | 660,180 |
| 2017-11-01 | 1,003,862 |
| 2017-12-01 | 742,184 |

```sql
SELECT CAST(DATE_TRUNC('month', purchased_at) AS DATE) AS period, SUM(price) AS gmv
FROM fct_order_items
WHERE is_valid AND purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2018-01-01'
GROUP BY 1
```

**Agent answers** (eval run 20260924, prompts before D24; shown for context: gold is never changed to match the agent):

- ❌ **raw_schema** (wrong_join): 2017-02-01 269,787; 2017-01-01 142,077; 2017-04-01 399,337 … (12 rows)

  ```sql
  SELECT DATE_TRUNC('MONTH', purchased_at) AS period, SUM(price * order_item_id) AS gmv FROM stg_order_items JOIN stg_orders ON stg_order_items.order_id = stg_orders.order_id WHERE purchased_at >= CAST('2017-01-01' AS DATE) AND purchased_at < CAST('2018-01-01' AS DATE) GROUP BY period LIMIT 200
  ```
- ✅ **semantic**: 2017-01-01 120,098; 2017-02-01 244,959; 2017-03-01 368,341 … (12 rows)
- ✅ **semantic_rag**: 2017-01-01 120,098; 2017-02-01 244,959; 2017-03-01 368,341 … (12 rows)

## m002: Which 10 states placed the most orders in 2017?

Ordered: yes. Rows: 10

_Note: "Placed" counts every order whatever its status (D24)._

| customer_state | orders |
|---|---|
| SP | 17,760 |
| RJ | 6,225 |
| MG | 5,414 |
| RS | 2,661 |
| PR | 2,270 |
| SC | 1,720 |
| BA | 1,592 |
| ES | 968 |
| GO | 955 |
| DF | 921 |

```sql
SELECT customer_state, COUNT(*) AS orders FROM fct_orders
WHERE purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2018-01-01'
GROUP BY 1 ORDER BY orders DESC LIMIT 10
```

**Agent answers** (eval run 20260924, prompts before D24; shown for context: gold is never changed to match the agent):

- 💥 **raw_schema** (hallucinated_column): Binder Error: Referenced column "customer_state" not found in FROM clause! Candidate bindings: "customer_id", "order_sta
- ❌ **semantic** (wrong_filter): SP 17,424; RJ 6,139; MG 5,323 … (10 rows)

  ```sql
  SELECT customer_state, COUNT(*) AS orders_count FROM fct_orders WHERE purchased_at >= CAST('2017-01-01' AS DATE) AND purchased_at < CAST('2018-01-01' AS DATE) AND is_valid GROUP BY customer_state ORDER BY orders_count DESC LIMIT 10
  ```
- ❌ **semantic_rag** (wrong_filter): SP 17,424; RJ 6,139; MG 5,323 … (10 rows)

  ```sql
  SELECT customer_state, COUNT(order_id) AS orders FROM fct_orders WHERE is_valid AND purchased_at >= CAST('2017-01-01' AS DATE) AND purchased_at < CAST('2018-01-01' AS DATE) GROUP BY 1 ORDER BY orders DESC LIMIT 10
  ```

## m004: Which 5 states had the worst late delivery rate in 2018?

Ordered: yes. Rows: 5

| customer_state | late_delivery_rate |
|---|---|
| AL | 22.2% |
| MA | 19.9% |
| CE | 19.1% |
| PI | 16.7% |
| PA | 16.6% |

```sql
SELECT customer_state, AVG(CASE WHEN is_late THEN 1 ELSE 0 END) AS late_delivery_rate
FROM fct_orders
WHERE is_delivered
  AND purchased_at >= DATE '2018-01-01' AND purchased_at < DATE '2018-09-01'
GROUP BY 1 ORDER BY late_delivery_rate DESC LIMIT 5
```

**Agent answers** (eval run 20260924, prompts before D24; shown for context: gold is never changed to match the agent):

- 💥 **raw_schema** (hallucinated_column): Binder Error: Referenced column "customer_state" not found in FROM clause! Candidate bindings: "customer_id", "order_sta
- ❌ **semantic** (wrong_metric_definition): AL 21.6%; MA 19.4%; CE 18.1% … (5 rows)

  ```sql
  SELECT customer_state, AVG(CASE WHEN is_late THEN 1 ELSE 0 END) AS late_delivery_rate_avg FROM fct_orders WHERE purchased_at >= CAST('2018-01-01' AS DATE) AND purchased_at < CAST('2018-09-01' AS DATE) GROUP BY customer_state ORDER BY late_delivery_rate_avg DESC LIMIT 5
  ```
- ❌ **semantic_rag** (other): AP 0.0%; RO 3.7%; AC 3.7% … (5 rows)

  ```sql
  SELECT customer_state, AVG(CASE WHEN is_late THEN 1 ELSE 0 END) AS late_delivery_rate FROM fct_orders WHERE is_delivered AND purchased_at >= CAST('2018-01-01' AS DATE) AND purchased_at < CAST('2018-09-01' AS DATE) GROUP BY 1 ORDER BY late_delivery_rate ASC LIMIT 5
  ```

## m008: Which 5 states get their deliveries fastest, by average delivery days?

Ordered: yes. Rows: 5

| customer_state | avg_delivery_days |
|---|---|
| SP | 8.74 |
| PR | 11.97 |
| MG | 11.98 |
| DF | 12.94 |
| SC | 14.95 |

```sql
SELECT customer_state, AVG(delivery_days) AS avg_delivery_days FROM fct_orders
WHERE is_delivered
  AND purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2018-09-01'
GROUP BY 1 ORDER BY avg_delivery_days ASC LIMIT 5
```

**Agent answers** (eval run 20260924, prompts before D24; shown for context: gold is never changed to match the agent):

- raw_schema: not replayed
- ✅ **semantic**: SP 8.74; PR 11.97; MG 11.98 … (5 rows)
- ✅ **semantic_rag**: SP 8.74; PR 11.97; MG 11.98 … (5 rows)

## m009: Show the number of distinct buyers per month in 2018.

Ordered: no. Rows: 8

| period | active_customers |
|---|---|
| 2018-05-01 | 6,775 |
| 2018-08-01 | 6,380 |
| 2018-07-01 | 6,172 |
| 2018-06-01 | 6,106 |
| 2018-04-01 | 6,863 |
| 2018-03-01 | 7,077 |
| 2018-01-01 | 7,088 |
| 2018-02-01 | 6,469 |

```sql
SELECT CAST(DATE_TRUNC('month', purchased_at) AS DATE) AS period,
       COUNT(DISTINCT customer_unique_id) AS active_customers
FROM fct_orders
WHERE is_valid AND purchased_at >= DATE '2018-01-01' AND purchased_at < DATE '2018-09-01'
GROUP BY 1
```

**Agent answers** (eval run 20260924, prompts before D24; shown for context: gold is never changed to match the agent):

- ❌ **raw_schema** (wrong_join): 2018-01-01 7,269; 2018-02-01 6,728; 2018-03-01 7,211 … (10 rows)

  ```sql
  SELECT DATE_TRUNC('MONTH', purchased_at) AS period, COUNT(DISTINCT customer_id) AS distinct_buyers FROM stg_orders WHERE purchased_at >= CAST('2018-01-01' AS DATE) AND purchased_at < CAST('2019-01-01' AS DATE) GROUP BY period ORDER BY period LIMIT 200
  ```
- ❌ **semantic** (wrong_metric_definition): 2018-01-01 7,166; 2018-02-01 6,569; 2018-03-01 7,115 … (8 rows)

  ```sql
  SELECT DATE_TRUNC('MONTH', purchased_at) AS period, COUNT(DISTINCT customer_unique_id) AS distinct_buyers FROM fct_orders WHERE purchased_at >= CAST('2018-01-01' AS DATE) AND purchased_at < CAST('2018-09-01' AS DATE) GROUP BY period ORDER BY period LIMIT 200
  ```
- ✅ **semantic_rag**: 2018-07-01 6,172; 2018-08-01 6,380; 2018-05-01 6,775 … (8 rows)

## m010: What was the cancellation rate in each month of 2017?

Ordered: no. Rows: 12

| period | cancellation_rate |
|---|---|
| 2017-01-01 | 1.6% |
| 2017-02-01 | 3.5% |
| 2017-03-01 | 2.4% |
| 2017-04-01 | 1.1% |
| 2017-05-01 | 1.6% |
| 2017-06-01 | 1.2% |
| 2017-07-01 | 2.0% |
| 2017-08-01 | 1.4% |
| 2017-09-01 | 1.4% |
| 2017-10-01 | 1.8% |
| 2017-11-01 | 1.6% |
| 2017-12-01 | 0.9% |

```sql
SELECT CAST(DATE_TRUNC('month', purchased_at) AS DATE) AS period,
       AVG(CASE WHEN is_canceled THEN 1 ELSE 0 END) AS cancellation_rate
FROM fct_orders
WHERE purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2018-01-01'
GROUP BY 1
```

**Agent answers** (eval run 20260924, prompts before D24; shown for context: gold is never changed to match the agent):

- ❌ **raw_schema** (wrong_join): 2017-01-01 0.0%; 2017-02-01 0.0%; 2017-03-01 0.0% … (12 rows)

  ```sql
  SELECT DATE_TRUNC('MONTH', purchased_at) AS period, COUNT(CASE WHEN order_status = 'cancelled' THEN 1 ELSE NULL END) / COUNT(*) AS cancellation_rate FROM stg_orders WHERE purchased_at >= CAST('2017-01-01' AS DATE) AND purchased_at < CAST('2018-01-01' AS DATE) GROUP BY period ORDER BY period LIMIT 200
  ```
- ✅ **semantic**: 2017-01-01 1.6%; 2017-02-01 3.5%; 2017-03-01 2.4% … (12 rows)
- ✅ **semantic_rag**: 2017-01-01 1.6%; 2017-02-01 3.5%; 2017-03-01 2.4% … (12 rows)

## m015: How many first-time buyers did we get in each month of 2017?

Ordered: no. Rows: 12

| period | new_customers |
|---|---|
| 2017-05-01 | 3,541 |
| 2017-12-01 | 5,439 |
| 2017-07-01 | 3,822 |
| 2017-04-01 | 2,325 |
| 2017-11-01 | 7,190 |
| 2017-08-01 | 4,130 |
| 2017-10-01 | 4,392 |
| 2017-06-01 | 3,102 |
| 2017-01-01 | 752 |
| 2017-03-01 | 2,571 |
| 2017-02-01 | 1,690 |
| 2017-09-01 | 4,075 |

```sql
SELECT CAST(DATE_TRUNC('month', purchased_at) AS DATE) AS period,
       COUNT(DISTINCT customer_unique_id) AS new_customers
FROM fct_orders
WHERE is_valid AND customer_order_number = 1
  AND purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2018-01-01'
GROUP BY 1
```

**Agent answers** (eval run 20260924, prompts before D24; shown for context: gold is never changed to match the agent):

- ❌ **raw_schema** (wrong_join): 2017-01-01 800; 2017-02-01 1,780; 2017-03-01 2,682 … (12 rows)

  ```sql
  SELECT DATE_TRUNC('MONTH', purchased_at) AS period, COUNT(DISTINCT customer_id) AS first_time_buyers FROM stg_orders WHERE YEAR(purchased_at) = 2017 GROUP BY period ORDER BY period LIMIT 200
  ```
- ✅ **semantic**: 2017-01-01 752; 2017-02-01 1,690; 2017-03-01 2,571 … (12 rows)
- ✅ **semantic_rag**: 2017-01-01 752; 2017-02-01 1,690; 2017-03-01 2,571 … (12 rows)

## m018: Compare GMV between São Paulo (SP) and Rio de Janeiro (RJ) in 2017.

Ordered: no. Rows: 2

| customer_state | gmv |
|---|---|
| RJ | 901,159 |
| SP | 2,190,452 |

```sql
SELECT customer_state, SUM(price) AS gmv FROM fct_order_items
WHERE is_valid AND customer_state IN ('SP', 'RJ')
  AND purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2018-01-01'
GROUP BY 1
```

**Agent answers** (eval run 20260924, prompts before D24; shown for context: gold is never changed to match the agent):

- 💥 **raw_schema** (other): invalid JSON from the sql model: Invalid \escape: line 2 column 18 (char 19)
- ❌ **semantic** (wrong_filter): SP 5,150,779; MG 1,568,856; DF 299,843 … (20 rows)

  ```sql
  SELECT customer_state, SUM(price) AS gmv FROM fct_order_items WHERE is_valid AND purchased_at >= CAST('2017-01-01' AS DATE) AND purchased_at < CAST('2018-09-01' AS DATE) GROUP BY customer_state LIMIT 200
  ```
- ✅ **semantic_rag**: SP 2,190,452; RJ 901,159

## m022: What was the average review score in each month of 2018?

Ordered: no. Rows: 8

| period | avg_review_score |
|---|---|
| 2018-01-01 | 4.04 |
| 2018-02-01 | 3.83 |
| 2018-03-01 | 3.75 |
| 2018-04-01 | 4.16 |
| 2018-05-01 | 4.19 |
| 2018-06-01 | 4.28 |
| 2018-07-01 | 4.26 |
| 2018-08-01 | 4.26 |

```sql
SELECT CAST(DATE_TRUNC('month', purchased_at) AS DATE) AS period,
       AVG(review_score) AS avg_review_score
FROM fct_orders
WHERE review_score IS NOT NULL
  AND purchased_at >= DATE '2018-01-01' AND purchased_at < DATE '2018-09-01'
GROUP BY 1
```

**Agent answers** (eval run 20260924, prompts before D24; shown for context: gold is never changed to match the agent):

- ❌ **raw_schema** (wrong_join): 2018-01-01 4.06; 2018-02-01 4.01; 2018-03-01 3.73 … (8 rows)

  ```sql
  SELECT DATE_TRUNC('MONTH', review_created_at) AS period, AVG(review_score) AS avg_review_score FROM stg_order_reviews WHERE review_created_at >= CAST('2018-01-01' AS DATE) AND review_created_at < CAST('2019-01-01' AS DATE) GROUP BY period ORDER BY period LIMIT 200
  ```
- ✅ **semantic**: 2018-07-01 4.26; 2018-01-01 4.04; 2018-02-01 3.83 … (8 rows)
- ✅ **semantic_rag**: 2018-01-01 4.04; 2018-02-01 3.83; 2018-03-01 3.75 … (8 rows)

## m023: What was the average delivery time in days for each month of 2017?

Ordered: no. Rows: 12

| period | avg_delivery_days |
|---|---|
| 2017-01-01 | 12.65 |
| 2017-02-01 | 13.17 |
| 2017-03-01 | 12.95 |
| 2017-04-01 | 14.92 |
| 2017-05-01 | 11.32 |
| 2017-06-01 | 12.01 |
| 2017-07-01 | 11.59 |
| 2017-08-01 | 11.15 |
| 2017-09-01 | 11.85 |
| 2017-10-01 | 11.86 |
| 2017-11-01 | 15.16 |
| 2017-12-01 | 15.39 |

```sql
SELECT CAST(DATE_TRUNC('month', purchased_at) AS DATE) AS period,
       AVG(delivery_days) AS avg_delivery_days
FROM fct_orders
WHERE is_delivered
  AND purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2018-01-01'
GROUP BY 1
```

**Agent answers** (eval run 20260924, prompts before D24; shown for context: gold is never changed to match the agent):

- raw_schema: not replayed
- ✅ **semantic**: 2017-01-01 12.65; 2017-02-01 13.17; 2017-03-01 12.95 … (12 rows)
- ✅ **semantic_rag**: 2017-01-01 12.65; 2017-02-01 13.17; 2017-03-01 12.95 … (12 rows)

## m024: What was GMV for each of Bahia, Pernambuco and Ceará (BA, PE, CE) in 2018?

_Reworded (D31): the old wording could fairly mean one combined total._

Ordered: no. Rows: 3

| customer_state | gmv |
|---|---|
| CE | 111,996 |
| BA | 272,158 |
| PE | 137,776 |

```sql
SELECT customer_state, SUM(price) AS gmv FROM fct_order_items
WHERE is_valid AND customer_state IN ('BA', 'PE', 'CE')
  AND purchased_at >= DATE '2018-01-01' AND purchased_at < DATE '2018-09-01'
GROUP BY 1
```

**Agent answers** (eval run 20260924, prompts before D24; shown for context: gold is never changed to match the agent):

- 💥 **raw_schema** (hallucinated_column): Binder Error: Referenced column "customer_state" not found in FROM clause! Candidate bindings: "customer_id", "order_sta
- ❌ **semantic** (wrong_metric_definition): 112,700 CE; 137,831 PE; 272,729 BA

  ```sql
  SELECT SUM(price) AS gmv, customer_state FROM fct_order_items WHERE customer_state IN ('BA', 'PE', 'CE') AND purchased_at >= CAST('2018-01-01' AS DATE) AND purchased_at < CAST('2018-09-01' AS DATE) GROUP BY customer_state LIMIT 200
  ```
- ❌ **semantic_rag** (other): 521,930

  ```sql
  SELECT SUM(price) AS gmv FROM fct_order_items WHERE is_valid AND purchased_at >= CAST('2018-01-01' AS DATE) AND purchased_at < CAST('2018-09-01' AS DATE) AND customer_state IN ('BA', 'PE', 'CE') LIMIT 200
  ```

## m028: How many orders were placed in Q1 2017 compared with Q1 2018?

Ordered: no. Rows: 2

_Note: "Placed" counts every order whatever its status (D24)._

| period | orders |
|---|---|
| 2017-01-01 | 5,262 |
| 2018-01-01 | 21,208 |

```sql
SELECT CAST(DATE_TRUNC('quarter', purchased_at) AS DATE) AS period, COUNT(*) AS orders
FROM fct_orders
WHERE (
    (purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2017-04-01')
    OR (purchased_at >= DATE '2018-01-01' AND purchased_at < DATE '2018-04-01'))
GROUP BY 1
```

**Agent answers** (eval run 20260924, prompts before D24; shown for context: gold is never changed to match the agent):

- 💥 **raw_schema** (other): invalid JSON from the sql model: Invalid \escape: line 2 column 18 (char 19)
- 💥 **semantic** (other): Binder Error: Could not choose a best candidate function for the function call "date_trunc(STRING_LITERAL, STRING_LITERA
- ❌ **semantic_rag** (wrong_filter): 2017-01-01 5,122; 2017-04-01 9,222; 2017-07-01 12,445 … (4 rows)

  ```sql
  SELECT CAST(DATE_TRUNC('QUARTER', purchased_at) AS DATE) AS period, COUNT(*) AS orders FROM fct_orders WHERE is_valid AND purchased_at >= CAST('2017-01-01' AS DATE) AND purchased_at < CAST('2018-01-01' AS DATE) GROUP BY 1 ORDER BY 1 LIMIT 200
  ```

## m029: What was the average order value for each seller tier in 2018?

Ordered: no. Rows: 4

| seller_tier | aov |
|---|---|
| mid | 129 |
| new_or_dormant | 183 |
| top | 154 |
| long_tail | 105 |

```sql
SELECT seller_tier, SUM(gmv) / COUNT(*) AS aov FROM fct_orders
WHERE is_valid AND items > 0
  AND purchased_at >= DATE '2018-01-01' AND purchased_at < DATE '2018-09-01'
GROUP BY 1
```

**Agent answers** (eval run 20260924, prompts before D24; shown for context: gold is never changed to match the agent):

- 💥 **raw_schema** (other): Binder Error: Values list "o" does not have a column named "seller_id"  LINE 1: ... avg_order_value FROM stg_orders AS o
- ❌ **semantic** (wrong_join): mid 141; new_or_dormant 207; long_tail 119 … (4 rows)

  ```sql
  SELECT o.seller_tier, AVG(o.gmv) AS avg_gmv_per_seller FROM fct_order_items AS oi INNER JOIN fct_orders AS o ON oi.order_id = o.order_id WHERE oi.purchased_at >= CAST('2018-01-01' AS DATE) AND oi.purchased_at < CAST('2018-09-01' AS DATE) AND oi.is_valid AND o.is_valid GROUP BY o.seller_tier LIMIT 200
  ```
- ❌ **semantic_rag** (wrong_join): new_or_dormant 156 3,938; top 136 22,889; mid 115 21,868 … (4 rows)

  ```sql
  SELECT seller_tier, AVG(price) AS avg_gmv_per_seller, COUNT(*) AS seller_count FROM fct_order_items WHERE is_valid AND purchased_at >= CAST('2018-01-01' AS DATE) AND purchased_at < CAST('2018-09-01' AS DATE) GROUP BY 1 ORDER BY avg_gmv_per_seller DESC LIMIT 10
  ```

## m035: How many orders did each seller tier handle in 2017?

Ordered: no. Rows: 4

_Note: Order-level seller tier is the tier of the order's highest-value item's seller (D8)._

| seller_tier | orders |
|---|---|
| mid | 16,757 |
| new_or_dormant | 3,897 |
| long_tail | 8,976 |
| top | 14,749 |

```sql
SELECT seller_tier, COUNT(*) AS orders FROM fct_orders
WHERE is_valid AND purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2018-01-01'
GROUP BY 1
```

**Agent answers** (eval run 20260924, prompts before D24; shown for context: gold is never changed to match the agent):

- 💥 **raw_schema** (other): Binder Error: Values list "o" does not have a column named "seller_id"  LINE 1: ... FROM stg_sellers AS s JOIN stg_order
- ❌ **semantic** (wrong_join): long_tail 10,545; top 16,735; mid 18,980 … (4 rows)

  ```sql
  SELECT seller_tier, COUNT(order_id) AS orders_count FROM fct_order_items WHERE purchased_at >= CAST('2017-01-01' AS DATE) AND purchased_at < CAST('2018-01-01' AS DATE) GROUP BY seller_tier LIMIT 200
  ```
- ❌ **semantic_rag** (wrong_join): mid 18,888; top 16,709; long_tail 10,471 … (4 rows)

  ```sql
  SELECT seller_tier, COUNT(order_id) AS order_count FROM fct_order_items WHERE is_valid AND purchased_at >= CAST('2017-01-01' AS DATE) AND purchased_at < CAST('2018-01-01' AS DATE) GROUP BY 1 ORDER BY order_count DESC LIMIT 10
  ```

## m036: Which 5 states give the lowest average review scores?

Ordered: yes. Rows: 5

| customer_state | avg_review_score |
|---|---|
| RR | 3.66 |
| MA | 3.75 |
| AL | 3.76 |
| SE | 3.80 |
| PA | 3.85 |

```sql
SELECT customer_state, AVG(review_score) AS avg_review_score FROM fct_orders
WHERE review_score IS NOT NULL
  AND purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2018-09-01'
GROUP BY 1 ORDER BY avg_review_score ASC LIMIT 5
```

**Agent answers** (eval run 20260924, prompts before D24; shown for context: gold is never changed to match the agent):

- 💥 **raw_schema** (hallucinated_column): Binder Error: Referenced column "customer_state" not found in FROM clause! Candidate bindings: "review_created_at", "has
- ✅ **semantic**: RR 3.66; MA 3.75; AL 3.76 … (5 rows)
- ✅ **semantic_rag**: RR 3.66; MA 3.75; AL 3.76 … (5 rows)

## m037: Rank the states by cancellation rate for 2018 and show the top 5.

Ordered: yes. Rows: 5

| customer_state | cancellation_rate |
|---|---|
| RR | 3.8% |
| RO | 2.7% |
| PI | 1.1% |
| SP | 1.1% |
| CE | 0.9% |

```sql
SELECT customer_state, AVG(CASE WHEN is_canceled THEN 1 ELSE 0 END) AS cancellation_rate
FROM fct_orders
WHERE purchased_at >= DATE '2018-01-01' AND purchased_at < DATE '2018-09-01'
GROUP BY 1 ORDER BY cancellation_rate DESC LIMIT 5
```

**Agent answers** (eval run 20260924, prompts before D24; shown for context: gold is never changed to match the agent):

- 💥 **raw_schema** (hallucinated_column): Binder Error: Referenced column "customer_state" not found in FROM clause! Candidate bindings: "customer_id", "order_sta
- ✅ **semantic**: RR 3.8%; RO 2.7%; PI 1.1% … (5 rows)
- ✅ **semantic_rag**: RR 3.8%; RO 2.7%; PI 1.1% … (5 rows)

## m040: What was the on-time delivery rate in each month of 2017?

Ordered: no. Rows: 12

| period | on_time_delivery_rate |
|---|---|
| 2017-01-01 | 97.1% |
| 2017-02-01 | 97.0% |
| 2017-03-01 | 95.4% |
| 2017-04-01 | 93.4% |
| 2017-05-01 | 97.0% |
| 2017-06-01 | 97.0% |
| 2017-07-01 | 97.2% |
| 2017-08-01 | 97.1% |
| 2017-09-01 | 95.6% |
| 2017-10-01 | 95.8% |
| 2017-11-01 | 87.6% |
| 2017-12-01 | 92.5% |

```sql
SELECT CAST(DATE_TRUNC('month', purchased_at) AS DATE) AS period,
       AVG(CASE WHEN is_late THEN 0 ELSE 1 END) AS on_time_delivery_rate
FROM fct_orders
WHERE is_delivered
  AND purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2018-01-01'
GROUP BY 1
```

**Agent answers** (eval run 20260924, prompts before D24; shown for context: gold is never changed to match the agent):

- ❌ **raw_schema** (wrong_join): 2017-01-01 0.0%; 2017-02-01 0.0%; 2017-03-01 0.0% … (12 rows)

  ```sql
  SELECT DATE_TRUNC('MONTH', purchased_at) AS period, COUNT(CASE WHEN delivered_at <= purchased_at THEN 1 ELSE NULL END) / COUNT(*) * 100 AS on_time_delivery_rate FROM stg_orders WHERE purchased_at >= CAST('2017-01-01' AS DATE) AND purchased_at < CAST('2018-01-01' AS DATE) GROUP BY period ORDER BY period LIMIT 200
  ```
- ✅ **semantic**: 2017-01-01 97.1%; 2017-02-01 97.0%; 2017-03-01 95.4% … (12 rows)
- ✅ **semantic_rag**: 2017-01-01 97.1%; 2017-02-01 97.0%; 2017-03-01 95.4% … (12 rows)

## m041: What are the 5 best-selling categories by GMV in São Paulo state (SP)?

Ordered: yes. Rows: 5

| product_category | gmv |
|---|---|
| bed_bath_table | 477,764 |
| health_beauty | 459,515 |
| watches_gifts | 430,456 |
| sports_leisure | 382,007 |
| computers_accessories | 346,798 |

```sql
SELECT product_category, SUM(price) AS gmv FROM fct_order_items
WHERE is_valid AND customer_state = 'SP'
  AND purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2018-09-01'
GROUP BY 1 ORDER BY gmv DESC LIMIT 5
```

**Agent answers** (eval run 20260924, prompts before D24; shown for context: gold is never changed to match the agent):

- 💥 **raw_schema** (hallucinated_column): Unknown column quantity.
- ❌ **semantic** (wrong_filter): bed_bath_table 477,794; health_beauty 460,474; watches_gifts 431,119 … (5 rows)

  ```sql
  SELECT product_category, SUM(price) AS gmv FROM fct_order_items WHERE customer_state = 'SP' AND is_valid = TRUE GROUP BY product_category ORDER BY gmv DESC LIMIT 5
  ```
- ✅ **semantic_rag**: bed_bath_table 477,764; health_beauty 459,515; watches_gifts 430,456 … (5 rows)

## m043: Show monthly GMV in 2018 for orders paid mainly with vouchers.

Ordered: no. Rows: 8

_Note: An order's payment type is its highest-value payment (D8)._

| period | gmv |
|---|---|
| 2018-01-01 | 25,371 |
| 2018-02-01 | 18,113 |
| 2018-03-01 | 21,515 |
| 2018-04-01 | 17,817 |
| 2018-05-01 | 17,076 |
| 2018-06-01 | 18,993 |
| 2018-07-01 | 15,564 |
| 2018-08-01 | 13,217 |

```sql
SELECT CAST(DATE_TRUNC('month', purchased_at) AS DATE) AS period, SUM(price) AS gmv
FROM fct_order_items
WHERE is_valid AND payment_type = 'voucher'
  AND purchased_at >= DATE '2018-01-01' AND purchased_at < DATE '2018-09-01'
GROUP BY 1
```

**Agent answers** (eval run 20260924, prompts before D24; shown for context: gold is never changed to match the agent):

- 💥 **raw_schema** (hallucinated_column): Binder Error: Referenced column "purchased_at" not found in FROM clause! Candidate bindings: "price", "product_id", "pay
- ❌ **semantic** (wrong_filter): 2016-10-01 759; 2017-01-01 3,998; 2017-02-01 4,740 … (20 rows)

  ```sql
  SELECT DATE_TRUNC('MONTH', purchased_at) AS period, SUM(price) AS gmv FROM fct_order_items WHERE is_valid AND payment_type = 'voucher' GROUP BY period ORDER BY period LIMIT 200
  ```
- ✅ **semantic_rag**: 2018-01-01 25,371; 2018-02-01 18,113; 2018-03-01 21,515 … (8 rows)

## m044: How many items per order do customers buy with each payment type?

Ordered: no. Rows: 4

| payment_type | items_per_order |
|---|---|
| boleto | 1.17 |
| credit_card | 1.14 |
| voucher | 1.09 |
| debit_card | 1.11 |

```sql
SELECT payment_type, AVG(items) AS items_per_order FROM fct_orders
WHERE is_valid AND items > 0
  AND purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2018-09-01'
GROUP BY 1
```

**Agent answers** (eval run 20260924, prompts before D24; shown for context: gold is never changed to match the agent):

- ❌ **raw_schema** (wrong_join): 895ab968e7bb0d5659d16cd74cd1650c 63 voucher; fedcd9f7ccdc8cba3a18defedd1a5547 36 voucher; fa65dad1b0e818e3ccc5cb0e39231352 29 voucher … (20 rows)

  ```sql
  SELECT oi.order_id, COUNT(*) AS item_count, p.payment_type FROM stg_order_items AS oi INNER JOIN stg_order_payments AS p ON oi.order_id = p.order_id GROUP BY oi.order_id, p.payment_type ORDER BY item_count DESC LIMIT 200
  ```
- 💥 **semantic** (hallucinated_column): Binder Error: Referenced column "items" not found in FROM clause! Candidate bindings: "customer_state", "customer_unique
- ❌ **semantic_rag** (wrong_filter): not_defined 3.00; boleto 1.17; credit_card 1.14 … (5 rows)

  ```sql
  SELECT payment_type, AVG(items) AS items_per_order FROM fct_orders WHERE is_valid AND items > 0 GROUP BY payment_type ORDER BY items_per_order DESC LIMIT 20
  ```
