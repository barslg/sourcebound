"""User administration: ban and delete with heir or legacy holder."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ConflictError, NotFoundError
from app.rbac.principal import Principal, bump_version
from app.rbac.require import require
from app.services.audit import write_audit


async def list_users(session: AsyncSession) -> list[dict[str, Any]]:
    """The user directory: identity flags without secrets."""
    rows = (
        await session.execute(
            sa.text(
                "SELECT id, email, display_name, is_superadmin, is_legacy,"
                " banned_at, banned_reason, created_at"
                " FROM users ORDER BY created_at"
            )
        )
    ).all()
    return [
        {
            "id": str(user_id),
            "email": email,
            "display_name": name,
            "is_superadmin": bool(admin),
            "is_legacy": bool(legacy),
            "banned_at": banned.isoformat() if banned else None,
            "banned_reason": reason,
            "created_at": created.isoformat() if created else None,
        }
        for user_id, email, name, admin, legacy, banned, reason, created in rows
    ]


async def ban_user(session: AsyncSession, *, actor: Principal, user_id: UUID, reason: str) -> None:
    """Ban an account: stamp it, revoke every session, refuse later sign-ins."""
    require(actor, "user.ban")
    exists = (
        await session.execute(
            sa.text("SELECT id FROM users WHERE id = :user"), {"user": str(user_id)}
        )
    ).first()
    if exists is None:
        raise NotFoundError("user not found")
    await session.execute(
        sa.text("UPDATE users SET banned_at = now(), banned_reason = :reason WHERE id = :user"),
        {"reason": reason, "user": str(user_id)},
    )
    from app.auth.session import revoke_user_sessions

    await revoke_user_sessions(session, user_id)
    await write_audit(
        session,
        actor_id=actor.user_id,
        action="user.ban",
        entity="user",
        entity_id=str(user_id),
        meta={"reason": reason},
    )
    bump_version()


async def _owned_count(session: AsyncSession, user_id: UUID) -> int:
    """Objects whose ownership must move to an heir or stay with a legacy holder."""
    invites = (
        await session.execute(
            sa.text("SELECT count(*) FROM invites WHERE created_by = :user"),
            {"user": str(user_id)},
        )
    ).scalar_one()
    operations = (
        await session.execute(
            sa.text("SELECT count(*) FROM operations WHERE actor_id = :user"),
            {"user": str(user_id)},
        )
    ).scalar_one()
    return int(invites) + int(operations)


async def delete_user(
    session: AsyncSession,
    *,
    actor: Principal,
    user_id: UUID,
    heir_id: UUID | None = None,
    legacy: bool = False,
) -> None:
    """Delete an account and cascade its personal data.

    Sessions, identities, and invite uses cascade in the schema
    (learner answers and progress will cascade the same way once the
    course tree of T03 declares those foreign keys). When the account
    owns objects, the request must name an heir (ownership moves) or
    pass legacy=true (the account becomes a legacy holder: it keeps
    the materials but can never sign in again).
    """
    require(actor, "user.delete")
    exists = (
        await session.execute(
            sa.text("SELECT id FROM users WHERE id = :user"), {"user": str(user_id)}
        )
    ).first()
    if exists is None:
        raise NotFoundError("user not found")
    owned = await _owned_count(session, user_id)
    if owned and heir_id is None and not legacy:
        raise ConflictError("this account owns objects: name an heir or delete with legacy=true")
    if heir_id is not None:
        heir = (
            await session.execute(
                sa.text("SELECT id FROM users WHERE id = :heir"), {"heir": str(heir_id)}
            )
        ).first()
        if heir is None:
            raise NotFoundError("heir not found")
        await session.execute(
            sa.text("UPDATE invites SET created_by = :heir WHERE created_by = :user"),
            {"heir": str(heir_id), "user": str(user_id)},
        )
        await session.execute(
            sa.text("UPDATE operations SET actor_id = :heir WHERE actor_id = :user"),
            {"heir": str(heir_id), "user": str(user_id)},
        )
    if legacy:
        await session.execute(
            sa.text("UPDATE users SET is_legacy = true WHERE id = :user"),
            {"user": str(user_id)},
        )
        from app.auth.session import revoke_user_sessions

        await revoke_user_sessions(session, user_id)
        await write_audit(
            session,
            actor_id=actor.user_id,
            action="user.delete.legacy",
            entity="user",
            entity_id=str(user_id),
            meta={},
        )
        bump_version()
        return
    await write_audit(
        session,
        actor_id=actor.user_id,
        action="user.delete",
        entity="user",
        entity_id=str(user_id),
        meta={"heir_id": str(heir_id) if heir_id else None},
    )
    await session.execute(sa.text("DELETE FROM users WHERE id = :user"), {"user": str(user_id)})
    bump_version()
