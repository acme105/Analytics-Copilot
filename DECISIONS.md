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

## D5. Default analysis window: 2017-01-01 to 2018-08-31 by purchase date (proposed, awaiting approval)

- **Options:** all data (Sep 2016 – Oct 2018); 2017-01 to 2018-08; 2017-01 to 2018-07, to avoid right-censored delivery data.
- **Chosen (proposed):** 2017-01 to 2018-08. It keeps 99.6% of orders and drops 2016 (329 orders, with an empty November) and Sep–Oct 2018 (20 orders, none delivered).
- **Trade-off:** delivery metrics for late August 2018 are right-censored. Slow orders were still `shipped` at extraction, so those weeks look better than they were. Delivery metrics will carry a note rather than a shorter window for everything.

## D6. Default date field is the purchase timestamp

- **Options:** purchase, approval or delivery timestamp as the default.
- **Chosen:** `order_purchase_timestamp`, the moment demand happened and the field every order has. Delivery metrics (on-time rate, delivery days) declare `order_delivered_customer_date` or the purchase date explicitly in their definition.
- **Trade-off:** "delivered in 2017" questions are ambiguous. The answer states the date field it used in `assumptions`, and the golden set includes questions that test this.
