"""Block generators: typed prompt assembly for teach/example/check/practice/test.

Generators never call the model; the runs executor owns generate_json.
The prompt prefix order is binding: system + rubric, whole approved
source sorted by (source_id, address), frozen plan + dossier, skeleton.
Cache scope is agent:block_type:course_id.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.agents.base import assemble
from app.course.tree import BLOCK_GENERATOR_SCHEMAS
from app.llm.prompts import cache_key as prompt_cache_key

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def ordered_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Approved sources in handle order; items without handles fall back to document order."""
    from app.course.handles import address_sort_key, handle_number

    if sources and all(item.get("handle") for item in sources):
        return sorted(sources, key=lambda item: handle_number(str(item["handle"])))

    def _key(item: dict[str, Any]) -> tuple[Any, ...]:
        return (str(item.get("source_id", "")), address_sort_key(str(item.get("address", ""))))

    return sorted(sources, key=_key)


def public_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prompt-safe sources: P-handles only, never passage or source UUIDs."""
    out = []
    for index, item in enumerate(ordered_sources(list(sources)), 1):
        out.append(
            {
                "handle": str(item.get("handle", f"P{index}")),
                "address": str(item.get("address", item.get("address_key", ""))),
                "text": str(item.get("text", "")),
            }
        )
    return out


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
    upstream_texts: list[dict[str, Any]] | None = None,
) -> str:
    """Full generator prompt with the binding prefix order."""
    dynamic = "\n\n## Approved sources\n" + json.dumps(
        {"course_language": course_language, "sources": public_sources(list(sources or []))},
        ensure_ascii=False,
        sort_keys=True,
    )
    dynamic += "\n\n## Frozen plan + dossier\n" + json.dumps(
        {"plan": plan or {}, "dossier": dossier or {}}, ensure_ascii=False, sort_keys=True
    )
    if upstream_texts:
        dynamic += "\n\n## Upstream blocks\n" + json.dumps(
            {"upstream": upstream_texts}, ensure_ascii=False, sort_keys=True
        )
    dynamic += "\n\n## Block skeleton\n" + json.dumps(
        dict(skeleton or {}), ensure_ascii=False, sort_keys=True
    )
    _ = prompt_cache_key(agent, block_type, course_id)
    return assemble(dynamic)


def check_block_output(payload: dict[str, Any], block_type: str = "reading") -> list[str]:
    """Validate a generator reply in code; return error strings."""
    errors: list[str] = []
    if not str(payload.get("block_type", "")).strip():
        errors.append("block_type is required")
    if not str(payload.get("text", "")).strip():
        errors.append("text is required")
    citations = payload.get("citations")
    if not isinstance(citations, list) or not citations:
        errors.append("at least one verified citation is required")
    else:
        for i, c in enumerate(citations):
            if not isinstance(c, dict) or not str(c.get("quote", "")).strip():
                errors.append(f"citations[{i}]: quote is required")
            handle = str(c.get("passage_id", "")) if isinstance(c, dict) else ""
            if handle and _UUID_RE.fullmatch(handle):
                errors.append(f"citations[{i}]: passage handle must be a P-handle, not a UUID")
    # per-type shape is NOT rejected here: the executor validates the
    # reply against its type schema and holds the block (M5) instead
    # of stopping the run on a paid reply.
    return errors


def schema_for(block_type: str) -> dict[str, Any]:
    """Strict per-type generator response schema."""
    return dict(BLOCK_GENERATOR_SCHEMAS.get(block_type, BLOCK_GENERATOR_SCHEMAS["reading"]))
