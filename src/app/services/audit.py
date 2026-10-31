"""Audit rows are written in the same transaction as the change."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


async def write_audit(
    session: AsyncSession,
    *,
    actor_id: UUID | None,
    action: str,
    entity: str = "",
    entity_id: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    """Append one audit row on the caller's session; failures propagate.

    The caller commits, so a failing audit insert rolls the whole
    change back instead of landing a change without its trail.
    """
    payload = json.dumps(meta or {})
    await session.execute(
        sa.text(
            "INSERT INTO audit_log (actor_id, action, entity, entity_id, meta)"
            " VALUES (:actor, :action, :entity, :entity_id, CAST(:meta AS jsonb))"
        ),
        {
            "actor": str(actor_id) if actor_id else None,
            "action": action,
            "entity": entity,
            "entity_id": entity_id,
            "meta": payload,
        },
    )
