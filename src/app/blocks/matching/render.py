"""Learner render contract for matching blocks: shuffled right side, never the key."""

from __future__ import annotations

import hashlib
from typing import Any


def _shuffled(rights: list[str]) -> list[str]:
    """Deterministic shuffle that never keeps the authored order (n > 1)."""
    ordered = sorted(
        range(len(rights)), key=lambda i: hashlib.sha256(f"{i}:{rights[i]}".encode()).hexdigest()
    )
    if len(rights) > 1 and ordered == list(range(len(rights))):
        ordered = ordered[1:] + ordered[:1]
    return [rights[i] for i in ordered]


def render_for_learner(content: dict[str, Any]) -> dict[str, Any]:
    """Learner-safe view: left column and a shuffled right column, no mapping."""
    pairs = [item for item in content.get("pairs", []) if isinstance(item, dict)]
    rights = [str(item.get("right", "")) for item in pairs]
    return {
        "left": [str(item.get("left", "")) for item in pairs],
        "right": _shuffled(rights),
    }
