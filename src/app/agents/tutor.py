"""Tutor: picks verbatim fragments of retrieved passages; code decides the rest.

The model never writes prose for the learner. Code retrieves the
passages (FTS, this course only), shows them under handles P1..Pn, and
keeps only the quotes that are verbatim in their passage.
"""

from __future__ import annotations

import json
from typing import Any

from app.agents.base import AGENT_SCHEMAS, assemble, system_prompt


def build_prompt(*, question: str, passages: list[dict[str, str]], course_language: str) -> str:
    """Typed tutor prompt: the question and handle-addressed passage texts."""
    return assemble(
        system_prompt("tutor"),
        json.dumps(
            {"course_language": course_language, "question": question, "passages": passages},
            ensure_ascii=False,
            sort_keys=True,
        ),
    )


MIN_QUOTE_WORDS = 6
MIN_QUOTE_CHARS = 40
_BOUNDARY = '.;:!?,)\u2014\u2013"\u201c\u201d'


def check_output(payload: dict[str, Any]) -> list[str]:
    """Shape check of a tutor reply.

    Unknown handles and bad items are not shape errors: code drops them
    after the (paid) call, so one bad item never costs the learner the
    answer. Verbatim verification happens against stored text.
    """
    if not isinstance(payload.get("quotes"), list):
        return ["quotes must be a list"]
    return []


def accept_quote(quote: str, passage_text: str) -> bool:
    """True when the quote is a verbatim, clause-aligned fragment of the passage.

    A fragment is long enough to carry a claim (>= 6 words or >= 40
    characters) and starts and ends on sentence or clause punctuation (or
    the passage edge), so a one-word or mid-clause substring never passes
    as a cited answer.
    """
    text = quote.strip()
    if len(text.split()) < MIN_QUOTE_WORDS and len(text) < MIN_QUOTE_CHARS:
        return False
    start = passage_text.find(text)
    while start != -1:
        end = start + len(text)
        before = passage_text[:start].rstrip()
        after = passage_text[end:].lstrip()
        opens = not before or before[-1] in _BOUNDARY
        closes = not after or text[-1] in _BOUNDARY or after[0] in _BOUNDARY
        if opens and closes:
            return True
        start = passage_text.find(text, start + 1)
    return False


def schema() -> dict[str, Any]:
    """The tutor response schema (strict subset, startup-checked)."""
    return dict(AGENT_SCHEMAS["tutor"])
