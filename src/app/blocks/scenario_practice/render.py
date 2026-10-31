"""Learner render contract for scenario practice: scenario plus items, never the key."""

from __future__ import annotations

from typing import Any


def render_for_learner(content: dict[str, Any]) -> dict[str, Any]:
    """Learner-safe view: scenario and item texts without tiers."""
    return {
        "scenario": str(content.get("scenario", "")),
        "items": [
            str(item.get("text", "")) for item in content.get("items", []) if isinstance(item, dict)
        ],
    }
