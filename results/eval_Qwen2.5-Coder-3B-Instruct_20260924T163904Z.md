# Eval results, development set (golden.yaml): Qwen/Qwen2.5-Coder-3B-Instruct (20260924T163904Z)

- Provider: vllm 0.9.2 (OpenAI-compatible); quantisation: none (fp16); hardware: Tesla T4, Tesla T4
- Code version: 86a4471; items: 120 (40 verified)
- 4 questions in flight at once on one vLLM server; latency includes queueing.
- Golden set used as a development set (D29).

## Headline by mode

| Mode | Execution accuracy (95% CI) | Correct refusals | False refusals | Grounded | p50 / p95 latency | Prompt tokens | Parse failures |
|---|---|---|---|---|---|---|---|
| semantic_plan | 95.0% (38/40; 83.5%–98.6%) | – | 0.0% | 80.0% | 13.3s / 24.0s | 5291 | 0.0% |

## Accuracy by difficulty

| Mode | easy | medium | hard |
|---|---|---|---|
| semantic_plan | 100.0% (20/20) | 90.0% (18/20) | – (0/0) |

## Why answers failed

| Mode | Failure labels |
|---|---|
| semantic_plan | wrong_filter: 1, wrong_time_grain: 1 |

## Repair and grounding

| Mode | Errors | Repaired | Repair success | Template fallback |
|---|---|---|---|---|
| semantic_plan | 0.0% | 0.0% | – | 20.0% |

# Eval results, held-out set (holdout.yaml): Qwen/Qwen2.5-Coder-3B-Instruct (20260924T163904Z)

- Provider: vllm 0.9.2 (OpenAI-compatible); quantisation: none (fp16); hardware: Tesla T4, Tesla T4
- Code version: 86a4471; items: 40 (0 verified)
- 4 questions in flight at once on one vLLM server; latency includes queueing.
- Golden set used as a development set (D29).
- **Provisional:** no gold items are verified yet, so every number below is provisional until the gold set is reviewed.

## Headline by mode

| Mode | Execution accuracy (95% CI) | Correct refusals | False refusals | Grounded | p50 / p95 latency | Prompt tokens | Parse failures |
|---|---|---|---|---|---|---|---|
| semantic_plan | 76.5% (26/34; 60.0%–87.6%) | 100.0% | 2.9% | 84.8% | 11.7s / 116.0s | 7542 | 0.6% |

## Accuracy by difficulty

| Mode | easy | medium | hard |
|---|---|---|---|
| semantic_plan | 83.3% (10/12) | 91.7% (11/12) | 50.0% (5/10) |

## Why answers failed

| Mode | Failure labels |
|---|---|
| semantic_plan | other: 2, wrong_metric_definition: 2, wrong_filter: 2, wrong_table_or_join: 1, wrong_time_grain: 1 |

## Repair and grounding

| Mode | Errors | Repaired | Repair success | Template fallback |
|---|---|---|---|---|
| semantic_plan | 0.0% | 5.9% | 0.0% | 15.2% |
