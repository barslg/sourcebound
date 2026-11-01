"""Grading for reading blocks: acknowledgement only, by code."""

from __future__ import annotations

from typing import Any


def grade(
    content: dict[str, Any],
    answer_key: dict[str, Any],
    submission: dict[str, Any],
) -> dict[str, Any]:
    """Reading has no wrong answer; any submission acknowledges."""
    _ = (content, answer_key)
    acknowledged = bool(submission.get("acknowledged", True))
    score = 1.0 if acknowledged else 0.0
    return {
        "score": score,
        "feedback": {"habit": "Read with the source beside you."},
    }
