"""Translation memory: segment reuse across blocks and runs.

Planted sentences never enter this store (errorspot exclusion): only
verified-true segments are cached and re-served.
"""

from __future__ import annotations

import hashlib

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.course.errorspot import is_excluded_from


def segment_hash(*, source_text: str, source_language: str, target_language: str) -> str:
    """Stable key of one cached segment translation."""
    canonical = f"{source_language}\u0000{target_language}\u0000{source_text}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def tm_lookup(
    session: AsyncSession,
    *,
    source_text: str,
    source_language: str,
    target_language: str,
) -> str | None:
    """Cached translation of one segment, or None on a miss."""
    row = (
        await session.execute(
            sa.text("SELECT translated_text FROM translation_memory WHERE source_hash = :digest"),
            {
                "digest": segment_hash(
                    source_text=source_text,
                    source_language=source_language,
                    target_language=target_language,
                )
            },
        )
    ).first()
    return str(row[0]) if row is not None else None


async def tm_store(
    session: AsyncSession,
    *,
    source_text: str,
    translated_text: str,
    source_language: str,
    target_language: str,
    planted: bool = False,
) -> None:
    """Cache one segment translation; planted rows are refused."""
    if is_excluded_from({"planted": planted}, "translation_memory"):
        return
    await session.execute(
        sa.text(
            "INSERT INTO translation_memory"
            " (source_hash, source_language, target_language, source_text, translated_text)"
            " VALUES (:digest, :source_lang, :target_lang, :source, :translated)"
            " ON CONFLICT (source_hash) DO NOTHING"
        ),
        {
            "digest": segment_hash(
                source_text=source_text,
                source_language=source_language,
                target_language=target_language,
            ),
            "source_lang": source_language,
            "target_lang": target_language,
            "source": source_text,
            "translated": translated_text,
        },
    )
