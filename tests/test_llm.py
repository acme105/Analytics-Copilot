"""Defensive JSON parsing and the single retry on malformed output."""

import pytest

from analytics_copilot.llm import LLMOutputError, Usage, complete_json, extract_json
from analytics_copilot.schemas import ScopeDecision, SQLGeneration

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


def test_assumption_notes_given_as_a_string_become_a_list() -> None:
    parsed = SQLGeneration.model_validate(
        {"sql": "SELECT 1", "assumptions": {"notes": "Default window used.", "filters": "valid"}}
    )
    assert parsed.assumptions.notes == ["Default window used."]
    assert parsed.assumptions.filters == ["valid"]


def test_raw_control_characters_inside_strings_are_tolerated() -> None:
    # The reply below has a real newline and tab inside the JSON string (m019).
    reply = '{"sql": "SELECT 1\n\tFROM t"}'
    assert extract_json(reply) == {"sql": "SELECT 1\n\tFROM t"}
