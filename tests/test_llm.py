"""Defensive JSON parsing and the single retry on malformed output."""

import pytest

from analytics_copilot.llm import LLMOutputError, Usage, complete_json, extract_json
from analytics_copilot.schemas import ScopeDecision

from .conftest import FakeLLM


@pytest.mark.parametrize(
    "text",
    [
        '{"in_scope": true, "reason": "ok"}',
        '```json\n{"in_scope": true, "reason": "ok"}\n```',
        'Sure! Here it is: {"in_scope": true, "reason": "ok"} Hope that helps.',
    ],
)
def test_extract_json_handles_fences_and_prose(text: str) -> None:
    assert extract_json(text) == {"in_scope": True, "reason": "ok"}


def test_extract_json_rejects_text_without_an_object() -> None:
    with pytest.raises(ValueError):
        extract_json("I think the answer is yes.")


async def test_retries_once_with_the_error_and_counts_the_failure() -> None:
    llm = FakeLLM(["not json at all", {"in_scope": False, "reason": "needs profit data"}])
    usage = Usage()
    result = await complete_json(
        llm, "summary", [{"role": "user", "content": "q"}], ScopeDecision, usage
    )
    assert result == ScopeDecision(in_scope=False, reason="needs profit data")
    assert usage.parse_failures == 1 and usage.llm_calls == 2
    retry_prompt = llm.calls[1][1][-1]["content"]
    assert "could not be parsed" in retry_prompt


async def test_gives_up_after_the_retry() -> None:
    llm = FakeLLM(["nope", '{"in_scope": "maybe"}'])
    usage = Usage()
    with pytest.raises(LLMOutputError):
        await complete_json(
            llm, "summary", [{"role": "user", "content": "q"}], ScopeDecision, usage
        )
    assert usage.parse_failures == 2
