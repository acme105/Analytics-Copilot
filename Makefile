DATASET := olistbr/brazilian-ecommerce
RAW_DIR := data/raw

.PHONY: install data profile warehouse serve lint test

install:
	uv sync

## Download the Olist CSVs into data/raw (needs a Kaggle API token).
data: $(RAW_DIR)/.downloaded

$(RAW_DIR)/.downloaded:
	@if [ ! -f "$$HOME/.kaggle/access_token" ] && [ ! -f "$$HOME/.kaggle/kaggle.json" ] \
	    && [ -z "$$KAGGLE_API_TOKEN" ] && [ -z "$$KAGGLE_KEY" ]; then \
	    echo "No Kaggle credentials found."; \
	    echo "Create a token at https://www.kaggle.com/settings -> API -> Create New Token,"; \
	    echo "then save it to ~/.kaggle/access_token (chmod 600) or set KAGGLE_API_TOKEN."; \
	    exit 1; \
	fi
	uv run kaggle datasets download $(DATASET) -p $(RAW_DIR) --unzip
	touch $@

## Profile the raw data and write results/phase0_profile.md.
profile: data
	uv run python -m analytics_copilot.profiling --data-dir $(RAW_DIR) --out results/phase0_profile.md

## Build warehouse/olist.duckdb (raw -> staging views -> mart tables).
warehouse: data
	uv run python -m analytics_copilot.warehouse --data-dir $(RAW_DIR)

## Run the insight API on http://localhost:8080 (LLM settings from .env if present).
serve:
	uv run $(if $(wildcard .env),--env-file .env,) uvicorn analytics_copilot.api:app --port 8080 --reload

lint:
	uv run ruff check .
	uv run ruff format --check .

test:
	uv run pytest
