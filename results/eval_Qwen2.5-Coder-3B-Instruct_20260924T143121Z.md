# Eval results: Qwen/Qwen2.5-Coder-3B-Instruct (20260924T143121Z)

- Provider: vllm 0.9.2 (OpenAI-compatible); quantisation: none (fp16); hardware: Tesla T4, Tesla T4
- Code version: e83df33; items: 120 (40 verified)
- 4 questions in flight at once on one vLLM server; latency includes queueing.
- Golden set used as a development set (D29).

## Headline by mode

| Mode | Execution accuracy (95% CI) | Correct refusals | False refusals | Grounded | p50 / p95 latency | Prompt tokens | Parse failures |
|---|---|---|---|---|---|---|---|
| semantic_plan | 85.0% (34/40; 70.9%–92.9%) | – | 0.0% | 85.0% | 7.8s / 46.5s | 3677 | 0.0% |

## Accuracy by difficulty

| Mode | easy | medium | hard |
|---|---|---|---|
| semantic_plan | 75.0% (15/20) | 95.0% (19/20) | – (0/0) |

## Why answers failed

| Mode | Failure labels |
|---|---|
| semantic_plan | wrong_filter: 4, wrong_metric_definition: 2 |

## Repair and grounding

| Mode | Errors | Repaired | Repair success | Template fallback |
|---|---|---|---|---|
| semantic_plan | 0.0% | 0.0% | – | 15.0% |
