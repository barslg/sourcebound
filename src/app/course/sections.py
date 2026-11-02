"""Source sections: the units the outline planner assigns to modules.

A big source set cannot be planned in one model call over every
passage. Consecutive passages under the same heading (web and markdown
addresses) or on the same page (pdf) form one section; the outline call
sees only section titles, sizes and a preview, assigns sections to
modules, and each module is then planned over its own passages.
"""

from __future__ import annotations

import re
from typing import Any

MAX_SECTIONS = 120
_HEADING_LEVELS = 2


def section_scope(passage: dict[str, Any]) -> str:
    """The grouping key of one passage: source + heading path (two levels) or page."""
    address = str(passage.get("address", passage.get("address_key", "")))
    source = str(passage.get("source_id", ""))
    if address.startswith("h:"):
        path = address[2:].rsplit("/para", 1)[0]
        return f"{source}|" + ">".join(path.split(">")[:_HEADING_LEVELS])
    match = re.fullmatch(r"p(\d+)/para\d+", address)
    if match:
        return f"{source}|page {match.group(1)}"
    return f"{source}|{address.rsplit('/', 1)[0] or 'body'}"


def sections_of(passages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Consecutive passages with one scope become one section, S1..Sn in document order."""
    sections: list[dict[str, Any]] = []
    for passage in passages:
        scope = section_scope(passage)
        if sections and sections[-1]["scope"] == scope:
            current = sections[-1]
        else:
            current = {
                "scope": scope,
                "source_id": str(passage.get("source_id", "")),
                "title": scope.split("|", 1)[1],
                "handles": [],
                "chars": 0,
                "preview": "",
            }
            sections.append(current)
        current["handles"].append(str(passage.get("handle", "")))
        current["chars"] += len(str(passage.get("text", "")))
        if not current["preview"] and passage.get("is_content", True):
            current["preview"] = " ".join(str(passage.get("text", "")).split())[:160]
    while len(sections) > MAX_SECTIONS:
        # merge the smallest section into a neighbour of the same source; a
        # section never crosses a source (the 2026-11-02 cap glued the end of
        # one page to the next page and a topic was assigned foreign passages)
        candidates = [
            i for i in range(len(sections)) if _same_source_neighbour(sections, i) is not None
        ]
        if not candidates:
            break
        index = min(candidates, key=lambda i: sections[i]["chars"])
        neighbour = _same_source_neighbour(sections, index)
        assert neighbour is not None
        keep, drop = sorted((index, neighbour))
        sections[keep]["handles"] += sections[drop]["handles"]
        sections[keep]["chars"] += sections[drop]["chars"]
        sections[keep]["preview"] = sections[keep]["preview"] or sections[drop]["preview"]
        del sections[drop]
    for number, section in enumerate(sections, 1):
        section["id"] = f"S{number}"
    return sections


def _same_source_neighbour(sections: list[dict[str, Any]], index: int) -> int | None:
    """The previous section of the same source, else the next, else None."""
    source = sections[index]["source_id"]
    if index > 0 and sections[index - 1]["source_id"] == source:
        return index - 1
    if index + 1 < len(sections) and sections[index + 1]["source_id"] == source:
        return index + 1
    return None


def public_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What the outline model sees: id, title, size, preview — never ids or texts."""
    return [
        {
            "id": section["id"],
            "title": section["title"],
            "passages": len(section["handles"]),
            "preview": section["preview"],
        }
        for section in sections
    ]


def module_handles(sections: list[dict[str, Any]], section_ids: list[str]) -> list[str]:
    """Passage handles of the sections one module was given, in document order."""
    wanted = set(section_ids)
    return [h for section in sections if section["id"] in wanted for h in section["handles"]]
