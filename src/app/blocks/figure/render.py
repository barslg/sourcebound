"""Learner render contract for figure blocks: caption plus code-rendered numbers."""

from __future__ import annotations

from typing import Any


def render_for_learner(content: dict[str, Any]) -> dict[str, Any]:
    """Learner-safe view: caption and numbers, never the key."""
    numbers = content.get("numbers", [])
    return {
        "caption": str(content.get("caption", "")),
        "numbers": [item for item in numbers if isinstance(item, (int, float))],
    }
