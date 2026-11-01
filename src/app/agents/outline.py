"""Outline: profile plus source sections in, modules with their sections out.

The first of two planning levels for a big source set: the model sees
section titles, sizes and previews (never passage texts), names the
modules in teaching order and assigns every section to one module or
to `uncovered` with a reason. Each module is then planned by the
architect over its own passages.
"""

from __future__ import annotations

import json
from typing import Any

from app.agents.base import AGENT_SCHEMAS, assemble, system_prompt


def build_prompt(
    profile: dict[str, Any],
    constraints: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    course_language: str = "en",
) -> str:
    """Full outline prompt: stable system prefix plus the typed section list."""
    dynamic = json.dumps(
        {
            "course_language": course_language,
            "profile": profile,
            "constraints": constraints,
            "sections": sections,
        },
        ensure_ascii=False,
    )
    return assemble(system_prompt("outline"), dynamic)


def check_output(payload: dict[str, Any], section_ids: list[str]) -> list[str]:
    """Validate an outline reply in code: every section placed once, ids known, modules distinct."""
    errors: list[str] = []
    known = list(section_ids)
    modules = payload.get("modules")
    if not isinstance(modules, list) or not modules:
        return ["modules must be a non-empty list"]
    seen_sections: dict[str, str] = {}
    seen_modules: set[str] = set()
    for position, module in enumerate(modules):
        if not isinstance(module, dict):
            errors.append(f"modules[{position}]: must be an object")
            continue
        node = str(module.get("plan_node_id", ""))
        if not node or node in seen_modules:
            errors.append(f"modules[{position}]: plan_node_id must be unique and non-empty")
        seen_modules.add(node)
        for key in ("title", "purpose", "entry_knowledge", "exit_knowledge"):
            if not str(module.get(key, "")).strip():
                errors.append(f"modules[{position}]: {key} is empty")
        sections = module.get("sections")
        if not isinstance(sections, list) or not sections:
            errors.append(f"modules[{position}]: needs at least one section")
            continue
        for section in sections:
            section = str(section)
            if section not in known:
                errors.append(f"modules[{position}]: unknown section {section!r}")
            elif section in seen_sections:
                errors.append(
                    f"modules[{position}]: section {section} already placed"
                    f" in {seen_sections[section]}"
                )
            seen_sections[section] = node
    declared = {
        str(item.get("section", ""))
        for item in payload.get("uncovered", [])
        if isinstance(item, dict) and str(item.get("reason", "")).strip()
    }
    missing = [s for s in known if s not in seen_sections and s not in declared]
    if missing:
        errors.append(
            "every section is placed in a module or listed in uncovered with a reason;"
            f" missing: {missing[:20]}"
        )
    return errors


def schema() -> dict[str, Any]:
    """The outline response schema (strict subset, startup-checked)."""
    return dict(AGENT_SCHEMAS["outline"])
