"""API tests with httpx.AsyncClient against the ASGI app, with the pipeline overridden."""

import httpx
import pytest

from analytics_copilot.api import app, get_pipeline

from .conftest import IN_SCOPE, sql_reply


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


async def test_health(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200 and response.json()["status"] == "ok"


async def test_metrics_catalogue_lists_all_governed_metrics(client: httpx.AsyncClient) -> None:
    body = (await client.get("/metrics")).json()
    assert len(body["metrics"]) == 24
    assert {"name", "description", "expression", "required_filters", "date_field", "unit"} <= set(
        body["metrics"][0]
    )
    assert body["time_grains"] == ["day", "week", "month", "quarter", "year"]


async def test_ask_returns_the_pipeline_response(client: httpx.AsyncClient, make_pipeline) -> None:
    pipeline, _ = make_pipeline(
        [IN_SCOPE, sql_reply("SELECT SUM(price) AS gmv FROM fct_order_items"), "GMV was R$ 350."]
    )
    app.dependency_overrides[get_pipeline] = lambda: pipeline
    response = await client.post("/ask", json={"question": "Total GMV?", "mode": "semantic"})
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "ok" and body["rows"] == [[350.0]]
    assert body["chart_spec"]["type"] == "number" and body["request_id"]


@pytest.mark.parametrize(
    "payload", [{"question": "Total GMV?", "mode": "vibes"}, {"question": ""}, {}]
)
async def test_ask_rejects_bad_requests(client: httpx.AsyncClient, payload: dict) -> None:
    assert (await client.post("/ask", json=payload)).status_code == 422
