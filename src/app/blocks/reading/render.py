"""Learner render contract for reading blocks: content only, never the key."""

from __future__ import annotations

from typing import Any

from app.course.grounding import HANDLE_MARKER_RE


def render_for_learner(content: dict[str, Any]) -> dict[str, Any]:
    """Learner-safe view: the body with a handle chip at every passage boundary.

    The body is split at every ⟦P<n>⟧ marker into text/handle segments
    so the learner UI shows a citation chip at each passage boundary
    instead of joining two passages into one anonymous paragraph. The
    writer's quotes are evidence for the checks, never learner text:
    the chips open the passage itself.
    """
    body = str(content.get("body", ""))
    segments: list[dict[str, Any]] = []
    pos = 0
    for match in HANDLE_MARKER_RE.finditer(body):
        if match.start() > pos:
            segments.append({"text": body[pos : match.start()]})
        segments.append({"text": "", "handle": match.group(1)})
        pos = match.end()
    if pos < len(body) or not segments:
        segments.append({"text": body[pos:]})
    return {"body": body, "segments": segments}
