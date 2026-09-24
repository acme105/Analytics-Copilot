"""Runtime settings, read from environment variables.

Switching LLM provider is a config change only: set LLM_BASE_URL, LLM_API_KEY,
SQL_MODEL and SUMMARY_MODEL (see .env.example).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """Everything the pipeline and API need at runtime."""

    llm_base_url: str = "http://localhost:8000/v1"
    llm_api_key: str = "not-needed"
    sql_model: str = "Qwen/Qwen2.5-Coder-3B-Instruct"
    summary_model: str = "Qwen/Qwen2.5-Coder-3B-Instruct"
    llm_timeout_s: float = 120.0
    warehouse_path: Path = Path("warehouse/olist.duckdb")
    max_rows: int = 200
    query_timeout_s: float = 10.0
    sql_candidates: int = 3  # self-consistency draws on the custom-SQL route

    @classmethod
    def from_env(cls) -> Settings:
        """Build settings from environment variables, falling back to the defaults."""
        env = os.environ
        default = cls()
        return cls(
            llm_base_url=env.get("LLM_BASE_URL", default.llm_base_url),
            llm_api_key=env.get("LLM_API_KEY", default.llm_api_key),
            sql_model=env.get("SQL_MODEL", default.sql_model),
            summary_model=env.get("SUMMARY_MODEL", default.summary_model),
            llm_timeout_s=float(env.get("LLM_TIMEOUT_S", default.llm_timeout_s)),
            warehouse_path=Path(env.get("WAREHOUSE_PATH", str(default.warehouse_path))),
            max_rows=int(env.get("MAX_ROWS", default.max_rows)),
            query_timeout_s=float(env.get("QUERY_TIMEOUT_S", default.query_timeout_s)),
            sql_candidates=int(env.get("SQL_CANDIDATES", default.sql_candidates)),
        )
