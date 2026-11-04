"""Per-user notifications: the newest ten, each with what the operation did."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.rbac.principal import Principal

NEWEST = 10


async def list_notifications(session: AsyncSession, *, actor: Principal) -> list[dict[str, Any]]:
    """The caller's newest notifications with the operation's kind, outcome, course and run."""
    rows = (
        await session.execute(
            sa.text(
                "SELECT n.id, n.kind, n.payload, n.read_at, n.created_at,"
                " o.kind, o.status, o.error, o.result ->> 'run_id',"
                " COALESCE(c.title, c2.title)"
                " FROM notifications n"
                " LEFT JOIN operations o ON o.id::text = n.payload ->> 'operation_id'"
                " LEFT JOIN runs r ON r.id::text = o.result ->> 'run_id'"
                " LEFT JOIN courses c ON c.id = r.course_id"
                " LEFT JOIN courses c2 ON c2.id::text = o.params ->> 'course_id'"
                " WHERE n.user_id = :user ORDER BY n.created_at DESC LIMIT :newest"
            ),
            {"user": str(actor.user_id), "newest": NEWEST},
        )
    ).all()
    return [
        {
            "id": str(note_id),
            "kind": str(kind),
            "payload": dict(payload or {}),
            "read": read_at is not None,
            "created_at": created.isoformat() if created else None,
            "operation_kind": str(op_kind or ""),
            "status": str(op_status or (payload or {}).get("status") or ""),
            "error": str(error) if error else None,
            "run_id": str(run_id) if run_id else None,
            "course_title": str(title) if title else None,
        }
        for (
            note_id,
            kind,
            payload,
            read_at,
            created,
            op_kind,
            op_status,
            error,
            run_id,
            title,
        ) in rows
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


async def clear_notifications(session: AsyncSession, *, actor: Principal) -> int:
    """Delete every notification of the caller; the count removed."""
    rows = (
        await session.execute(
            sa.text("DELETE FROM notifications WHERE user_id = :user RETURNING id"),
            {"user": str(actor.user_id)},
        )
    ).all()
    return len(rows)
