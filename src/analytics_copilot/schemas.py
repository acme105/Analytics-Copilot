"""Request and response models for the insight API, and the model-output schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from analytics_copilot.charts import ChartSpec

Mode = Literal["raw_schema", "semantic", "semantic_rag"]
Status = Literal["ok", "refused", "error"]


# --- What the models must return (parsed by llm.complete_json) ---


class ScopeDecision(BaseModel):
    """Scope-check output."""

    in_scope: bool
    reason: str


class GeneratedAssumptions(BaseModel):
    """Assumptions the SQL model states about its query."""

    date_field: str | None = None
    filters: list[str] = []
    notes: list[str] = []


class SQLGeneration(BaseModel):
    """SQL-generation output."""

    sql: str
    metrics_used: list[str] = []
    assumptions: GeneratedAssumptions = GeneratedAssumptions()
    chart_hint: str | None = None


# --- API ---


class AskRequest(BaseModel):
    """Body of POST /ask."""

    question: str = Field(min_length=3, max_length=500)
    mode: Mode = "semantic_rag"


class MetricDefinition(BaseModel):
    """A governed metric definition, as stored in the semantic layer."""

    name: str
    label: str
    description: str
    view: str
    expression: str
    required_filters: list[str]
    date_field: str
    unit: str


class Assumptions(BaseModel):
    """What the answer assumed, so a reader can judge it."""

    date_field: str | None = None
    filters: list[str] = []
    data_coverage: str
    notes: list[str] = []


class Grounding(BaseModel):
    """Result of checking the summary's numbers against the rows."""

    passed: bool
    ungrounded: list[str] = []
    fallback_used: bool = False


class UsageReport(BaseModel):
    """LLM usage for one request."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_calls: int = 0
    parse_failures: int = 0


class AskResponse(BaseModel):
    """Body returned by POST /ask."""

    request_id: str
    status: Status
    mode: Mode
    question: str
    answer_summary: str
    sql: str | None = None
    tables_used: list[str] = []
    metrics_used: list[MetricDefinition] = []
    assumptions: Assumptions | None = None
    chart_spec: ChartSpec | None = None
    columns: list[str] = []
    rows: list[list[Any]] = []
    row_count: int = 0
    truncated: bool = False
    repaired: bool = False
    grounding: Grounding | None = None
    latency_ms: dict[str, float] = {}
    usage: UsageReport = UsageReport()
    error: str | None = None
