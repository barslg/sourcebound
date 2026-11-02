"""Translator: plain segments in, translated segments out.

Code owns everything structural: segment extraction, the glossary term
table, re-assembly, and every check. The model translates the `text`
of each segment id exactly once. Answer keys, citations, markers, and
the planted-error flag never reach the prompt.
"""

from __future__ import annotations

import json
from typing import Any

from app.agents.base import AGENT_SCHEMAS, assemble


def build_translator_prompt(
    *,
    segments: list[dict[str, str]],
    source_language: str,
    target_language: str,
    glossary: dict[str, str],
    course_id: str = "",
) -> str:
    """Typed translator prompt; structural fields never enter it."""
    if not segments:
        raise ValueError("translator needs at least one segment")
    for segment in segments:
        if not segment.get("id") or not str(segment.get("text", "")).strip():
            raise ValueError("every segment needs an id and non-empty text")
    dynamic = "\n\n## ".join(
        [
            "Languages\n"
            + json.dumps(
                {"source_language": source_language, "target_language": target_language},
                ensure_ascii=False,
                sort_keys=True,
            ),
            "Glossary\n" + json.dumps(glossary, ensure_ascii=False, sort_keys=True),
            "Segments\n"
            + json.dumps(
                [{"id": seg["id"], "text": seg["text"]} for seg in segments],
                ensure_ascii=False,
            ),
        ]
    )
    prompt = assemble(dynamic)
    _ = course_id
    return prompt


def build_glossary_prompt(
    *,
    terms: dict[str, str],
    source_language: str,
    target_language: str,
) -> str:
    """Glossary-first pass: translate bare terms before any block."""
    if not terms:
        raise ValueError("glossary pass needs at least one term")
    segments = [{"id": term, "text": term} for term in sorted(terms)]
    return assemble(
        "\n\n## ".join(
            [
                "Languages\n"
                + json.dumps(
                    {
                        "source_language": source_language,
                        "target_language": target_language,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "Terms\n" + json.dumps(terms, ensure_ascii=False, sort_keys=True),
                "Segments\n" + json.dumps(segments, ensure_ascii=False),
            ]
        ),
    )


def check_translator_output(payload: dict[str, Any], *, expected_ids: set[str]) -> list[str]:
    """Validate a translator reply in code; return error strings."""
    errors: list[str] = []
    segments = payload.get("segments")
    if not isinstance(segments, list) or not segments:
        return ["segments must be a non-empty list"]
    seen: list[str] = []
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            errors.append(f"segments[{index}]: must be an object")
            continue
        if not segment.get("id"):
            errors.append(f"segments[{index}]: id is required")
        if not isinstance(segment.get("text"), str) or not segment["text"].strip():
            errors.append(f"segments[{index}]: text must be non-empty")
        seen.append(str(segment.get("id", "")))
    if sorted(seen) != sorted(expected_ids):
        errors.append(f"segment ids {sorted(seen)} do not match {sorted(expected_ids)}")
    if len(set(seen)) != len(seen):
        errors.append("segment ids must be unique")
    return errors


def schema() -> dict[str, Any]:
    """The translator response schema (strict subset, startup-checked)."""
    return dict(AGENT_SCHEMAS["translator"])
