"""Choice block: stem with options; grading by code against the key."""

from __future__ import annotations

from typing import Any


def validate(content: dict[str, Any]) -> list[str]:
    """Strict shape: stem plus at least two options."""
    errors: list[str] = []
    if not isinstance(content.get("stem"), str) or not content["stem"].strip():
        errors.append("stem must be a non-empty string")
    options = content.get("options")
    if not isinstance(options, list) or len(options) < 2:
        errors.append("options must hold at least two strings")
    elif not all(isinstance(item, str) and item.strip() for item in options):
        errors.append("options must all be non-empty strings")
    return errors
