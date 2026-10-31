"""Per-user notifications: newest-first listing with a read flag."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.rbac.principal import Principal


async def list_notifications(session: AsyncSession, *, actor: Principal) -> list[dict[str, Any]]:
    """The caller's notifications, newest first, with a read flag."""
    rows = (
        await session.execute(
            sa.text(
                "SELECT id, kind, payload, read_at, created_at FROM notifications"
                " WHERE user_id = :user ORDER BY created_at DESC"
            ),
            {"user": str(actor.user_id)},
        )
    ).all()
    return [
        {
            "id": str(note_id),
            "kind": str(kind),
            "payload": dict(payload or {}),
            "read": read_at is not None,
            "created_at": created.isoformat() if created else None,
        }
        for note_id, kind, payload, read_at, created in rows
    ]


async def mark_notification_read(
    session: AsyncSession, *, actor: Principal, notification_id: UUID
) -> None:
    """Mark one of the caller's own notifications as read."""
    from app.errors import NotFoundError

    existing = (
        await session.execute(
            sa.text("SELECT id FROM notifications WHERE id = :note AND user_id = :user"),
            {"note": str(notification_id), "user": str(actor.user_id)},
        )
    ).first()
    if existing is None:
        raise NotFoundError("notification not found")
    await session.execute(
        sa.text("UPDATE notifications SET read_at = now() WHERE id = :note"),
        {"note": str(notification_id)},
    )
