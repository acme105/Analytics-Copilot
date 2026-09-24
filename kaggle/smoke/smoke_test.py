"""Kaggle smoke test: real questions through the /ask pipeline, in all three modes.

Runs headless via `make kaggle-smoke`. Steps: install the pinned serving stack, unpack
the project code (private dataset acme105/olist-copilot-code), build the warehouse from
the attached Olist dataset, start vLLM, ask each question in each mode, and write
/kaggle/working/results/smoke_<timestamp>.json for `kaggle kernels output`.
"""

import asyncio
import glob
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

WORK = Path("/kaggle/working")
MODEL = "Qwen/Qwen2.5-Coder-3B-Instruct"
MODES = ["raw_schema", "semantic", "semantic_rag"]
# Ad-hoc smoke questions, not the golden set. They span a plain count, a ranking, a trend,
# a category filter, the delivered-vs-purchased date trap, and one refusal.
QUESTIONS = [
    "How many orders were placed in March 2018?",
    "Which 5 states had the highest GMV in 2018?",
    "Show the monthly late delivery rate in 2018.",
    "What was the average review score for the bed_bath_table category?",
    "How many delivered orders were there in 2017?",
    "What was our profit margin last quarter?",
]


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def install() -> None:
    """Pinned serving stack (DECISIONS D3) plus the project's own dependencies."""
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "--no-cache-dir", "--retries", "10",
         "--timeout", "120", "vllm==0.9.2", "transformers==4.53.2", "sqlglot>=25",
         "duckdb>=1.1", "pyyaml>=6.0"],
        check=True,
    )  # fmt: skip


def unpack_code() -> Path:
    """Copy the project into /kaggle/working/repo and put its src/ on sys.path.

    Kaggle may or may not extract the uploaded archive, so handle both.
    """
    repo = WORK / "repo"
    extracted = glob.glob("/kaggle/input/**/src/analytics_copilot/pipeline.py", recursive=True)
    if extracted:
        shutil.copytree(Path(extracted[0]).parents[2], repo, dirs_exist_ok=True)
    else:
        archive = glob.glob("/kaggle/input/**/copilot_code.tar.gz", recursive=True)
        assert archive, "Code dataset not attached: acme105/olist-copilot-code"
        with tarfile.open(archive[0]) as tar:
            tar.extractall(repo)
    sys.path.insert(0, str(repo / "src"))
    code_version = (repo / "VERSION").read_text().strip() if (repo / "VERSION").exists() else "?"
    log(f"code version {code_version}")
    return repo


def start_vllm() -> subprocess.Popen:
    """Start the OpenAI-compatible server and wait for /health (up to 20 minutes)."""
    import requests

    env = {**os.environ, "VLLM_USE_V1": "0"}
    log_file = open(WORK / "vllm.log", "w")  # noqa: SIM115 - kept open for the server's life
    server = subprocess.Popen(
        [sys.executable, "-m", "vllm.entrypoints.openai.api_server", "--model", MODEL,
         "--dtype", "half", "--max-model-len", "8192", "--gpu-memory-utilization", "0.90",
         "--port", "8000"],
        stdout=log_file, stderr=subprocess.STDOUT, env=env,
    )  # fmt: skip
    deadline = time.time() + 20 * 60
    while time.time() < deadline and server.poll() is None:
        try:
            if requests.get("http://localhost:8000/health", timeout=2).status_code == 200:
                log(f"vLLM up: {MODEL}")
                return server
        except requests.ConnectionError:
            pass
        time.sleep(10)
    print((WORK / "vllm.log").read_text()[-5000:])
    raise RuntimeError("vLLM did not start; log tail above")


def gpu_name() -> str:
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True, text=True
    )
    return ", ".join(result.stdout.split("\n")[:2]).strip(", ")


async def ask_all(warehouse: Path) -> list[dict]:
    from analytics_copilot.config import Settings
    from analytics_copilot.pipeline import AskPipeline

    settings = Settings(
        llm_base_url="http://localhost:8000/v1",
        sql_model=MODEL,
        summary_model=MODEL,
        warehouse_path=warehouse,
    )
    pipeline = AskPipeline.from_settings(settings)
    results = []
    for question in QUESTIONS:
        for mode in MODES:
            response = await pipeline.ask(question, mode)
            results.append(response.model_dump(mode="json"))
            seconds = response.latency_ms.get("total", 0) / 1000
            log(f"[{mode}] {response.status} in {seconds:.1f}s :: {question}")
            print(f"    SQL: {(response.sql or '-')[:300]}")
            print(f"    rows: {response.rows[:3]}")
            print(f"    answer: {response.answer_summary[:300]}", flush=True)
    return results


def main() -> None:
    log("installing")
    install()
    repo = unpack_code()

    from analytics_copilot.warehouse import build_warehouse

    orders_csv = glob.glob("/kaggle/input/**/olist_orders_dataset.csv", recursive=True)[0]
    data_dir = Path(orders_csv).parent
    warehouse = build_warehouse(data_dir, WORK / "olist.duckdb")
    log(f"warehouse built from {data_dir}")

    server = start_vllm()
    try:
        results = asyncio.run(ask_all(warehouse))
    finally:
        server.terminate()

    out = WORK / "results"
    out.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = {
        "run": {
            "kind": "smoke",
            "timestamp_utc": stamp,
            "code_version": (repo / "VERSION").read_text().strip(),
            "provider": "vllm",
            "model": MODEL,
            "quantisation": "none (fp16)",
            "hardware": gpu_name(),
            "vllm": version("vllm"),
            "transformers": version("transformers"),
        },
        "results": results,
    }
    path = out / f"smoke_{stamp}.json"
    path.write_text(json.dumps(payload, indent=2))
    ok = sum(r["status"] == "ok" for r in results)
    log(f"wrote {path}: {ok}/{len(results)} ok")


if __name__ == "__main__":
    main()
