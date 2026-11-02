"""Reader: one Wonder-high call per topic with quoted evidence.

Seven rubric items per block: task fit, grounding, continuity, answer
key, audience and language, planted-error integrity, coverage (the
block carries the complete claim of every passage it cites). Verdicts
are pass/hold/unknown; a hold returns the block with its reason.
"""

from __future__ import annotations

import json
from typing import Any

from app.agents.base import AGENT_SCHEMAS, assemble

RUBRIC_ITEMS: tuple[str, ...] = (
    "task_fit",
    "grounding",
    "continuity",
    "answer_key",
    "audience_language",
    "planted_error",
    "coverage",
)

VERDICTS = ("pass", "hold", "unknown")


def build_reader_prompt(
    *,
    topic_plan_node_id: str,
    course_language: str = "en",
    sources: list[dict[str, Any]] | None = None,
    plan: dict[str, Any] | None = None,
    previously: list[dict[str, Any]] | None = None,
    blocks: list[dict[str, Any]] | None = None,
) -> str:
    """Full reader prompt with the binding input order."""
    from app.agents.generate_block import public_sources

    dynamic = "\n\n## Approved sources\n" + json.dumps(
        {"course_language": course_language, "sources": public_sources(list(sources or []))},
        ensure_ascii=False,
        sort_keys=True,
    )
    dynamic += "\n\n## Scenario\n" + json.dumps(plan or {}, ensure_ascii=False, sort_keys=True)
    dynamic += "\n\n## Previously\n" + json.dumps(
        {"previously": previously or []}, ensure_ascii=False, sort_keys=True
    )
    dynamic += "\n\n## Block skeleton\n" + json.dumps(
        {"topic_plan_node_id": topic_plan_node_id, "blocks": blocks or []},
        ensure_ascii=False,
        sort_keys=True,
    )
    return assemble(dynamic)


NOT_APPLICABLE = "not applicable"


def _strings_of(value: Any) -> list[str]:
    """Every string anywhere in a stored block's content or key."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings_of(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings_of(v)]
    return []


def _normal(text: str) -> str:
    return " ".join(str(text).lower().split())


def verify_evidence(
    reply: dict[str, Any],
    *,
    blocks: list[dict[str, Any]],
    passages: list[dict[str, Any]],
) -> dict[str, Any]:
    """Turn every rubric item whose evidence is not a verbatim span into `unknown`.

    A verdict is an opinion until its evidence exists: the span must be
    found (case and whitespace folded) in the block's content or key or
    in one of the topic's passages. `not applicable` is accepted only
    for planted_error on a block that is not a spot_the_error exercise.
    The caller treats unknown as undecided (ask once more, then hold),
    so a reader that asserts without quoting never passes a block.
    """
    haystack_passages = [_normal(str(p.get("text", ""))) for p in passages]
    by_node = {str(b.get("plan_node_id", "")): b for b in blocks}
    out: dict[str, Any] = json.loads(json.dumps(reply))
    for verdict in out.get("verdicts", []):
        if not isinstance(verdict, dict):
            continue
        block = by_node.get(str(verdict.get("plan_node_id", "")), {})
        strings = _strings_of(block.get("content", {})) + _strings_of(block.get("answer_key", {}))
        strings += _strings_of(block.get("text", ""))
        own = [_normal(s) for s in strings]
        spot = str(block.get("block_type", "")) == "spot_the_error"
        items = verdict.get("items", {})
        if not isinstance(items, dict):
            continue
        for item_id, item in items.items():
            if not isinstance(item, dict):
                continue
            evidence = _normal(str(item.get("evidence", "")))
            if item_id == "planted_error" and not spot and evidence == NOT_APPLICABLE:
                continue
            found = bool(evidence) and (
                any(evidence in text for text in own)
                or any(evidence in text for text in haystack_passages)
            )
            if not found:
                item["verdict"] = "unknown"
                item["evidence"] = f"evidence not found verbatim: {item.get('evidence', '')}"[:500]
        if (
            any(isinstance(i, dict) and i.get("verdict") == "unknown" for i in items.values())
            and verdict.get("verdict") == "pass"
        ):
            verdict["verdict"] = "unknown"
    return out


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
    judged = {str(v.get("plan_node_id", "")) for v in verdicts if isinstance(v, dict)}
    for node_id in sorted(node_ids - judged):
        errors.append(f"no verdict for block {node_id!r}: every block must be judged")
    return errors


def schema() -> dict[str, Any]:
    """The reader response schema (strict subset, startup-checked)."""
    return dict(AGENT_SCHEMAS["reader"])
