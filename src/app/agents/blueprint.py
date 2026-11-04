"""Blueprint (lecture-first planning): the author's brief in, annotated topics and the case out.

One call writes the whole course as a list: the learner, the through-line
(a project built topic by topic or characters who carry it), modules in
teaching order and under each the topics with their key points and the
case step each one carries, so every lecture can be written on its own
and in parallel. No sources are read; the model writes from the brief.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from app.agents.base import AGENT_SCHEMAS, assemble
from app.agents.outline import normalise_ids
from app.course.tree import BlueprintOutput


def brief_of(constraints: list[dict[str, Any]]) -> dict[str, Any]:
    """The author's brief as one object: the `brief` constraint rows by key, text values only."""
    brief: dict[str, Any] = {}
    for row in constraints:
        if str(row.get("kind", "")) != "brief":
            continue
        raw = row.get("value")
        value: dict[str, Any] = raw if isinstance(raw, dict) else {}
        brief[str(row.get("key", ""))] = value.get("text", value.get("items", ""))
    return brief


def build_prompt(
    profile: dict[str, Any], brief: dict[str, Any], course_language: str = "en"
) -> str:
    """Typed blueprint input: language, profile, the four brief fields."""
    dynamic = json.dumps(
        {"course_language": course_language, "profile": profile, "brief": brief},
        ensure_ascii=False,
    )
    return assemble(dynamic)


def check_output(payload: dict[str, Any]) -> list[str]:
    """Validate a blueprint reply in code: shape, unique ids, `requires` naming earlier topics."""
    try:
        parsed = BlueprintOutput.model_validate(normalise_ids(payload))
    except PydanticValidationError as exc:
        return [f"schema: {error['loc']}: {error['msg']}" for error in exc.errors()]
    errors: list[str] = []
    seen_modules: set[str] = set()
    seen_topics: list[str] = []
    for module in parsed.modules:
        if module.plan_node_id in seen_modules:
            errors.append(f"module {module.plan_node_id}: plan_node_id is not unique")
        seen_modules.add(module.plan_node_id)
        for topic in module.topics:
            if topic.plan_node_id in seen_topics:
                errors.append(f"topic {topic.plan_node_id}: plan_node_id is not unique")
            for required in topic.requires:
                if required not in seen_topics:
                    errors.append(
                        f"topic {topic.plan_node_id}: requires {required!r}, which is not an"
                        " earlier topic"
                    )
            seen_topics.append(topic.plan_node_id)
    return errors


def topics_of(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Every topic as one row with its module id, in course order."""
    rows: list[dict[str, Any]] = []
    for module in payload.get("modules", []):
        if not isinstance(module, dict):
            continue
        for topic in module.get("topics", []):
            if isinstance(topic, dict):
                rows.append({"module": str(module.get("plan_node_id", "")), **topic})
    return rows


def schema() -> dict[str, Any]:
    """The blueprint response schema (strict subset, startup-checked)."""
    return dict(AGENT_SCHEMAS["blueprint"])
