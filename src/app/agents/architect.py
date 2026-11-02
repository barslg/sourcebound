"""Architect (planning layer 2): one module of the scenario in, its block plan out.

The model sees the whole scenario (every topic as one line), the
module's own topics in full and the module's passages grouped by topic
(each with its handle, address and kind); it writes objectives, the
misconception its checks target, and the block skeletons per topic:
role, type, a brief that never contains the answer, source refs inside
the topic's own sections, and for an example the input, action and
result on the course case. Code checks the tree invariants.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from app.agents.base import AGENT_SCHEMAS, assemble
from app.course.tree import ArchitectOutput, CourseTree, validate_tree


def build_dynamic(
    profile: dict[str, Any],
    constraints: list[dict[str, Any]],
    prescription: dict[str, list[str]],
    passages: list[dict[str, Any]],
    course_language: str = "en",
    source_languages: list[str] | None = None,
    module_scope: dict[str, Any] | None = None,
    scenario: list[dict[str, Any]] | None = None,
) -> str:
    """Typed dynamic part: language, passages (with kind and topic), profile, scope, scenario."""
    from app.blocks.registry import BLOCK_TYPES as _REGISTRY_TYPES
    from app.course.grounding import passage_kind
    from app.course.methodologies import METHODOLOGIES
    from app.course.tree import BLOCK_TYPES as _ALLOWED_BY_KIND

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
    ordered: dict[str, Any] = {
        "course_language": course_language,
        "source_languages": sorted(set(source_languages or [])),
        "passages": [
            {**passage, "kind": passage_kind(str(passage.get("text", "")))} for passage in passages
        ],
        "profile": profile,
        "constraints": constraints,
        "prescription": prescription,
        "block_plan_by_methodology": block_plan,
        "block_types_by_topic_kind": {
            kind: [t for t in allowed if t in _REGISTRY_TYPES]
            for kind, allowed in _ALLOWED_BY_KIND.items()
        },
    }
    if scenario is not None:
        ordered["scenario"] = scenario
    if module_scope is not None:
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
    scenario: list[dict[str, Any]] | None = None,
) -> str:
    """Full architect prompt: the typed input (the system prompt travels as instructions)."""
    return assemble(
        build_dynamic(
            profile,
            constraints,
            prescription,
            passages,
            course_language,
            source_languages,
            module_scope=module_scope,
            scenario=scenario,
        )
    )


def check_output(
    payload: dict[str, Any],
    prescription: dict[str, list[str]],
    handles: set[str] | None = None,
    passages: list[dict[str, Any]] | None = None,
    module_scope: dict[str, Any] | None = None,
) -> list[str]:
    """Validate an architect reply in code; return error strings.

    With `module_scope` the reply is exactly the scoped module under its
    outline id, its lesson topics are the scope's topics in order (same
    ids and titles) plus at most one module test last, every topic
    hands over, and a lesson block cites only passages of its topic's
    sections (`passages[].topic`). When `handles` is given, a ref
    outside it is rejected; with `passages`, coverage is checked: the
    plan cites at least 60 % of the content passages or lists the rest
    in `uncovered` with reasons.
    """
    try:
        output = ArchitectOutput.model_validate(payload)
    except PydanticValidationError as exc:
        return [f"schema: {error['loc']}: {error['msg']}" for error in exc.errors()]
    errors = validate_tree(CourseTree(modules=output.modules), prescription)
    errors += _example_spec_errors(output)
    if module_scope is not None:
        wanted = str(module_scope.get("plan_node_id", ""))
        if len(output.modules) != 1 or output.modules[0].plan_node_id != wanted:
            errors.append(f"module scope: exactly one module with plan_node_id {wanted!r}")
        else:
            errors += _scope_topic_errors(output.modules[0], module_scope)
    errors += _handoff_errors(output, require_last=module_scope is not None)
    if passages:
        errors += _coverage_errors(output, passages)
        errors += _topic_section_errors(output, passages)
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
    output.estimate_inputs.blocks = block_count
    return errors


def _example_spec_errors(output: Any) -> list[str]:
    """An example the architect plans names its input, action and result on the case.

    Checked on the architect's reply only: a stored plan (author-edited
    or written before example specs existed) is never refused for it.
    """
    return [
        f"block {block.plan_node_id}: an example names its input, action and result"
        " on the course case"
        for module in output.modules
        for topic in module.topics
        for block in topic.blocks
        if block.role == "example" and not block.example_spec.complete()
    ]


def _scope_topic_errors(module: Any, module_scope: dict[str, Any]) -> list[str]:
    """The lesson topics are the scenario's, in order; one module test may close the module."""
    scoped = [
        (str(t.get("plan_node_id", "")), str(t.get("title", "")))
        for t in module_scope.get("topics", [])
        if isinstance(t, dict)
    ]
    if not scoped:
        return []
    lessons = [t for t in module.topics if t.kind == "lesson"]
    tests = [t for t in module.topics if t.kind == "module_test"]
    errors: list[str] = []
    if [t.plan_node_id for t in lessons] != [node for node, _ in scoped]:
        errors.append(
            f"module {module.plan_node_id}: lesson topics must be the scenario's"
            f" {[node for node, _ in scoped]} in order, got {[t.plan_node_id for t in lessons]}"
        )
    if len(tests) > 1 or (tests and module.topics[-1].kind != "module_test"):
        errors.append(f"module {module.plan_node_id}: at most one module test, placed last")
    return errors


def _topic_section_errors(output: Any, passages: list[dict[str, Any]]) -> list[str]:
    """A lesson block cites only passages of its topic's own sections."""
    owner = {str(p.get("handle", "")): str(p.get("topic", "")) for p in passages if p.get("topic")}
    if not owner:
        return []
    errors: list[str] = []
    for module in output.modules:
        for topic in module.topics:
            if topic.kind != "lesson":
                continue
            outside = sorted(
                {
                    ref.handle
                    for block in topic.blocks
                    for ref in block.source_refs
                    if owner.get(ref.handle, topic.plan_node_id) != topic.plan_node_id
                }
            )
            if outside:
                errors.append(
                    f"topic {topic.plan_node_id}: cites {outside} outside its own sections"
                )
    return errors


def _handoff_errors(output: Any, require_last: bool = False) -> list[str]:
    """One through-line handoff per topic boundary, or the plan is rejected.

    Topics in plan order: every topic except the last must hand the
    case over (through_line.handoffs naming that topic); a scoped
    module hands its last topic to the next module too.
    """
    topics = [topic for module in output.modules for topic in module.topics]
    if len(topics) < 2 and not require_last:
        return []
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
    """Whole-plan passage coverage, checked in code: cite 60 % or declare the rest."""
    errors: list[str] = []
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
    if content_handles:
        share = len(cited & content_handles) / len(content_handles)
        declared = {item.handle for item in output.uncovered if item.reason.strip()}
        missing = content_handles - cited - declared
        # a thin plan that forgot a handful of leftovers is accepted: the merge declares
        # them "not cited by any module's plan" (a $0.12 module reply was rejected over one)
        if share < 0.6 and len(missing) >= 2 and len(missing) > 0.1 * len(content_handles):
            errors.append(
                f"plan cites {round(share * 100)}% of content passages; "
                f"uncovered passages must be listed in uncovered: {sorted(missing)[:20]}"
            )
    return errors


def schema() -> dict[str, Any]:
    """The architect response schema (strict subset, startup-checked)."""
    return dict(AGENT_SCHEMAS["architect"])
