"""The golden eval set: loading, validation and the leakage check."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, model_validator

from analytics_copilot.retrieval import tokens

DEFAULT_GOLDEN_PATH = Path(__file__).resolve().parents[3] / "evals" / "golden.yaml"

# Questions used to tune prompts (smoke test, D22). Golden items must not resemble them.
SMOKE_QUESTIONS = [
    "How many orders were placed in March 2018?",
    "Which 5 states had the highest GMV in 2018?",
    "Show the monthly late delivery rate in 2018.",
    "What was the average review score for the bed_bath_table category?",
    "How many delivered orders were there in 2017?",
    "What was our profit margin last quarter?",
]


class GoldenItem(BaseModel):
    """One eval question with its expected behaviour and gold SQL."""

    id: str
    question: str
    difficulty: Literal["easy", "medium", "hard", "refuse"]
    category: str
    expected_behaviour: Literal["answer", "refuse"] = "answer"
    gold_sql: str | None = None
    ordered: bool = False
    ambiguous: bool = False
    notes: str = ""
    verified: bool = False

    @model_validator(mode="after")
    def _gold_sql_matches_behaviour(self) -> GoldenItem:
        if self.expected_behaviour == "answer" and not self.gold_sql:
            raise ValueError(f"{self.id}: answerable items need gold_sql")
        if self.expected_behaviour == "refuse" and self.gold_sql:
            raise ValueError(f"{self.id}: refusal items must not have gold_sql")
        return self


def load_golden(path: Path = DEFAULT_GOLDEN_PATH) -> list[GoldenItem]:
    """Load and validate the golden set. Ids must be unique."""
    items = [GoldenItem(**raw) for raw in yaml.safe_load(path.read_text())["items"]]
    ids = [item.id for item in items]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"Duplicate golden ids: {sorted(duplicates)}")
    return items


def near_duplicates(
    items: list[GoldenItem], others: list[str], threshold: float = 0.5
) -> list[tuple[str, str, float]]:
    """(item id, other question, word-overlap score) for golden questions too close to
    any of ``others``. Used to keep examples and tuning questions out of the eval."""
    found = []
    for item in items:
        q = tokens(item.question)
        for other in others:
            o = tokens(other)
            score = len(q & o) / len(q | o) if q | o else 0.0
            if score >= threshold:
                found.append((item.id, other, round(score, 2)))
    return found
