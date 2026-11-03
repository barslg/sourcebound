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
        segments = [{"id": "body", "text": str(content["body"])}]
        # Generation persists the same text under `text`: a copy of the
        # body is translated once and copied; a different text is its own segment.
        text = content.get("text")
        if isinstance(text, str) and text.strip() and text.strip() != str(content["body"]).strip():
            segments.append({"id": "text", "text": text})
        return segments
    if block_type == "figure":
        return [{"id": "caption", "text": str(content["caption"])}]
    if block_type == "diagram":
        nodes, edges = content["nodes"], content["edges"]
        if not isinstance(nodes, list) or not isinstance(edges, list):
            raise ValidationError("diagram nodes and edges must be lists")
        segments = [{"id": "caption", "text": str(content["caption"])}]
        segments += [
            {"id": f"nodes[{i}].label", "text": str(n["label"])} for i, n in enumerate(nodes)
        ]
        segments += [
            {"id": f"edges[{i}].label", "text": str(e["label"])}
            for i, e in enumerate(edges)
            if str(e.get("label", "")).strip()
        ]
        return segments
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
    if block_type == "ordering":
        items = content["items"]
        if not isinstance(items, list):
            raise ValidationError("ordering items must be a list")
        segments = [{"id": "prompt", "text": str(content["prompt"])}]
        segments += [
            {"id": f"items[{index}]", "text": str(item)} for index, item in enumerate(items)
        ]
        return segments
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


def key_segments(block_type: str, answer_key: dict[str, Any]) -> list[dict[str, str]]:
    """Answer-key texts translated as their own checked segments.

    Cloze blanks and the spot-the-error true sentence live only in the
    key; they are translated separately (ids under `key:`), checked like
    any segment, and never enter translation memory.
    """
    if block_type == "cloze":
        blanks = answer_key.get("blanks")
        if isinstance(blanks, list):
            return [
                {"id": f"key:blanks[{i}]", "text": str(b)}
                for i, b in enumerate(blanks)
                if str(b).strip() and str(b) != "___"
            ]
        return []
    if block_type == "spot_the_error":
        true_sentence = answer_key.get("true_sentence")
        if isinstance(true_sentence, str) and true_sentence.strip():
            return [{"id": "key:true_sentence", "text": true_sentence}]
        return []
    return []


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
        text = rebuilt.get("text")
        if isinstance(text, str) and text.strip():
            same = text.strip() == str(content["body"]).strip()
            rebuilt["text"] = rebuilt["body"] if same else take("text")
    elif block_type == "figure":
        rebuilt["caption"] = take("caption")
    elif block_type == "diagram":
        rebuilt["caption"] = take("caption")
        rebuilt["nodes"] = [
            {**n, "label": take(f"nodes[{i}].label")} for i, n in enumerate(content["nodes"])
        ]
        rebuilt["edges"] = [
            {**e, "label": take(f"edges[{i}].label") if str(e.get("label", "")).strip() else ""}
            for i, e in enumerate(content["edges"])
        ]
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
    elif block_type == "ordering":
        rebuilt["prompt"] = take("prompt")
        rebuilt["items"] = [take(f"items[{i}]") for i in range(len(content["items"]))]
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
