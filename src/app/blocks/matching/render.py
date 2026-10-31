"""Learner render contract for matching blocks: shuffled sides, never the key."""

from __future__ import annotations

from typing import Any


def render_for_learner(content: dict[str, Any]) -> dict[str, Any]:
    """Learner-safe view: left and right columns without the mapping."""
    pairs = [item for item in content.get("pairs", []) if isinstance(item, dict)]
    return {
        "left": [str(item.get("left", "")) for item in pairs],
        "right": [str(item.get("right", "")) for item in pairs],
    }
