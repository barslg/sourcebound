"""Passage shaping shared by ingest, planning, generation and replay.

A passage that ends a sentence with a colon and continues as a list is
ONE claim unit: the intro sentence and its items. Lists come in two
shapes — marked lines ("- item", "1. item") and unmarked "Label: text"
paragraphs (the shape the Meta docs uploads have) — and both are joined
here, once, so every reader of passages sees the same units.
"""

from __future__ import annotations

import re
from typing import Any

BULLET_LINE_RE = re.compile(r"^\s*(?:[-•*]|\d+[.)])\s+\S")
# "Allow once: run this stage one time only." / "--yolo: disable approval." /
# "proxy-only (default): Muse Code approves outbound connections."
LABEL_LINE_RE = re.compile(r"^(?P<label>[^:\n]{1,60}?): \S")
_MAX_LABEL_WORDS = 6


def is_list_lines(text: str) -> bool:
    """True when every non-empty line of the text is a marked list line."""
    lines = [line for line in str(text).splitlines() if line.strip()]
    return bool(lines) and all(BULLET_LINE_RE.match(line) for line in lines)


def label_item(text: str) -> str | None:
    """The label of a one-paragraph "Label: text" item, else None."""
    match = LABEL_LINE_RE.match(str(text).strip())
    if match is None:
        return None
    label = match.group("label").strip()
    if len(label.split()) > _MAX_LABEL_WORDS or "\n" in label:
        return None
    return label


def _scope(passage: dict[str, Any]) -> str:
    """Heading scope of a passage: its address without the paragraph number."""
    address = str(passage.get("address", passage.get("address_key", "")))
    return address.rsplit("/para", 1)[0]


def _same_source(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return "source_id" not in a or "source_id" not in b or a.get("source_id") == b.get("source_id")


def rejoin_list_passages(passages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join a colon intro with the list that follows it, keeping the first address.

    After a passage whose text ends with ':', consecutive passages that
    are marked list lines, or one-paragraph "Label: text" items under
    the same heading, are absorbed as "- ..." lines. The merged passage
    keeps the intro's id and address and lists the absorbed ids in
    `merged_ids`, so citations into an absorbed paragraph still resolve.
    """
    out: list[dict[str, Any]] = []
    absorbing = False
    for passage in passages:
        text = str(passage.get("text", ""))
        previous = out[-1] if out else None
        if previous is not None and _same_source(previous, passage):
            prev_text = str(previous.get("text", ""))
            opens = prev_text.rstrip().endswith(":")
            if (opens or absorbing) and is_list_lines(text):
                out[-1] = _absorb(previous, passage, text.strip())
                absorbing = True
                continue
            label = label_item(text)
            if (opens or absorbing) and label is not None and _scope(previous) == _scope(passage):
                out[-1] = _absorb(previous, passage, f"- {text.strip()}")
                absorbing = True
                continue
            if opens and passage.get("code") and not absorbing:
                out[-1] = _absorb(previous, passage, text.strip("\n"))
                continue
        absorbing = False
        out.append(passage)
    return out


def _absorb(merged: dict[str, Any], passage: dict[str, Any], lines: str) -> dict[str, Any]:
    joined = dict(merged)
    joined["text"] = str(merged.get("text", "")).rstrip() + "\n" + lines
    ids = list(merged.get("merged_ids") or [])
    own = merged.get("passage_id") or merged.get("id")
    if own and own not in ids:
        ids.append(str(own))
    absorbed = passage.get("passage_id") or passage.get("id")
    if absorbed:
        ids.append(str(absorbed))
    joined["merged_ids"] = ids
    return joined
