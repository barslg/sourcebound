"""Lecture writer (lecture-first): one topic's full lecture from the blueprint alone.

The typed input keeps its stable part first (language, brief, learner and
case, every topic as a line) so the prefix is cached across the course;
the topic's own row and the case steps before it come last.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from app.agents.base import AGENT_SCHEMAS, assemble
from app.course.text_checks import python_code_holds
from app.course.tree import LectureOutput

MIN_PARAGRAPHS = 4


def build_prompt(
    *,
    brief: dict[str, Any],
    picture: dict[str, Any],
    topics: list[dict[str, Any]],
    own: dict[str, Any],
    previous_steps: list[dict[str, Any]],
    course_language: str = "en",
) -> str:
    """Typed lecture input: stable course facts first, then this topic's row and the case so far."""
    dynamic = json.dumps(
        {
            "course_language": course_language,
            "brief": brief,
            "learner": str(picture.get("learner", "")),
            "case": str(picture.get("case", "")),
            "topics": [
                {
                    "plan_node_id": str(t.get("plan_node_id", "")),
                    "title": str(t.get("title", "")),
                    "annotation": str(t.get("annotation", "")),
                }
                for t in topics
            ],
            "own": own,
            "previous_steps": previous_steps,
        },
        ensure_ascii=False,
    )
    return assemble(dynamic)


def check_output(payload: dict[str, Any], node_id: str) -> list[str]:
    """Validate a lecture reply in code: shape, its topic id, enough paragraphs, code compiles."""
    try:
        parsed = LectureOutput.model_validate(payload)
    except PydanticValidationError as exc:
        return [f"schema: {error['loc']}: {error['msg']}" for error in exc.errors()]
    errors: list[str] = []
    if parsed.plan_node_id != node_id:
        errors.append(f"plan_node_id must be {node_id!r}, not {parsed.plan_node_id!r}")
    kept = [p for p in parsed.paragraphs if p.strip()]
    if len(kept) < MIN_PARAGRAPHS:
        errors.append(f"a lecture has at least {MIN_PARAGRAPHS} paragraphs; {len(kept)} given")
    if len(kept) != len(parsed.paragraphs):
        errors.append("every paragraph has text; drop the empty ones")
    errors += python_code_holds("\n\n".join(kept))
    return errors


def schema() -> dict[str, Any]:
    """The lecture response schema (strict subset, startup-checked)."""
    return dict(AGENT_SCHEMAS["lecture"])
