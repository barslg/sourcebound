"""Learner render contract for choice blocks: stem plus options, never the key."""

from __future__ import annotations

from typing import Any


def render_for_learner(content: dict[str, Any]) -> dict[str, Any]:
    """Learner-safe view: stem and options without the correct index."""
    return {
        "stem": str(content.get("stem", "")),
        "options": [str(item) for item in content.get("options", [])],
    }
