"""Learner read side: enrolment, outline, topic view, passages, progress.

Enrolment is a student grant scoped (by an invite) to the course id or
to one of the course's scope tags; the permission code alone never
enrols (learner.view is a teacher code). A learner reads the PUBLISHED
snapshot only, at the block versions pinned in it, through each type's
learner serializer and the translation layer; answer keys never leave
the grader. Caller: controllers/learn.py (the learner screens).
"""

from __future__ import annotations

import hashlib
from importlib import import_module
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import NotFoundError
from app.rbac.principal import Principal
from app.rbac.require import require
from app.rbac.tags import group_keys_of

STUDENT_ROLE = "student"
ASSESSED_TYPES = frozenset({"choice", "matching", "cloze", "scenario_practice", "spot_the_error"})
SPOT_OPTIONS = 4


def enrolment_tags(principal: Principal) -> set[str]:
    """Tags of the principal's student grants: the only source of enrolment."""
    return {
        tag for grant in principal.grants if grant.role == STUDENT_ROLE for tag in grant.scope_tags
    }


def _not_found() -> NotFoundError:
    return NotFoundError("not found")


async def enrolled_course(
    session: AsyncSession, principal: Principal, course_id: UUID, *, code: str
) -> dict[str, Any]:
    """The published course with its snapshot, or 404 (also when not enrolled)."""
    require(principal, code)
    row = (
        await session.execute(
            sa.text(
                "SELECT c.id, c.title, c.language, c.scope_tags, cv.snapshot, c.owner_id"
                " FROM courses c JOIN course_versions cv ON cv.id = c.published_version_id"
                " WHERE c.id = :course AND c.status = 'published'"
            ),
            {"course": str(course_id)},
        )
    ).first()
    if row is None:
        raise _not_found()
    tags = enrolment_tags(principal)
    if str(row[0]) not in tags and not (set(group_keys_of(row[5], row[3] or [])) & tags):
        raise _not_found()
    return {
        "id": str(row[0]),
        "title": str(row[1]),
        "language": str(row[2]),
        "snapshot": dict(row[4] or {}),
    }


async def preview_course(session: AsyncSession, course_id: UUID) -> dict[str, Any]:
    """The draft course in the snapshot shape the learner services read.

    The author's preview is the learner's reading over the current
    tree and block versions: one outline, one topic page, one passage
    panel, through the same serializers. Access (learner.view and
    ownership) is the controller's check; this builds the document.
    """
    from app.services.courses import get_course, read_tree

    course = await get_course(session, course_id=course_id)
    tree = await read_tree(session, course_id=course_id)
    return {
        "id": str(course_id),
        "title": str(course.get("title", "")),
        "language": str(course.get("language", "en")),
        "snapshot": {"tree": tree, "profile": course.get("profile")},
        "preview": True,
    }


async def list_enrolled(
    session: AsyncSession, principal: Principal, *, threshold: float
) -> list[dict[str, Any]]:
    """Published courses the principal is enrolled in, with progress."""
    require(principal, "learn.read")
    tags = sorted(enrolment_tags(principal))
    if not tags:
        return []
    rows = (
        await session.execute(
            sa.text(
                "SELECT c.id FROM courses c WHERE c.status = 'published'"
                " AND c.published_version_id IS NOT NULL"
                " AND (c.id::text = ANY(:tags) OR EXISTS (SELECT 1 FROM unnest(c.scope_tags) t"
                " WHERE (c.owner_id::text || ':' || t) = ANY(:tags)))"
                " ORDER BY c.created_at, c.id"
            ),
            {"tags": tags},
        )
    ).all()
    out: list[dict[str, Any]] = []
    for (course_id,) in rows:
        course = await enrolled_course(session, principal, UUID(str(course_id)), code="learn.read")
        topics = await topic_entries(session, course)
        progress = progress_of(
            [b for t in topics for b in t["blocks"]],
            await attempts_of(
                session, principal.user_id, [b["id"] for t in topics for b in t["blocks"]]
            ),
            threshold,
        )
        out.append(
            {
                "id": course["id"],
                "title": course["title"],
                "language": course["language"],
                "audience": str((course["snapshot"].get("profile") or {}).get("audience", "")),
                "progress": progress,
            }
        )
    return out


