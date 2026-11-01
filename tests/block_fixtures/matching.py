"""Fixture for matching blocks with a sentinel answer key."""

from __future__ import annotations

from typing import Any

SENTINEL = "SENTINEL_MATCHING_ANSWER_KEY_do_not_render"


def fixture() -> tuple[dict[str, Any], dict[str, Any]]:
    """Sample duty mapping plus an answer key carrying the sentinel."""
    content = {
        "pairs": [
            {"left": "Provider", "right": "train staff"},
            {"left": "Deployer", "right": "oversee use"},
        ]
    }
    answer_key = {
        "mapping": {"Provider": "train staff", "Deployer": "oversee use"},
        "passage": "P1",
        "note": SENTINEL,
    }
    return content, answer_key
