"""Learner render contract for matching blocks: ids on both columns, shuffled right, no key."""

from __future__ import annotations

import hashlib
from typing import Any


def displayed_order(rights: list[str]) -> list[int]:
    """Authored indices in the order the learner sees the right column.

    Deterministic and never the authored order (n > 1). Caller: this
    module's renderer and services/grading.py, which translates the
    displayed right ids back to authored indices before grading.
    """
    ordered = sorted(
        range(len(rights)), key=lambda i: hashlib.sha256(f"{i}:{rights[i]}".encode()).hexdigest()
    )
    if len(rights) > 1 and ordered == list(range(len(rights))):
        ordered = ordered[1:] + ordered[:1]
    return ordered


def left_id(position: int) -> str:
    return f"l{position}"


def right_id(position: int) -> str:
    """Id of the right item shown at `position` (stable for one content version)."""
    return f"r{position}"


def render_for_learner(content: dict[str, Any]) -> dict[str, Any]:
    """Learner-safe view: lefts and shuffled rights, each with an opaque id."""
    pairs = [item for item in content.get("pairs", []) if isinstance(item, dict)]
    rights = [str(item.get("right", "")) for item in pairs]
    return {
        "left": [
            {"id": left_id(i), "text": str(item.get("left", ""))} for i, item in enumerate(pairs)
        ],
        "right": [
            {"id": right_id(shown), "text": rights[authored]}
            for shown, authored in enumerate(displayed_order(rights))
        ],
    }
