"""FastAPI wrapper around the pipeline. Run with ``make serve``."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Any

from fastapi import Depends, FastAPI

from analytics_copilot.config import Settings
from analytics_copilot.observability import configure_logging
from analytics_copilot.pipeline import AskPipeline
from analytics_copilot.schemas import AskRequest, AskResponse
from analytics_copilot.semantic import SemanticLayer, load_semantic_layer

configure_logging()
app = FastAPI(title="Olist Analytics Copilot", version="0.1.0")


@lru_cache
def get_settings() -> Settings:
    """Settings, read once from the environment."""
    return Settings.from_env()


@lru_cache
def get_pipeline() -> AskPipeline:
    """The shared pipeline, built on first use."""
    return AskPipeline.from_settings(get_settings())


@lru_cache
def get_layer() -> SemanticLayer:
    """The semantic layer, loaded once."""
    return load_semantic_layer()


@app.post("/ask")
async def ask(
    request: AskRequest, pipeline: Annotated[AskPipeline, Depends(get_pipeline)]
) -> AskResponse:
    """Answer a question in plain English with checked SQL, a chart spec and a summary."""
    return await pipeline.ask(request.question, request.mode)


@app.get("/metrics")
def metrics(layer: Annotated[SemanticLayer, Depends(get_layer)]) -> dict[str, Any]:
    """The semantic layer catalogue: metrics, dimensions, time grains and default window."""
    return {
        "default_window": layer.default_window.model_dump(),
        "time_grains": layer.time_grains,
        "dimensions": {name: d.model_dump() for name, d in layer.dimensions.items()},
        "metrics": [{"name": name, **m.model_dump()} for name, m in layer.metrics.items()],
    }


@app.get("/health")
def health(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, Any]:
    """Liveness plus the two things most likely to be misconfigured."""
    return {
        "status": "ok",
        "warehouse_found": settings.warehouse_path.exists(),
        "llm_base_url": settings.llm_base_url,
    }
