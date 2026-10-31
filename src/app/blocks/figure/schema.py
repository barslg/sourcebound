"""Figure block: code renders from source numbers; the model writes only the caption."""

from __future__ import annotations

from typing import Any


def validate(content: dict[str, Any]) -> list[str]:
    """Strict shape: caption plus numeric series plus one citation."""
    errors: list[str] = []
    if not isinstance(content.get("caption"), str) or not content["caption"].strip():
        errors.append("caption must be a non-empty string")
    numbers = content.get("numbers")
    if not isinstance(numbers, list) or not numbers:
        errors.append("numbers must be a non-empty list from the source")
    elif not all(isinstance(item, (int, float)) for item in numbers):
        errors.append("numbers must all be numeric")
    if not isinstance(content.get("citation"), dict):
        errors.append("citation is required")
    return errors
