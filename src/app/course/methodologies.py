"""Methodology registry: declared in code, synced to the DB at startup.

Each methodology prescribes block roles per topic kind; the architect
and validate_tree() honour the prescription. T03 seeds the generic
two; T05 adds the full Merrill pipeline with its seven block types.
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
        "name": "Merrill's First Principles (T03 skeleton)",
        "description": "Activation, demonstration, application, integration; full pipeline in T05.",
        "compatible_with": ["apply", "analyze"],
        "prescription": {
            "lesson": ["teach", "example", "practice", "check"],
            "module_test": ["test"],
        },
    },
)


def prescription_for(codes: list[str]) -> dict[str, list[str]]:
    """Merged role prescription for the chosen methodologies.

    The first methodology that prescribes a topic kind wins for that
    kind, so mixed profiles stay deterministic.
    """
    merged: dict[str, list[str]] = {}
    wanted = list(codes)
    for entry in METHODOLOGIES:
        if entry["code"] not in wanted:
            continue
        prescription = entry["prescription"]
        assert isinstance(prescription, dict)
        for kind, roles in prescription.items():
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
