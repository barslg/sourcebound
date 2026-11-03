"""Grading for diagram blocks: acknowledgement only, by code."""

from __future__ import annotations

from typing import Any


def grade(
    content: dict[str, Any],
    answer_key: dict[str, Any],
    submission: dict[str, Any],
) -> dict[str, Any]:
    """Diagrams are read, not answered; any view acknowledges."""
    _ = (content, answer_key)
    acknowledged = bool(submission.get("acknowledged", True))
    return {
        "score": 1.0 if acknowledged else 0.0,
        "feedback": {"habit": "Follow the arrows against the passage's order."},
    }
