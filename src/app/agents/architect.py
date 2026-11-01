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
    module_scope: dict[str, Any] | None = None,
) -> str:
    """Typed dynamic part: language, sources, profile, prescription, module scope.

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
    from app.course.grounding import allowed_block_types, passage_kind

    ordered = {
        "course_language": course_language,
        "source_languages": sorted(set(source_languages or [])),
        # R6: each passage carries its kind; the block types code allows per
        # kind (a figure only where a passage has something to draw) once.
        "passages": [
            {**passage, "kind": passage_kind(str(passage.get("text", "")))} for passage in passages
        ],
        "block_types_by_passage_kind": {
            kind: list(allowed_block_types(sample))
            for kind, sample in (("has_numbers", "1"), ("has_list", "- a"), ("prose", "a"))
        },
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
    if module_scope is not None:
        # two-level planning: this call plans ONE module of the outline over its own passages
        ordered["module_scope"] = module_scope
    return json.dumps(ordered, ensure_ascii=False)


def build_prompt(
    profile: dict[str, Any],
    constraints: list[dict[str, Any]],
    prescription: dict[str, list[str]],
    passages: list[dict[str, Any]],
    course_language: str = "en",
    source_languages: list[str] | None = None,
    module_scope: dict[str, Any] | None = None,
) -> str:
    """Full architect prompt: stable system prefix plus typed input."""
    return assemble(
        system_prompt("architect"),
        build_dynamic(
            profile,
            constraints,
            prescription,
            passages,
            course_language,
            source_languages,
            module_scope=module_scope,
        ),
    )


def check_output(
    payload: dict[str, Any],
    prescription: dict[str, list[str]],
    handles: set[str] | None = None,
    passages: list[dict[str, Any]] | None = None,
    module_scope: dict[str, Any] | None = None,
) -> list[str]:
    """Validate an architect reply in code; return error strings.

    With `module_scope` (two-level planning) the reply must be exactly
    the scoped module, under its outline id, with a handoff for every
    topic — the last one hands over to the next module.

    When `handles` (the step's own P-handles) is given, a source ref
    outside it is rejected: handles are step-local, so P99 from
    another step must hold the block, never crash the run. When
    `passages` (handle -> text) is given, topic coverage is checked:
    a topic title may not name a concept that only an unassigned
    passage carries, and the plan must cite at least 60 % of the
    content passages or list the rest in `uncovered` with reasons.
    """
    try:
        output = ArchitectOutput.model_validate(payload)
    except PydanticValidationError as exc:
        return [f"schema: {error['loc']}: {error['msg']}" for error in exc.errors()]
    errors = validate_tree(CourseTree(modules=output.modules), prescription)
    if module_scope is not None:
        wanted = str(module_scope.get("plan_node_id", ""))
        if len(output.modules) != 1 or output.modules[0].plan_node_id != wanted:
            errors.append(f"module scope: exactly one module with plan_node_id {wanted!r}")
    errors += _handoff_errors(output, require_last=module_scope is not None)
    if passages:
        errors += _coverage_errors(output, passages)
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


def _handoff_errors(output: Any, require_last: bool = False) -> list[str]:
    """One through-line handoff per topic boundary, or the plan is rejected.

    Topics in plan order: every topic except the last must hand the
    case over (through_line.handoffs naming that topic); a scoped
    module hands its last topic to the next module too. A run that
    produced no handoff for a boundary holds at the plan step.
    """
    topics = [topic for module in output.modules for topic in module.topics]
    if len(topics) < 2 and not require_last:
        return []
    # a topic's own through_line_note IS its handoff: the schema asks for it on
    # every topic, so the handoffs list may repeat it but never has to
    handed = {t.plan_node_id for t in topics if t.through_line_note.strip()}
    if output.through_line is not None:
        handed |= {h.topic_plan_node_id for h in output.through_line.handoffs if h.note.strip()}
    boundary = topics if require_last else topics[:-1]
    return [
        f"through-line: no handoff for topic boundary after {topic.title!r}"
        for topic in boundary
        if topic.plan_node_id not in handed
    ]


def _coverage_errors(output: Any, passages: list[dict[str, Any]]) -> list[str]:
    """Whole-plan passage coverage and figure gating, checked in code.

    A topic title naming something the sources lack is the author's
    call (a Plan-page warning), never a stop: the word-level rejection
    stopped 6 of 6 real plans on 'test' and 'staged' (T11 review H3).
    """
    errors: list[str] = []
    by_handle = {str(p.get("handle", "")): str(p.get("text", "")) for p in passages}
    content_handles = {
        str(p.get("handle", ""))
        for p in passages
        if p.get("is_content", True) and str(p.get("handle", ""))
    }
    cited: set[str] = set()
    for module in output.modules:
        for topic in module.topics:
            for block in topic.blocks:
                for ref in block.source_refs:
                    cited.add(ref.handle)
            for block in topic.blocks:
                if block.block_type != "figure":
                    continue
                from app.course.grounding import passage_kind

                kinds = {passage_kind(by_handle.get(ref.handle, "")) for ref in block.source_refs}
                if kinds <= {"prose"}:
                    errors.append(
                        f"figure: block {block.plan_node_id} cites only prose passages; "
                        "a figure needs numbers or a structure to draw"
                    )
    if content_handles:
        share = len(cited & content_handles) / len(content_handles)
        declared = {item.handle for item in output.uncovered if item.reason.strip()}
        missing = content_handles - cited - declared
        if share < 0.6 and missing:
            errors.append(
                f"plan cites {round(share * 100)}% of content passages; "
                f"uncovered passages must be listed in uncovered: {sorted(missing)}"
            )
    return errors


def schema() -> dict[str, Any]:
    """The architect response schema (strict subset, startup-checked)."""
    return dict(AGENT_SCHEMAS["architect"])
