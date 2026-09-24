# Failure analysis: semantic_plan, run 20260924T145727Z (code 8e53e7c)

All 120 golden questions were checked one by one: 88 were right (74 of 105 answerable + 14 of 15 refusals) and 32 went wrong (31 answerable + 1 missed refusal). Each failure below was diagnosed by reading the agent's SQL (and its plan, on the metric-plan route) against the gold SQL.

Route: **P** = metric plan (the model picks a governed metric, code writes the SQL); **C** = custom SQL.

## Summary

| Root cause | Failures | Ids |
|---|---|---|
| A. No governed metric fits, so the planner forces the nearest one | 8 | e019, e025, e035, e036, m038, h015, h018, r014 |
| B. Dates in the plan: off-by-one end dates, invented periods, dropped periods | 6 | e002, e005, e015, e033, m042, h019 |
| C. Plan misreads the question (wrong shape, metric or filter) | 6 | e017, e040, m002, m006, m033, h001 |
| D. Custom SQL logic errors (multi-step reasoning) | 10 | m039, h002, h003, h006, h008, h010, h011, h013, h016, h020 |
| E. Malformed JSON from the model | 1 | m019 |
| F. Scorer false negative (the answer was right) | 1 | h007 |

- **By route:** 20 failures on the plan route, 12 on custom SQL. The plan route answers 85 questions (78% right); custom SQL answers 20 (40% right).
- **A, B and C (20 of 32) are fixable with deterministic engineering:** more governed metrics, code-computed dates, stricter routing.
- **D (10 of 32) is the model's reasoning limit on multi-step SQL.** It needs a stronger SQL model, or decomposition.
- **No gold answer was found to be wrong.** One scorer rule needs extending (F).

## A. Missing metric: the planner forces the nearest one (8)

The catalogue has no metric for these, and the planner picks something close instead of going to custom SQL.

| Id | Question | What the agent did | Why it's wrong |
|---|---|---|---|
| e019 | Orders canceled or unavailable in 2018 | `orders` + `payment_type = 'not_defined'` → 0 | No canceled-count metric, so it invented a filter. Gold: 460 |
| e025 | Total freight charged in 2018 | `freight_ratio`, by day | A ratio, not a total. Gold: R$1,252,100 |
| e035 | Freight paid by MG customers in 2017 | `freight_ratio` for MG → 16.6% | A ratio, not a total. Gold: R$118,865 |
| e036 | Money paid by debit card in 2018 | `gmv` filtered to debit_card → R$145,405 | Item prices, not payments (payments include freight). Gold: R$169,302 |
| m038 | Amount paid with each payment type, 2017 | `gmv` by payment type | Same: item prices instead of payment values |
| h015 | 5 states with the slowest **median** delivery | `avg_delivery_days`, sorted **asc** | Average ≠ median, and "slowest" needs descending. Gold: AM, RR, AP … |
| h018 | 5 states paying the most freight **per order**, 2018 | `freight_ratio` by state | A ratio, not R$ per order. Gold: RR R$60.60 … |
| r014 | What we paid shipping carriers (should refuse) | `freight_ratio` for 2018 | Answered with customer freight; carrier cost isn't in the data |

## B. Dates in the plan (6)

| Id | Question | The plan's dates | Why it's wrong |
|---|---|---|---|
| e002 | GMV, first half of 2018 | end `2018-06-30` (exclusive) | Loses 30 June: R$5,598,753 vs R$5,614,133 |
| e005 | Share of delivered orders late, overall | `2018-01-01` → `2019-01-01` | "Overall" means the whole window; it invented 2018 (7.7% vs 6.8%) |
| e015 | Share of 1–2 star reviews | `2017-01-01` → `2018-01-01` | No period asked; it invented 2017 |
| e033 | Average review score for RJ | `2017-01-01` → `2018-01-01` | No period asked; it invented 2017 |
| m042 | Top 5 states for new customers **in 2018** | no dates | Dropped the asked period, so it used the whole window |
| h019 | Review score, H1 vs H2 2017 | ends `06-30` / `12-31`, plus sort desc limit 1 | Off-by-one ends, and `limit 1` kept only one half |

## C. Plan misreads the question (6)

