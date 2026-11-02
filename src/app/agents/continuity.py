"""Continuity reader: one pass per module over topic handoffs."""

from __future__ import annotations

import json
from typing import Any

from app.agents.base import AGENT_SCHEMAS, assemble


def build_continuity_prompt(
    *,
    module_plan_node_id: str,
    course_language: str = "en",
    handoffs: list[dict[str, Any]] | None = None,
    dossier: dict[str, Any] | None = None,
) -> str:
    """Typed continuity prompt: handoffs plus the frozen dossier."""
    return assemble(
        json.dumps(
            {
                "course_language": course_language,
                "module_plan_node_id": module_plan_node_id,
                "handoffs": handoffs or [],
                "dossier": dossier or {},
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    )


def check_continuity_output(payload: dict[str, Any]) -> list[str]:
    """Validate a continuity reply in code; return error strings."""
    errors: list[str] = []
    if not isinstance(payload.get("handoffs"), list):
        errors.append("handoffs must be a list")
    if not isinstance(payload.get("issues"), list):
        errors.append("issues must be a list")
    return errors


def schema() -> dict[str, Any]:
    """The continuity response schema (strict subset, startup-checked)."""
    return dict(AGENT_SCHEMAS["continuity"])
