"""Block generators: typed prompt assembly for teach/example/check/practice/test.

Generators never call the model; the runs executor owns generate_json.
The prompt prefix order is binding: system + rubric, whole approved
source sorted by (source_id, address), frozen plan + dossier, skeleton.
Cache scope is agent:block_type:course_id.
"""

from __future__ import annotations

import json
from typing import Any

from app.agents.base import AGENT_SCHEMAS, assemble, system_prompt
from app.llm.prompts import cache_key as prompt_cache_key


def ordered_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Approved sources sorted by (source_id, address) in reading order."""
    from app.runs.executor import address_sort_key

    def _key(item: dict[str, Any]) -> tuple[Any, ...]:
        return (str(item.get("source_id", "")), address_sort_key(str(item.get("address", ""))))

    return sorted(sources, key=_key)


def build_generation_prompt(
    *,
    agent: str,
    block_type: str,
    course_id: str,
    course_language: str = "en",
    sources: list[dict[str, Any]] | None = None,
    plan: dict[str, Any] | None = None,
    dossier: dict[str, Any] | None = None,
    skeleton: dict[str, Any] | None = None,
) -> str:
    """Full generator prompt with the binding prefix order."""
    system = system_prompt(agent)
    dynamic = "\n\n## Approved sources\n" + json.dumps(
        {
            "course_language": course_language,
            "sources": ordered_sources(list(sources or [])),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    dynamic += "\n\n## Frozen plan + dossier\n" + json.dumps(
        {"plan": plan or {}, "dossier": dossier or {}}, ensure_ascii=False, sort_keys=True
    )
    dynamic += "\n\n## Block skeleton\n" + json.dumps(
        dict(skeleton or {}), ensure_ascii=False, sort_keys=True
    )
    _ = prompt_cache_key(agent, block_type, course_id)
    return assemble(system, dynamic)


def check_block_output(payload: dict[str, Any]) -> list[str]:
    """Validate a generator reply in code; return error strings."""
    errors: list[str] = []
    if not str(payload.get("block_type", "")).strip():
        errors.append("block_type is required")
    if not str(payload.get("text", "")).strip():
        errors.append("text is required")
    citations = payload.get("citations")
    if not isinstance(citations, list) or not citations:
        errors.append("at least one verified citation is required")
    return errors


def schema() -> dict[str, Any]:
    """The generator response schema (strict subset, startup-checked)."""
    return dict(AGENT_SCHEMAS["block_teach"])
