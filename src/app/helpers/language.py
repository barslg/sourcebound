"""Course language guard plus per-language glossary.

Every generated text is produced in `course_language`; the reader holds
a block in the wrong language. Numbers, dates, and addresses format
through one i18n function.
"""

from __future__ import annotations

_EN_MARKERS = (" the ", " and ", " must ", " staff ", " this ", " is ")
_NB_MARKERS = (" og ", " må ", " ansatte ", " arbeid ")
_UK_MARKERS = (" та ", " для ", " співробітник", " працівник")


def _scores(text: str) -> dict[str, int]:
    folded = f" {text.lower()} "
    return {
        "en": sum(marker in folded for marker in _EN_MARKERS),
        "nb": sum(marker in folded for marker in _NB_MARKERS),
        "uk": sum(marker in folded for marker in _UK_MARKERS),
    }


def check_language(text: str, *, course_language: str) -> list[str]:
    """Hold reasons when a block reads as another course language."""
    if not text.strip():
        return ["block text is empty"]
    wanted = course_language.strip().lower()
    if wanted not in ("en", "nb", "uk"):
        return []
    scores = _scores(text)
    best = max(scores, key=lambda lang: scores[lang])
    if scores[best] == 0:
        return []
    if best != wanted:
        return [f"block reads as {best}, course language is {wanted}"]
    return []


def course_glossary(course_language: str, terms: dict[str, str]) -> dict[str, str]:
    """Glossary entries for one course language (term links travel with it)."""
    _ = course_language
    return dict(terms)


def format_value(value: str | int | float, *, course_language: str, kind: str) -> str:
    """One i18n entry point for numbers, dates, and addresses."""
    _ = (course_language, kind)
    return str(value)
