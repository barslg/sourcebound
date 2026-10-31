"""Code checks on translated segments and re-assembled blocks.

Every number, placeholder, list length, and the planted-error invariant
is verified here, never trusted from the translator reply.
"""

from __future__ import annotations

import re
from typing import Any

from app.course.errorspot import MUTATION_KINDS

_GAP = "___"


def numbers_of(text: str) -> list[str]:
    """Number tokens of one string (translation must not change them)."""
    return re.findall(r"\d+(?:[.,]\d+)*", text)


def check_segment_pair(source: str, translated: str) -> list[str]:
    """Per-segment checks: non-empty, placeholders kept, numbers unchanged."""
    errors: list[str] = []
    if not source.strip():
        errors.append("source segment is empty")
        return errors
    if not translated.strip():
        errors.append("translated segment is empty")
        return errors
    if source.count(_GAP) != translated.count(_GAP):
        errors.append(f"gap markers changed: {source.count(_GAP)} vs {translated.count(_GAP)}")
    if numbers_of(source) != numbers_of(translated):
        errors.append(f"numbers changed: {numbers_of(source)} vs {numbers_of(translated)}")
    return errors


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _numbers_in(value: Any) -> list[str]:
    """Every number token under any content shape, in traversal order."""
    if isinstance(value, str):
        return numbers_of(value)
    if isinstance(value, dict):
        found: list[str] = []
        for item in value.values():
            found += _numbers_in(item)
        return found
    if isinstance(value, list):
        found = []
        for item in value:
            found += _numbers_in(item)
        return found
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [str(value)]
    return []


def verify_block_translation(
    block_type: str, source: dict[str, Any], translated: dict[str, Any]
) -> list[str]:
    """Structure parity between source and re-assembled translated content."""
    errors: list[str] = []
    if sorted(_numbers_in(source)) != sorted(_numbers_in(translated)):
        errors.append("numbers changed in translation")
    if block_type == "choice":
        if len(_as_list(translated.get("options"))) != len(_as_list(source.get("options"))):
            errors.append("option count changed in translation")
    elif block_type == "matching":
        if len(_as_list(translated.get("pairs"))) != len(_as_list(source.get("pairs"))):
            errors.append("pair count changed in translation")
    elif block_type == "scenario_practice":
        if len(_as_list(translated.get("items"))) != len(_as_list(source.get("items"))):
            errors.append("item count changed in translation")
    elif block_type == "spot_the_error":
        if len(_as_list(translated.get("sentences"))) != len(_as_list(source.get("sentences"))):
            errors.append("sentence count changed in translation")
    for key in ("body", "stem", "text", "scenario", "caption"):
        if key in source and not str(translated.get(key, "")).strip():
            errors.append(f"{key} translated to empty")
    return errors


def check_glossary_use(
    segments: list[dict[str, str]],
    translated: dict[str, str],
    glossary: dict[str, str],
) -> list[str]:
    """Every glossary source term present must surface as its target term.

    The target term matches case-insensitively, or by its 6-character
    stem so inflected forms (anvenderen, kompetansen) still count.
    """
    errors: list[str] = []
    for segment in segments:
        source_text = segment["text"]
        target_text = translated.get(segment["id"], "")
        for source_term, target_term in glossary.items():
            if source_term.lower() not in source_text.lower():
                continue
            folded = target_text.lower()
            wanted = target_term.lower()
            if wanted not in folded and wanted[:6] not in folded:
                errors.append(
                    f"segment {segment['id']}: glossary term {target_term!r} missing in translation"
                )
    return errors


def verify_spot_error(
    source: dict[str, Any], translated: dict[str, Any], answer_key: dict[str, Any]
) -> list[str]:
    """Exactly one planted sentence of the same kind must remain.

    Code re-derives the planted sentence by index (the flag never left
    code): the translated planted sentence must differ from the
    translated true sentence, the kind must be unchanged, and
    kind-specific markers are re-checked where the target language
    cannot move them (numbers, citation brackets).
    """
    errors: list[str] = []
    source_sentences = _as_list(source.get("sentences"))
    translated_sentences = _as_list(translated.get("sentences"))
    if len(translated_sentences) != len(source_sentences) or len(source_sentences) < 2:
        return ["sentence count changed in translation"]
    if translated.get("kind") != source.get("kind"):
        return [f"mutation kind changed: {source.get('kind')} vs {translated.get('kind')}"]
    kind = str(source.get("kind", ""))
    if kind not in MUTATION_KINDS:
        return [f"unknown mutation kind: {kind}"]
    planted_index = source.get("planted_index")
    if (
        not isinstance(planted_index, int)
        or not 0 <= planted_index < len(translated_sentences)
        or translated.get("planted_index") != planted_index
    ):
        return ["planted index moved in translation"]
    planted_text = str(translated_sentences[planted_index])
    companions = [
        str(sentence)
        for index, sentence in enumerate(translated_sentences)
        if index != planted_index
    ]
    if planted_text in companions:
        return ["planted error lost in translation: no sentence differs"]
    if kind == "number_date":
        planted_numbers = numbers_of(planted_text)
        if any(numbers_of(other) == planted_numbers for other in companions):
            errors.append("planted number/date error lost in translation")
    elif kind == "bad_citation":
        if planted_text.count("[") < 1 or all("[" in other for other in companions):
            errors.append("planted citation flag lost in translation")
    _ = answer_key
    return errors
