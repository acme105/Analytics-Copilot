# Gold review: batch 1 of 6 (e001–e020)

For each item, check that the gold SQL answers the question the way you would, under the
signed-off definitions. Reply with the ids to approve and any to change (and how).

Defaults applied everywhere: valid orders only for sales metrics (canceled/unavailable excluded),
delivered orders for delivery metrics, purchase date for all dates, and the analysis window
Jan 2017 – Aug 2018 when no period is given.

## e001: How many orders were placed in 2017?

**Gold answer:** 44,379

```sql
SELECT COUNT(*) AS orders FROM fct_orders
WHERE is_valid AND purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2018-01-01'
```

## e002: How much GMV did Olist generate in the first half of 2018?

**Gold answer:** 5,614,133.04

```sql
SELECT SUM(price) AS gmv FROM fct_order_items
WHERE is_valid AND purchased_at >= DATE '2018-01-01' AND purchased_at < DATE '2018-07-01'
```

## e003: What was the average order value in 2018?

**Gold answer:** 137.14

```sql
SELECT SUM(gmv) / COUNT(*) AS aov FROM fct_orders
WHERE is_valid AND items > 0
  AND purchased_at >= DATE '2018-01-01' AND purchased_at < DATE '2018-09-01'
```

## e004: What is the overall average review score?

**Gold answer:** 4.0886

```sql
SELECT AVG(review_score) AS avg_review_score FROM fct_orders
WHERE review_score IS NOT NULL
  AND purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2018-09-01'
```

## e005: What share of delivered orders arrived late overall?

**Gold answer:** 0.0679

```sql
SELECT AVG(CASE WHEN is_late THEN 1 ELSE 0 END) AS late_delivery_rate FROM fct_orders
WHERE is_delivered
  AND purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2018-09-01'
```

## e006: How many sellers were active in 2017?

**Gold answer:** 1,766

```sql
SELECT COUNT(DISTINCT seller_id) AS active_sellers FROM fct_order_items
WHERE is_valid AND purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2018-01-01'
```

## e007: How many unique customers bought something in 2018?

**Gold answer:** 52,337

```sql
SELECT COUNT(DISTINCT customer_unique_id) AS active_customers FROM fct_orders
WHERE is_valid AND purchased_at >= DATE '2018-01-01' AND purchased_at < DATE '2018-09-01'
```

## e008: What was the cancellation rate in 2017?

**Gold answer:** 0.0160

```sql
SELECT AVG(CASE WHEN is_canceled THEN 1 ELSE 0 END) AS cancellation_rate FROM fct_orders
WHERE purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2018-01-01'
```

## e009: On average, how many days does delivery take?

**Gold answer:** 12.54

```sql
SELECT AVG(delivery_days) AS avg_delivery_days FROM fct_orders
WHERE is_delivered
  AND purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2018-09-01'
```

## e010: What was the freight ratio in 2018?

**Gold answer:** 0.1706

```sql
SELECT SUM(freight_value) / SUM(price) AS freight_ratio FROM fct_order_items
WHERE is_valid AND purchased_at >= DATE '2018-01-01' AND purchased_at < DATE '2018-09-01'
```

## e011: What is the average number of items per order?

**Gold answer:** 1.1414

```sql
SELECT AVG(items) AS items_per_order FROM fct_orders
WHERE is_valid AND items > 0
  AND purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2018-09-01'
```

## e012: How many new customers did we get in 2018?

**Gold answer:** 51,668

```sql
SELECT COUNT(DISTINCT customer_unique_id) AS new_customers FROM fct_orders
WHERE is_valid AND customer_order_number = 1
  AND purchased_at >= DATE '2018-01-01' AND purchased_at < DATE '2018-09-01'
```

## e013: What percentage of payment value was paid by credit card?

**Gold answer:** 0.7846

```sql
SELECT SUM(payment_value) FILTER (WHERE payment_type = 'credit_card') / SUM(payment_value)
       AS credit_card_payment_share
FROM fct_payments
WHERE is_valid AND purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2018-09-01'
```

## e014: What is the average number of credit card instalments?

**Gold answer:** 3.5033

```sql
SELECT AVG(installments) AS avg_installments FROM fct_payments
WHERE is_valid AND payment_type = 'credit_card'
  AND purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2018-09-01'
```

## e015: What share of reviews were 1 or 2 stars?

**Gold answer:** 0.1463

```sql
SELECT AVG(CASE WHEN review_score <= 2 THEN 1 ELSE 0 END) AS low_review_share
FROM fct_orders
WHERE review_score IS NOT NULL
  AND purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2018-09-01'
```

## e016: What was GMV in November 2017?

**Gold answer:** 1,003,862.14

```sql
SELECT SUM(price) AS gmv FROM fct_order_items
WHERE is_valid AND purchased_at >= DATE '2017-11-01' AND purchased_at < DATE '2017-12-01'
```

## e017: How many orders came from São Paulo state in 2018?

**Gold answer:** 23,598

```sql
SELECT COUNT(*) AS orders FROM fct_orders
WHERE is_valid AND customer_state = 'SP'
  AND purchased_at >= DATE '2018-01-01' AND purchased_at < DATE '2018-09-01'
```

## e018: What was the on-time delivery rate in 2017?

**Gold answer:** 0.9435

```sql
SELECT AVG(CASE WHEN is_late THEN 0 ELSE 1 END) AS on_time_delivery_rate FROM fct_orders
WHERE is_delivered
  AND purchased_at >= DATE '2017-01-01' AND purchased_at < DATE '2018-01-01'
```

## e019: How many orders were canceled or unavailable in 2018?

**Gold answer:** 460

```sql
SELECT COUNT(*) AS canceled_orders FROM fct_orders
WHERE is_canceled
  AND purchased_at >= DATE '2018-01-01' AND purchased_at < DATE '2018-09-01'
```

## e020: What was the average review score in 2018?

**Gold answer:** 4.0899

```sql
SELECT AVG(review_score) AS avg_review_score FROM fct_orders
WHERE review_score IS NOT NULL
  AND purchased_at >= DATE '2018-01-01' AND purchased_at < DATE '2018-09-01'
```
