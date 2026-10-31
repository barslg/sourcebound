"""Spot the error: exactly one planted sentence; grading by code."""

from __future__ import annotations

from typing import Any


def validate(content: dict[str, Any]) -> list[str]:
    """Strict shape: at least two sentences, one planted index, known kind."""
    from app.course.errorspot import MUTATION_KINDS

    errors: list[str] = []
    sentences = content.get("sentences")
    if not isinstance(sentences, list) or len(sentences) < 2:
        errors.append("sentences must hold at least two strings")
    elif not all(isinstance(item, str) and item.strip() for item in sentences):
        errors.append("sentences must all be non-empty strings")
    planted = content.get("planted_index")
    in_range = (
        isinstance(sentences, list) and isinstance(planted, int) and 0 <= planted < len(sentences)
    )
    if not in_range:
        errors.append("planted_index must point at one sentence")
    if content.get("kind") not in MUTATION_KINDS:
        errors.append(f"kind must be one of {sorted(MUTATION_KINDS)}")
    return errors
