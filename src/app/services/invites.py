"""Invite links on a course or on a course group (a tag).

An invite carries an optional seat count and an optional expiry and
is created by a permission holder. Accepting while signed in grants
the student role scoped to the invite's courses; the seat count is
enforced atomically with a row lock so concurrent accepts cannot
oversell the last seat.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ConflictError, NotFoundError
from app.rbac.principal import Principal, bump_version
from app.rbac.require import require
from app.services.audit import write_audit

STUDENT_ROLE_NAME = "student"
STUDENT_PERMISSION_CODES: tuple[str, ...] = ("section.courses",)


async def create_invite(
    session: AsyncSession,
    *,
    actor: Principal,
    course_tags: list[str],
    max_uses: int | None = None,
    expires_at: datetime | None = None,
    granted_role: str = STUDENT_ROLE_NAME,
) -> UUID:
    """Create an invite link; returns its token."""
    require(actor, "invite.create")
    if not course_tags:
        from app.errors import ValidationError

        raise ValidationError("an invite needs at least one course or group tag")
    if max_uses is not None and max_uses < 1:
        from app.errors import ValidationError

        raise ValidationError("max_uses must be positive")
    row = (
        await session.execute(
            sa.text(
                "INSERT INTO invites (scope, course_tags, max_uses, expires_at,"
                " created_by, granted_role)"
                " VALUES (:scope, :tags, :max_uses, :expires, :by, :role)"
                " RETURNING token"
            ),
            {
                "scope": ",".join(sorted(course_tags)),
                "tags": sorted(course_tags),
                "max_uses": max_uses,
                "expires": expires_at,
                "by": str(actor.user_id),
                "role": granted_role,
            },
        )
    ).one()
    token = UUID(str(row[0]))
    await write_audit(
        session,
        actor_id=actor.user_id,
        action="invite.create",
        entity="invite",
        entity_id=str(token),
        meta={"course_tags": sorted(course_tags), "max_uses": max_uses},
    )
    bump_version()
    return token


async def revoke_invite(session: AsyncSession, *, actor: Principal, token: UUID) -> None:
    """Revoke an invite link; later accepts are refused."""
    require(actor, "invite.revoke")
    exists = (
        await session.execute(
            sa.text("SELECT id FROM invites WHERE token = :token"), {"token": str(token)}
        )
    ).first()
    if exists is None:
        raise NotFoundError("invite not found")
    await session.execute(
        sa.text("UPDATE invites SET revoked_at = now() WHERE token = :token"),
        {"token": str(token)},
    )
    await write_audit(
        session,
        actor_id=actor.user_id,
        action="invite.revoke",
        entity="invite",
        entity_id=str(token),
        meta={},
    )
    bump_version()


async def _ensure_student_role(session: AsyncSession, role_name: str) -> None:
    from app.rbac.roles import create_role

    exists = (
        await session.execute(
            sa.text("SELECT id FROM roles WHERE name = :name"), {"name": role_name}
        )
    ).first()
    if exists is None:
        await create_role(
            session,
            name=role_name,
            permission_codes=list(STUDENT_PERMISSION_CODES),
            actor=None,
        )


async def accept_invite(session: AsyncSession, *, user_id: UUID, token: UUID) -> list[str]:
    """Accept an invite: consume one seat and grant the scoped student role.

    Runs in the caller's transaction with the invite row locked, so two
    simultaneous accepts on a one-seat invite leave exactly one winner.
    """
    row = (
        await session.execute(
            sa.text(
                "SELECT id, course_tags, max_uses, used_count, expires_at,"
                " revoked_at, granted_role FROM invites WHERE token = :token FOR UPDATE"
            ),
            {"token": str(token)},
        )
    ).first()
    if row is None:
        raise NotFoundError("invite not found")
    invite_id, course_tags, max_uses, used_count, expires_at, revoked_at, granted_role = row
    now = datetime.now(UTC)
    if revoked_at is not None:
        raise ConflictError("this invite was revoked")
    if expires_at is not None:
        expiry = expires_at
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        if expiry <= now:
            raise ConflictError("this invite expired")
    if max_uses is not None and int(used_count) >= int(max_uses):
        raise ConflictError("this invite has no seats left")
    already = (
        await session.execute(
            sa.text("SELECT id FROM invite_uses WHERE invite_id = :invite AND user_id = :user"),
            {"invite": str(invite_id), "user": str(user_id)},
        )
    ).first()
    if already is not None:
        return sorted(course_tags or [])
    await session.execute(
        sa.text("UPDATE invites SET used_count = used_count + 1 WHERE id = :invite"),
        {"invite": str(invite_id)},
    )
    await session.execute(
        sa.text(
            "INSERT INTO invite_uses (invite_id, user_id) VALUES (:invite, :user)"
            " ON CONFLICT (invite_id, user_id) DO NOTHING"
        ),
        {"invite": str(invite_id), "user": str(user_id)},
    )
    await _ensure_student_role(session, str(granted_role))
    from app.rbac.roles import assign_role

    await assign_role(
        session, user_id, str(granted_role), scope_tags=list(course_tags or []), actor=None
    )
    await write_audit(
        session,
        actor_id=user_id,
        action="invite.accept",
        entity="invite",
        entity_id=str(token),
        meta={"course_tags": list(course_tags or [])},
    )
    bump_version()
    return sorted(course_tags or [])
