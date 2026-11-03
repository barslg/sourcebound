"""Grading for ordering blocks, by code with partial credit per position."""

from __future__ import annotations

from typing import Any


def grade(
    content: dict[str, Any],
    answer_key: dict[str, Any],
    submission: dict[str, Any],
) -> dict[str, Any]:
    """Fraction of positions holding the authored step, by code.

    The submission is `order_by_index`: authored indices in the order
    the learner arranged them (services/grading.py maps the served ids).
    The key is `order_by_index` too, so a translated block grades
    exactly like its source.
    """
    _ = content
    expected = [int(i) for i in answer_key.get("order_by_index", []) if str(i).isdigit()]
    given = [int(i) for i in submission.get("order_by_index", []) if str(i).isdigit()]
    if not expected:
        return {"score": 0.0, "feedback": {"habit": ""}}
    hits = sum(1 for want, got in zip(expected, given, strict=False) if want == got)
    return {
        "score": hits / len(expected),
        "feedback": {"habit": "Order the steps as the passage states them, not as they feel."},
    }
