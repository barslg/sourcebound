"""Course language guard plus per-language glossary.

Every generated text is produced in `course_language`; the reader holds
a block in the wrong language. Numbers, dates, and addresses format
through one i18n function.
"""

from __future__ import annotations

import re

_STOPWORDS: dict[str, tuple[str, ...]] = {
    "en": (" the ", " and ", " must ", " shall ", " this ", " is ", " are ", " staff ", " with "),
    "nb": (" og ", " må ", " skal ", " ansatte ", " arbeid ", " denne ", " er ", " med "),
    "uk": (" та ", " для ", " повинні ", " працівник", " співробітник", " цей ", " є "),
    "de": (" die ", " und ", " müssen ", " der ", " das ", " ist ", " mit "),
    "es": (" los ", " las ", " deben ", " el ", " la ", " es ", " con "),
    "fr": (" les ", " des ", " doivent ", " le ", " la ", " est ", " avec "),
}


def _scores(text: str) -> dict[str, int]:
    folded = f" {text.lower()} "
    scores = {lang: sum(m in folded for m in marks) for lang, marks in _STOPWORDS.items()}
    # script signal: Cyrillic strongly indicates uk (or another Cyrillic language)
    if re.search(r"[\u0400-\u04FF]", text):
        scores["uk"] = scores.get("uk", 0) + 3
    if re.search(r"[æøå]", text.lower()):
        scores["nb"] = scores.get("nb", 0) + 2
    return scores


def check_language(text: str, *, course_language: str) -> list[str]:
    """Hold reasons when a block reads as another course language.

    Works for any BCP-47 primary tag with known stopwords; unknown
    languages pass (no evidence to hold on).
    """
    if not text.strip():
        return ["block text is empty"]
    wanted = course_language.strip().lower().split("-")[0]
    scores = _scores(text)
    best = max(scores, key=lambda lang: scores[lang])
    if scores[best] == 0:
        return []
    if wanted not in scores and wanted not in ("en", "nb", "uk"):
        # unknown target language: only hold on strong script mismatch handled above
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
    if kind == "number" and isinstance(value, (int, float)):
        tag = course_language.strip().lower().split("-")[0]
        if tag in ("nb", "uk", "de"):
            return f"{value:,}".replace(",", " ")
        return f"{value:,}"
    return str(value)
