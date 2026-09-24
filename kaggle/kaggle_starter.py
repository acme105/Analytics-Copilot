# Kaggle starter: Phase 0 profiling + vLLM smoke test.
# Paste each "CELL" block into its own Kaggle notebook cell, in order.
#
# Notebook settings (right sidebar) before running:
#   - Accelerator: GPU T4 x2
#   - Internet: On (needs a phone-verified Kaggle account)
#   - Input: + Add Input -> search "brazilian-ecommerce" -> add the olistbr dataset


# ===== CELL 1: check the GPU and find the dataset =====
import glob
import os
import subprocess

print(subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True).stdout)

hits = glob.glob("/kaggle/input/**/olist_orders_dataset.csv", recursive=True)
assert hits, "Dataset not attached: + Add Input -> 'brazilian-ecommerce' (olistbr)"
DATA_DIR = os.path.dirname(hits[0])
print("DATA_DIR =", DATA_DIR)
print(sorted(os.listdir(DATA_DIR)))


# ===== CELL 2: load every CSV into DuckDB and count rows =====
%pip install -q duckdb

import duckdb

con = duckdb.connect()
for path in sorted(glob.glob(f"{DATA_DIR}/*.csv")):
    table = os.path.basename(path).removesuffix(".csv")
    con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM read_csv_auto('{path}')")
    rows = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    print(f"{table:<45} {rows:>10,}")


# ===== CELL 3: date range and monthly order volume =====
print(con.sql("""
    SELECT MIN(order_purchase_timestamp) AS first_order,
           MAX(order_purchase_timestamp) AS last_order
    FROM olist_orders_dataset
"""))

monthly = con.sql("""
    SELECT date_trunc('month', order_purchase_timestamp) AS month, COUNT(*) AS orders
    FROM olist_orders_dataset
    GROUP BY 1 ORDER BY 1
""").df()
monthly  # sparse edge months show up here


# ===== CELL 4: null rates per column =====
import pandas as pd

tables = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
nulls = pd.concat(
    con.sql(f"SUMMARIZE {t}").df()[["column_name", "column_type", "null_percentage"]].assign(table=t)
    for t in tables
)
nulls[nulls["null_percentage"].astype(float) > 0].sort_values("null_percentage", ascending=False)


# ===== CELL 5: duplicate keys and orphaned foreign keys =====
checks = {
    "dup orders.order_id": "SELECT COUNT(*) - COUNT(DISTINCT order_id) FROM olist_orders_dataset",
    "dup customers.customer_id": "SELECT COUNT(*) - COUNT(DISTINCT customer_id) FROM olist_customers_dataset",
    "dup products.product_id": "SELECT COUNT(*) - COUNT(DISTINCT product_id) FROM olist_products_dataset",
    "dup sellers.seller_id": "SELECT COUNT(*) - COUNT(DISTINCT seller_id) FROM olist_sellers_dataset",
    "dup reviews.review_id": "SELECT COUNT(*) - COUNT(DISTINCT review_id) FROM olist_order_reviews_dataset",
    "orphan items->orders": """SELECT COUNT(*) FROM olist_order_items_dataset i
        WHERE i.order_id NOT IN (SELECT order_id FROM olist_orders_dataset)""",
    "orphan items->products": """SELECT COUNT(*) FROM olist_order_items_dataset i
        WHERE i.product_id NOT IN (SELECT product_id FROM olist_products_dataset)""",
    "orphan orders->customers": """SELECT COUNT(*) FROM olist_orders_dataset o
        WHERE o.customer_id NOT IN (SELECT customer_id FROM olist_customers_dataset)""",
    "orders with no items": """SELECT COUNT(*) FROM olist_orders_dataset o
        WHERE o.order_id NOT IN (SELECT order_id FROM olist_order_items_dataset)""",
    "categories without translation": """SELECT COUNT(DISTINCT product_category_name) FROM olist_products_dataset
        WHERE product_category_name NOT IN
              (SELECT product_category_name FROM product_category_name_translation)""",
}
for name, sql in checks.items():
    print(f"{name:<35} {con.execute(sql).fetchone()[0]:>8,}")


# ===== CELL 6: install vLLM =====
# vLLM reinstalls torch, so this takes several minutes. The version is pinned
# because newer releases may not support T4 (Turing) GPUs. --no-cache-dir and
# retries avoid "DO NOT MATCH THE HASHES" errors from a corrupted download;
# if that error appears, just run this cell again.
# transformers is pinned because 4.54+ also registers "aimv2", which crashes
# vLLM 0.9.2 at startup ("'aimv2' is already used by a Transformers config").
%pip install -q --no-cache-dir --retries 10 --timeout 120 "vllm==0.9.2" "transformers==4.53.2" openai

import sys

check = subprocess.run(
    [sys.executable, "-c",
     "import vllm, transformers, vllm.entrypoints.openai.api_server; "
     "print('vllm', vllm.__version__, '| transformers', transformers.__version__)"],
    capture_output=True, text=True,
)
print(check.stdout or check.stderr[-2000:])
assert check.returncode == 0, "vLLM is not installed; rerun this cell before cell 7"


# ===== CELL 7: start the model server in the background =====
import time
import requests

os.environ["VLLM_USE_V1"] = "0"  # the V0 engine runs on T4

# Default: 3B in fp16 on one GPU (~6 GB of weights, no quantization needed).
MODEL = "Qwen/Qwen2.5-Coder-3B-Instruct"
SERVE_ARGS = []
# Alternative: 7B AWQ 4-bit on one GPU. Uncomment to use instead.
# MODEL = "Qwen/Qwen2.5-Coder-7B-Instruct-AWQ"
# SERVE_ARGS = ["--quantization", "awq"]

log = open("/kaggle/working/vllm.log", "w")
server = subprocess.Popen(
    [sys.executable, "-m", "vllm.entrypoints.openai.api_server",
     "--model", MODEL, "--dtype", "half", "--max-model-len", "8192",
     "--gpu-memory-utilization", "0.90", "--port", "8000", *SERVE_ARGS],
    stdout=log, stderr=subprocess.STDOUT,
)

deadline = time.time() + 20 * 60
while time.time() < deadline:
    if server.poll() is not None:
        break
    try:
        if requests.get("http://localhost:8000/health", timeout=2).status_code == 200:
            print("vLLM is up:", MODEL)
            break
    except requests.ConnectionError:
        pass
    time.sleep(10)
else:
    server.poll()

if server.returncode is not None or time.time() >= deadline:
    print(open("/kaggle/working/vllm.log").read()[-4000:])
    raise RuntimeError("vLLM did not start; see the log above")


# ===== CELL 8: text-to-SQL smoke test against the real data =====
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="not-needed")

schema = "\n".join(
    f"{t}({', '.join(c[0] + ' ' + c[1] for c in con.execute(f'DESCRIBE {t}').fetchall())})"
    for t in tables
)
question = "What was the monthly number of delivered orders in 2017?"

reply = client.chat.completions.create(
    model=MODEL,
    temperature=0,
    messages=[
        {"role": "system", "content": "You write DuckDB SQL. Reply with one SELECT statement only, "
                                      "no explanation and no markdown fences.\n\nTables:\n" + schema},
        {"role": "user", "content": question},
    ],
)
sql = reply.choices[0].message.content.strip().removeprefix("```sql").removesuffix("```").strip()
print(sql)
print(reply.usage)
con.sql(sql).df()
