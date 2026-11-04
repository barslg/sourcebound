"""Repairs and checks of an exercise reply's key that code can do without asking the model again.

A writer that lists ordering steps shuffled while its `answer` states the order,
or marks a cloze gap with four underscores, is repaired here; a cloze whose
`blanks` repeat the ___ placeholder instead of the answers is reported, since
only the model knows the answers.
"""

from __future__ import annotations

import re
from typing import Any

_GAP_RE = re.compile(r"(?<![A-Za-z0-9])_{3,}(?![A-Za-z0-9])")
_PLACEHOLDER_RE = re.compile(r"\s*_+\s*")


def ordered_steps(steps: list[str], answer: str) -> list[str]:
    """The steps in the order the key's `answer` states them, when it names every step once."""
    text = answer.lower()
    positions = [text.find(step.lower().strip().rstrip(".")) for step in steps]
    if any(p < 0 for p in positions) or len(set(positions)) != len(positions):
        return steps
    return [step for _, step in sorted(zip(positions, steps, strict=True))]


def normalised_gaps(text: str) -> str:
    """Every cloze gap written as exactly ___, whatever run of underscores the writer used."""
    return _GAP_RE.sub("___", text)


def cloze_blank_holds(output: dict[str, Any]) -> list[str]:
    """A remark for a cloze whose `blanks` hold the placeholder or nothing, not the answers."""
    if str(output.get("block_type", "")) != "cloze":
        return []
    raw = output.get("blanks")
    blanks = [str(b) for b in raw] if isinstance(raw, list) else []
    if not blanks or any(_PLACEHOLDER_RE.fullmatch(b) for b in blanks):
        return [
            "cloze: `blanks` must hold the answers for the gaps in order, never the ___"
            " placeholder or an empty string"
        ]
    return []
