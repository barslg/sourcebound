"""Fixture for figure blocks with a sentinel answer key."""

from __future__ import annotations

from typing import Any

SENTINEL = "SENTINEL_FIGURE_ANSWER_KEY_do_not_render"


def fixture() -> tuple[dict[str, Any], dict[str, Any]]:
    """Sample fine ceilings plus an answer key carrying the sentinel."""
    content = {
        "caption": "Article 99 fine ceilings in million euro.",
        "numbers": [15, 35],
        "citation": {"passage_id": "P1", "quote": "15 million"},
    }
    answer_key = {"source_numbers": [15, 35], "note": SENTINEL}
    return content, answer_key


def correct_submission() -> dict[str, Any]:
    """An acknowledging submission scores full marks."""
    return {"acknowledged": True}


def wrong_submission() -> dict[str, Any]:
    """A refused acknowledgement scores zero."""
    return {"acknowledged": False}
