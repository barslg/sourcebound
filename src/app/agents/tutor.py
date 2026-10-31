"""Tutor: picks verbatim fragments of retrieved passages; code decides the rest.

The model never writes prose for the learner. Code retrieves the
passages (FTS, this course only), shows them under handles P1..Pn, and
keeps only the quotes that are verbatim in their passage.
"""

from __future__ import annotations

import json
from typing import Any

from app.agents.base import AGENT_SCHEMAS, assemble, system_prompt


def build_prompt(*, question: str, passages: list[dict[str, str]], course_language: str) -> str:
    """Typed tutor prompt: the question and handle-addressed passage texts."""
    return assemble(
        system_prompt("tutor"),
        json.dumps(
            {"course_language": course_language, "question": question, "passages": passages},
            ensure_ascii=False,
            sort_keys=True,
        ),
    )


def check_output(payload: dict[str, Any], handles: set[str]) -> list[str]:
    """Shape check of a tutor reply; verbatim verification happens against stored text."""
    errors: list[str] = []
    quotes = payload.get("quotes")
    if not isinstance(quotes, list):
        return ["quotes must be a list"]
    for index, item in enumerate(quotes):
        if not isinstance(item, dict) or item.get("handle") not in handles:
            errors.append(f"quotes[{index}]: unknown handle")
        elif not str(item.get("quote", "")).strip():
            errors.append(f"quotes[{index}]: empty quote")
    return errors


def schema() -> dict[str, Any]:
    """The tutor response schema (strict subset, startup-checked)."""
    return dict(AGENT_SCHEMAS["tutor"])
