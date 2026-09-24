"""Turn per-question eval records into metrics and a markdown report."""

from __future__ import annotations

import math
import statistics
from collections import Counter
from typing import Any

MODES = ("raw_schema", "semantic", "semantic_rag", "semantic_plan")
DIFFICULTIES = ("easy", "medium", "hard")


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson confidence interval for a proportion."""
    if total == 0:
        return (0.0, 0.0)
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def _rate(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))
    return ordered[index]


def accuracy(records: list[dict]) -> dict[str, Any]:
    """Execution accuracy over answerable records, with a 95% interval."""
    answerable = [r for r in records if r["expected"] == "answer"]
    correct = sum(bool(r["correct"]) for r in answerable)
    low, high = wilson_interval(correct, len(answerable))
    return {
        "n": len(answerable),
        "correct": correct,
        "execution_accuracy": _rate(correct, len(answerable)),
        "ci95": [low, high],
    }


def mode_summary(records: list[dict]) -> dict[str, Any]:
    """All headline metrics for one mode's records."""
    answerable = [r for r in records if r["expected"] == "answer"]
    refusable = [r for r in records if r["expected"] == "refuse"]
    ok = [r for r in records if r["status"] == "ok"]
    repaired = [r for r in answerable if r["repaired"]]
    uncached = [r for r in records if r["cached_calls"] == 0]
    latencies = [r["latency_ms"].get("total", 0.0) for r in uncached]
    usage = [r["usage"] for r in records]
    calls = sum(u["llm_calls"] for u in usage)
    failures = Counter(r["failure_label"] for r in answerable if r["failure_label"])
    return {
        **accuracy(records),
        "correct_refusal_rate": _rate(
            sum(r["outcome"] == "correct_refusal" for r in refusable), len(refusable)
        ),
        "false_refusal_rate": _rate(
            sum(r["outcome"] == "false_refusal" for r in answerable), len(answerable)
        ),
        "grounding_pass_rate": _rate(
            sum(bool(r["grounding"] and r["grounding"]["passed"]) for r in ok), len(ok)
        ),
        "template_fallback_rate": _rate(
            sum(bool(r["grounding"] and r["grounding"]["fallback_used"]) for r in ok), len(ok)
        ),
        "error_rate": _rate(sum(r["status"] == "error" for r in answerable), len(answerable)),
        "repair_rate": _rate(len(repaired), len(answerable)),
        "repair_success_rate": _rate(sum(bool(r["correct"]) for r in repaired), len(repaired)),
        "parse_failure_rate": _rate(sum(u["parse_failures"] for u in usage), calls),
        "latency_ms_p50": statistics.median(latencies) if latencies else None,
        "latency_ms_p95": _percentile(latencies, 0.95),
        "latency_measured_on": len(latencies),
        "prompt_tokens_mean": statistics.mean(u["prompt_tokens"] for u in usage) if usage else None,
        "completion_tokens_mean": (
            statistics.mean(u["completion_tokens"] for u in usage) if usage else None
        ),
        "failure_labels": dict(failures.most_common()),
        "by_difficulty": {
            d: accuracy([r for r in records if r["difficulty"] == d]) for d in DIFFICULTIES
        },
        "by_category": {
            c: accuracy([r for r in records if r["category"] == c])
            for c in sorted({r["category"] for r in answerable})
        },
    }


def summarise(records: list[dict]) -> dict[str, Any]:
    """Metrics per mode, for verified items and for all items (provisional)."""
    verified = [r for r in records if r["verified"]]
    modes = [m for m in MODES if any(r["mode"] == m for r in records)]
    return {
        "items": len({r["id"] for r in records}),
        "verified_items": len({r["id"] for r in verified}),
        "all_items": {m: mode_summary([r for r in records if r["mode"] == m]) for m in modes},
        "verified_only": {m: mode_summary([r for r in verified if r["mode"] == m]) for m in modes}
        if verified
        else None,
    }


def _pct(value: float | None) -> str:
    return "–" if value is None else f"{100 * value:.1f}%"


def _ms(value: float | None) -> str:
    return "–" if value is None else f"{value / 1000:.1f}s"


def to_markdown(run: dict[str, Any], summary: dict[str, Any]) -> str:
    """Human-readable report: run details, headline table and breakdowns."""
    scope = "verified_only" if summary["verified_only"] else "all_items"
    metrics = summary[scope]
    lines = [
        f"# Eval results: {run['model']} ({run['timestamp_utc']})",
        "",
        f"- Provider: {run['provider']}; quantisation: {run['quantisation']}; "
        f"hardware: {run['hardware']}",
        f"- Code version: {run['code_version']}; items: {summary['items']} "
        f"({summary['verified_items']} verified)",
    ]
    lines += [f"- {note}" for note in run.get("notes", [])]
    if scope == "all_items":
        lines.append(
            "- **Provisional:** no gold items are verified yet, so every number below is "
            "provisional until the gold set is reviewed."
        )
    lines += [
        "",
        "## Headline by mode",
        "",
        "| Mode | Execution accuracy (95% CI) | Correct refusals | False refusals "
        "| Grounded | p50 / p95 latency | Prompt tokens | Parse failures |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for mode, m in metrics.items():
        low, high = m["ci95"]
        lines.append(
            f"| {mode} | {_pct(m['execution_accuracy'])} ({m['correct']}/{m['n']}; "
            f"{_pct(low)}–{_pct(high)}) | {_pct(m['correct_refusal_rate'])} "
            f"| {_pct(m['false_refusal_rate'])} | {_pct(m['grounding_pass_rate'])} "
            f"| {_ms(m['latency_ms_p50'])} / {_ms(m['latency_ms_p95'])} "
            f"| {m['prompt_tokens_mean']:.0f} | {_pct(m['parse_failure_rate'])} |"
        )
    lines += ["", "## Accuracy by difficulty", "", "| Mode | " + " | ".join(DIFFICULTIES) + " |"]
    lines.append("|---|" + "---|" * len(DIFFICULTIES))
    for mode, m in metrics.items():
        cells = [
            f"{_pct(m['by_difficulty'][d]['execution_accuracy'])} "
            f"({m['by_difficulty'][d]['correct']}/{m['by_difficulty'][d]['n']})"
            for d in DIFFICULTIES
        ]
        lines.append(f"| {mode} | " + " | ".join(cells) + " |")
    lines += ["", "## Why answers failed", "", "| Mode | Failure labels |", "|---|---|"]
    for mode, m in metrics.items():
        labels = ", ".join(f"{k}: {v}" for k, v in m["failure_labels"].items()) or "none"
        lines.append(f"| {mode} | {labels} |")
    lines += ["", "## Repair and grounding", "", "| Mode | Errors | Repaired | Repair success "
              "| Template fallback |", "|---|---|---|---|---|"]  # fmt: skip
    for mode, m in metrics.items():
        lines.append(
            f"| {mode} | {_pct(m['error_rate'])} | {_pct(m['repair_rate'])} "
            f"| {_pct(m['repair_success_rate'])} | {_pct(m['template_fallback_rate'])} |"
        )
    return "\n".join(lines) + "\n"
