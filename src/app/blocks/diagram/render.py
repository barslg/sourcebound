"""Learner render contract for diagram blocks: caption, nodes and edges; the browser draws."""

from __future__ import annotations

from typing import Any


def render_for_learner(content: dict[str, Any]) -> dict[str, Any]:
    """Learner-safe view: the caption and the typed graph (no source text, no key)."""
    return {
        "caption": str(content.get("caption", "")),
        "nodes": [
            {"id": str(n.get("id", "")), "label": str(n.get("label", ""))}
            for n in content.get("nodes", [])
            if isinstance(n, dict)
        ],
        "edges": [
            {
                "from": str(e.get("from", "")),
                "to": str(e.get("to", "")),
                "label": str(e.get("label", "")),
            }
            for e in content.get("edges", [])
            if isinstance(e, dict)
        ],
    }
