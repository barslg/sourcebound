"""Methodology registry: declared in code, synced to the DB at startup.

Merrill's First Principles is the demo path: Activation (one choice
pre-question on the case), Demonstration (reading, figure only when the
passage holds data), Application by Bloom target (cloze/matching for
remember-understand, scenario_practice for apply-analyze), Integration
(spot_the_error). The module final test is choice + matching + one
spot_the_error. Backward Design and Kolb are registered so the compiler
can expand them; the demo need not run them.
"""

from __future__ import annotations

from typing import Any

METHODOLOGIES: tuple[dict[str, Any], ...] = (
    {
        "code": "direct_instruction",
        "name": "Direct instruction",
        "description": "Teach, show an example, then check understanding per topic.",
        "compatible_with": ["remember", "understand"],
        "prescription": {
            "lesson": ["teach", "example", "check"],
            "module_test": ["test"],
        },
    },
    {
        "code": "merrill_first_principles",
        "name": "Merrill's First Principles",
        "description": (
            "Activation, demonstration, application, integration per topic;"
            " module test of choice, matching, and one spot_the_error."
        ),
        "compatible_with": ["remember", "understand", "apply", "analyze"],
        "pipeline": "merrill",
        "prescription": {
            "lesson": ["teach", "example", "check", "practice", "practice"],
            "module_test": ["test", "test", "test"],
            "lesson_blocks": {
                "activation": [["teach", "choice"]],
                "demonstration": [["teach", "reading"]],
                "demonstration_data": [["teach", "reading"], ["teach", "figure"]],
                "application_low": [["practice", "cloze"], ["practice", "matching"]],
                "application_high": [
                    ["practice", "scenario_practice"],
                    ["check", "cloze"],
                ],
                "integration": [["practice", "spot_the_error"]],
                "example": [["example", "reading"]],
            },
            "module_test_blocks": [
                ["test", "choice"],
                ["test", "matching"],
                ["test", "spot_the_error"],
            ],
        },
    },
    {
        "code": "backward_design",
        "name": "Backward Design",
        "description": "Outcomes first, then evidence, then learning plan per topic.",
        "compatible_with": ["apply", "evaluate"],
        "pipeline": "backward_design",
        "prescription": {
            "lesson": ["teach", "practice", "check"],
            "module_test": ["test", "test"],
        },
    },
    {
        "code": "kolb_experiential",
        "name": "Kolb Experiential Learning",
        "description": "Concrete experience, reflection, concepts, active try-out.",
        "compatible_with": ["apply", "analyze"],
        "pipeline": "kolb",
        "prescription": {
            "lesson": ["teach", "example", "practice", "check"],
            "module_test": ["test", "test"],
        },
    },
)


def methodology_codes() -> list[str]:
    """Every registered methodology code."""
    return [str(entry["code"]) for entry in METHODOLOGIES]


def _is_role_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def prescription_for(codes: list[str]) -> dict[str, list[str]]:
    """Merged role prescription for the chosen methodologies.

    The first methodology that prescribes a topic kind wins for that
    kind, so mixed profiles stay deterministic. Only plain role lists
    count; detailed block maps (lesson_blocks, module_test_blocks)
    are compiler input, not role contracts.
    """
    merged: dict[str, list[str]] = {}
    wanted = list(codes)
    for entry in METHODOLOGIES:
        if entry["code"] not in wanted:
            continue
        prescription = entry["prescription"]
        assert isinstance(prescription, dict)
        for kind, roles in prescription.items():
            if kind not in ("lesson", "module_test") or not _is_role_list(roles):
                continue
            merged.setdefault(kind, list(roles))
    return merged


async def sync_methodologies(session: Any) -> None:
    """Upsert every declared methodology; never delete author rows."""
    import json

    import sqlalchemy as sa

    for entry in METHODOLOGIES:
        await session.execute(
            sa.text(
                "INSERT INTO methodologies (code, name, description, compatible_with, prescription)"
                " VALUES (:code, :name, :description,"
                " CAST(:compatible AS jsonb), CAST(:prescription AS jsonb))"
                " ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name,"
                " description = EXCLUDED.description,"
                " compatible_with = EXCLUDED.compatible_with,"
                " prescription = EXCLUDED.prescription"
            ),
            {
                "code": entry["code"],
                "name": entry["name"],
                "description": entry["description"],
                "compatible": json.dumps(entry["compatible_with"]),
                "prescription": json.dumps(entry["prescription"]),
            },
        )
