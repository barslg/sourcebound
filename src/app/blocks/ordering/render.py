"""Learner render contract for ordering blocks: steps shuffled, opaque ids, never the key."""

from __future__ import annotations

import hashlib
from typing import Any


def displayed_order(items: list[str]) -> list[int]:
    """Authored indices in the order the learner sees the steps.

    Deterministic for one content version and never the authored order
    once there are three or more steps (the schema's minimum). Callers:
    this module's renderer and services/grading.py, which maps the
    served ids back to authored indices before grading.
    """
    ordered = sorted(
        range(len(items)), key=lambda i: hashlib.sha256(f"{i}:{items[i]}".encode()).hexdigest()
    )
    if len(items) > 2 and ordered == list(range(len(items))):
        ordered = ordered[1:] + ordered[:1]
    return ordered


def step_id(position: int) -> str:
    """Id of the step shown at `position` (stable for one content version)."""
    return f"s{position}"


def render_for_learner(content: dict[str, Any]) -> dict[str, Any]:
    """Learner-safe view: the prompt and the steps in displayed order with opaque ids."""
    items = [str(item) for item in content.get("items", [])]
    return {
        "prompt": str(content.get("prompt", "")),
        "items": [
            {"id": step_id(shown), "text": items[authored]}
            for shown, authored in enumerate(displayed_order(items))
        ],
    }
