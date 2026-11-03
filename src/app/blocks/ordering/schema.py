"""Ordering block: the steps of a procedure in their authored order; grading by code."""

from __future__ import annotations

from typing import Any

MIN_STEPS = 3


def validate(content: dict[str, Any]) -> list[str]:
    """Strict shape: a prompt plus at least three distinct, non-empty steps."""
    errors: list[str] = []
    if not isinstance(content.get("prompt"), str) or not content["prompt"].strip():
        errors.append("prompt must be a non-empty string")
    items = content.get("items")
    if not isinstance(items, list) or len(items) < MIN_STEPS:
        errors.append(f"items must hold at least {MIN_STEPS} steps")
        return errors
    texts = [str(item).strip() for item in items]
    if any(not text for text in texts):
        errors.append("items must all be non-empty strings")
    if len(set(texts)) != len(texts):
        errors.append("items must be distinct")
    return errors
