# Olist dataset

**Source:** [Brazilian E-Commerce Public Dataset by Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce) (`olistbr/brazilian-ecommerce`), verified with the Kaggle CLI on 2026-09-24.
**Licence:** CC BY-NC-SA 4.0. Non-commercial use with attribution, and derivatives must use the same licence.
**What it is:** real, anonymised orders from the Olist marketplace in Brazil. Company and partner names in review text were replaced with names of Game of Thrones houses.

Raw files are not committed. Run `make data` to download them into `data/raw/` (needs a Kaggle token, see the root README). On Kaggle, attach the dataset to the notebook instead: the files appear under `/kaggle/input/`.

All numbers below come from `make profile`. The full report is [results/phase0_profile.md](../results/phase0_profile.md).

## Tables

| Table (short name) | File | Rows | Grain (one row per…) | Primary key |
|---|---|---|---|---|
| `orders` | `olist_orders_dataset.csv` | 99,441 | order | `order_id` |
| `order_items` | `olist_order_items_dataset.csv` | 112,650 | item line within an order | (`order_id`, `order_item_id`) |
| `order_payments` | `olist_order_payments_dataset.csv` | 103,886 | payment within an order | (`order_id`, `payment_sequential`) |
| `order_reviews` | `olist_order_reviews_dataset.csv` | 99,224 | review–order pair | none clean, see quirks |
| `customers` | `olist_customers_dataset.csv` | 99,441 | order's customer record | `customer_id` |
| `products` | `olist_products_dataset.csv` | 32,951 | product | `product_id` |
| `sellers` | `olist_sellers_dataset.csv` | 3,095 | seller | `seller_id` |
| `geolocation` | `olist_geolocation_dataset.csv` | 1,000,163 | zip prefix × coordinate sample | none |
| `category_translation` | `product_category_name_translation.csv` | 71 | Portuguese category name | `product_category_name` |

## Key columns

- **orders:** `customer_id`, `order_status` (8 values), and five timestamps: `order_purchase_timestamp`, `order_approved_at`, `order_delivered_carrier_date`, `order_delivered_customer_date` and `order_estimated_delivery_date`.
- **order_items:** `product_id`, `seller_id`, `price` (BRL, excludes freight), `freight_value` (BRL) and `shipping_limit_date`. One row per unit: an order for 3 of the same product has 3 rows.
- **order_payments:** `payment_type` (credit_card, boleto, voucher, debit_card, not_defined), `payment_installments` and `payment_value` (BRL, includes freight).
- **order_reviews:** `review_score` (1–5), optional title and message (Portuguese), `review_creation_date` and `review_answer_timestamp`.
- **customers:** `customer_unique_id`, `customer_zip_code_prefix` (5 characters, text), `customer_city` and `customer_state` (27 states).
- **products:** `product_category_name` (Portuguese) and physical attributes. The column names `product_name_lenght` and `product_description_lenght` are misspelled in the source.
- **sellers:** `seller_zip_code_prefix`, `seller_city` and `seller_state` (23 states).

## Join keys

```
customers.customer_id ──1:1── orders.customer_id
orders.order_id ──1:N── order_items.order_id
orders.order_id ──1:N── order_payments.order_id
orders.order_id ──1:N── order_reviews.order_id
order_items.product_id ──N:1── products.product_id
order_items.seller_id ──N:1── sellers.seller_id
products.product_category_name ──N:1── category_translation.product_category_name
*_zip_code_prefix ──N:N── geolocation.geolocation_zip_code_prefix   (not a key; aggregate first)
```

There are no orphaned foreign keys: every item, payment and review points at an existing order, product and seller.

## Quirks that affect metrics

1. **`customer_id` is per order, not per person.** Each order gets a new `customer_id` (99,441 of them), but there are only 96,096 real people (`customer_unique_id`). Customer counts, new customers and repeat purchase rate must use `customer_unique_id`, or repeat purchase comes out as zero. Only 2,997 people ordered more than once, so the repeat rate is low (about 3%).
2. **Date-field ambiguity.** Delivered orders in 2017 are 43,428 by purchase date but 40,930 by delivery date. Every metric must name its date field. The default is `order_purchase_timestamp`.
3. **Sparse edge months.** Sep–Dec 2016 has 329 orders in total and November 2016 has none. After August 2018 there are 20 orders and none are delivered. The last week of August 2018 is already thin (130 orders in the week of 27 August).
4. **Order status.** 97% of orders are `delivered`. There are 625 `canceled` and 609 `unavailable` orders, and 1,107 were still `shipped` when the data was extracted. 775 orders have no items, mostly `unavailable` (603) and `canceled` (164). They contribute nothing to GMV but do count as orders unless filtered out.
5. **Status/date contradictions.** 8 `delivered` orders have no delivery date, 6 `canceled` orders have one, and 166 orders were handed to the carrier before they were purchased. Delivery metrics use only delivered orders that have a delivery date.
6. **Reviews are not unique.** 814 rows repeat an existing `review_id`: 789 review ids are shared across several orders (one review for a multi-order purchase), and 547 orders have more than one review. 768 orders have no review. Review metrics take the latest review per order.
7. **Payments don't always match items.** For 249 orders, total payments differ from item price + freight by more than 1 BRL (vouchers, interest on instalments). GMV comes from `order_items.price`, never from payments. Other payment oddities: 1 order has no payment row, 3 payments have type `not_defined` and 2 have 0 instalments.
8. **Categories.** 610 products (1.85%) have no category. Two categories have no English translation: `pc_gamer` and `portateis_cozinha_e_preparadores_de_alimentos`. Use the Portuguese name as a fallback and `unknown` for nulls.
9. **Geolocation is noisy.** It has 1,000,163 rows for 19,015 zip prefixes: 261,831 rows are exact duplicates and 42 points fall outside Brazil. 157 customer zip prefixes are missing from it entirely. Use state and city for location dimensions, not coordinates.
10. **Future dates.** Some `shipping_limit_date` values run into 2020. They are seller deadlines, not events, so ignore them for windows.
11. **Seasonality.** The busiest day is Black Friday 2017 (24 November: 1,176 orders). This is real, not an error, but it dominates November 2017.
12. **Zero freight.** 383 item lines have `freight_value = 0`. They are kept, and freight ratio is computed over the totals.

## Recommended analysis window

**1 January 2017 to 31 August 2018, by purchase date** (20 full months, 99,092 orders: 99.6% of the total).

- Why: 2016 has 329 orders in three isolated months with a gap in November, so month-on-month and year-on-year comparisons there are meaningless. After August 2018 nothing is delivered.
- Caveat: for delivery-time metrics, orders from the last weeks of August 2018 are right-censored. Orders that were slow to arrive were still `shipped` when the data was extracted, so late-delivery rates for those weeks will look better than they were. Metric definitions should flag this.
- The warehouse keeps all rows. The window is a default filter in the semantic layer, and every answer states it in `assumptions`.
