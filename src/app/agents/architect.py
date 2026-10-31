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
) -> str:
    """Typed dynamic part: profile, constraints, prescription, handles."""
    return json.dumps(
        {
            "profile": profile,
            "constraints": constraints,
            "prescription": prescription,
            "passages": passages,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def build_prompt(
    profile: dict[str, Any],
    constraints: list[dict[str, Any]],
    prescription: dict[str, list[str]],
    passages: list[dict[str, Any]],
) -> str:
    """Full architect prompt: stable system prefix plus typed input."""
    return assemble(
        system_prompt("architect"),
        build_dynamic(profile, constraints, prescription, passages),
    )


def check_output(payload: dict[str, Any], prescription: dict[str, list[str]]) -> list[str]:
    """Validate an architect reply in code; return error strings."""
    try:
        output = ArchitectOutput.model_validate(payload)
    except PydanticValidationError as exc:
        return [f"schema: {error['loc']}: {error['msg']}" for error in exc.errors()]
    errors = validate_tree(CourseTree(modules=output.modules), prescription)
    block_count = sum(len(topic.blocks) for module in output.modules for topic in module.topics)
    if output.estimate_inputs.blocks != block_count:
        errors.append(
            f"estimate blocks {output.estimate_inputs.blocks} != tree blocks {block_count}"
        )
    return errors


def schema() -> dict[str, Any]:
    """The architect response schema (strict subset, startup-checked)."""
    return dict(AGENT_SCHEMAS["architect"])
