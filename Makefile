DATASET := olistbr/brazilian-ecommerce
KAGGLE_USER := acme105
CODE_DATASET := $(KAGGLE_USER)/olist-copilot-code
SMOKE_KERNEL := $(KAGGLE_USER)/olist-copilot-smoke
BUILD := build/kaggle-code
RAW_DIR := data/raw

.PHONY: install data profile warehouse serve lint test kaggle-code kaggle-smoke kaggle-status kaggle-results

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

## Upload the committed code (HEAD) to the private Kaggle dataset the notebooks attach.
kaggle-code:
	@git diff --quiet HEAD -- src semantic || echo "WARNING: uncommitted changes are NOT uploaded (git archive HEAD)"
	rm -rf $(BUILD) && mkdir -p $(BUILD)/stage
	git archive HEAD src semantic pyproject.toml | tar -x -C $(BUILD)/stage
	git rev-parse --short HEAD > $(BUILD)/stage/VERSION
	COPYFILE_DISABLE=1 tar -czf $(BUILD)/copilot_code.tar.gz -C $(BUILD)/stage .  # no macOS ._ files
	rm -rf $(BUILD)/stage
	printf '{"title": "olist-copilot-code", "id": "$(CODE_DATASET)", "licenses": [{"name": "unknown"}]}' > $(BUILD)/dataset-metadata.json
	if uv run kaggle datasets status $(CODE_DATASET) >/dev/null 2>&1; then \
	    uv run kaggle datasets version -p $(BUILD) -m "code $$(git rev-parse --short HEAD)"; \
	else \
	    uv run kaggle datasets create -p $(BUILD); \
	fi

## Run the smoke test on a Kaggle T4, then poll with `make kaggle-status`.
kaggle-smoke:
	uv run kaggle kernels push -p kaggle/smoke --accelerator NvidiaTeslaT4

kaggle-status:
	uv run kaggle kernels status $(SMOKE_KERNEL)

## Download the smoke run's outputs into results/kaggle/.
kaggle-results:
	mkdir -p results/kaggle
	uv run kaggle kernels output $(SMOKE_KERNEL) -p results/kaggle
