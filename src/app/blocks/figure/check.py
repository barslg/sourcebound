"""Grading for figure blocks: acknowledgement only, by code."""

from __future__ import annotations

from typing import Any


def grade(
    content: dict[str, Any],
    answer_key: dict[str, Any],
    submission: dict[str, Any],
) -> dict[str, Any]:
    """Figures are read, not answered; any view acknowledges."""
    _ = (content, answer_key)
    acknowledged = bool(submission.get("acknowledged", True))
    return {
        "score": 1.0 if acknowledged else 0.0,
        "feedback": {
            "wrong": "",
            "true": "",
            "passage": "",
            "habit": "Check the numbers against the source.",
        },
    }
