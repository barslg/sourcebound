"""Content-addressed reply store: a request already answered is never paid twice.

The key is the SHA-256 of the canonical JSON of (model, prompt version,
request body). Like the call log, this module never commits.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm import llm_replies


def request_hash(model: str, prompt_version: str, request: dict[str, Any], variant: int = 0) -> str:
    """Stable hex digest identifying one answered request.

    Variant scopes explicit "new draft" requests: variant 0 is the live
    answer, a raised variant re-pays for a fresh draft of the same body.
    """
    canonical = json.dumps(
        {"model": model, "prompt_version": prompt_version, "request": request, "variant": variant},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def get_reply(session: AsyncSession, digest: str) -> dict[str, Any] | None:
    """Return the stored reply for a request hash, or None on a miss."""
    result = await session.execute(
        sa.select(llm_replies.c.response).where(llm_replies.c.request_hash == digest)
    )
    reply = result.scalar()
    assert reply is None or isinstance(reply, dict)
    return reply


async def put_reply(
    session: AsyncSession,
    digest: str,
    model: str,
    prompt_version: str,
    response: dict[str, Any],
) -> None:
    """Store a reply; re-storing the same hash replaces the row."""
    await session.execute(
        insert(llm_replies)
        .values(
            request_hash=digest,
            model=model,
            prompt_version=prompt_version,
            response=response,
        )
        .on_conflict_do_update(
            index_elements=[llm_replies.c.request_hash],
            set_={"response": response},
        )
    )
