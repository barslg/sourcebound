"""Scenario practice: classify workplace uses with supporting passages."""

from __future__ import annotations

from typing import Any


def validate(content: dict[str, Any]) -> list[str]:
    """Strict shape: scenario plus at least one item with a passage."""
    errors: list[str] = []
    if not isinstance(content.get("scenario"), str) or not content["scenario"].strip():
        errors.append("scenario must be a non-empty string")
    items = content.get("items")
    if not isinstance(items, list) or not items:
        errors.append("items must be a non-empty list")
        return errors
    for index, item in enumerate(items):
        if not isinstance(item, dict) or not item.get("text") or not item.get("passage_id"):
            errors.append(f"items[{index}]: text and passage_id are required")
    return errors
