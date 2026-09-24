"""End-to-end /ask pipeline behaviour with a mocked LLM over the tiny warehouse."""

import httpx
import openai

from .conftest import IN_SCOPE, sql_reply

TOYS_BY_STATE = (
    "SELECT customer_state, SUM(price) AS gmv FROM fct_order_items "
    "WHERE is_valid GROUP BY 1 ORDER BY gmv DESC LIMIT 10"
)


async def test_happy_path_returns_grounded_answer_with_work_shown(make_pipeline) -> None:
    pipeline, llm = make_pipeline(
        [
            IN_SCOPE,
            sql_reply(TOYS_BY_STATE, metrics=["gmv", "not_a_metric"]),
            "RJ had the highest GMV at R$ 250, ahead of SP at R$ 100.",
        ]
    )
    response = await pipeline.ask("GMV by state", mode="semantic")

    assert response.status == "ok"
    assert response.rows == [["RJ", 250.0], ["SP", 100.0]]
    assert response.row_count == 2 and not response.truncated
    assert response.tables_used == ["fct_order_items"]
    assert [m.name for m in response.metrics_used] == ["gmv"]  # unknown names dropped
    assert response.metrics_used[0].expression == "SUM(price)"
    assert response.assumptions.date_field == "purchased_at"
    assert "2017-01-01" in response.assumptions.data_coverage
    assert response.chart_spec.type == "bar"
    assert response.grounding.passed
    assert {
        "scope",
        "retrieve",
        "generate_sql",
        "validate",
        "execute",
        "summarise",
        "total",
    } <= set(response.latency_ms)
    assert response.usage.llm_calls == 3 and response.usage.prompt_tokens == 300
    assert [role for role, _ in llm.calls] == ["summary", "sql", "summary"]


async def test_out_of_scope_is_refused_without_generating_sql(make_pipeline) -> None:
    pipeline, llm = make_pipeline([{"in_scope": False, "reason": "No cost or profit data."}])
    response = await pipeline.ask("What was our profit margin?")
    assert response.status == "refused"
    assert response.answer_summary == "No cost or profit data."
    assert response.sql is None and len(llm.calls) == 1


async def test_invalid_sql_is_repaired_once(make_pipeline) -> None:
    pipeline, llm = make_pipeline(
        [
            IN_SCOPE,
            sql_reply("SELECT SUM(profit) AS p FROM fct_orders"),
            sql_reply(TOYS_BY_STATE),
            "RJ had R$ 250 of GMV.",
        ]
    )
    response = await pipeline.ask("GMV by state", mode="semantic")
    assert response.status == "ok" and response.repaired
    assert "Unknown column profit" in llm.calls[2][1][-1]["content"]


async def test_execution_error_is_repaired_once_then_fails_cleanly(make_pipeline) -> None:
    broken = "SELECT customer_state, gmv FROM fct_order_items LIMIT 5"  # gmv is on fct_orders
    pipeline, _ = make_pipeline([IN_SCOPE, sql_reply(broken), sql_reply(broken)])
    response = await pipeline.ask("GMV by state", mode="semantic")
    assert response.status == "error" and response.repaired
    assert response.error and "gmv" in response.error


async def test_ungrounded_summary_is_regenerated_then_replaced_by_a_template(make_pipeline) -> None:
    pipeline, llm = make_pipeline(
        [
            IN_SCOPE,
            sql_reply(TOYS_BY_STATE),
            "RJ had R$ 999 of GMV.",
            "RJ grew 42% year on year.",
        ]
    )
    response = await pipeline.ask("GMV by state", mode="semantic")
    assert response.status == "ok"
    assert response.grounding.fallback_used and response.grounding.ungrounded == ["42%"]
    assert response.answer_summary == (
        "The query returned 2 rows. First row: customer_state = RJ, gmv = 250.00."
    )
    assert "R$ 999" in llm.calls[3][1][-1]["content"]


async def test_malformed_json_is_retried_and_counted(make_pipeline) -> None:
    pipeline, _ = make_pipeline(
        [IN_SCOPE, "```sql\nSELECT 1\n```", sql_reply(TOYS_BY_STATE), "RJ had R$ 250 of GMV."]
    )
    response = await pipeline.ask("GMV by state", mode="semantic")
    assert response.status == "ok" and response.usage.parse_failures == 1


async def test_modes_see_different_context(make_pipeline) -> None:
    replies = [IN_SCOPE, sql_reply("SELECT COUNT(*) AS n FROM stg_orders"), "There are 3 orders."]
    pipeline, llm = make_pipeline(replies)
    await pipeline.ask("How many orders?", mode="raw_schema")
    raw_prompt = llm.calls[1][1][0]["content"]
    assert "TABLE stg_orders" in raw_prompt and "Governed metrics" not in raw_prompt

    pipeline, llm = make_pipeline([IN_SCOPE, sql_reply(TOYS_BY_STATE), "RJ had R$ 250 of GMV."])
    await pipeline.ask("What was total revenue?", mode="semantic_rag")
    rag_prompt = llm.calls[1][1][0]["content"]
    assert "TABLE fct_orders" in rag_prompt and "- gmv (GMV" in rag_prompt
    assert "- avg_review_score" not in rag_prompt  # only retrieved metrics are shown
    assert "Example questions with correct SQL" in rag_prompt


async def test_raw_mode_cannot_query_marts(make_pipeline) -> None:
    pipeline, _ = make_pipeline([IN_SCOPE, sql_reply(TOYS_BY_STATE), sql_reply(TOYS_BY_STATE)])
    response = await pipeline.ask("GMV by state", mode="raw_schema")
    assert response.status == "error" and "Unknown table fct_order_items" in response.error


async def test_unreachable_llm_provider_is_a_clean_error(make_pipeline) -> None:
    pipeline, llm = make_pipeline([])

    async def unreachable(role, messages):
        raise openai.APIConnectionError(request=httpx.Request("POST", "http://llm"))

    llm.complete = unreachable
    response = await pipeline.ask("GMV by state")
    assert response.status == "error"
    assert response.error.startswith("LLM provider error (APIConnectionError)")
    assert response.answer_summary == "The language model is unavailable right now."
