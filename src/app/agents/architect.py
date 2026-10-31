"""Architect: profile plus passages in, course plan tree out.

The model fills plain text inside the plan schema; code checks the
tree invariants (source ref per block, topic-without-answer briefs,
module purposes, methodology prescription, estimate size).
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from app.agents.base import AGENT_SCHEMAS, assemble, system_prompt
from app.course.tree import ArchitectOutput, CourseTree, validate_tree


def build_dynamic(
    profile: dict[str, Any],
    constraints: list[dict[str, Any]],
    prescription: dict[str, list[str]],
    passages: list[dict[str, Any]],
    course_language: str = "en",
    source_languages: list[str] | None = None,
) -> str:
    """Typed dynamic part: language, sources, profile, prescription.

    Sources come first so the stable approved-source prefix stays
    cache-friendly; the plan contract follows.
    """
    ordered = {
        "course_language": course_language,
        "source_languages": sorted(set(source_languages or [])),
        "passages": passages,
        "profile": profile,
        "constraints": constraints,
        "prescription": prescription,
    }
    return json.dumps(ordered, ensure_ascii=False)


def build_prompt(
    profile: dict[str, Any],
    constraints: list[dict[str, Any]],
    prescription: dict[str, list[str]],
    passages: list[dict[str, Any]],
    course_language: str = "en",
    source_languages: list[str] | None = None,
) -> str:
    """Full architect prompt: stable system prefix plus typed input."""
    return assemble(
        system_prompt("architect"),
        build_dynamic(
            profile, constraints, prescription, passages, course_language, source_languages
        ),
    )


def check_output(
    payload: dict[str, Any],
    prescription: dict[str, list[str]],
    handles: set[str] | None = None,
) -> list[str]:
    """Validate an architect reply in code; return error strings.

    When `handles` (the step's own P-handles) is given, a source ref
    outside it is rejected: handles are step-local, so P99 from
    another step must hold the block, never crash the run.
    """
    try:
        output = ArchitectOutput.model_validate(payload)
    except PydanticValidationError as exc:
        return [f"schema: {error['loc']}: {error['msg']}" for error in exc.errors()]
    errors = validate_tree(CourseTree(modules=output.modules), prescription)
    if handles is not None:
        for module in output.modules:
            for topic in module.topics:
                for block in topic.blocks:
                    for ref in block.source_refs:
                        if ref.handle not in handles:
                            errors.append(
                                f"block {block.plan_node_id}: unknown handle {ref.handle!r}"
                            )
    block_count = sum(len(topic.blocks) for module in output.modules for topic in module.topics)
    if output.estimate_inputs.blocks != block_count:
        errors.append(
            f"estimate blocks {output.estimate_inputs.blocks} != tree blocks {block_count}"
        )
    return errors


def schema() -> dict[str, Any]:
    """The architect response schema (strict subset, startup-checked)."""
    return dict(AGENT_SCHEMAS["architect"])
