"""Outline (scenario, planning layer 1): profile plus source sections in, annotated topics out.

The model sees section titles, sizes, kinds and previews (never passage
texts) and writes the whole course as one short list: the learner, the
one case the course builds, modules in teaching order and, under each,
topics with a two-sentence annotation, the terms they introduce, what
they prepare the next topic for, the earlier topics they require and
the sections they cover. Every section is placed in exactly one topic
or listed in `uncovered` with a reason; questions whose answer changes
the course go to the author before anything is generated. The
architect then plans each module's blocks over this list.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from app.agents.base import AGENT_SCHEMAS, assemble
from app.course.tree import ScenarioOutput


def build_prompt(
    profile: dict[str, Any],
    constraints: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    course_language: str = "en",
) -> str:
    """Typed outline input: language, profile, answered constraints, section list."""
    dynamic = json.dumps(
        {
            "course_language": course_language,
            "profile": profile,
            "constraints": constraints,
            "sections": sections,
        },
        ensure_ascii=False,
    )
    return assemble(dynamic)


def normalise_ids(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop a restated module and give every colliding id a fresh one; ids are code's.

    The 2026-11-03 Python course outline restated three of its seven
    modules at its end and was rejected for the repeated ids after
    $0.86. A verbatim repeat (same id, same sections) is dropped; a
    module or topic whose id an earlier one already carries is renamed
    by position (m<n>, m<n>-t<k>); every `requires` entry is mapped to
    the latest earlier topic that carried the old id. Ids the model
    kept unique stay as written. Caller: runs/executor.py (the outline
    check and the stored scenario).
    """
    import copy

    out = copy.deepcopy(payload)
    modules = out.get("modules")
    if not isinstance(modules, list):
        return out
    seen: set[tuple[str, tuple[str, ...]]] = set()
    kept: list[Any] = []
    for module in modules:
        if isinstance(module, dict):
            signature = (
                str(module.get("plan_node_id", "")),
                tuple(str(x) for x in module.get("sections", []) or []),
            )
            if signature in seen:
                continue
            seen.add(signature)
        kept.append(module)
    modules[:] = kept
    module_ids: set[str] = set()
    topic_ids: set[str] = set()
    old_to_new: dict[str, str] = {}
    for m_index, module in enumerate(modules):
        if not isinstance(module, dict):
            continue
        module_id = str(module.get("plan_node_id", "")).strip()
        if not module_id or module_id in module_ids:
            module_id = f"m{m_index + 1}"
            while module_id in module_ids:
                module_id += "x"
        module_ids.add(module_id)
        module["plan_node_id"] = module_id
        topics = module.get("topics")
        if not isinstance(topics, list):
            continue
        for t_index, topic in enumerate(topics):
            if not isinstance(topic, dict):
                continue
            old_id = str(topic.get("plan_node_id", "")).strip()
            new_id = old_id
            if not new_id or new_id in topic_ids:
                new_id = f"{module_id}-t{t_index + 1}"
                while new_id in topic_ids:
                    new_id += "x"
            requires = topic.get("requires")
            if isinstance(requires, list):
                topic["requires"] = [old_to_new.get(str(r), str(r)) for r in requires]
            old_to_new[old_id] = new_id
            topic_ids.add(new_id)
            topic["plan_node_id"] = new_id
    return out


def check_output(payload: dict[str, Any], section_ids: list[str]) -> list[str]:
    """Validate an outline reply in code (ids renumbered first; see normalise_ids).

    Every section is placed in one module and, inside it, in one topic
    (or declared uncovered); topic ids are unique; `requires` names
    only earlier topics; question keys are unique.
    """
    try:
        parsed = ScenarioOutput.model_validate(normalise_ids(payload))
    except PydanticValidationError as exc:
        return [f"schema: {error['loc']}: {error['msg']}" for error in exc.errors()]
    errors: list[str] = []
    known = list(section_ids)
    seen_sections: dict[str, str] = {}
    seen_modules: set[str] = set()
    seen_topics: list[str] = []
    for position, module in enumerate(parsed.modules):
        node = module.plan_node_id
        if node in seen_modules:
            errors.append(f"modules[{position}]: plan_node_id {node!r} is not unique")
        seen_modules.add(node)
        module_sections = list(module.sections)
        for section in module_sections:
            if section not in known:
                errors.append(f"module {node}: unknown section {section!r}")
            elif section in seen_sections:
                errors.append(
                    f"module {node}: section {section} already placed in {seen_sections[section]}"
                )
            seen_sections[section] = node
        covered: dict[str, str] = {}
        for topic in module.topics:
            if topic.plan_node_id in seen_topics:
                errors.append(f"topic {topic.plan_node_id}: plan_node_id is not unique")
            for required in topic.requires:
                if required not in seen_topics:
                    errors.append(
                        f"topic {topic.plan_node_id}: requires {required!r}, which is not an"
                        " earlier topic"
                    )
            seen_topics.append(topic.plan_node_id)
            for section in topic.sections:
                if section not in module_sections:
                    errors.append(
                        f"topic {topic.plan_node_id}: section {section} is not one of module"
                        f" {node}'s sections"
                    )
                elif section in covered:
                    errors.append(
                        f"topic {topic.plan_node_id}: section {section} already covered by"
                        f" topic {covered[section]}"
                    )
                covered[section] = topic.plan_node_id
        orphans = [s for s in module_sections if s not in covered and s in known]
        if orphans:
            errors.append(f"module {node}: sections {orphans} belong to no topic")
    declared = {
        str(item.get("section", ""))
        for item in parsed.uncovered
        if str(item.get("reason", "")).strip()
    }
    missing = [s for s in known if s not in seen_sections and s not in declared]
    if missing:
        errors.append(
            "every section is placed in a module or listed in uncovered with a reason;"
            f" missing: {missing[:20]}"
        )
    keys = [question.key for question in parsed.questions]
    if len(set(keys)) != len(keys):
        errors.append("question keys must be unique")
    return errors


def scenario_of(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Topic plan_node_id -> its scenario row (annotation, terms, prepares_for, requires)."""
    out: dict[str, dict[str, Any]] = {}
    for module in payload.get("modules", []):
        if not isinstance(module, dict):
            continue
        for topic in module.get("topics", []):
            if not isinstance(topic, dict):
                continue
            out[str(topic.get("plan_node_id", ""))] = {
                "module": str(module.get("plan_node_id", "")),
                "title": str(topic.get("title", "")),
                "annotation": str(topic.get("annotation", "")),
                "introduces_terms": [str(t) for t in topic.get("introduces_terms", [])],
                "prepares_for": str(topic.get("prepares_for", "")),
                "requires": [str(r) for r in topic.get("requires", [])],
                "sections": [str(s) for s in topic.get("sections", [])],
            }
    return out


def schema() -> dict[str, Any]:
    """The outline response schema (strict subset, startup-checked)."""
    return dict(AGENT_SCHEMAS["outline"])
