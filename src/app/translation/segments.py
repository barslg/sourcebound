"""Translation segment pipeline: code owns structure, the model fills text."""

from __future__ import annotations

from typing import Any

from app.errors import ValidationError


def extract_segments(block_type: str, content: dict[str, Any]) -> list[dict[str, str]]:
    """Plain translatable strings of one block with stable segment ids.

    Structure, markers, numbers, citations, and answer keys never leave
    code: only the human-readable strings below reach the translator.
    """
    if block_type == "reading":
        return [{"id": "body", "text": str(content["body"])}]
    if block_type == "figure":
        return [{"id": "caption", "text": str(content["caption"])}]
    if block_type == "choice":
        segments = [{"id": "stem", "text": str(content["stem"])}]
        options = content["options"]
        if not isinstance(options, list):
            raise ValidationError("choice options must be a list")
        segments += [
            {"id": f"options[{index}]", "text": str(option)} for index, option in enumerate(options)
        ]
        return segments
    if block_type == "matching":
        pairs = content["pairs"]
        if not isinstance(pairs, list):
            raise ValidationError("matching pairs must be a list")
        segments = []
        for index, pair in enumerate(pairs):
            segments.append({"id": f"pairs[{index}].left", "text": str(pair["left"])})
            segments.append({"id": f"pairs[{index}].right", "text": str(pair["right"])})
        return segments
    if block_type == "cloze":
        return [{"id": "text", "text": str(content["text"])}]
    if block_type == "scenario_practice":
        segments = [{"id": "scenario", "text": str(content["scenario"])}]
        items = content["items"]
        if not isinstance(items, list):
            raise ValidationError("scenario items must be a list")
        segments += [
            {"id": f"items[{index}].text", "text": str(item["text"])}
            for index, item in enumerate(items)
        ]
        return segments
    if block_type == "spot_the_error":
        sentences = content["sentences"]
        if not isinstance(sentences, list):
            raise ValidationError("spot_the_error sentences must be a list")
        return [
            {"id": f"sentences[{index}]", "text": str(sentence)}
            for index, sentence in enumerate(sentences)
        ]
    raise ValidationError(f"unknown block type: {block_type}")


def segment_ids(block_type: str, content: dict[str, Any]) -> set[str]:
    """Stable id set of one block (the translator must return it whole)."""
    return {segment["id"] for segment in extract_segments(block_type, content)}


def planted_segment_id(
    content: dict[str, Any], answer_key: dict[str, Any] | None = None
) -> str | None:
    """Segment id of the planted sentence; code re-derives it by index."""
    sentences = content.get("sentences")
    planted = (answer_key or content).get("planted_index")
    if isinstance(sentences, list) and isinstance(planted, int) and 0 <= planted < len(sentences):
        return f"sentences[{planted}]"
    return None


def reassemble(
    block_type: str, content: dict[str, Any], translations: dict[str, str]
) -> dict[str, Any]:
    """Rebuild block content from translated segments; code keeps the rest."""
    import copy

    rebuilt = copy.deepcopy(content)

    def take(segment_id: str) -> str:
        try:
            return translations[segment_id]
        except KeyError:
            raise ValidationError(f"missing translation for {segment_id}") from None

    if block_type == "reading":
        rebuilt["body"] = take("body")
    elif block_type == "figure":
        rebuilt["caption"] = take("caption")
    elif block_type == "choice":
        rebuilt["stem"] = take("stem")
        rebuilt["options"] = [take(f"options[{i}]") for i in range(len(content["options"]))]
    elif block_type == "matching":
        rebuilt["pairs"] = [
            {
                **pair,
                "left": take(f"pairs[{i}].left"),
                "right": take(f"pairs[{i}].right"),
            }
            for i, pair in enumerate(content["pairs"])
        ]
    elif block_type == "cloze":
        rebuilt["text"] = take("text")
    elif block_type == "scenario_practice":
        rebuilt["scenario"] = take("scenario")
        rebuilt["items"] = [
            {**item, "text": take(f"items[{i}].text")} for i, item in enumerate(content["items"])
        ]
    elif block_type == "spot_the_error":
        rebuilt["sentences"] = [take(f"sentences[{i}]") for i in range(len(content["sentences"]))]
    else:
        raise ValidationError(f"unknown block type: {block_type}")
    return rebuilt
