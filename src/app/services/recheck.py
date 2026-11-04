"""Re-run the code checks over a stored course and lift holds they no longer find.

A check fixed after a run is not a reason to pay for a rewrite: the block is
the same, only the judge changed. `recheck_held` recomputes every code hold
over the held blocks (SELECT + UPDATE on blocks only, no model call) and
releases a block when no line remains and the topic's reader passed this very
version; with no line and no reading of this version the block is a draft
again, for the rework reread. Callers: `POST /courses/{id}/held/recheck`,
`rework_blocks_step` (before it pays for a rewrite), `scripts/replay_holds.py`.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.course.replay import is_code_hold, join_hold_lines, plan_coverage_errors, replay_blocks


async def load_blocks(session: AsyncSession, *, course_id: UUID) -> list[dict[str, Any]]:
    """Every block of one course with its current version, plan content and citations."""
    rows = (
        await session.execute(
            sa.text(
                "SELECT b.id, b.plan_node_id, b.role, b.block_type, b.status,"
                " coalesce(b.hold_reason, ''), bv.id, bv.content, bv.answer_key,"
                " t.plan_node_id, t.kind, t.title, t.objectives,"
                " (SELECT content FROM block_versions"
                "  WHERE block_id = b.id AND content -> 'source_handles' IS NOT NULL"
                "  ORDER BY version_no DESC LIMIT 1),"
                " b.accepted_version_id = b.current_version_id"
                " FROM blocks b JOIN topics t ON t.id = b.topic_id"
                " JOIN modules m ON m.id = t.module_id"
                " LEFT JOIN block_versions bv ON bv.id = b.current_version_id"
                " WHERE m.course_id = :c ORDER BY m.ordinal, t.ordinal, b.ordinal"
            ),
            {"c": str(course_id)},
        )
    ).all()
    version_ids = [str(r[6]) for r in rows if r[6] is not None]
    citations = (
        await session.execute(
            sa.text(
                "SELECT block_version_id, passage_id, quote FROM citations"
                " WHERE block_version_id::text = ANY(:ids) ORDER BY quote_start"
            ),
            {"ids": version_ids},
        )
    ).all()
    cited: dict[str, list[tuple[str, str]]] = {}
    for version_id, passage_id, quote in citations:
        cited.setdefault(str(version_id), []).append((str(passage_id), str(quote)))
    return [
        {
            "id": str(r[0]),
            "node": str(r[1]),
            "role": str(r[2]),
            "block_type": str(r[3]),
            "status": str(r[4]),
            "hold_reason": str(r[5]),
            "version_id": str(r[6]) if r[6] is not None else "",
            "content": dict(r[7] or {}),
            "answer_key": dict(r[8] or {}),
            "topic": str(r[9]),
            "topic_kind": str(r[10]),
            "topic_title": str(r[11]),
            "objectives": list(r[12] or []),
            "plan_content": dict(r[13] or {}),
            "author_accepted": bool(r[14]),
            "citations": cited.get(str(r[6]), []) if r[6] is not None else [],
        }
        for r in rows
    ]


async def replay_course(session: AsyncSession, *, course_id: UUID) -> dict[str, Any]:
    """Per-block verdicts of the code checks over one course, with the plan verdict."""
    from app.services import courses as course_service
    from app.services.planruns import _course_sources

    raw_tree = await course_service.read_tree(session, course_id=course_id)
    numbered = await _course_sources(session, course_id=course_id, raw_tree=raw_tree)
    blocks = await load_blocks(session, course_id=course_id)
    return {
        "blocks": replay_blocks(blocks, numbered),
        "plan_errors": plan_coverage_errors(blocks, numbered),
        "passages": len(numbered),
    }


async def _reader_passed_this_version(
    session: AsyncSession, *, course_id: UUID, topic_node: str, version_id: str
) -> bool:
    """True when a reader step of this topic finished after the version was written."""
    row = (
        await session.execute(
            sa.text(
                "SELECT 1 FROM run_steps rs JOIN runs r ON r.id = rs.run_id"
                " JOIN block_versions bv ON bv.id = :v"
                " WHERE r.course_id = :c AND rs.step_key = :key AND rs.status = 'done'"
                " AND rs.finished_at >= bv.created_at LIMIT 1"
            ),
            {"v": version_id, "c": str(course_id), "key": f"reader:{topic_node}"},
        )
    ).first()
    return row is not None


async def recheck_held(
    session: AsyncSession, *, course_id: UUID, hold_accepted: bool = False
) -> dict[str, int]:
    """Recompute the code holds of every held block; release what nothing holds any more.

    With `hold_accepted` (the author's button on an unpublished course) an
    accepted block that a current check rejects is held too, so a check added
    after the run reaches the blocks it already accepted. A version the author
    accepted by hand (`accept_block`) stays accepted, as on the reader path.
    """
    from app.services import courses as course_service

    await course_service.get_course(session, course_id=course_id)
    replayed = await replay_course(session, course_id=course_id)
    rechecked = released = unread = newly_held = 0
    for row in replayed["blocks"]:
        if (
            hold_accepted
            and row["stored_status"] == "accepted"
            and row["holds"]
            and not row["author_accepted"]
        ):
            await session.execute(
                sa.text("UPDATE blocks SET status = 'held', hold_reason = :r WHERE id = :b"),
                {"r": join_hold_lines(row["holds"]), "b": row["id"]},
            )
            newly_held += 1
            continue
        if row["stored_status"] != "held":
            continue
        rechecked += 1
        stored = row["stored_reason"].split("; ")
        kept = [line for line in stored if line and not is_code_hold(line)]
        lines = kept + list(row["holds"])
        if not lines:
            if await _reader_passed_this_version(
                session,
                course_id=course_id,
                topic_node=row["topic_node"],
                version_id=row["version_id"],
            ):
                await session.execute(
                    sa.text(
                        "UPDATE blocks SET status = 'accepted', hold_reason = NULL,"
                        " accepted_version_id = current_version_id WHERE id = :b"
                    ),
                    {"b": row["id"]},
                )
                released += 1
            else:
                # nothing holds it, nobody has read this version: a draft
                # again, and the next rework reread (or rewrite run) reads it
                await session.execute(
                    sa.text("UPDATE blocks SET status = 'draft', hold_reason = NULL WHERE id = :b"),
                    {"b": row["id"]},
                )
                unread += 1
            continue
        reason = join_hold_lines(lines)
        if reason != row["stored_reason"]:
            await session.execute(
                sa.text("UPDATE blocks SET hold_reason = :r WHERE id = :b"),
                {"r": reason, "b": row["id"]},
            )
    return {
        "rechecked": rechecked,
        "released": released,
        "unread": unread,
        "held": rechecked - released - unread,
        "newly_held": newly_held,
    }
