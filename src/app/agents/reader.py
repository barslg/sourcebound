"""Reader: one Wonder-high call per topic with quoted evidence.

Six rubric items per block: task fit, grounding, continuity, answer key,
audience and language, planted-error integrity. Verdicts are
pass/hold/unknown; a hold returns the block with its reason.
"""

from __future__ import annotations

import json
from typing import Any

from app.agents.base import AGENT_SCHEMAS, assemble, system_prompt

RUBRIC_ITEMS: tuple[str, ...] = (
    "task_fit",
    "grounding",
    "continuity",
    "answer_key",
    "audience_language",
    "planted_error",
)

VERDICTS = ("pass", "hold", "unknown")


def build_reader_prompt(
    *,
    topic_plan_node_id: str,
    course_language: str = "en",
    sources: list[dict[str, Any]] | None = None,
    plan: dict[str, Any] | None = None,
    dossier: dict[str, Any] | None = None,
    blocks: list[dict[str, Any]] | None = None,
) -> str:
    """Full reader prompt with the binding prefix order."""
    from app.agents.generate_block import ordered_sources

    system = system_prompt("reader")
    dynamic = "\n\n## Approved sources\n" + json.dumps(
        {"course_language": course_language, "sources": ordered_sources(list(sources or []))},
        ensure_ascii=False,
        sort_keys=True,
    )
    dynamic += "\n\n## Frozen plan + dossier\n" + json.dumps(
        {"plan": plan or {}, "dossier": dossier or {}}, ensure_ascii=False, sort_keys=True
    )
    dynamic += "\n\n## Block skeleton\n" + json.dumps(
        {"topic_plan_node_id": topic_plan_node_id, "blocks": blocks or []},
        ensure_ascii=False,
        sort_keys=True,
    )
    return assemble(system, dynamic)


def check_reader_output(payload: dict[str, Any], *, node_ids: set[str]) -> list[str]:
    """Validate a reader reply in code; return error strings."""
    errors: list[str] = []
    verdicts = payload.get("verdicts")
    if not isinstance(verdicts, list) or not verdicts:
        return ["verdicts must be a non-empty list"]
    for position, verdict in enumerate(verdicts):
        if not isinstance(verdict, dict):
            errors.append(f"verdicts[{position}]: must be an object")
            continue
        node_id = str(verdict.get("plan_node_id", ""))
        if node_id not in node_ids:
            errors.append(f"verdicts[{position}]: unknown plan node {node_id!r}")
        if verdict.get("verdict") not in VERDICTS:
            errors.append(f"verdicts[{position}]: verdict must be pass/hold/unknown")
        items = verdict.get("items")
        if not isinstance(items, dict):
            errors.append(f"verdicts[{position}]: items must be a fixed rubric object")
            continue
        for required in RUBRIC_ITEMS:
            item = items.get(required)
            if not isinstance(item, dict):
                errors.append(f"verdicts[{position}]: missing rubric item {required}")
                continue
            if item.get("verdict") not in VERDICTS:
                errors.append(f"verdicts[{position}]: {required} verdict must be pass/hold/unknown")
            if not str(item.get("evidence", "")).strip():
                errors.append(f"verdicts[{position}]: {required}: evidence is required")
    return errors


def schema() -> dict[str, Any]:
    """The reader response schema (strict subset, startup-checked)."""
    return dict(AGENT_SCHEMAS["reader"])
