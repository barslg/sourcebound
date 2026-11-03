"""Fixture for diagram blocks with a sentinel answer key."""

from __future__ import annotations

from typing import Any

SENTINEL = "SENTINEL_DIAGRAM_ANSWER_KEY_do_not_render"


def fixture() -> tuple[dict[str, Any], dict[str, Any]]:
    """A two-step flow from the passage plus an answer key carrying the sentinel."""
    content = {
        "caption": "The literacy routine as the passage states it.",
        "nodes": [{"id": "n0", "label": "train staff"}, {"id": "n1", "label": "oversee use"}],
        "edges": [{"from": "n0", "to": "n1", "label": "then"}],
        "citations": [{"passage_id": "P1", "quote": "train staff"}],
    }
    answer_key = {"note": SENTINEL}
    return content, answer_key