async def topic_entries(session: AsyncSession, course: dict[str, Any]) -> list[dict[str, Any]]:
    """Topics of the snapshot in order, each with its learner-valid blocks.

    A block is learner-valid when its pinned version holds content that
    passes the type's schema; plan-only skeletons are not served.
    """
    snap_blocks = [
        (module, topic, block)
        for module in course["snapshot"].get("tree", {}).get("modules", [])
        for topic in module.get("topics", [])
        for block in topic.get("blocks", [])
    ]
    version_ids = await _version_ids(session, snap_blocks)
    versions = await _versions(session, [v for v in version_ids.values() if v])
    entries: list[dict[str, Any]] = []
    for module in course["snapshot"].get("tree", {}).get("modules", []):
        for topic in module.get("topics", []):
            blocks: list[dict[str, Any]] = []
            for ordinal, block in enumerate(topic.get("blocks", [])):
                vid = version_ids.get(block["id"])
                version = versions.get(vid) if vid else None
                if version is None or not _valid(block["block_type"], version["content"]):
                    continue
                blocks.append(
                    {
                        "id": block["id"],
                        "block_type": block["block_type"],
                        "role": block.get("role", ""),
                        "ordinal": ordinal,
                        "version_id": vid,
                        "plan_node_id": version["plan_node_id"],
                        "content": version["content"],
                        "answer_key": version["answer_key"],
                        "status": str(block.get("status") or ""),
                        "hold_reason": str(block.get("hold_reason") or ""),
                    }
                )
            entries.append(
                {
                    "id": topic["id"],
                    "plan_node_id": topic.get("plan_node_id", ""),
                    "module_id": module["id"],
                    "module_title": module.get("title", ""),
                    "module_purpose": module.get("purpose", ""),
                    "kind": topic.get("kind", "lesson"),
                    "title": topic.get("title", ""),
                    "objectives": topic.get("objectives", []),
                    "blocks": blocks,
                }
            )
    return entries


async def _version_ids(
    session: AsyncSession, snap_blocks: list[tuple[Any, Any, Any]]
) -> dict[str, str | None]:
    """Pinned version per block; snapshots from before pinning use the current one."""
    pinned = {b["id"]: b.get("version_id") for _, _, b in snap_blocks}
    missing = [bid for bid, vid in pinned.items() if not vid]
    if missing:
        rows = (
            await session.execute(
                sa.text("SELECT id, current_version_id FROM blocks WHERE id::text = ANY(:ids)"),
                {"ids": missing},
            )
        ).all()
        for bid, vid in rows:
            pinned[str(bid)] = str(vid) if vid else None
    return pinned


