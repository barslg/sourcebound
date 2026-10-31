"""Invite links on a course or on a course group (a tag).

An invite carries an optional seat count and an optional expiry and
is created by a permission holder. Only the SHA-256 hash of the token
is stored; the raw token is shown once at creation. Accepting while
signed in grants the student role scoped to the invite's courses; the
seat count is enforced atomically with a row lock so concurrent
accepts cannot oversell the last seat. Audit rows carry the invite id,
never the token.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ConflictError, NotFoundError
from app.rbac.principal import Principal
from app.rbac.require import require, require_scope
from app.services.audit import write_audit

STUDENT_ROLE_NAME = "student"
STUDENT_PERMISSION_CODES: tuple[str, ...] = ("section.courses",)


def hash_invite_token(token: str) -> str:
    """SHA-256 hex of an invite token for storage and lookup."""
    return hashlib.sha256(token.encode()).hexdigest()


def mint_invite_token() -> str:
    """A fresh raw invite token, shown once and never stored."""
    return secrets.token_hex(32)


async def create_invite(
    session: AsyncSession,
    *,
    actor: Principal,
    course_tags: list[str],
    max_uses: int | None = None,
    expires_at: datetime | None = None,
) -> str:
    """Create an invite link; returns the raw token shown once."""
    require(actor, "invite.create")
    if not course_tags:
        from app.errors import ValidationError

        raise ValidationError("an invite needs at least one course or group tag")
    if max_uses is not None and max_uses < 1:
        from app.errors import ValidationError

        raise ValidationError("max_uses must be positive")
    require_scope(actor, course_tags, code="invite.create")
    raw_token = mint_invite_token()
    row = (
        await session.execute(
            sa.text(
                "INSERT INTO invites (scope, course_tags, max_uses, expires_at,"
                " created_by, granted_role, token_hash)"
                " VALUES (:scope, :tags, :max_uses, :expires, :by, :role, :hash)"
                " RETURNING id"
            ),
            {
                "scope": ",".join(sorted(course_tags)),
                "tags": sorted(course_tags),
                "max_uses": max_uses,
                "expires": expires_at,
                "by": str(actor.user_id),
                "role": STUDENT_ROLE_NAME,
                "hash": hash_invite_token(raw_token),
            },
        )
    ).one()
    invite_id = str(row[0])
    await write_audit(
        session,
        actor_id=actor.user_id,
        action="invite.create",
        entity="invite",
        entity_id=invite_id,
        meta={"course_tags": sorted(course_tags), "max_uses": max_uses},
    )
    return raw_token


async def list_invites(session: AsyncSession, *, actor: Principal) -> list[dict[str, Any]]:
    """Invites visible in the actor's scope; superadmins and global actors see all."""
    try:
        require(actor, "invite.create")
    except Exception:
        require(actor, "invite.revoke")
    rows = (
        await session.execute(
            sa.text(
                "SELECT id, course_tags, max_uses, used_count, expires_at, revoked_at"
                " FROM invites ORDER BY created_at DESC"
            )
        )
    ).all()
    visible_scope = actor.scope_for_any(("invite.create", "invite.revoke"))
    visible: list[dict[str, Any]] = []
    for invite_id, tags, max_uses, used_count, expires_at, revoked_at in rows:
        if not actor.is_superadmin and visible_scope is not None:
            if set(tags or []).isdisjoint(visible_scope):
                continue
        visible.append(
            {
                "id": str(invite_id),
                "course_tags": list(tags or []),
                "max_uses": max_uses,
                "used_count": int(used_count),
                "expires_at": expires_at.isoformat() if expires_at else None,
                "revoked_at": revoked_at.isoformat() if revoked_at else None,
            }
        )
    return visible


