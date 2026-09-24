# Eval results: Qwen/Qwen2.5-Coder-3B-Instruct (20260924T155559Z)

- Provider: vllm 0.9.2 (OpenAI-compatible); quantisation: none (fp16); hardware: Tesla T4, Tesla T4
- Code version: c5e45b3; items: 120 (40 verified)
- 4 questions in flight at once on one vLLM server; latency includes queueing.
- Golden set used as a development set (D29).

## Headline by mode

| Mode | Execution accuracy (95% CI) | Correct refusals | False refusals | Grounded | p50 / p95 latency | Prompt tokens | Parse failures |
|---|---|---|---|---|---|---|---|
| semantic_plan | 85.0% (34/40; 70.9%–92.9%) | – | 0.0% | 85.0% | 9.7s / 55.1s | 4675 | 0.0% |

## Accuracy by difficulty

| Mode | easy | medium | hard |
|---|---|---|---|
| semantic_plan | 90.0% (18/20) | 80.0% (16/20) | – (0/0) |

## Why answers failed

| Mode | Failure labels |
|---|---|
| semantic_plan | wrong_filter: 2, other: 2, wrong_time_grain: 1, wrong_metric_definition: 1 |

## Repair and grounding

| Mode | Errors | Repaired | Repair success | Template fallback |
|---|---|---|---|---|
| semantic_plan | 0.0% | 0.0% | – | 15.0% |
