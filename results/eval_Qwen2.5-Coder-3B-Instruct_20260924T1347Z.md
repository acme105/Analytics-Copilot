# Eval results: Qwen/Qwen2.5-Coder-3B-Instruct (20260924T1347Z)

- Provider: vllm 0.9.2 on Kaggle (answers); scored locally by replaying the recorded replies; quantisation: none (fp16); hardware: Tesla T4 (Kaggle)
- Code version: 292c5d0 (answers); gold from 202b2a1 incl. D24 changes; items: 120 (20 verified)
- The Kaggle notebook answered all 360 questions (56.6 min, sequential) and then failed while saving (a model query returned an INTERVAL). Results were rebuilt by replaying the recorded replies with the same code version.
- 4 raw_schema answers could not be replayed (their repair reply was not reproducible) and are excluded: e030/raw_schema, h003/raw_schema, m008/raw_schema, m023/raw_schema
- 55 summaries could not be replayed (row order differs between machines); they do not affect correctness.
- Latency from the Kaggle log (seconds per answered question): raw_schema p50 10.6 / p95 16.2, semantic p50 9.8 / p95 17.5, semantic_rag p50 8.5 / p95 15.7. Scope-check replies were reused across modes within that run, so these slightly understate latency for semantic and semantic_rag.
- Agent prompts predate D24 ('placed' = all statuses); gold includes it.

## Headline by mode

| Mode | Execution accuracy (95% CI) | Correct refusals | False refusals | Grounded | p50 / p95 latency | Prompt tokens | Parse failures |
|---|---|---|---|---|---|---|---|
| raw_schema | 10.0% (2/20; 2.8%–30.1%) | – | 0.0% | 76.9% | – / – | 1257 | 0.0% |
| semantic | 70.0% (14/20; 48.1%–85.5%) | – | 0.0% | 90.0% | – / – | 3068 | 0.0% |
| semantic_rag | 85.0% (17/20; 64.0%–94.8%) | – | 0.0% | 90.0% | – / – | 2405 | 0.0% |

## Accuracy by difficulty

| Mode | easy | medium | hard |
|---|---|---|---|
| raw_schema | 10.0% (2/20) | – (0/0) | – (0/0) |
| semantic | 70.0% (14/20) | – (0/0) | – (0/0) |
| semantic_rag | 85.0% (17/20) | – (0/0) | – (0/0) |

## Why answers failed

| Mode | Failure labels |
|---|---|
| raw_schema | wrong_join: 11, hallucinated_column: 7 |
| semantic | wrong_metric_definition: 5, wrong_join: 1 |
| semantic_rag | wrong_filter: 2, wrong_metric_definition: 1 |

## Repair and grounding

| Mode | Errors | Repaired | Repair success | Template fallback |
|---|---|---|---|---|
| raw_schema | 35.0% | 35.0% | 0.0% | 23.1% |
| semantic | 0.0% | 0.0% | – | 10.0% |
| semantic_rag | 0.0% | 0.0% | – | 10.0% |
