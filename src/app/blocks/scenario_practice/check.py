"""Grading for scenario practice, by code with partial credit."""

from __future__ import annotations

from typing import Any


def grade(
    content: dict[str, Any],
    answer_key: dict[str, Any],
    submission: dict[str, Any],
) -> dict[str, Any]:
    """Fraction of items with the right tier and supporting passage."""
    _ = content
    expected = answer_key.get("labels", {})
    given = submission.get("labels", {})
    passages = submission.get("passages", {})
    expected_passages = answer_key.get("passages", {})
    if not expected:
        return {"score": 0.0, "feedback": {"wrong": "", "true": "", "passage": "P1", "habit": ""}}
    hits = sum(
        1
        for item, tier in expected.items()
        if given.get(item) == tier and passages.get(item) == expected_passages.get(item)
    )
    return {
        "score": hits / len(expected),
        "feedback": {
            "wrong": str(given),
            "true": str(expected),
            "passage": str(answer_key.get("passage", "P1")),
            "habit": "Sort each use by its article citation.",
        },
    }
