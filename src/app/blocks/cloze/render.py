"""Learner render contract for cloze blocks: gapped text only, never the key."""

from __future__ import annotations

from typing import Any


def render_for_learner(content: dict[str, Any]) -> dict[str, Any]:
    """Learner-safe view: the gapped text without blank answers."""
    return {"text": str(content.get("text", ""))}
