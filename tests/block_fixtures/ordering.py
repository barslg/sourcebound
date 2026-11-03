"""Fixture for ordering blocks with a sentinel answer key."""

from __future__ import annotations

from typing import Any

SENTINEL = "SENTINEL_ORDERING_ANSWER_KEY_do_not_render"


def fixture() -> tuple[dict[str, Any], dict[str, Any]]:
    """Three steps of a procedure in authored order plus a key carrying the sentinel."""
    content = {
        "prompt": "Put the steps of the literacy routine in order.",
        "items": ["Name the AI system in use", "Train the staff who use it", "Record the training"],
    }
    answer_key = {"order_by_index": [0, 1, 2], "passage": "P1", "note": SENTINEL}
    return content, answer_key
