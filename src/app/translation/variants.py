"""Learner language choice with source-language fallback and the grader contract.

A learner picks one language for the course. Blocks translated with
status ok render in that language; every other block falls back to the
source language with a visible notice, never a silent gap.

A translation belongs to the block's CONTENT, not to a course version:
rows carry the digest of the source content and key they translated,
so a snapshot or a publish keeps them and an edit of the block makes
them stale (the source is served until it is translated again).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


def block_digest(content: dict[str, Any], answer_key: dict[str, Any]) -> str:
    """Stable digest of the source content and key a translation was made from."""
    canonical = json.dumps(
        {"content": content, "answer_key": answer_key},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


async def latest_block_row(
    session: AsyncSession, *, course_id: UUID, language: str, plan_node_id: str
) -> dict[str, Any] | None:
    """Newest translation row of one block in one language, any course version."""
    row = (
        await session.execute(
            sa.text(
                "SELECT tb.content, tb.status, tb.translated_answer_key, tb.source_digest,"
                " tb.variant FROM translation_blocks tb"
                " JOIN course_translations ct ON ct.id = tb.translation_id"
                " JOIN course_versions cv ON cv.id = ct.course_version_id"
                " WHERE cv.course_id = :course AND ct.language = :lang"
                " AND tb.plan_node_id = :node"
                " ORDER BY ct.created_at DESC, tb.id LIMIT 1"
            ),
            {"course": str(course_id), "lang": language, "node": plan_node_id},
        )
    ).first()
    if row is None:
        return None
    return {
        "content": row[0],
        "status": str(row[1]),
        "translated_answer_key": row[2],
        "source_digest": row[3],
        "variant": int(row[4] or 0),
    }


async def learner_block(
    session: AsyncSession,
    *,
    course_id: UUID,
    language: str,
    block: dict[str, Any],
    source_language: str | None = None,
) -> dict[str, Any]:
    """One block for one learner: content and the matching key of ONE language.

    `block` carries plan_node_id, content, and answer_key of the source.
    A translated block serves its translation and its translated key
    (what the grader needs); everything else serves the source content
    and the source key with a localised notice. The key never reaches
    the learner route; it is for the grader only.
    """
    from app.i18n.messages import fallback_notice

    content = dict(block["content"])
    answer_key = dict(block.get("answer_key", {}))
    resolved_source = source_language or await _course_language(session, course_id=course_id)
    row = await latest_block_row(
        session, course_id=course_id, language=language, plan_node_id=str(block["plan_node_id"])
    )
    if (
        row is not None
        and row["status"] == "ok"
        and row["source_digest"] == block_digest(content, answer_key)
    ):
        return {
            "content": row["content"],
            "answer_key": dict(row["translated_answer_key"] or {}),
            "language": language,
            "fallback_notice": None,
        }
    return {
        "content": content,
        "answer_key": answer_key,
        "language": resolved_source,
        "fallback_notice": fallback_notice(
            target_language=language, source_language=resolved_source
        ),
    }


async def _course_language(session: AsyncSession, *, course_id: UUID) -> str:
    course = (
        await session.execute(
            sa.text("SELECT language FROM courses WHERE id = :course"),
            {"course": str(course_id)},
        )
    ).first()
    return str(course[0]) if course is not None and course[0] else "en"


async def available_languages(session: AsyncSession, *, course_id: UUID) -> list[dict[str, Any]]:
    """Languages a learner can pick for one course (newest status per language)."""
    rows = (
        await session.execute(
            sa.text(
                "SELECT DISTINCT ON (ct.language) ct.language, ct.status"
                " FROM course_translations ct JOIN course_versions cv"
                " ON cv.id = ct.course_version_id WHERE cv.course_id = :course"
                " ORDER BY ct.language, ct.created_at DESC"
            ),
            {"course": str(course_id)},
        )
    ).all()
    return [{"language": str(lang), "status": str(status)} for lang, status in rows]
