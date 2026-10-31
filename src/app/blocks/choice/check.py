"""Grading for choice blocks, by code with no model call."""

from __future__ import annotations

from typing import Any


def grade(
    content: dict[str, Any],
    answer_key: dict[str, Any],
    submission: dict[str, Any],
) -> dict[str, Any]:
    """Single/multi choice: exact index match scores full marks."""
    _ = content
    correct = answer_key.get("correct")
    picked = submission.get("picked")
    if isinstance(correct, list):
        chosen = picked if isinstance(picked, list) else [] if picked is None else [picked]
        score = 1.0 if sorted(chosen) == sorted(correct) else 0.0
    else:
        score = 1.0 if picked == correct else 0.0
    return {
        "score": score,
        "feedback": {
            "wrong": "" if score == 1.0 else str(picked),
            "true": str(correct),
            "passage": str(answer_key.get("passage", "P1")),
            "habit": "Answer from the passage, not from memory.",
        },
    }
