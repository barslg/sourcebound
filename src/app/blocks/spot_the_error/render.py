"""Learner render contract for spot-the-error: sentences only, never the key."""

from __future__ import annotations

from typing import Any


def render_for_learner(content: dict[str, Any]) -> dict[str, Any]:
    """Learner-safe view: numbered sentences without the planted index."""
    return {"sentences": [str(item) for item in content.get("sentences", [])]}
