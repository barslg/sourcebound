"""Grading for matching blocks, by code with partial credit."""

from __future__ import annotations

from typing import Any


def grade(
    content: dict[str, Any],
    answer_key: dict[str, Any],
    submission: dict[str, Any],
) -> dict[str, Any]:
    """Fraction of correctly matched left items, by code."""
    _ = content
    expected = answer_key.get("mapping", {})
    given = submission.get("mapping", {})
    if not expected:
        return {"score": 0.0, "feedback": {"wrong": "", "true": "", "passage": "P1", "habit": ""}}
    hits = sum(1 for left, right in expected.items() if given.get(left) == right)
    score = hits / len(expected)
    return {
        "score": float(score),
        "feedback": {
            "wrong": str(given),
            "true": str(expected),
            "passage": str(answer_key.get("passage", "P1")),
            "habit": "Match each item to its passage, not by word shape.",
        },
    }
