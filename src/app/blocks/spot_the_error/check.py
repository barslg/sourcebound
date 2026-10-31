"""Grading for spot-the-error blocks, by code with partial credit."""

from __future__ import annotations

from typing import Any


def planted_view(content: dict[str, Any], answer_key: dict[str, Any]) -> dict[str, Any]:
    """The grader's view of one exercise: sentences from content, truth from the key."""
    return {
        "sentences": list(content.get("sentences", [])),
        "planted_sentence": str(answer_key.get("planted_sentence", "")),
        "planted_index": int(answer_key.get("planted_index", -1)),
        "kind": str(answer_key.get("kind", "negation")),
        "passage": str(answer_key.get("passage", "P1")),
        "true_sentence": str(answer_key.get("true_sentence", "")),
        "fully_correct": bool(answer_key.get("fully_correct", False)),
    }


def grade(
    content: dict[str, Any],
    answer_key: dict[str, Any],
    submission: dict[str, Any],
) -> dict[str, Any]:
    """Name the sentence (or none), the kind, the passage, the fix."""
    from app.course.errorspot import grade_spot_error

    return grade_spot_error(planted_view(content, answer_key), submission=submission)
