"""Check that every number in a summary comes from, or follows directly from, the rows.

"Directly derivable" means: a cell value; a ratio shown as a percentage; a column
total; the difference, ratio or percentage change between two values in the same
column; the row count or a rank position; a date part; or a number in the question.
Matching allows for the rounding the summary shows (e.g. 0.06791 -> "6.8%").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from itertools import permutations

# e.g. 1,234.5 | R$ 13.4M | 6.8% | 12k | 2.1 million
_NUMBER = re.compile(
    r"(?<![\w.])(?:R\$\s?)?-?\d[\d,]*(?:\.\d+)?"
    r"(?:\s?(?:%|percent\b|k\b|K\b|m\b|M\b|bn\b|million\b|thousand\b|billion\b))?",
)
_SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "bn": 1e9, "billion": 1e9}
_MAX_PAIRWISE_VALUES = 24  # beyond this, n^2 derived values make chance matches likely


@dataclass(frozen=True)
class NumberMention:
    """A number as written in the summary."""

    text: str
    value: float
    is_percent: bool
    tolerance: float


@dataclass
class GroundingResult:
    """Outcome of the grounding check."""

    passed: bool
    checked: int = 0
    ungrounded: list[str] = field(default_factory=list)


def extract_numbers(text: str) -> list[NumberMention]:
    """Find numbers in ``text`` with their value and the rounding tolerance they imply."""
    mentions = []
    for match in _NUMBER.finditer(text):
        raw = match.group(0)
        body = raw.replace("R$", "").strip()
        suffix = re.search(r"(%|percent|k|K|m|M|bn|million|thousand|billion)$", body)
        unit = suffix.group(1) if suffix else ""
        digits = body[: suffix.start()].strip() if suffix else body
        digits = digits.replace(",", "")
        decimals = len(digits.split(".")[1]) if "." in digits else 0
        scale = _SCALE.get(unit.lower(), 1.0) if unit not in ("%", "percent") else 1.0
        value = float(digits) * scale
        # Half a unit of the last shown digit, plus 0.5% for loose rounding.
        tolerance = 0.5 * 10 ** (-decimals) * scale + abs(value) * 0.005
        mentions.append(NumberMention(raw.strip(), value, unit in ("%", "percent"), tolerance))
    return mentions


def _candidates(rows: list[tuple], question: str) -> list[float]:
    values: list[float] = [float(len(rows))]
    values += [float(i) for i in range(1, len(rows) + 1)]  # rank positions ("top 3")
    columns: list[list[float]] = [[] for _ in rows[0]] if rows else []
    for row in rows:
        for i, cell in enumerate(row):
            if isinstance(cell, bool):
                continue
            if isinstance(cell, int | float):
                values.append(float(cell))
                columns[i].append(float(cell))
            elif isinstance(cell, date | datetime):
                values += [float(cell.year), float(cell.month), float(cell.day)]
    for column in columns:
        if not column:
            continue
        values.append(sum(column))
        if len(column) <= _MAX_PAIRWISE_VALUES:
            for a, b in permutations(column, 2):
                values.append(a - b)
                if b:
                    values += [a / b, (a - b) / b]
    values += [m.value for m in extract_numbers(question)]
    return values


def check_grounding(summary: str, rows: list[tuple], question: str = "") -> GroundingResult:
    """Return whether every number in ``summary`` is grounded in ``rows``.

    Percentages may match a stored ratio (0.068 -> 6.8%) or a stored percentage.
    Signs are ignored, since "fell by 120" states a difference of -120.
    """
    candidates = _candidates(rows, question)
    mentions = extract_numbers(summary)
    ungrounded = []
    for mention in mentions:
        targets = [abs(mention.value)]
        if mention.is_percent:
            targets.append(abs(mention.value) / 100)
        tolerances = [mention.tolerance, mention.tolerance / 100]
        grounded = any(
            abs(abs(c) - t) <= tol
            for c in candidates
            for t, tol in zip(targets, tolerances, strict=False)
        )
        if not grounded:
            ungrounded.append(mention.text)
    return GroundingResult(passed=not ungrounded, checked=len(mentions), ungrounded=ungrounded)
