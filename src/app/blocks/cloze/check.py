"""Grading for cloze blocks, by code with partial credit."""

from __future__ import annotations

from typing import Any


def _fold(value: str) -> str:
    return " ".join(value.strip().lower().split())


def grade(
    content: dict[str, Any],
    answer_key: dict[str, Any],
    submission: dict[str, Any],
) -> dict[str, Any]:
    """Fraction of blanks filled exactly (case-insensitive), by code."""
    _ = content
    expected = [str(item) for item in answer_key.get("blanks", [])]
    given = [str(item) for item in submission.get("blanks", [])]
    if not expected:
        return {"score": 0.0, "feedback": {"wrong": "", "true": "", "passage": "P1", "habit": ""}}
    hits = sum(1 for want, got in zip(expected, given, strict=False) if _fold(want) == _fold(got))
    return {
        "score": hits / len(expected),
        "feedback": {
            "wrong": str(given),
            "true": str(expected),
            "passage": str(answer_key.get("passage", "P1")),
            "habit": "Fill each gap from the passage wording.",
        },
    }
