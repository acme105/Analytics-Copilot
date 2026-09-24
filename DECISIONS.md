# Decisions

Each entry records the decision, the options considered, why this one, and the trade-off accepted.

## D1. One OpenAI-compatible client for every model provider

- **Options:** the anthropic SDK plus a separate client for open models; LiteLLM; one `AsyncOpenAI` client configured by base URL, model name and key.
- **Chosen:** one `AsyncOpenAI` client. vLLM, most hosted open-model APIs and Anthropic's compatibility endpoint all speak the OpenAI protocol, so switching provider is a config change, never a code change.
- **Trade-off:** provider-specific features (for example Anthropic's native structured outputs) are not available through the compatibility layer. If one is needed, a thin adapter goes behind the same `llm/` interface.

## D2. Primary model: Qwen2.5-Coder-3B-Instruct in fp16 on one T4

- **Options:** 7B fp16 split across both T4s (tensor parallelism 2); 7B AWQ 4-bit on one T4; 3B fp16 on one T4.
- **Chosen:** 3B fp16. It fits on one T4 with no quantization and no multi-GPU setup, which were the two riskiest steps on Kaggle. It also downloads and starts faster, which saves weekly GPU quota.
- **Trade-off:** weaker SQL on hard, multi-step questions. The 7B and OmniSQL-7B run as comparison models, so the gap is measured, not assumed.
- **Evidence:** the first smoke test (2026-09-24) answered "monthly delivered orders in 2017" with a single total of 40,930. That is the count by *delivery* date: by purchase date it is 43,428, and the answer wasn't broken down by month either. So the model can write runnable SQL but picks the date field and grain on its own. This is what the semantic layer is for.

## D3. Serving stack pinned to vLLM 0.9.2 + transformers 4.53.2

- **Options:** the latest vLLM; vLLM 0.9.x with the V0 engine; transformers + bitsandbytes without a server.
- **Chosen:** `vllm==0.9.2`, `transformers==4.53.2`, `VLLM_USE_V1=0`, `--dtype half`. Verified working on Kaggle T4 on 2026-09-24.
- **Why the pins:** T4s (compute capability 7.5) have no bf16, and newer vLLM releases may not support them. Kaggle's preinstalled transformers (4.54+) registers an `aimv2` config that crashes vLLM 0.9.2 at import.
- **Trade-off:** we're on an older vLLM and miss newer engine features. That's acceptable for a 3B–7B model evaluated in batch.

## D4. Customer identity is `customer_unique_id`

- **Options:** `customer_id`; `customer_unique_id`.
- **Chosen:** `customer_unique_id`. `customer_id` is issued per order (99,441 ids for 96,096 people), so it would make every customer look new and repeat purchase rate would be 0%.
- **Trade-off:** none worth noting. Joins still go through `customer_id`, and the person-level id comes from the customers table.

## D5. Default analysis window: 2017-01-01 to 2018-08-31 by purchase date (approved 2026-09-24)

- **Options:** all data (Sep 2016 – Oct 2018); 2017-01 to 2018-08; 2017-01 to 2018-07, to avoid right-censored delivery data.
- **Chosen:** 2017-01 to 2018-08. It keeps 99.6% of orders and drops 2016 (329 orders, with an empty November) and Sep–Oct 2018 (20 orders, none delivered).
- **Trade-off:** delivery metrics for late August 2018 are right-censored. Slow orders were still `shipped` at extraction, so those weeks look better than they were. Delivery metrics will carry a note rather than a shorter window for everything.

## D6. Default date field is the purchase timestamp

- **Options:** purchase, approval or delivery timestamp as the default.
- **Chosen:** `order_purchase_timestamp` for all 18 metrics, including delivery metrics. It's the moment demand happened and the field every order has. "On-time rate in March" therefore means "of orders placed in March, the share that arrived on time", a cohort view. That keeps delivery metrics comparable with orders and GMV for the same month.
- **Trade-off:** "delivered in 2017" questions are ambiguous. The answer states the date field it used in `assumptions`, and the golden set includes questions that test this.

## D7. Warehouse layers: raw text → staging views → materialised marts

- **Options:** query the CSVs directly; typed raw tables only; raw → staging → marts (dbt-style), built by a Python script.
- **Chosen:** raw tables loaded with every column as text, `stg_*` views that type every column and hold the cleaning rules as commented SQL, and `fct_*` / `dim_*` tables materialised at build time.
- **Why:** typing is explicit and reviewable in one place, not guessed by a CSV sniffer. Marts are materialised because they contain window functions (seller tiers, customer order number) that shouldn't rerun on every question.
- **Trade-off:** no dbt. The SQL lives in `warehouse.py` as ordered statements, with no lineage graph or per-model tests. At 10 tables that's simpler to read and to explain; dbt would pay off at 50+ models.

## D8. Order-level metrics use the order's highest-value item and payment for dimensions

- **Options:** fan orders out to every category/seller (double counts orders); allocate order metrics by item-value share (fractional orders); pick one primary item and payment per order.
- **Chosen:** primary item and payment by value. It affects few orders: 1.3% have several sellers, 0.8% several categories and 2.3% several payment types.
- **Trade-off:** a small misattribution on those orders for order-grain metrics (orders, AOV, delivery, reviews). Item-grain metrics (GMV, freight ratio, active sellers) use each item's own category and seller tier, so they are exact.

## D9. Seller tier: point-in-time percentile of trailing 3-month GMV

- **Options:** a fixed tier from all-time GMV; the current month's GMV; trailing 3 full months before the order month.
- **Chosen:** trailing 3 full months, ranked within each month: top 10% = `top`, next 40% = `mid`, rest = `long_tail`, no trailing GMV = `new_or_dormant`.
- **Why:** all-time GMV leaks the future (a seller who grows in 2018 would be "top" in 2017), and current-month GMV makes tier and outcome circular. The trailing window only uses information available at the time.
- **Trade-off:** a seller's tier changes month to month, and new sellers spend up to 3 months as `new_or_dormant`. Measured: `top` sellers carry 41% of GMV in the window.

## D10. "Late" compares calendar dates, not timestamps

- **Options:** `delivered_at > estimated_delivery_at` as timestamps; compare as dates.
- **Chosen:** dates. The estimate is a date stored as midnight, so a timestamp comparison marks an order arriving at 15:00 on the promised day as late.
- **Measured:** late delivery rate is 6.79% by date and 8.13% by timestamp over the window. That 1.3-point gap is entirely orders that arrived on the promised day.

## D11. Semantic layer: one aggregate expression over one mart per metric, compiled to SQL

- **Options:** a metrics framework (MetricFlow, Cube); let the LLM write full SQL from metric descriptions; a small in-house compiler.
- **Chosen:** each metric in `semantic/metrics.yaml` is one aggregate expression over one pre-joined mart, plus required filters and a date field. `compile_metric` adds the window, time grain, dimensions and filters. No joins happen at query time, because the marts already carry every dimension.
- **Why:** about 100 lines of code that fully explain what each number means. It gives the eval harness a deterministic, governed baseline to compare the LLM against.
- **Trade-off:** metrics can't combine views (for example GMV per delivered order) without a new mart column. That's acceptable at 18 metrics.

## D12. Cancellation includes `unavailable` orders (signed off 2026-09-24)

- **Options:** `canceled` only; `canceled` + `unavailable`.
- **Chosen:** both. An `unavailable` order was placed and then couldn't be fulfilled. From the customer's side it's a cancellation.
- **Measured:** cancellation rate over the window is 1.19%. It would be 0.59% counting `canceled` only.

## D13. Repeat purchase rate is a 90-day rate on first-order cohorts (signed off 2026-09-24)

- **Options:** share of the period's active customers who had any earlier order; customers with 2+ orders within the period; the share of first-time customers who order again within 90 days.
- **Chosen:** 90 days. Of customers whose first valid order falls in the period, the share whose second valid order came within 90 days of it. A first order only counts if it was placed at least 90 days before the data cutoff (last valid purchase, 3 Sep 2018), so monthly cohorts stop at June 2018.
- **Why:** a fixed follow-up window makes every cohort comparable. A plain "2+ orders in the period" rate grows with the period length, and late cohorts would look worse only because they had less time.
- **Measured:** 2.31% over the window, and 1.6%–3.9% by monthly cohort.
- **Open question:** 45% of these repeats (801 of 1,772 second orders within 90 days) came on the same day as the first order, 775 of them within an hour. They look like one basket split into two checkouts. Excluding second orders within 1 hour gives 1.30%. Kept in for now, as specified.

The other drafted definitions (valid orders, delivery metrics grouped by purchase month, credit-card-only instalments, card share by value, order-level dimensions from the highest-value item and payment) were signed off unchanged on 2026-09-24.

## D14. The SQL guard adds or lowers LIMIT instead of rejecting a query without one

- **Options:** reject SQL with no LIMIT and send it back for repair (the spec's first idea); rewrite the LIMIT.
- **Chosen:** rewrite. A missing LIMIT is added and a larger one is lowered to the cap (200 rows, fetched as 201 so truncation can be reported). The tests assert that a missing LIMIT gets added.
- **Why:** a missing LIMIT is harmless to fix mechanically. Rejecting it would spend a repair call (latency and tokens) and count as an error on questions the model answered correctly, such as a single-row total.
- **Trade-off:** the model is never taught to include LIMIT, which is fine because the guard always will.

## D15. Guard plus a locked-down connection: two independent defences

- **Chosen:** sqlglot guard (one SELECT, no write or admin nodes anywhere in the tree, allowed tables only, no table functions, known columns only), then a DuckDB connection opened `read_only=True` with `enable_external_access=false` and a timer that calls `interrupt()`.
- **Why:** a parser-based guard can miss an edge case. The connection-level settings mean a missed write fails as read-only, a missed `read_csv` can't reach the file system, and a runaway query is stopped. Tests cover each defence on its own, without the guard in front.
- **Trade-off:** the column check works on names only. A real column on the wrong table passes the guard and fails in DuckDB, and the repair step then handles it.

## D16. The three modes differ only in context; `raw_schema` sees the typed staging views

- **Options for raw_schema:** the raw text tables; the typed `stg_*` views.
- **Chosen:** `stg_*`. Raw tables store every column as text, which would measure casting skill instead of semantic understanding. `semantic` and `semantic_rag` see and may query only the `fct_*` marts.
- **Why:** it keeps the comparison fair: same model and rules, and only the business context changes. Mode tests assert what each prompt contains.

## D17. Lexical retrieval for semantic_rag, not embeddings

- **Options:** embeddings with a vector store; BM25; weighted word overlap on metric names, synonyms and descriptions.
- **Chosen:** word overlap (name, label and synonym matches score 3; description matches score 1; top 4 metrics), and Jaccard similarity for the top 3 example queries.
- **Why:** 18 metrics and 14 examples are a small, curated corpus. Synonyms in the YAML do the job embeddings would. There's no extra model competing for the T4, and every retrieval can be explained by the words that matched.
- **Trade-off:** a paraphrase that shares no words with a synonym ("how much did we sell") misses. The eval breakdown by mode will show whether that costs accuracy.
- **Guardrail:** the example library must not overlap the golden set, or `semantic_rag` would be scored on recall.

## D18. Grounding: every number in the summary must be a row value or a one-step derivation

- **Chosen:** a regex pulls numbers (with R$, %, k/M/million), and each must match, within the rounding it shows, one of: a cell; a ratio written as a percentage; a column total; the difference, ratio or percentage change between two values in a column (only for columns of 24 values or fewer); the row count or a rank; a date part; or a number in the question. One regeneration with the offending numbers named, then a template summary built from the first row.
- **Why:** deterministic and explainable. A failure lists the exact numbers that weren't grounded.
- **Trade-off:** it checks numbers, not claims. "Sales rose" over falling rows would pass. Pairwise derivations over short columns can match an invented number by chance, which is why the cap is 24 values.

## D19. Operational failures return a response, never a 500

- **Chosen:** `/ask` always returns an `AskResponse`. Guard, SQL and output-parse failures give `status: error` with the reason. An unreachable or failing LLM provider gives a clear "LLM provider error" status. Anything unexpected is logged with a stack trace and the request id, and returns a generic error.
- **Why:** the front end and the eval harness can treat every outcome uniformly, and failures are counted, not lost.

## D20. Eval scoring rules (signed off 2026-09-24)

- **Extra columns: lenient.** A prediction is correct if every gold column is present with matching values (matched by content, not name). Extra predicted columns are ignored.
- **Percent scale: accepted and flagged.** A column that matches gold exactly ×100 (6.79 vs 0.0679) counts as correct and is logged as `scale: percent`.
- **Ambiguous questions follow the governed default.** Questions with two fair readings (for example "delivered orders in 2017") are tagged `ambiguous: true`. Gold uses the governed default (purchase date, D6), and accuracy on ambiguous items is reported separately.
- **Floats:** relative tolerance 1e-3. Row order only counts when the item says `ordered: true`.

## D21. Semantic prompts say which date to use when the question doesn't

- **Change:** one rule added to the `semantic` and `semantic_rag` prompts: if the question doesn't say which date to use, use the metric's date field (purchase date) and state it in `assumptions.notes`. `raw_schema` doesn't get it; it stays the no-business-knowledge baseline.
- **Why this isn't tuning on the test set:** it applies D6, a decision made in Phase 1, and it was added before any golden question existed. The smoke test showed the need: `raw_schema` answered "delivered orders in 2017" with 40,930 by delivery date, while the governed answer is 43,426 by purchase date.

## D22. Signed-off decisions are sent to the SQL model as explicit rules, from the semantic layer

- **Problem (smoke test, 2026-09-24):** `semantic` mode had the metric definitions but still counted canceled orders, skipped the delivered-only filter and ignored the default window. The decisions lived in DECISIONS.md and were only implied inside 18 metric definitions, so a 3B model missed them.
- **Options:** hard-code rules in the prompt; fine-tune; put the rules in the semantic layer and generate the prompt from it.
- **Chosen:** `semantic/metrics.yaml` now holds `business_rules` (valid orders only, delivered orders for delivery metrics, reviewed orders for review metrics, purchase date for everything, the default window, people not order ids, BRL excluding freight) and column descriptions for the key mart columns. Semantic prompts render them, with the rules and a 3-point self-check placed **last**, where small models attend most. Each metric now lists "required filters" on its own line.
- **Also changed:**
  - **All modes:** generic SQL tips (use date ranges, never partial date strings; take each column from a table in FROM or JOIN). These fixed plain SQL errors and carry no business knowledge, so `raw_schema` stays a fair baseline.
  - **Summary:** English number format. The model had written "2,96 million", which the grounding check read as 296 million.
- **Guardrails:**
  - Tests assert that every business rule reaches the semantic prompts and none reaches `raw_schema`, and that every column note names a real column.
  - The fixes came from the 6 smoke questions, so those questions (and near-copies) are excluded from the golden set. Otherwise the eval would reward prompt tuning on the same questions.
- **Trade-off:** the semantic prompt grows by about 500 tokens (to about 2,600 in `semantic` and 1,800 in `semantic_rag`).

## D23. Eval harness design and the first (provisional) run

- **Golden set:** 120 items in `evals/golden.yaml`: 40 easy, 45 medium, 20 hard, 15 should-refuse. The gold SQL is written against the marts and follows the signed-off definitions. All items start `verified: false`; headline numbers switch to verified-only once any item is verified.
- **Leakage guard:** a test fails if any golden question scores 0.6 or more in word overlap with an example query or a smoke (tuning) question. The check flagged one real near-copy, which was replaced (h005). Four others shared only phrasing and were reworded.
- **Scoring:** D20 rules, implemented in `evals/scoring.py`. Columns are matched by content, trying each assignment of predicted columns to gold columns. Gold keeps only numeric columns where a label's wording can't be predicted ("late" vs "on time").
- **Failure labels:** automatic first pass from comparing the predicted and gold SQL with sqlglot, in this order: hallucinated column, then different tables (wrong join), then different aggregate or a missing definition flag such as `is_valid` (wrong metric definition), then different DATE_TRUNC grain, then different WHERE columns or date bounds (wrong filter). Every label is recorded as `auto`, so a reviewer can override it.
- **Cache:** replies are keyed by a hash of the model, role and full prompt. Latency is only computed over answers with no cached calls.
- **Defensive parsing fix found by the smoke re-run:** after D21, the model wrote `assumptions.notes` as a string, and strict validation turned 5 of the 18 answers into errors. The schema now accepts a single string as a one-item list.
- **Notebook:** `kaggle/eval/run_eval.ipynb`, a private kernel with one step per cell (settings, install, warehouse, model server, gold, run, headline, categories, failure gallery, save).
- **Trade-off:** answers run sequentially, so latency is clean per question but the run is slower than batched serving.

## D24. "Orders placed" counts every order, whatever its status (decided 2026-09-24, gold review batch 1)

- **Rule:** a question that counts orders that were *placed* counts all orders, including canceled and unavailable ones. "Orders" alone still means valid orders (D5, D12). When "placed in 2018" only names the period of another metric (for example a delivery rate), that metric keeps its own filters.
- **Effect on the golden set:** 8 gold answers changed (e001, e027, e032, m002, m005, m021, m028, h011). For example, e001 is now 45,101, up from 44,379. h016 ("placed a second order") is left for review because it interacts with the new-customer definition.
- **Effect on the agent:** a matching business rule was added to the semantic layer. It changes every semantic prompt, so it applies from the next eval run. The run in progress used the earlier prompts and is scored against the earlier gold.
- **Why this is not tuning on the test set:** the definition came from the owner's review of what the question means, not from model output. The gold changed to match the wording, not to match a prediction.

## D25. Customer identity confirmed: `customer_unique_id` is the only person-level key

- **Checked because:** 2018 looked like "almost everyone is new" (51,668 of 52,337 buyers).
- **Evidence:** `customer_id` is unique per order (99,441 ids, never more than 1 order each). Zip prefix, city and state are shared locations: 11,898 zip + city pairs hold more than one person. `customer_unique_id` has 96,096 values, and 93,099 of those people (96.9%) ordered exactly once. So the "all new" pattern is a real property of the data, not a wrong key.

## D26. First eval run: failure while saving, replay, and fixes

- **What happened:** the Kaggle notebook answered all 360 questions in 56.6 minutes, then crashed in the save cell. One model query returned an INTERVAL (Python `timedelta`), which JSON can't encode.
- **Recovery:** the recorded replies were replayed locally with the exact code version that ran (292c5d0) and scored against the current gold. 4 `raw_schema` answers depended on a repair reply that couldn't be replayed, so they are excluded and listed in the results file. 55 summaries differed only in row order and don't affect correctness.
- **Fixes:**
  - the executor now turns INTERVAL into days;
  - the results writer falls back to text for any unusual value, so a finished run can't be lost at the save step;
  - a fresh run's cache only records (`replay=False`). In this run the scope-check reply was reused across modes, which excluded most answers from the latency statistics.
- **Known limitation found:** in `raw_schema` the failure label is almost always "wrong join", because that mode queries `stg_*` tables and gold uses `fct_*` marts. For that mode the label doesn't show the real cause yet.
