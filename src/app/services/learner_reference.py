"""Reference pages of a course for its reader: numbered sources and the glossary.

A citation chip shows the number of its source in the course's source
list (reading order of first citation), the way a footnote points at a
bibliography; the sources page lists every approved source under that
number. The glossary is the scenario's `introduces_terms` per topic,
each with the first sentence of that topic's readings that names it.
Callers: controllers/learn.py and controllers/t03.py (the two pages),
services/learner.py (chip numbers, the topic's own terms).
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.course.grounding import HANDLE_MARKER_RE
from app.rbac.principal import Principal

_SENTENCE_RE = re.compile(r"[^.!?\n]+[.!?]?")
_MARKUP_RE = re.compile(r"\*\*|`|^\s*[-*]\s+|^\s*\d+\.\s+")


def plain(text: str) -> str:
    """The sentence as the learner reads it: no handle markers, no markdown marks."""
    without_markers = HANDLE_MARKER_RE.sub("", text)
    return " ".join(_MARKUP_RE.sub("", without_markers).split())


def definition_of(term: str, body: str) -> tuple[str, str]:
    """(first sentence of `body` naming `term`, handle of the passage it draws on)."""
    pattern = re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)
    handle = ""
    for match in _SENTENCE_RE.finditer(body):
        sentence = match.group(0)
        markers = HANDLE_MARKER_RE.findall(sentence)
        if markers:
            handle = str(markers[-1])
        if pattern.search(HANDLE_MARKER_RE.sub("", sentence)):
            return plain(sentence), handle
    return "", ""


async def source_numbers(session: AsyncSession, version_ids: list[str]) -> dict[str, int]:
    """source id -> its number, by the first block version (in course order) citing it."""
    if not version_ids:
        return {}
    rows = (
        await session.execute(
            sa.text(
                "SELECT c.block_version_id, p.source_id FROM citations c"
                " JOIN passages p ON p.id = c.passage_id"
                " WHERE c.block_version_id::text = ANY(:ids)"
                " ORDER BY c.quote_start, c.quote, c.passage_id"
            ),
            {"ids": version_ids},
        )
    ).all()
    by_version: dict[str, list[str]] = defaultdict(list)
    for version_id, source_id in rows:
        by_version[str(version_id)].append(str(source_id))
    numbers: dict[str, int] = {}
    for version_id in version_ids:
        for source_id in by_version.get(version_id, []):
            numbers.setdefault(source_id, len(numbers) + 1)
    return numbers


def _version_ids(topics: list[dict[str, Any]]) -> list[str]:
    return [b["version_id"] for t in topics for b in t["blocks"] if b.get("version_id")]


async def sources_page(session: AsyncSession, course: dict[str, Any]) -> dict[str, Any]:
    """Every approved source of the course under its citation number."""
    from app.services.learner import topic_entries

    topics = await topic_entries(session, course)
    version_ids = _version_ids(topics)
    numbers = await source_numbers(session, version_ids)
    rows = (
        await session.execute(
            sa.text(
                "SELECT s.id, s.title, s.kind, s.media, s.licence, s.as_of, s.storage_path,"
                " (SELECT count(DISTINCT c.passage_id) FROM citations c"
                "  JOIN passages p ON p.id = c.passage_id WHERE p.source_id = s.id"
                "  AND c.block_version_id::text = ANY(:ids)),"
                " (SELECT count(DISTINCT c.block_version_id) FROM citations c"
                "  JOIN passages p ON p.id = c.passage_id WHERE p.source_id = s.id"
                "  AND c.block_version_id::text = ANY(:ids))"
                " FROM course_sources cs JOIN sources s ON s.id = cs.source_id"
                " AND s.approval = 'approved' WHERE cs.course_id = :course"
                " AND cs.approval = 'approved' ORDER BY s.title, s.id"
            ),
            {"course": course["id"], "ids": version_ids},
        )
    ).all()
    entries: list[dict[str, Any]] = []
    for r in rows:
        source_id = str(r[0])
        title = str(r[1] or "")
        address = ""
        if str(r[2]) == "url":
            # rows crawled before 2026-11-03 kept the URL only as their title
            looks_like_url = title.startswith(("http://", "https://"))
            address = str(r[6] or "") or (title if looks_like_url else "")
        entries.append(
            {
                "number": numbers.get(source_id, 0),
                "id": source_id,
                "title": title or address,
                "kind": str(r[2]),
                "media": str(r[3]),
                "licence": str(r[4] or ""),
                "as_of": r[5].isoformat() if r[5] else None,
                "address": address,
                "passages_cited": int(r[7] or 0),
                "blocks_cited": int(r[8] or 0),
            }
        )
    cited = sorted((e for e in entries if e["number"]), key=lambda e: e["number"])
    uncited = [e for e in entries if not e["number"]]
    for offset, entry in enumerate(uncited, start=len(cited) + 1):
        entry["number"] = offset
    return {"course_id": course["id"], "title": course["title"], "sources": cited + uncited}


async def glossary(session: AsyncSession, course: dict[str, Any]) -> dict[str, Any]:
    """The scenario's terms per topic with the sentence that introduces each."""
    from app.services.learner import citation_rows, handle_maps, topic_entries

    terms: list[dict[str, Any]] = []
    for topic in await topic_entries(session, course):
        readings = [b for b in topic["blocks"] if b["block_type"] == "reading"]
        for term in topic.get("terms", []):
            entry: dict[str, Any] = {
                "term": term,
                "definition": "",
                "topic_id": topic["id"],
                "topic_title": topic["title"],
                "passage_id": None,
                "block_id": None,
            }
            for block in readings:
                definition, handle = definition_of(term, str(block["content"].get("body", "")))
                if not definition:
                    continue
                entry["definition"] = definition
                entry["block_id"] = block["id"]
                if handle:
                    rows = await citation_rows(session, block["version_id"])
                    handles, _ = handle_maps(block, rows)
                    entry["passage_id"] = handles.get(handle)
                break
            terms.append(entry)
    terms.sort(key=lambda e: str(e["term"]).lower())
    return {"course_id": course["id"], "title": course["title"], "terms": terms}


def topic_terms(topic: dict[str, Any], blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The topic's own terms with their definition from the served reading blocks."""
    out: list[dict[str, Any]] = []
    for term in topic.get("terms", []):
        definition = ""
        for block in blocks:
            if block["block_type"] != "reading":
                continue
            definition, _ = definition_of(term, str(block["content"].get("body", "")))
            if definition:
                break
        out.append({"term": term, "definition": definition})
    return out


async def preview_sources(session: AsyncSession, course_id: UUID) -> dict[str, Any]:
    """The draft's sources page (the author's preview)."""
    from app.services.learner import preview_course

    return await sources_page(session, await preview_course(session, course_id))


async def preview_glossary(session: AsyncSession, course_id: UUID) -> dict[str, Any]:
    """The draft's glossary (the author's preview)."""
    from app.services.learner import preview_course

    return await glossary(session, await preview_course(session, course_id))


async def course_sources(
    session: AsyncSession, principal: Principal, course_id: UUID
) -> dict[str, Any]:
    """The published course's sources page for an enrolled learner."""
    from app.services.learner import enrolled_course

    return await sources_page(
        session, await enrolled_course(session, principal, course_id, code="learn.read")
    )


async def course_glossary(
    session: AsyncSession, principal: Principal, course_id: UUID
) -> dict[str, Any]:
    """The published course's glossary for an enrolled learner."""
    from app.services.learner import enrolled_course

    return await glossary(
        session, await enrolled_course(session, principal, course_id, code="learn.read")
    )
