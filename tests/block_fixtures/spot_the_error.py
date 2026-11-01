"""Fixture for spot-the-error with a sentinel answer key."""

from __future__ import annotations

from typing import Any

SENTINEL = "SENTINEL_SPOT_THE_ERROR_ANSWER_KEY_do_not_render"


def fixture() -> tuple[dict[str, Any], dict[str, Any]]:
    """Two sentences for learners; index and kind live only in the key."""
    content = {
        "sentences": [
            "Deployers must train staff.",
            "The GDPR never applies to AI at work.",
        ],
    }
    answer_key = {
        "planted_sentence": "The GDPR never applies to AI at work.",
        "planted_index": 1,
        "kind": "scope",
        "passage": "P1",
        "true_sentence": "The GDPR may apply alongside the AI Act.",
        "note": SENTINEL,
    }
    return content, answer_key
