"""Reading block: taught passage text with verified citations."""

from __future__ import annotations

from typing import Any


def validate(content: dict[str, Any]) -> list[str]:
    """Strict shape: non-empty body plus at least one citation."""
    errors: list[str] = []
    if not isinstance(content.get("body"), str) or not content["body"].strip():
        errors.append("body must be a non-empty string")
    citations = content.get("citations")
    if not isinstance(citations, list) or not citations:
        errors.append("citations must be a non-empty list")
        return errors
    for index, citation in enumerate(citations):
        if not isinstance(citation, dict):
            errors.append(f"citations[{index}]: must be an object")
            continue
        if not citation.get("passage_id"):
            errors.append(f"citations[{index}]: passage_id is required")
        if not citation.get("quote"):
            errors.append(f"citations[{index}]: quote is required")
    return errors