| Id | Question | What the plan did | Why it's wrong |
|---|---|---|---|
| e017 | Orders from SP in 2018 | `share_of` SP → 44% | Should be a filter (count 23,598), not a share |
| e040 | Which month had the highest GMV | no grain, sort desc, limit 1 | Returned the total; needed `grain: month` |
| m002 | 10 states that **placed** the most orders | `orders` | Should be `orders_placed` (D24); it counted valid orders only |
| m006 | GMV each seller tier generated | `gmv_per_seller` | Wrong metric: GMV per seller, not total GMV |
| m033 | Late rate, Black Friday week 2017 | added `group_by state`, filter **BA**, grain week | Made up a state filter; the question is about everyone |
| h001 | Categories with the most GMV growth, H1 2017 → H1 2018 | two periods plus sort by GMV | Ranked each half's GMV instead of the *difference*; growth needs custom SQL. Also off-by-one ends |

## D. Custom SQL logic errors (10)

| Id | Question | What the SQL did | Why it's wrong |
|---|---|---|---|
| m039 | Delivery days, late vs on-time | Grouped by month; "late" and "on-time" columns are the same AVG | Never split by `is_late` |
| h002 | Month-over-month % change in orders, 2018 | Filtered to 2018, then LAG | January has no previous month, so it's NULL (gold compares with Dec 2017) |
| h003 | State that improved its late rate most | One row per state over both years, same value twice | Never split 2017 from 2018; threshold on combined orders; sign reversed |
| h006 | Categories with the most 1–2★ reviews (≥1,000 reviewed) | Added `is_valid` | Review metrics count all reviewed orders; `unknown` drops from 39.5% to 18.7% |
| h008 | Month with the biggest GMV drop | % change sorted **desc** | Found the biggest *rise*; gold is Dec 2017 |
| h010 | AOV, first vs repeat orders | One overall AOV | Never split first vs repeat |
| h011 | Customers who **placed** 3+ orders | `is_valid AND customer_order_number >= 3` | Valid orders only (placed means all statuses): 236 vs 247 |
| h013 | Share of 2018 orders where freight > half the item value | Item-level comparison, weighted by price | The question is per order: 4.6% vs 16.1% |
| h016 | Q1 2018 new customers who ordered again by August | 2018 new customers with `repeat_within_90d` | Wrong cohort (Jan–Aug, not Q1) and wrong definition (90 days, not "by August") |
| h020 | Average days from first to second order | `GROUP BY customer` → 200 rows | Averaged per customer instead of overall |

## E. Malformed JSON (1)

| Id | What happened |
|---|---|
| m019 | The plan was invalid (it filtered on a metric name). The custom-SQL reply then contained a raw control character, and strict JSON parsing rejected it twice. |

## F. Scorer false negative (1)

| Id | What happened |
|---|---|
| h007 | **The answer was correct**: all four averages match gold exactly. But the year came back as `2017-01-01` (a DATE_TRUNC to year), and gold has the integer `2017`. The scorer doesn't treat these as equal. |

## Recommended fixes, in order of expected gain

1. **Complete the metric catalogue (A: 7 answerable + 1 refusal).** Add `total_freight`, `freight_per_order`, `payment_value`, `canceled_orders` and `median_delivery_days`. Also tell the planner to choose custom SQL when no metric *exactly* fits instead of the nearest one. For r014, state in the scope prompt that freight is what customers paid, not carrier cost.
2. **Let code compute dates (B: 6).** The planner names the period (a year, half, quarter, month, week or date range, or none), and code produces exact half-open dates. Add a grounding check: no dates in the plan unless the question names a period.
3. **Tighten the plan contract (C: 6).** Explain filter vs share_of ("how many from X" is a filter), make "which month/week…" imply a grain plus top-1, and route growth/difference questions to custom SQL. Checks can catch some of this, for example a filter or group_by the question never mentions (m033).
4. **Parse JSON leniently (E: 1).** Allow control characters inside strings (`strict=False`).
5. **Extend the scorer (F: 1).** Treat a 1 January date as equal to an integer year. This is a scoring-rule change (an extension of D20), so it needs the owner's sign-off.
6. **Custom SQL (D: 10) is the model's limit.** The 3B model loses track of multi-step logic: splitting by a flag, cohorts, comparisons across periods. Options are a stronger SQL model for this route (OmniSQL-7B) or decomposition (plan the steps, then write the SQL).

**Caveat (D29):** fixes 1–3 respond to failures seen on the golden set, so the next run will overstate generalisation. A fresh held-out set is needed for a clean number.
