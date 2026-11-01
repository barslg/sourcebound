"""Grading for matching blocks, by code with partial credit."""

from __future__ import annotations

from typing import Any


def _by_index(mapping: dict[Any, Any], lefts: list[str], rights: list[str]) -> dict[int, int]:
    """Normalise a mapping to left-index -> right-index.

    Accepts index keys (translated keys), numeric values, or legacy
    display-text keys resolved through the block content.
    """
    out: dict[int, int] = {}
    for left, right in mapping.items():
        if isinstance(left, int) or (isinstance(left, str) and left.isdigit()):
            li = int(left)
        elif str(left) in lefts:
            li = lefts.index(str(left))
        else:
            continue
        if isinstance(right, int) or (isinstance(right, str) and str(right).isdigit()):
            ri = int(right)
        elif str(right) in rights:
            ri = rights.index(str(right))
        else:
            continue
        out[li] = ri
    return out


def grade(
    content: dict[str, Any],
    answer_key: dict[str, Any],
    submission: dict[str, Any],
) -> dict[str, Any]:
    """Fraction of correctly matched left items, by code.

    Keys are compared by item index: a translated key
    (`mapping_by_index`) grades a translated submission exactly like
    the source key grades the source submission. Legacy text mappings
    are resolved through content and keep working.
    """
    _raw: Any = content.get("pairs")
    pairs: list[Any] = _raw if isinstance(_raw, list) else []
    lefts = [str(p.get("left", "")) for p in pairs if isinstance(p, dict)]
    rights = [str(p.get("right", "")) for p in pairs if isinstance(p, dict)]
    raw_expected = answer_key.get("mapping_by_index") or answer_key.get("mapping", {})
    raw_given = submission.get("mapping_by_index") or submission.get("mapping", {})
    expected = _by_index(dict(raw_expected), lefts, rights)
    given = _by_index(dict(raw_given), lefts, rights)
    if not expected:
        return {"score": 0.0, "feedback": {"habit": ""}}
    hits = sum(1 for left, right in expected.items() if given.get(left) == right)
    score = hits / len(expected)
    return {
        "score": float(score),
        "feedback": {"habit": "Match each item to its passage, not by word shape."},
    }
