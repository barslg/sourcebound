"""Corrector (lecture-first): one pass over a draft that accepts it or returns it corrected.

The reply wraps the stage's own schema: `accepted` with `corrected: null`
keeps the draft; otherwise `corrected` is the whole draft in the same
shape and replaces it. The code checks' remarks travel in the input; the
corrector is the only judge after them.
"""

from __future__ import annotations

import json
from typing import Any

from app.agents.base import assemble
from app.course.tree import corrector_schema


def build_prompt(
    *,
    stage: str,
    context: dict[str, Any],
    draft: dict[str, Any],
    remarks: list[str],
    course_language: str = "en",
) -> str:
    """Typed corrector input: the stable context first, then the draft and the code's remarks."""
    dynamic = json.dumps(
        {
            "course_language": course_language,
            "stage": stage,
            "context": context,
            "draft": draft,
            "remarks": remarks,
        },
        ensure_ascii=False,
    )
    return assemble(dynamic)


def check_output(payload: dict[str, Any]) -> list[str]:
    """A rejected draft carries its corrected version; an accepted one carries none."""
    accepted = payload.get("accepted")
    corrected = payload.get("corrected")
    if not isinstance(accepted, bool):
        return ["accepted must be a boolean"]
    if not accepted and not isinstance(corrected, dict):
        return ["a draft that is not accepted needs the whole corrected draft in `corrected`"]
    return []


def apply(payload: dict[str, Any], draft: dict[str, Any]) -> dict[str, Any]:
    """The draft to keep: the corrector's version when it gave one, else the original."""
    corrected = payload.get("corrected")
    if payload.get("accepted") or not isinstance(corrected, dict):
        return dict(draft)
    return dict(corrected)


def schema(stage_schema: dict[str, Any]) -> dict[str, Any]:
    """The corrector response schema around one stage's schema."""
    return corrector_schema(stage_schema)
