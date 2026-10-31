"""Learner render contract for reading blocks: content only, never the key."""

from __future__ import annotations

from typing import Any


def render_for_learner(content: dict[str, Any]) -> dict[str, Any]:
    """Learner-safe view: body plus citation quotes, no answer material."""
    return {
        "body": str(content.get("body", "")),
        "citations": [
            {
                "handle": str(item.get("handle", item.get("passage_id", ""))),
                "quote": str(item.get("quote", "")),
            }
            for item in content.get("citations", [])
            if isinstance(item, dict)
        ],
    }
