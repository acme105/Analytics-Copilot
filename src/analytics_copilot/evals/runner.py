"""Run the golden set through the /ask pipeline and score it.

Locally: ``make eval ARGS="--limit 10"``. On Kaggle the eval notebook calls these
functions cell by cell. Each run writes ``results/eval_<model>_<timestamp>.json``
and a markdown table next to it.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections.abc import Callable
from contextvars import ContextVar
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from analytics_copilot.config import Settings
from analytics_copilot.evals.golden import GoldenItem, load_golden
from analytics_copilot.evals.report import MODES, summarise, to_markdown
from analytics_copilot.evals.scoring import classify_failure, compare_results
from analytics_copilot.executor import QueryResult, run_query
from analytics_copilot.llm import Completion, LLMClient, Message, OpenAICompatibleClient, Role
from analytics_copilot.pipeline import AskPipeline
from analytics_copilot.schemas import AskResponse, Mode

MAX_ROWS_KEPT = 20  # rows stored per record, enough to inspect a failure
# Cache hits for the question running in the current asyncio task (each task gets its own
# copy of the context, so counts stay per question when questions run concurrently).
_QUESTION_HITS: ContextVar[list[int] | None] = ContextVar("question_hits", default=None)


class CachingLLM:
    """Wraps an LLM client and stores every reply in a JSONL file, keyed by a hash of
    the model tag, role and full prompt. Any prompt change produces a new key, so the
    cache can't hide it.

    ``replay=False`` (a fresh run) only records: every call reaches the model, so latency
    is real for every answer. ``replay=True`` answers from the file first, for re-scoring
    a run without the GPU."""

    def __init__(self, inner: LLMClient, path: Path, model_tag: str, replay: bool = True) -> None:
        self.inner = inner
        self.path = path
        self.model_tag = model_tag
        self.replay = replay
        self.hits = 0
        self._store: dict[str, dict] = {}
        if path.exists():
            for line in path.read_text().splitlines():
                entry = json.loads(line)
                self._store[entry["key"]] = entry
        path.parent.mkdir(parents=True, exist_ok=True)

    def _key(self, role: Role, messages: list[Message], temperature: float, sample: int) -> str:
        # Greedy calls keep the original key so earlier recordings still replay.
        extra = [temperature, sample] if (temperature, sample) != (0.0, 0) else []
        payload = json.dumps([self.model_tag, role, messages, *extra], sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    async def complete(
        self, role: Role, messages: list[Message], temperature: float = 0.0, sample: int = 0
    ) -> Completion:
        """Return the cached reply if present, otherwise call the model and store it."""
        key = self._key(role, messages, temperature, sample)
        if self.replay and key in self._store:
            self.hits += 1
            counter = _QUESTION_HITS.get()
            if counter is not None:
                counter[0] += 1
            e = self._store[key]
            return Completion(e["text"], e["prompt_tokens"], e["completion_tokens"])
        completion = await self.inner.complete(role, messages, temperature, sample)
        entry = {"key": key, "text": completion.text, "prompt_tokens": completion.prompt_tokens,
                 "completion_tokens": completion.completion_tokens}  # fmt: skip
        self._store[key] = entry
        with self.path.open("a") as f:
            f.write(json.dumps(entry) + "\n")
        return completion


async def run_gold(items: list[GoldenItem], warehouse: Path) -> dict[str, QueryResult]:
    """Run every gold query once. Raises with the item id if any gold query fails."""
    results = {}
    for item in items:
        if item.gold_sql is None:
            continue
        try:
            results[item.id] = await run_query(item.gold_sql, warehouse, timeout_s=60)
        except Exception as error:
            raise RuntimeError(f"Gold SQL for {item.id} failed: {error}") from error
    return results


def _jsonable(value: Any) -> Any:
    return value.isoformat() if isinstance(value, date | datetime) else value


def score_item(
    item: GoldenItem, mode: Mode, response: AskResponse, gold: QueryResult | None, cached: int
) -> dict[str, Any]:
    """One eval record: what the agent did, the gold answer, and the verdict."""
    correct: bool | None = None
    failure_label = None
    match_reason = ""
    scaled: list[int] = []
    if item.expected_behaviour == "refuse":
        outcome = "correct_refusal" if response.status == "refused" else "missed_refusal"
    elif response.status == "refused":
        outcome, correct, failure_label = "false_refusal", False, "other"
    elif response.status == "error":
        outcome, correct = "error", False
        failure_label = classify_failure(response.sql, item.gold_sql or "", response.error)
    else:
        match = compare_results(gold.rows if gold else [], response.rows, item.ordered)
        correct, match_reason, scaled = match.correct, match.reason, match.scaled_columns
        outcome = "correct" if correct else "wrong"
        if not correct:
            failure_label = classify_failure(response.sql, item.gold_sql or "", None)
    return {
        "id": item.id,
        "mode": mode,
        "question": item.question,
        "difficulty": item.difficulty,
        "category": item.category,
        "expected": item.expected_behaviour,
        "verified": item.verified,
        "ordered": item.ordered,
        "status": response.status,
        "outcome": outcome,
        "correct": correct,
        "failure_label": failure_label,
        "failure_label_source": "auto" if failure_label else None,
        "match_reason": match_reason,
        "scaled_columns": scaled,
        "pred_sql": response.sql,
        "gold_sql": item.gold_sql,
        "pred_columns": response.columns,
        "pred_rows": response.rows[:MAX_ROWS_KEPT],
        "gold_columns": gold.columns if gold else [],
        "gold_rows": [[_jsonable(v) for v in row] for row in gold.rows[:MAX_ROWS_KEPT]]
        if gold
        else [],
        "answer_summary": response.answer_summary,
        "grounding": response.grounding.model_dump() if response.grounding else None,
        "repaired": response.repaired,
        "latency_ms": response.latency_ms,
        "usage": response.usage.model_dump(),
        "error": response.error,
        "cached_calls": cached,
    }


async def run_eval(
    pipeline: AskPipeline,
    items: list[GoldenItem],
    modes: list[Mode],
    gold: dict[str, QueryResult],
    on_record: Callable[[dict], None] | None = None,
    concurrency: int = 1,
) -> list[dict]:
    """Ask every item in every mode and score it, ``concurrency`` questions at a time.

    Records come back in item order whatever order they finish in. ``on_record`` sees
    each record as it lands. With concurrency above 1, latency includes queueing on the
    shared model server; the run metadata should say so.
    """
    semaphore = asyncio.Semaphore(concurrency)
    jobs = [(item, mode) for item in items for mode in modes]

    async def one(item: GoldenItem, mode: Mode) -> dict:
        async with semaphore:
            counter = [0]
            _QUESTION_HITS.set(counter)
            response = await pipeline.ask(item.question, mode)
            record = score_item(item, mode, response, gold.get(item.id), counter[0])
        if on_record:
            on_record(record)
        return record

    return list(await asyncio.gather(*(one(item, mode) for item, mode in jobs)))


def write_results(
    run: dict[str, Any], records: list[dict], out_dir: Path
) -> tuple[Path, Path, dict[str, Any]]:
    """Write the JSON (run, summary, records) and markdown report; return paths and summary."""
    summary = summarise(records)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"eval_{run['model'].split('/')[-1]}_{run['timestamp_utc']}"
    json_path = out_dir / f"{stem}.json"
    md_path = out_dir / f"{stem}.md"
    payload = {"run": run, "summary": summary, "records": records}
    # default=str: never lose a finished run to one unusual value in a model's result.
    json_path.write_text(json.dumps(payload, indent=2, default=str))
    md_path.write_text(to_markdown(run, summary))
    return json_path, md_path, summary


def print_progress(record: dict) -> None:
    """One line per answer: verdict, mode, seconds and question."""
    mark = {"correct": "✅", "correct_refusal": "✅"}.get(record["outcome"], "❌")
    seconds = record["latency_ms"].get("total", 0) / 1000
    label = f" [{record['failure_label']}]" if record["failure_label"] else ""
    print(f"{mark} {record['id']:<5} {record['mode']:<12} {seconds:5.1f}s "
          f"{record['outcome']}{label} :: {record['question'][:70]}", flush=True)  # fmt: skip


def main() -> None:
    """CLI: run the eval against the configured LLM endpoint."""
    parser = argparse.ArgumentParser(description="Run the golden eval set.")
    parser.add_argument("--mode", choices=[*MODES, "all"], default="all")
    parser.add_argument("--limit", type=int, default=None, help="first N items only")
    parser.add_argument("--cache", type=Path, default=Path("results/.cache/llm.jsonl"))
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--out", type=Path, default=Path("results"))
    parser.add_argument("--hardware", default="local")
    parser.add_argument("--concurrency", type=int, default=1)
    args = parser.parse_args()

    settings = Settings.from_env()
    llm: LLMClient = OpenAICompatibleClient(settings)
    if not args.no_cache:
        llm = CachingLLM(llm, args.cache, settings.sql_model)
    pipeline = AskPipeline.from_settings(settings, llm=llm)
    items = load_golden()[: args.limit]
    modes: list[Mode] = list(MODES) if args.mode == "all" else [args.mode]

    gold = asyncio.run(run_gold(items, settings.warehouse_path))
    records = asyncio.run(
        run_eval(
            pipeline, items, modes, gold, on_record=print_progress, concurrency=args.concurrency
        )
    )
    run = {
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "provider": settings.llm_base_url,
        "model": settings.sql_model,
        "quantisation": "provider default",
        "hardware": args.hardware,
        "code_version": "local",
        "modes": modes,
        "limit": args.limit,
        "concurrency": args.concurrency,
        "cache_hits": llm.hits if isinstance(llm, CachingLLM) else 0,
    }
    json_path, md_path, _ = write_results(run, records, args.out)
    print(f"Wrote {json_path} and {md_path}")


if __name__ == "__main__":
    main()
