"""LLM access behind one small interface with two roles: ``sql`` and ``summary``.

Every provider (vLLM on Kaggle, hosted APIs, Anthropic's compatibility endpoint)
speaks the OpenAI chat protocol, so one client covers them all (DECISIONS D1).
Open models sometimes return malformed JSON, so ``complete_json`` parses
defensively, retries once with the parse error, and counts failures.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal, Protocol, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from analytics_copilot.config import Settings

Role = Literal["sql", "summary"]
Message = dict[str, str]
T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class Completion:
    """One model reply and its token counts."""

    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class Usage:
    """Token and call counts accumulated over one request."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_calls: int = 0
    parse_failures: int = 0

    def record(self, completion: Completion) -> None:
        """Add one completion's tokens to the totals."""
        self.prompt_tokens += completion.prompt_tokens
        self.completion_tokens += completion.completion_tokens
        self.llm_calls += 1


class LLMClient(Protocol):
    """Anything that can complete a chat for a role. Tests pass a fake.

    ``sample`` numbers repeated draws of the same prompt (self-consistency); it has no
    effect on the model and only keeps cached draws apart.
    """

    async def complete(
        self, role: Role, messages: list[Message], temperature: float = 0.0, sample: int = 0
    ) -> Completion: ...


class LLMOutputError(RuntimeError):
    """The model did not produce usable structured output after a retry."""


class OpenAICompatibleClient:
    """LLM client for any OpenAI-compatible endpoint, with one model per role."""

    def __init__(self, settings: Settings) -> None:
        self._client = AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            timeout=settings.llm_timeout_s,
        )
        self._models: dict[Role, str] = {
            "sql": settings.sql_model,
            "summary": settings.summary_model,
        }

    async def complete(
        self, role: Role, messages: list[Message], temperature: float = 0.0, sample: int = 0
    ) -> Completion:
        """Complete a chat with the model configured for ``role``."""
        response = await self._client.chat.completions.create(
            model=self._models[role], messages=messages, temperature=temperature
        )
        usage = response.usage
        return Completion(
            text=response.choices[0].message.content or "",
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
        )


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> dict:
    """Parse the JSON object in a model reply.

    Accepts a bare object, an object inside a markdown fence, or an object with
    prose around it (takes the outermost braces).

    Raises:
        ValueError: no JSON object could be parsed.
    """
    fenced = _FENCE.search(text)
    candidate = fenced.group(1) if fenced else text
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in the reply")
    # strict=False: small models sometimes put raw newlines or tabs inside JSON strings.
    parsed = json.loads(candidate[start : end + 1], strict=False)
    if not isinstance(parsed, dict):
        raise ValueError("reply JSON is not an object")
    return parsed


async def complete_json(
    llm: LLMClient,
    role: Role,
    messages: list[Message],
    schema: type[T],
    usage: Usage,
    temperature: float = 0.0,
    sample: int = 0,
) -> T:
    """Ask for JSON matching ``schema``; on a parse or validation error, retry once.

    Every failed parse is counted in ``usage.parse_failures`` (an eval metric).

    Raises:
        LLMOutputError: the retry also failed.
    """
    for attempt in range(2):
        completion = await llm.complete(role, messages, temperature=temperature, sample=sample)
        usage.record(completion)
        try:
            return schema.model_validate(extract_json(completion.text))
        except (ValueError, ValidationError) as error:
            usage.parse_failures += 1
            if attempt == 1:
                raise LLMOutputError(f"invalid JSON from the {role} model: {error}") from error
            messages = [
                *messages,
                {"role": "assistant", "content": completion.text},
                {
                    "role": "user",
                    "content": f"Your reply could not be parsed: {error}. "
                    "Reply again with only the JSON object in the required format.",
                },
            ]
    raise AssertionError("unreachable")
