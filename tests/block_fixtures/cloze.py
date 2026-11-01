"""Fixture for cloze blocks with a sentinel answer key."""

from __future__ import annotations

from typing import Any

SENTINEL = "SENTINEL_CLOZE_ANSWER_KEY_do_not_render"


def fixture() -> tuple[dict[str, Any], dict[str, Any]]:
    """Sample gapped duty plus an answer key carrying the sentinel."""
    content = {"text": "___ must ensure ___ among staff.", "blanks": ["___", "___"]}
    answer_key = {"blanks": ["Deployers", "training"], "passage": "P1", "note": SENTINEL}
    return content, answer_key
