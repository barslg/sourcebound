"""Cloze block: gapped text; grading by code against the key."""

from __future__ import annotations

from typing import Any


def validate(content: dict[str, Any]) -> list[str]:
    """Strict shape: gapped text plus at least one blank."""
    errors: list[str] = []
    if not isinstance(content.get("text"), str) or "___" not in content["text"]:
        errors.append("text must hold at least one ___ gap")
    blanks = content.get("blanks")
    if not isinstance(blanks, list) or not blanks:
        errors.append("blanks must be a non-empty list")
    return errors
