"""Spot the error: learner content is sentences only; the key stays server-side."""

from __future__ import annotations

from typing import Any


def validate(content: dict[str, Any]) -> list[str]:
    """Strict shape: at least two sentences; no key material in content."""
    errors: list[str] = []
    if "planted_index" in content or "kind" in content or "answer" in content:
        errors.append("content must not carry planted_index, kind, or answer key")
    sentences = content.get("sentences")
    if not isinstance(sentences, list) or len(sentences) < 2:
        errors.append("sentences must hold at least two strings")
    elif not all(isinstance(item, str) and item.strip() for item in sentences):
        errors.append("sentences must all be non-empty strings")
    return errors
