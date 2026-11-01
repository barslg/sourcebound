"""Fixture for reading blocks with a sentinel answer key."""

from __future__ import annotations

from typing import Any

SENTINEL = "SENTINEL_READING_ANSWER_KEY_do_not_render"


def fixture() -> tuple[dict[str, Any], dict[str, Any]]:
    """Sample content plus an answer key carrying the sentinel."""
    content = {
        "body": "Deployers must ensure staff can use the system safely.",
        "citations": [{"passage_id": "P1", "quote": "ensure staff can use"}],
    }
    answer_key = {"note": SENTINEL}
    return content, answer_key
