"""Learner language choice with source-language fallback.

A learner picks one language for the course. Blocks translated with
status ok render in that language; every other block falls back to the
source language with a visible notice, never a silent gap.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


async def resolve_learner_block(
    session: AsyncSession,
    *,
    course_id: UUID,
    version_id: UUID,
    language: str,
    plan_node_id: str,
    source_content: dict[str, Any],
    source_language: str | None = None,
) -> dict[str, Any]:
    """Translated block for one learner, or the source with a notice."""
    row = (
        await session.execute(
            sa.text(
                "SELECT tb.content, tb.status, ct.source_language"
                " FROM translation_blocks tb"
                " JOIN course_translations ct ON ct.id = tb.translation_id"
                " WHERE ct.course_version_id = :version AND ct.language = :lang"
                " AND tb.plan_node_id = :node"
            ),
            {"version": str(version_id), "lang": language, "node": plan_node_id},
        )
    ).first()
    resolved_source = source_language or await _course_language(
        session, course_id=course_id, version_id=version_id
    )
    if row is not None and str(row[1]) == "ok":
        return {
            "content": row[0],
            "language": language,
            "fallback_notice": None,
        }
    return {
        "content": source_content,
        "language": resolved_source,
        "fallback_notice": (
            f"This block is not yet translated into {language};"
            f" showing the {resolved_source} original."
        ),
    }


async def _course_language(session: AsyncSession, *, course_id: UUID, version_id: UUID) -> str:
    row = (
        await session.execute(
            sa.text(
                "SELECT ct.source_language FROM course_translations ct"
                " WHERE ct.course_version_id = :version LIMIT 1"
            ),
            {"version": str(version_id)},
        )
    ).first()
    if row is not None:
        return str(row[0])
    course = (
        await session.execute(
            sa.text("SELECT language FROM courses WHERE id = :course"),
            {"course": str(course_id)},
        )
    ).first()
    return str(course[0]) if course is not None and course[0] else "en"


async def available_languages(session: AsyncSession, *, version_id: UUID) -> list[dict[str, Any]]:
    """Languages a learner can pick for one course version."""
    rows = (
        await session.execute(
            sa.text(
                "SELECT language, status FROM course_translations"
                " WHERE course_version_id = :version ORDER BY language"
            ),
            {"version": str(version_id)},
        )
    ).all()
    return [{"language": str(lang), "status": str(status)} for lang, status in rows]