async def revoke_invite(session: AsyncSession, *, actor: Principal, invite_id: UUID) -> None:
    """Revoke an invite link by id; later accepts are refused."""
    require(actor, "invite.revoke")
    row = (
        await session.execute(
            sa.text("SELECT id, course_tags FROM invites WHERE id = :invite"),
            {"invite": str(invite_id)},
        )
    ).first()
    if row is None:
        raise NotFoundError("invite not found")
    require_scope(actor, list(row[1] or []), code="invite.revoke")
    await session.execute(
        sa.text("UPDATE invites SET revoked_at = now() WHERE id = :invite"),
        {"invite": str(row[0])},
    )
    await write_audit(
        session,
        actor_id=actor.user_id,
        action="invite.revoke",
        entity="invite",
        entity_id=str(row[0]),
        meta={},
    )


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


async def _consume_seat(
    session: AsyncSession,
    *,
    user_id: UUID,
    invite_id: object,
    course_tags: list[str] | None,
    max_uses: int | None,
    used_count: int,
    expires_at: datetime | None,
    revoked_at: datetime | None,
) -> list[str]:
    """Consume one seat and grant the scoped student role for a locked invite."""
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
    await _ensure_student_role(session, STUDENT_ROLE_NAME)
    from app.rbac.roles import assign_role

    await assign_role(
        session, user_id, STUDENT_ROLE_NAME, scope_tags=list(course_tags or []), actor=None
    )
    await write_audit(
        session,
        actor_id=user_id,
        action="invite.accept",
        entity="invite",
        entity_id=str(invite_id),
        meta={"course_tags": list(course_tags or [])},
    )
    return sorted(course_tags or [])


async def accept_invite(session: AsyncSession, *, user_id: UUID, token_hash: str) -> list[str]:
    """Accept an invite: consume one seat and grant the scoped student role.

    Runs in the caller's transaction with the invite row locked, so two
    simultaneous accepts on a one-seat invite leave exactly one winner.
    The lookup key is the token hash; plaintext tokens never reach the DB.
    """
    row = (
        await session.execute(
            sa.text(
                "SELECT id, course_tags, max_uses, used_count, expires_at,"
                " revoked_at FROM invites WHERE token_hash = :hash FOR UPDATE"
            ),
            {"hash": token_hash},
        )
    ).first()
    if row is None:
        raise NotFoundError("invite not found")
    invite_id, course_tags, max_uses, used_count, expires_at, revoked_at = row
    return await _consume_seat(
        session,
        user_id=user_id,
        invite_id=invite_id,
        course_tags=list(course_tags or []),
        max_uses=max_uses,
        used_count=int(used_count),
        expires_at=expires_at,
        revoked_at=revoked_at,
    )


async def accept_invite_by_id(
    session: AsyncSession, *, user_id: UUID, invite_id: UUID
) -> list[str]:
    """Accept an invite by id (the signed-out flow stashes the id, not the token)."""
    row = (
        await session.execute(
            sa.text(
                "SELECT id, course_tags, max_uses, used_count, expires_at,"
                " revoked_at FROM invites WHERE id = :invite FOR UPDATE"
            ),
            {"invite": str(invite_id)},
        )
    ).first()
    if row is None:
        raise NotFoundError("invite not found")
    found_id, course_tags, max_uses, used_count, expires_at, revoked_at = row
    return await _consume_seat(
        session,
        user_id=user_id,
        invite_id=found_id,
        course_tags=list(course_tags or []),
        max_uses=max_uses,
        used_count=int(used_count),
        expires_at=expires_at,
        revoked_at=revoked_at,
    )


async def invite_details(session: AsyncSession, *, token_hash: str) -> tuple[UUID, list[str]]:
    """The (id, course tags) behind a token hash; 404 when the token is unknown."""
    row = (
        await session.execute(
            sa.text("SELECT id, course_tags FROM invites WHERE token_hash = :hash"),
            {"hash": token_hash},
        )
    ).first()
    if row is None:
        raise NotFoundError("invite not found")
    return UUID(str(row[0])), sorted(row[1] or [])
