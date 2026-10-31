"""Fixture for spot-the-error with a sentinel answer key."""

from __future__ import annotations

from typing import Any

SENTINEL = "SENTINEL_SPOT_THE_ERROR_ANSWER_KEY_do_not_render"


def fixture() -> tuple[dict[str, Any], dict[str, Any]]:
    """Two true sentences plus one planted negation; key carries the sentinel."""
    content = {
        "sentences": [
            "Deployers must train staff.",
            "The GDPR never applies to AI at work.",
        ],
        "planted_index": 1,
        "kind": "scope",
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


def correct_submission() -> dict[str, Any]:
    """Naming the sentence, kind, passage, and fix scores full marks."""
    return {
        "sentence": "The GDPR never applies to AI at work.",
        "kind": "scope",
        "passage": "P1",
        "correct_value": "The GDPR may apply alongside the AI Act.",
    }


def wrong_submission() -> dict[str, Any]:
    """Missing the sentence scores zero."""
    return {"sentence": "Deployers must train staff.", "kind": "scope"}
