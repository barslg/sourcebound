"""Fixture for choice blocks with a sentinel answer key."""

from __future__ import annotations

from typing import Any

SENTINEL = "SENTINEL_CHOICE_ANSWER_KEY_do_not_render"


def fixture() -> tuple[dict[str, Any], dict[str, Any]]:
    """Sample pre-question plus an answer key carrying the sentinel."""
    content = {
        "stem": "Who must ensure AI literacy at work?",
        "options": ["Providers and deployers", "Nobody", "Only vendors"],
    }
    answer_key = {"correct": 0, "passage": "P1", "note": SENTINEL}
    return content, answer_key
