"""Retrieve the metric definitions and example queries most relevant to a question.

Lexical overlap on names, synonyms and descriptions, not embeddings: with 18
metrics and a few dozen examples it's exact enough, needs no extra model on the
GPU, and every retrieval decision can be explained by pointing at matching words.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from analytics_copilot.semantic import SemanticLayer

DEFAULT_EXAMPLES_PATH = Path(__file__).resolve().parents[2] / "semantic" / "examples.yaml"

_STOPWORDS = frozenset(
    "a an and are as at be by did do does for from how in is it of on or per the to was "
    "were what when which who with what's show me give list our we".split()
)


@dataclass(frozen=True)
class Example:
    """A hand-written question and its correct SQL against the marts."""

    question: str
    sql: str


def tokens(text: str) -> set[str]:
    """Lower-case word tokens without stopwords, with a trailing plural 's' removed."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words} - _STOPWORDS


def load_examples(path: Path = DEFAULT_EXAMPLES_PATH) -> list[Example]:
    """Load the example query library."""
    return [Example(**item) for item in yaml.safe_load(path.read_text())["examples"]]


def top_metrics(layer: SemanticLayer, question: str, k: int = 4) -> list[str]:
    """Names of the ``k`` metrics that best match ``question``, best first.

    A word matching a metric's name, label or synonyms scores 3; a word matching
    its description scores 1. Metrics with no match are never returned.
    """
    q = tokens(question)
    scored = []
    for name, metric in layer.metrics.items():
        strong = tokens(" ".join([name.replace("_", " "), metric.label, *metric.synonyms]))
        score = 3 * len(q & strong) + len(q & tokens(metric.description))
        if score:
            scored.append((score, name))
    return [name for _, name in sorted(scored, key=lambda s: (-s[0], s[1]))[:k]]


def top_examples(examples: list[Example], question: str, k: int = 3) -> list[Example]:
    """The ``k`` examples whose questions share the most words with ``question``."""
    q = tokens(question)

    def similarity(example: Example) -> float:
        e = tokens(example.question)
        return len(q & e) / len(q | e) if q | e else 0.0

    ranked = sorted(examples, key=similarity, reverse=True)
    return [e for e in ranked[:k] if similarity(e) > 0]
