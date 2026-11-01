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
    from app.blocks.registry import BLOCK_TYPES as _REGISTRY_TYPES
    from app.course.methodologies import METHODOLOGIES
    from app.course.tree import BLOCK_TYPES as _ALLOWED_BY_KIND

    # The methodology's block plan (Merrill: activation choice,
    # demonstration reading/figure, application by Bloom, integration
    # spot_the_error, module test choice+matching+spot) — the model
    # must see the (role, block_type) pairs, not only the role list.
    codes = (
        [str(c) for c in profile.get("methodology_codes", [])] if isinstance(profile, dict) else []
    )
    block_plan = {
        str(entry["code"]): {
            key: value
            for key, value in dict(entry.get("prescription", {})).items()
            if key in ("lesson_blocks", "module_test_blocks")
        }
        for entry in METHODOLOGIES
        if str(entry["code"]) in codes
    }
    ordered = {
        "course_language": course_language,
        "source_languages": sorted(set(source_languages or [])),
        "passages": passages,
        "profile": profile,
        "constraints": constraints,
        "prescription": prescription,
        "block_plan_by_methodology": block_plan,
        # closed vocabularies, derived from the same registries the
        # code validates against: block_type must come from this list
        # for the topic's kind, never invented.
        "block_types_by_topic_kind": {
            kind: [t for t in allowed if t in _REGISTRY_TYPES]
            for kind, allowed in _ALLOWED_BY_KIND.items()
        },
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
    # The estimate is derived from the tree by code; a model-written count is never a reason
    # to reject a plan that passed every structural check.
    block_count = sum(len(topic.blocks) for module in output.modules for topic in module.topics)
    output.estimate_inputs.blocks = block_count
    return errors


def schema() -> dict[str, Any]:
    """The architect response schema (strict subset, startup-checked)."""
    return dict(AGENT_SCHEMAS["architect"])
