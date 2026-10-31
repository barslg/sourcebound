"""Saboteur: rewrites only non-numeric mutation kinds.

Code owns number/date swaps and the answer key; the model rewrites
negation, scope, entity swaps, cause reversals, and bad citations.
"""

from __future__ import annotations

import json
from typing import Any

from app.agents.base import AGENT_SCHEMAS, assemble, system_prompt
from app.course.errorspot import MUTATION_KINDS, rewrite_allowed


def build_saboteur_prompt(
    *, sentence: str, kind: str, course_language: str = "en", hold_reason: str = ""
) -> str:
    """Typed saboteur prompt; number/date kinds are refused in code.

    A rewrite or rework passes the reader's hold reason (bounded) so
    the new error does not repeat what the reader rejected.
    """
    if kind not in MUTATION_KINDS:
        raise ValueError(f"unknown mutation kind: {kind}")
    if not rewrite_allowed(kind):
        raise ValueError("number/date swaps are owned by code, never the model")
    task: dict[str, str] = {"course_language": course_language, "sentence": sentence, "kind": kind}
    note = " ".join(hold_reason.split())[:500]
    if note:
        task["reader_note"] = note
    return assemble(
        system_prompt("saboteur"),
        json.dumps(task, ensure_ascii=False, sort_keys=True),
    )


def check_saboteur_output(payload: dict[str, Any]) -> list[str]:
    """Validate a saboteur reply in code; return error strings."""
    errors: list[str] = []
    if not str(payload.get("sentence", "")).strip():
        errors.append("sentence is required")
    if payload.get("kind") not in MUTATION_KINDS:
        errors.append(f"kind must be one of {sorted(MUTATION_KINDS)}")
    if not str(payload.get("mutated", "")).strip():
        errors.append("mutated is required")
    if str(payload.get("mutated", "")) == str(payload.get("sentence", "")):
        errors.append("mutated must differ from the sentence")
    return errors


def schema() -> dict[str, Any]:
    """The saboteur response schema (strict subset, startup-checked)."""
    return dict(AGENT_SCHEMAS["saboteur"])
