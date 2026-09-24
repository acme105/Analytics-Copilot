# AI Analytics Copilot (Olist)

Ask business questions in plain English about the [Olist marketplace dataset](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce) and get back answers you can check: governed metric definitions, the SQL that ran, and a summary grounded in the returned rows. Every part is evaluated against a golden dataset.

**Status:** Phase 2 (insight API) done. 18 governed metrics are defined in [semantic/metrics.yaml](semantic/metrics.yaml), and the `/ask` pipeline is in [src/analytics_copilot/pipeline.py](src/analytics_copilot/pipeline.py). Data notes are in [data/README.md](data/README.md).

## Prerequisites

- [uv](https://docs.astral.sh/uv/) and Python 3.12 (`uv sync` installs it).
- A Kaggle API token: kaggle.com → Settings → API → Create New Token. Save it to `~/.kaggle/access_token` and run `chmod 600` on it.
- For GPU runs on Kaggle: a phone-verified Kaggle account (needed for internet access in notebooks).

## Quick start

```bash
make install   # uv sync
make data      # download the 9 Olist CSVs into data/raw/
make profile   # regenerate results/phase0_profile.md
make warehouse # build warehouse/olist.duckdb
make test      # unit tests + metric checks against the warehouse
make serve     # insight API on http://localhost:8080 (needs an LLM endpoint, see .env.example)
```

## API

| Endpoint | What it does |
|---|---|
| `POST /ask` `{question, mode}` | Answers with SQL, tables and metric definitions used, assumptions, chart spec, rows, a grounded summary, per-stage latency and token usage. `mode` is `raw_schema`, `semantic` or `semantic_rag` (default). |
| `GET /metrics` | The semantic layer catalogue. |
| `GET /health` | Liveness, whether the warehouse was found, and the configured LLM endpoint. |

Design decisions and their trade-offs are logged in [DECISIONS.md](DECISIONS.md).

Data licence: CC BY-NC-SA 4.0 (Olist).