async def _versions(session: AsyncSession, version_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not version_ids:
        return {}
    rows = (
        await session.execute(
            sa.text(
                "SELECT bv.id, bv.content, bv.answer_key, b.plan_node_id FROM block_versions bv"
                " JOIN blocks b ON b.id = bv.block_id WHERE bv.id::text = ANY(:ids)"
            ),
            {"ids": version_ids},
        )
    ).all()
    return {
        str(r[0]): {
            "content": dict(r[1] or {}),
            "answer_key": dict(r[2] or {}),
            "plan_node_id": str(r[3]),
        }
        for r in rows
    }


def _valid(block_type: str, content: dict[str, Any]) -> bool:
    try:
        return not import_module(f"app.blocks.{block_type}.schema").validate(content)
    except ModuleNotFoundError:
        return False


async def attempts_of(
    session: AsyncSession, user_id: UUID, block_ids: list[str]
) -> dict[str, dict[str, float | int]]:
    """Per block: attempt count, latest and best score for one learner."""
    if not block_ids:
        return {}
    rows = (
        await session.execute(
            sa.text(
                "SELECT block_id, count(*), max(score),"
                " (array_agg(score ORDER BY attempt_no DESC))[1] FROM learner_answers"
                " WHERE user_id = :user AND block_id::text = ANY(:ids) GROUP BY block_id"
            ),
            {"user": str(user_id), "ids": block_ids},
        )
    ).all()
    return {
        str(r[0]): {"count": int(r[1]), "best_score": float(r[2]), "last_score": float(r[3])}
        for r in rows
    }


def progress_of(
    blocks: list[dict[str, Any]],
    attempts: dict[str, dict[str, float | int]],
    threshold: float,
) -> dict[str, Any]:
    """progress_of with the mastery threshold given (reports pass settings)."""
    total = len(blocks)
    attempted = sum(1 for b in blocks if b["id"] in attempts)
    assessed = [b for b in blocks if b["block_type"] in ASSESSED_TYPES]
    mastery: float | None = None
    status = "not_started"
    if assessed:
        mastery = round(
            sum(float(attempts.get(b["id"], {}).get("last_score", 0.0)) for b in assessed)
            / len(assessed),
            4,
        )
    if attempted:
        status = "in_progress"
        if assessed:
            done = all(b["id"] in attempts for b in assessed)
            if done and mastery is not None and mastery >= threshold:
                status = "mastered"
        elif attempted == total:
            status = "complete"
    return {
        "blocks_total": total,
        "blocks_attempted": attempted,
        "completion": round(attempted / total, 4) if total else 0.0,
        "assessed": len(assessed),
        "mastery": mastery,
        "status": status,
    }


async def course_outline(
    session: AsyncSession,
    principal: Principal,
    course_id: UUID,
    *,
    language: str | None,
    threshold: float,
) -> dict[str, Any]:
    """Modules, topics and progress of the published version."""
    course = await enrolled_course(session, principal, course_id, code="learn.read")
    return await _outline(session, principal, course, language=language, threshold=threshold)


async def preview_outline(
    session: AsyncSession,
    principal: Principal,
    course_id: UUID,
    *,
    language: str | None,
    threshold: float,
) -> dict[str, Any]:
    """The draft's modules and topics as the learner will see them, with held counts."""
    course = await preview_course(session, course_id)
    return await _outline(session, principal, course, language=language, threshold=threshold)


async def _outline(
    session: AsyncSession,
    principal: Principal,
    course: dict[str, Any],
    *,
    language: str | None,
    threshold: float,
) -> dict[str, Any]:
    from app.translation.variants import available_languages

    course_id = UUID(course["id"])
    topics = await topic_entries(session, course)
    attempts = await attempts_of(
        session, principal.user_id, [b["id"] for t in topics for b in t["blocks"]]
    )
    modules: dict[str, dict[str, Any]] = {}
    for topic in topics:
        module = modules.setdefault(
            topic["module_id"],
            {
                "id": topic["module_id"],
                "title": topic["module_title"],
                "purpose": topic["module_purpose"],
                "topics": [],
            },
        )
        module["topics"].append(
            {
                "id": topic["id"],
                "kind": topic["kind"],
                "title": topic["title"],
                "objectives": topic["objectives"],
                "progress": progress_of(topic["blocks"], attempts, threshold),
                "held": sum(1 for b in topic["blocks"] if b.get("status") == "held"),
            }
        )
    return {
        "id": course["id"],
        "title": course["title"],
        "language": language or course["language"],
        "source_language": course["language"],
        "available_languages": await available_languages(session, course_id=course_id),
        "progress": progress_of([b for t in topics for b in t["blocks"]], attempts, threshold),
        "modules": list(modules.values()),
    }


async def course_of_topic(session: AsyncSession, topic_id: UUID) -> UUID:
    row = (
        await session.execute(
            sa.text(
                "SELECT m.course_id FROM topics t JOIN modules m ON m.id = t.module_id"
                " WHERE t.id = :t"
            ),
            {"t": str(topic_id)},
        )
    ).first()
    if row is None:
        raise _not_found()
    return UUID(str(row[0]))


async def course_of_block(session: AsyncSession, block_id: UUID) -> UUID:
    from app.services.courses import block_course_id

    try:
        return await block_course_id(session, block_id=block_id)
    except NotFoundError:
        raise _not_found() from None


async def find_topic(
    session: AsyncSession, principal: Principal, topic_id: UUID, *, code: str
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """(course, topic entry, all topic entries); 404 outside the published snapshot."""
    course = await enrolled_course(
        session, principal, await course_of_topic(session, topic_id), code=code
    )
    topics = await topic_entries(session, course)
    topic = next((t for t in topics if t["id"] == str(topic_id)), None)
    if topic is None:
        raise _not_found()
    return course, topic, topics


def handle_maps(
    block: dict[str, Any], rows: list[dict[str, Any]]
) -> tuple[dict[str, str], list[str]]:
    """(handle -> passage id, passage ids in citation order) of one block version.

    Handles come from the content citations matched to the stored
    citation rows by quote; rows without a content handle get P<n>.
    Spot-the-error content carries none: its key names the passage handle.
    """
    content = block["content"]
    raw = content.get("citations")
    if isinstance(content.get("citation"), dict):
        raw = [content["citation"]]
    by_quote = {
        str(c.get("quote", "")): str(c.get("handle", ""))
        for c in (raw if isinstance(raw, list) else [])
        if isinstance(c, dict) and c.get("handle")
    }
    handles: dict[str, str] = {}
    order: list[str] = []
    for index, row in enumerate(rows, start=1):
        pid = row["passage_id"]
        if pid in order:
            continue
        order.append(pid)
        handle = by_quote.get(row["quote"]) or f"P{index}"
        handles.setdefault(handle, pid)
    if block["block_type"] == "spot_the_error" and order:
        handles = {str(block["answer_key"].get("passage", "P1")): order[0]}
    return handles, order


async def citation_rows(session: AsyncSession, version_id: str) -> list[dict[str, Any]]:
    """Citation rows of one version whose source binding is still approved.

    A chip of a withdrawn (pending) binding would open a passage the
    companion panel refuses, so it is not rendered at all.
    """
    rows = (
        await session.execute(
            sa.text(
                "SELECT c.passage_id, c.quote, p.address, p.address_key, s.title"
                " FROM citations c JOIN passages p ON p.id = c.passage_id"
                " JOIN sources s ON s.id = p.source_id AND s.approval = 'approved'"
                " JOIN block_versions bv ON bv.id = c.block_version_id"
                " JOIN blocks b ON b.id = bv.block_id JOIN topics t ON t.id = b.topic_id"
                " JOIN modules m ON m.id = t.module_id"
                " JOIN course_sources cs ON cs.source_id = s.id AND cs.course_id = m.course_id"
                " AND cs.approval = 'approved' WHERE c.block_version_id = :v"
                " ORDER BY c.quote_start, c.quote, c.passage_id"
            ),
            {"v": version_id},
        )
    ).all()
    return [
        {
            "passage_id": str(r[0]),
            "quote": str(r[1]),
            "address": dict(r[2] or {}),
            "address_key": str(r[3]),
            "title": str(r[4]),
        }
        for r in rows
    ]


def chip(handle: str, row: dict[str, Any], locale: str) -> dict[str, str]:
    """One citation chip: handle, reader-facing title, full and short address, passage id."""
    from app.i18n.citations import display_title, format_citation_address, format_citation_short

    address = {**row["address"], "address_key": row["address_key"]}
    return {
        "handle": handle,
        "source_title": display_title(str(row["title"]), address),
        "address_label": format_citation_address(address, locale),
        "address_short": format_citation_short(address, locale),
        "passage_id": row["passage_id"],
    }


def chips_of(
    rows: list[dict[str, Any]], handles: dict[str, str], locale: str
) -> list[dict[str, str]]:
    by_pid = {pid: handle for handle, pid in handles.items()}
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for row in rows:
        if row["passage_id"] in seen:
            continue
        seen.add(row["passage_id"])
        out.append(chip(by_pid.get(row["passage_id"], ""), row, locale))
    return out


async def _spot_options(
    session: AsyncSession, course_id: str, block: dict[str, Any], cited: list[str], locale: str
) -> list[dict[str, str]]:
    """The cited passage among decoys of the same course, in a keyless order."""
    from app.providers.retrieval import scoped_join

    rows = (
        await session.execute(
            sa.text(
                "SELECT passages.id, passages.address, passages.address_key, s.title"
                " FROM passages" + scoped_join(UUID(course_id)) + " WHERE passages.is_content"
            ),
            {"course": course_id},
        )
    ).all()
    pool = [
        {
            "passage_id": str(r[0]),
            "address": dict(r[1] or {}),
            "address_key": str(r[2]),
            "title": str(r[3]),
        }
        for r in rows
    ]
    seed = block["id"]

    def rank(row: dict[str, Any]) -> str:
        return hashlib.sha256(f"{seed}:{row['passage_id']}".encode()).hexdigest()

    decoys = sorted((r for r in pool if r["passage_id"] not in cited), key=rank)
    chosen = [r for r in pool if r["passage_id"] in cited][:1] + decoys[: SPOT_OPTIONS - 1]
    return [chip("", r, locale) for r in sorted(chosen, key=rank)]


def locale_of(language: str) -> str:
    return language if language in ("en", "nb", "uk") else "en"


async def block_view(
    session: AsyncSession,
    course: dict[str, Any],
    block: dict[str, Any],
    *,
    language: str,
    attempts: dict[str, dict[str, float | int]],
) -> dict[str, Any]:
    """One block for one learner: serializer output, chips, attempts, no key."""
    from app.blocks.registry import learner_serializers
    from app.translation.variants import learner_block

    resolved = await learner_block(
        session,
        course_id=UUID(course["id"]),
        language=language,
        block={
            "plan_node_id": block["plan_node_id"],
            "content": block["content"],
            "answer_key": block["answer_key"],
        },
        source_language=course["language"],
    )
    rows = await citation_rows(session, block["version_id"])
    handles, order = handle_maps(block, rows)
    locale = locale_of(language)
    content = learner_serializers()["card"](block["block_type"], resolved["content"], {})
    entry: dict[str, Any] = {
        "id": block["id"],
        "block_type": block["block_type"],
        "role": block["role"],
        "ordinal": block["ordinal"],
        "content": content,
        "citations": []
        if block["block_type"] == "spot_the_error"
        else chips_of(rows, handles, locale),
        "language": resolved["language"],
        "fallback_notice": resolved["fallback_notice"],
        "attempts": attempts.get(block["id"], {"count": 0, "best_score": None, "last_score": None}),
        "status": str(block.get("status") or ""),
        "hold_reason": str(block.get("hold_reason") or ""),
    }
    if block["block_type"] == "spot_the_error":
        entry["passage_options"] = await _spot_options(session, course["id"], block, order, locale)
    return entry


async def record_served_language(
    session: AsyncSession, user_id: UUID, block_ids: list[str], language: str
) -> None:
    """Remember which language each block was served in (the grader's default)."""
    for block_id in block_ids:
        await session.execute(
            sa.text(
                "INSERT INTO learner_block_views (user_id, block_id, language)"
                " VALUES (:u, :b, :l) ON CONFLICT (user_id, block_id)"
                " DO UPDATE SET language = EXCLUDED.language, updated_at = now()"
            ),
            {"u": str(user_id), "b": block_id, "l": language},
        )


async def served_language(session: AsyncSession, user_id: UUID, block_id: str) -> str | None:
    """The language this learner was last served this block in, if any."""
    row = (
        await session.execute(
            sa.text(
                "SELECT language FROM learner_block_views WHERE user_id = :u AND block_id = :b"
            ),
            {"u": str(user_id), "b": block_id},
        )
    ).first()
    return str(row[0]) if row else None


async def topic_view(
    session: AsyncSession,
    principal: Principal,
    topic_id: UUID,
    *,
    language: str | None,
    threshold: float,
) -> dict[str, Any]:
    """One topic with its blocks through the learner serializers."""
    course, topic, _ = await find_topic(session, principal, topic_id, code="learn.read")
    return await _topic(session, principal, course, topic, language=language, threshold=threshold)


async def preview_topic(
    session: AsyncSession,
    principal: Principal,
    course_id: UUID,
    topic_id: UUID,
    *,
    language: str | None,
    threshold: float,
) -> dict[str, Any]:
    """One topic of the draft as the learner will read it; nothing is recorded."""
    course = await preview_course(session, course_id)
    topic = next(
        (t for t in await topic_entries(session, course) if t["id"] == str(topic_id)), None
    )
    if topic is None:
        raise _not_found()
    return await _topic(
        session, principal, course, topic, language=language, threshold=threshold, record=False
    )


async def _topic(
    session: AsyncSession,
    principal: Principal,
    course: dict[str, Any],
    topic: dict[str, Any],
    *,
    language: str | None,
    threshold: float,
    record: bool = True,
) -> dict[str, Any]:
    language = language or course["language"]
    attempts = await attempts_of(session, principal.user_id, [b["id"] for b in topic["blocks"]])
    blocks = [
        await block_view(session, course, b, language=language, attempts=attempts)
        for b in topic["blocks"]
    ]
    if record:
        await record_served_language(
            session, principal.user_id, [b["id"] for b in blocks], language
        )
    # Through-line: the handoff of this topic renders as one Next line.
    handoffs = (course["snapshot"].get("tree", {}).get("through_line") or {}).get("handoffs", [])
    note = next(
        (
            str(h.get("note", ""))
            for h in handoffs
            if isinstance(h, dict) and h.get("topic_plan_node_id") == topic.get("plan_node_id")
        ),
        "",
    )
    all_topics = [
        t
        for m in course["snapshot"].get("tree", {}).get("modules", [])
        for t in m.get("topics", [])
    ]
    is_last = bool(all_topics) and topic.get("plan_node_id") == all_topics[-1].get("plan_node_id")
    return {
        "next_note": note if note and not is_last else "",
        "id": topic["id"],
        "course_id": course["id"],
        "course_title": str(course.get("title", "")),
        "module_id": topic["module_id"],
        "kind": topic["kind"],
        "title": topic["title"],
        "objectives": topic["objectives"],
        "language": language,
        "progress": progress_of(topic["blocks"], attempts, threshold),
        "blocks": blocks,
    }


async def passage_view(
    session: AsyncSession, principal: Principal, passage_id: UUID, *, language: str
) -> dict[str, Any]:
    """Passage text for the companion panel, scoped to an enrolled course."""
    require(principal, "learn.read")
    for row in await _passage_rows(session, passage_id):
        try:
            await enrolled_course(session, principal, UUID(str(row[6])), code="learn.read")
        except NotFoundError:
            continue
        return _passage_entry(row, language)
    raise _not_found()


async def preview_passage(
    session: AsyncSession, course_id: UUID, passage_id: UUID, *, language: str
) -> dict[str, Any]:
    """Passage text for the author's preview panel, scoped to the draft course."""
    for row in await _passage_rows(session, passage_id):
        if str(row[6]) == str(course_id):
            return _passage_entry(row, language)
    raise _not_found()


async def _passage_rows(session: AsyncSession, passage_id: UUID) -> list[Any]:
    return list(
        (
            await session.execute(
                sa.text(
                    "SELECT p.id, p.text, p.address, p.address_key, s.title, s.as_of,"
                    " cs.course_id FROM passages p"
                    " JOIN sources s ON s.id = p.source_id AND s.approval = 'approved'"
                    " JOIN course_sources cs ON cs.source_id = p.source_id"
                    " AND cs.approval = 'approved' WHERE p.id = :p"
                ),
                {"p": str(passage_id)},
            )
        ).all()
    )


def _passage_entry(row: Any, language: str) -> dict[str, Any]:
    label = chip(
        "",
        {
            "passage_id": str(row[0]),
            "address": dict(row[2] or {}),
            "address_key": str(row[3]),
            "title": str(row[4]),
        },
        locale_of(language),
    )
    return {
        "id": str(row[0]),
        "text": str(row[1]),
        "address_label": label["address_label"],
        "source_title": label["source_title"],
        "as_of": row[5].isoformat() if row[5] else None,
        "course_id": str(row[6]),
    }
