"""Matching block: left items meet their right mates; grading by code."""

from __future__ import annotations

from typing import Any


def validate(content: dict[str, Any]) -> list[str]:
    """Strict shape: at least one left/right pair."""
    errors: list[str] = []
    pairs = content.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        errors.append("pairs must be a non-empty list")
        return errors
    for index, pair in enumerate(pairs):
        if not isinstance(pair, dict) or not pair.get("left") or not pair.get("right"):
            errors.append(f"pairs[{index}]: left and right are required")
    return errors
