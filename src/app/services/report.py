"""Teacher report: per learner and topic, plus find-the-error counters by kind.

Everything is computed from learner_answers by code: completion and
mastery use the same progress function as the learner's own outline,
and hits / false alarms / misses are the classifications stored with
each attempt (never re-graded). Caller: controllers/t03.py get_report
(the teacher report screen and its CSV download).
"""

from __future__ import annotations

import csv
import io
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import learner
from app.services.grading import add_counts
from app.services.invites import group_keys_of

CSV_COLUMNS = (
    "section",
    "learner",
    "email",
    "topic",
    "kind",
    "completion",
    "mastery",
    "status",
    "last_activity",
    "hits",
    "false_alarms",
    "misses",
)


async def _snapshot_course(session: AsyncSession, course_id: UUID) -> dict[str, Any]:
    row = (
        await session.execute(
            sa.text(
                "SELECT c.id, c.title, c.language, c.scope_tags, cv.snapshot, c.owner_id"
                " FROM courses c LEFT JOIN course_versions cv ON cv.id = c.published_version_id"
                " WHERE c.id = :c"
            ),
            {"c": str(course_id)},
        )
    ).one()
    return {
        "id": str(row[0]),
        "title": str(row[1]),
        "language": str(row[2]),
        "scope_tags": list(row[3] or []),
        "snapshot": dict(row[4] or {}),
        "owner_id": str(row[5]),
    }


async def _learners(session: AsyncSession, course: dict[str, Any]) -> list[dict[str, Any]]:
    tags = [course["id"], *group_keys_of(course["owner_id"], course["scope_tags"])]
    rows = (
        await session.execute(
            sa.text(
                "SELECT DISTINCT u.id, u.display_name, u.email FROM users u WHERE u.id IN ("
                " SELECT ur.user_id FROM user_roles ur JOIN roles r ON r.id = ur.role_id"
                " WHERE r.name = :role AND ur.scope_tags && CAST(:tags AS text[])"
                " UNION SELECT la.user_id FROM learner_answers la"
                " JOIN blocks b ON b.id = la.block_id"
                " JOIN topics t ON t.id = b.topic_id JOIN modules m ON m.id = t.module_id"
                " WHERE m.course_id = :course) ORDER BY u.email"
            ),
            {"role": learner.STUDENT_ROLE, "tags": tags, "course": course["id"]},
        )
    ).all()
    return [{"id": str(r[0]), "name": str(r[1] or ""), "email": str(r[2])} for r in rows]


async def course_report(
    session: AsyncSession, course_id: UUID, *, threshold: float
) -> dict[str, Any]:
    """The report for one course's published version."""
    course = await _snapshot_course(session, course_id)
    topics = await learner.topic_entries(session, course) if course["snapshot"] else []
    block_ids = [b["id"] for t in topics for b in t["blocks"]]
    rows = (
        await session.execute(
            sa.text(
                "SELECT user_id, block_id, count(*), max(created_at),"
                " (array_agg(score ORDER BY attempt_no DESC))[1],"
                " (array_agg(result ORDER BY attempt_no) FILTER (WHERE result ? 'hit'))"
                " FROM learner_answers WHERE block_id::text = ANY(:ids)"
                " GROUP BY user_id, block_id"
            ),
            {"ids": block_ids},
        )
    ).all()
    by_user: dict[str, dict[str, dict[str, Any]]] = {}
    last_seen: dict[tuple[str, str], Any] = {}
    kinds: dict[str, dict[str, dict[str, int]]] = {}
    for user, block, count, last, score, results in rows:
        by_user.setdefault(str(user), {})[str(block)] = {
            "count": int(count),
            "last_score": float(score),
            "best_score": float(score),
        }
        last_seen[(str(user), str(block))] = last
        for result in results or []:
            add_counts(kinds.setdefault(str(user), {}), dict(result))
    out: list[dict[str, Any]] = []
    course_kinds: dict[str, dict[str, int]] = {}
    for person in await _learners(session, course):
        attempts = by_user.get(person["id"], {})
        topic_rows = []
        for topic in topics:
            ids = [b["id"] for b in topic["blocks"]]
            stamps = [last_seen[(person["id"], i)] for i in ids if (person["id"], i) in last_seen]
            topic_rows.append(
                {
                    "topic_id": topic["id"],
                    "title": topic["title"],
                    "kind": topic["kind"],
                    **learner.progress_of(topic["blocks"], attempts, threshold),
                    "last_activity": max(stamps).isoformat() if stamps else None,
                }
            )
        stamps = [v for (u, _), v in last_seen.items() if u == person["id"]]
        mine = kinds.get(person["id"], {})
        for kind, counts in mine.items():
            bucket = course_kinds.setdefault(kind, {"hits": 0, "false_alarms": 0, "misses": 0})
            for name, value in counts.items():
                bucket[name] += value
        out.append(
            {
                "user_id": person["id"],
                "display_name": person["name"],
                "email": person["email"],
                "last_activity": max(stamps).isoformat() if stamps else None,
                "topics": topic_rows,
                "find_the_error": mine,
            }
        )
    return {
        "course_id": course["id"],
        "title": course["title"],
        "published": bool(course["snapshot"]),
        "learners": out,
        "find_the_error": course_kinds,
    }


def _cell(value: Any) -> str:
    """Spreadsheet-safe text: a formula prefix is defused."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def report_csv(report: dict[str, Any]) -> str:
    """Long-format CSV: one row per learner and topic, one per learner and error kind."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for person in report["learners"]:
        who = [_cell(person["display_name"]), _cell(person["email"])]
        for topic in person["topics"]:
            writer.writerow(
                [
                    "topic",
                    *who,
                    _cell(topic["title"]),
                    topic["kind"],
                    topic["completion"],
                    "" if topic["mastery"] is None else topic["mastery"],
                    topic["status"],
                    topic["last_activity"] or "",
                    "",
                    "",
                    "",
                ]
            )
        for kind, counts in sorted(person["find_the_error"].items()):
            writer.writerow(
                [
                    "error_kind",
                    *who,
                    "",
                    kind,
                    "",
                    "",
                    "",
                    "",
                    counts["hits"],
                    counts["false_alarms"],
                    counts["misses"],
                ]
            )
    return buffer.getvalue()
