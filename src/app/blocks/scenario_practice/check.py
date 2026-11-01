"""Grading for scenario practice, by code with partial credit."""

from __future__ import annotations

from typing import Any


def _index_of(item: Any, texts: list[str]) -> int | None:
    if isinstance(item, int) or (isinstance(item, str) and str(item).isdigit()):
        return int(item)
    if str(item) in texts:
        return texts.index(str(item))
    return None


def _by_index(mapping: dict[Any, Any], texts: list[str]) -> dict[int, Any]:
    out: dict[int, Any] = {}
    for item, value in mapping.items():
        idx = _index_of(item, texts)
        if idx is not None:
            out[idx] = value
    return out


def grade(
    content: dict[str, Any],
    answer_key: dict[str, Any],
    submission: dict[str, Any],
) -> dict[str, Any]:
    """Fraction of items with the right tier and supporting passage.

    Comparison is by item index: translated keys (`labels_by_index`,
    `passages_by_index`) grade translated submissions exactly like the
    source; legacy text-keyed maps resolve through content.
    """
    _raw: Any = content.get("items")
    items: list[Any] = _raw if isinstance(_raw, list) else []
    texts = [str(it.get("text", "")) for it in items if isinstance(it, dict)]
    expected = _by_index(
        dict(answer_key.get("labels_by_index") or answer_key.get("labels", {})), texts
    )
    given = _by_index(dict(submission.get("labels", {})), texts)
    passages = _by_index(dict(submission.get("passages", {})), texts)
    expected_passages = _by_index(
        dict(answer_key.get("passages_by_index") or answer_key.get("passages", {})), texts
    )
    if not expected:
        return {"score": 0.0, "feedback": {"habit": ""}}
    hits = sum(
        1
        for item, tier in expected.items()
        if given.get(item) == tier and passages.get(item) == expected_passages.get(item)
    )
    return {
        "score": hits / len(expected),
        "feedback": {"habit": "Sort each use by its article citation."},
    }
