# AI Analytics Copilot (Olist)

Ask business questions in plain English about the [Olist marketplace dataset](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce) and get back answers you can check: governed metric definitions, the SQL that ran, and a summary grounded in the returned rows. Every part is evaluated against a golden dataset.

**Status:** Phase 0 (dataset verification) done. See [data/README.md](data/README.md) and [results/phase0_profile.md](results/phase0_profile.md).

## Prerequisites

- [uv](https://docs.astral.sh/uv/) and Python 3.12 (`uv sync` installs it).
- A Kaggle API token: kaggle.com → Settings → API → Create New Token. Save it to `~/.kaggle/access_token` and run `chmod 600` on it.
- For GPU runs on Kaggle: a phone-verified Kaggle account (needed for internet access in notebooks).

## Quick start

```bash
make install   # uv sync
make data      # download the 9 Olist CSVs into data/raw/
make profile   # regenerate results/phase0_profile.md
```

Design decisions and their trade-offs are logged in [DECISIONS.md](DECISIONS.md).

Data licence: CC BY-NC-SA 4.0 (Olist).
