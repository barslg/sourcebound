"""Learner render contract for reading blocks: content only, never the key."""

from __future__ import annotations

import re
from typing import Any

_MARKER_RE = re.compile(r"⟦(P[1-9][0-9]*)⟧")


def render_for_learner(content: dict[str, Any]) -> dict[str, Any]:
    """Learner-safe view: body, handle chips at passage boundaries, quotes.

    The body is split at every ⟦P<n>⟧ marker into text/handle segments
    so the learner UI shows a citation chip at each passage boundary
    instead of joining two passages into one anonymous paragraph.
    """
    body = str(content.get("body", ""))
    segments: list[dict[str, Any]] = []
    pos = 0
    for match in _MARKER_RE.finditer(body):
        if match.start() > pos:
            segments.append({"text": body[pos : match.start()]})
        segments.append({"text": "", "handle": match.group(1)})
        pos = match.end()
    if pos < len(body) or not segments:
        segments.append({"text": body[pos:]})
    return {
        "body": body,
        "segments": segments,
        "citations": [
            {
                "handle": str(item.get("handle", item.get("passage_id", ""))),
                "quote": str(item.get("quote", "")),
            }
            for item in content.get("citations", [])
            if isinstance(item, dict)
        ],
    }
