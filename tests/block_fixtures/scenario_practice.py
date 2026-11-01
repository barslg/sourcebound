"""Fixture for scenario practice with a sentinel answer key."""

from __future__ import annotations

from typing import Any

SENTINEL = "SENTINEL_SCENARIO_PRACTICE_ANSWER_KEY_do_not_render"


def fixture() -> tuple[dict[str, Any], dict[str, Any]]:
    """Sample tier sorting plus an answer key carrying the sentinel."""
    content = {
        "scenario": "Sort two workplace AI uses into risk tiers.",
        "items": [
            {"text": "CV screening", "passage_id": "P1"},
            {"text": "Spell check", "passage_id": "P2"},
        ],
    }
    answer_key = {
        "labels": {"CV screening": "high-risk", "Spell check": "minimal"},
        "passages": {"CV screening": "P1", "Spell check": "P2"},
        "passage": "P1",
        "note": SENTINEL,
    }
    return content, answer_key
