"""Translated answer keys: keys by segment id / item index, never text.

A translated exercise must grade a correct translated answer exactly
like the source. Display-text keys (matching mapping, scenario labels,
cloze blanks, spot planted/true sentences) are rebuilt here from the
segment translations: matching/scenario become index-keyed, cloze
blanks and the spot sentences carry their translated strings.
"""

from __future__ import annotations

from typing import Any


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[Any, Any]:
    return value if isinstance(value, dict) else {}


def build_translated_answer_key(
    block_type: str,
    content: dict[str, Any],
    answer_key: dict[str, Any],
    translations: dict[str, str],
    translated_content: dict[str, Any],
) -> dict[str, Any]:
    """The answer key for one translated block (stored per translation)."""
    key: dict[str, Any] = {}
    if answer_key.get("passage") is not None:
        key["passage"] = answer_key["passage"]
    if block_type == "choice":
        if "correct" in answer_key:
            key["correct"] = answer_key["correct"]
        return key
    if block_type == "matching":
        pairs = _list(content.get("pairs"))
        lefts = [str(p.get("left", "")) for p in pairs if isinstance(p, dict)]
        rights = [str(p.get("right", "")) for p in pairs if isinstance(p, dict)]
        stored = _dict(answer_key.get("mapping_by_index"))
        mapping = _dict(answer_key.get("mapping"))
        by_index: dict[str, int] = {
            str(left): int(right)
            for left, right in stored.items()
            if str(left).isdigit() and str(right).isdigit()
        }
        for left, right in mapping.items():
            if not by_index and str(left) in lefts and str(right) in rights:
                by_index[str(lefts.index(str(left)))] = rights.index(str(right))
        if not by_index:  # pairs are already matched in order
            by_index = {str(i): i for i in range(len(pairs))}
        key["mapping_by_index"] = by_index
        return key
    if block_type == "cloze":
        blanks = []
        source_blanks = _list(answer_key.get("blanks"))
        for i in range(len(source_blanks)):
            blanks.append(translations.get(f"key:blanks[{i}]", str(source_blanks[i])))
        key["blanks"] = blanks
        return key
    if block_type == "scenario_practice":
        items = _list(content.get("items"))
        texts = [str(it.get("text", "")) for it in items if isinstance(it, dict)]
        labels = _dict(answer_key.get("labels"))
        passages = _dict(answer_key.get("passages"))
        labels_by_index = {
            str(k): str(v) for k, v in _dict(answer_key.get("labels_by_index")).items()
        }
        passages_by_index = {
            str(k): str(v) for k, v in _dict(answer_key.get("passages_by_index")).items()
        }
        for item, tier in labels.items():
            if str(item) in texts:
                labels_by_index.setdefault(str(texts.index(str(item))), str(tier))
        for item, pid in passages.items():
            if str(item) in texts:
                passages_by_index.setdefault(str(texts.index(str(item))), str(pid))
        key["labels_by_index"] = labels_by_index
        key["passages_by_index"] = passages_by_index
        return key
    if block_type == "spot_the_error":
        sentences = _list(translated_content.get("sentences"))
        planted_index = answer_key.get("planted_index")
        key["planted_index"] = planted_index
        key["kind"] = answer_key.get("kind", "negation")
        key["fully_correct"] = bool(answer_key.get("fully_correct", False))
        if isinstance(planted_index, int) and 0 <= planted_index < len(sentences):
            key["planted_sentence"] = str(sentences[planted_index])
        elif answer_key.get("planted_sentence") is not None:
            key["planted_sentence"] = translations.get(
                f"sentences[{planted_index}]", str(answer_key["planted_sentence"])
            )
        key["true_sentence"] = translations.get(
            "key:true_sentence", str(answer_key.get("true_sentence", ""))
        )
        return key
    # reading / figure: acknowledgement keys carry no display text.
    for field in ("source_numbers", "answer"):
        if field in answer_key:
            key[field] = answer_key[field]
    return key
