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
from collections.abc import Callable, Collection
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
STUDENT_PERMISSION_CODES: tuple[str, ...] = (
    "section.courses",
    "learn.read",
    "learn.answer",
    "learn.ask",
)


GROUP_SEP = ":"


def _is_course_id(tag: str) -> bool:
    try:
        UUID(tag)
    except ValueError:
        return False
    return True


def group_key(owner_id: UUID | str, tag: str) -> str:
    """The stored key of a group tag: owner-scoped, so two owners' "oslo" never meet."""
    return f"{owner_id}{GROUP_SEP}{tag}"


def display_tag(stored: str) -> str:
    """What the author typed: a course id as is, a group key without its owner prefix."""
    owner, sep, tag = stored.partition(GROUP_SEP)
    return tag if sep and _is_course_id(owner) else stored


def group_keys_of(owner_id: UUID | str, tags: Collection[str]) -> list[str]:
    """Group keys one owner's course tags open (what a student grant is compared against)."""
    return [group_key(owner_id, tag) for tag in tags]


async def _owned(session: AsyncSession, owner_id: UUID) -> tuple[set[str], set[str]]:
    """(ids, group tags) of the courses one user owns."""
    rows = (
        await session.execute(
            sa.text("SELECT id, scope_tags FROM courses WHERE owner_id = :owner"),
            {"owner": str(owner_id)},
        )
    ).all()
    return {str(r[0]) for r in rows}, {t for r in rows for t in (r[1] or [])}


async def _qualified_tags(
    session: AsyncSession, actor: Principal, course_tags: list[str]
) -> list[str]:
    """Stored keys for the named tags, 404 unless the actor may edit EVERY named course.

    A course id must be a course the actor owns (a superadmin: any existing
    course); a group tag must be carried by a course the actor owns, and is
    stored under the actor's id.
    """
    from app.errors import ValidationError

    owned_ids, owned_tags = await _owned(session, actor.user_id)
    stored: list[str] = []
    for raw in course_tags:
        tag = raw.strip()
        if not tag or (GROUP_SEP in tag and not _is_course_id(tag)):
            raise ValidationError("a tag is a course id or a group name without ':'")
        if _is_course_id(tag):
            exists = (
                tag in owned_ids
                or actor.is_superadmin
                and (
                    await session.execute(
                        sa.text("SELECT 1 FROM courses WHERE id = :c"), {"c": tag}
                    )
                ).first()
                is not None
            )
            if not exists:
                raise NotFoundError("object not found")
            stored.append(tag)
        else:
            if tag not in owned_tags:
                raise NotFoundError("object not found")
            stored.append(group_key(actor.user_id, tag))
    return sorted(set(stored))


async def _manageable(session: AsyncSession, actor: Principal) -> Callable[[list[str]], bool]:
    """Predicate: an invite's stored tags are all inside this actor's own courses."""
    if actor.is_superadmin:
        return lambda tags: True
    owned_ids, _ = await _owned(session, actor.user_id)
    prefix = f"{actor.user_id}{GROUP_SEP}"

    def inside(tags: list[str]) -> bool:
        return bool(tags) and all(t in owned_ids or t.startswith(prefix) for t in tags)

    return inside


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
    stored_tags = await _qualified_tags(session, actor, course_tags)
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
                "scope": ",".join(stored_tags),
                "tags": stored_tags,
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
        meta={"course_tags": stored_tags, "max_uses": max_uses},
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
                "SELECT i.id, i.course_tags, i.max_uses, i.used_count, i.expires_at, i.revoked_at,"
                " i.created_by, cu.display_name FROM invites i"
                " LEFT JOIN users cu ON cu.id = i.created_by ORDER BY i.created_at DESC"
            )
        )
    ).all()
    accepted: dict[str, list[dict[str, str]]] = {}
    for invite_id, user_id, display_name in (
        await session.execute(
            sa.text(
                "SELECT iu.invite_id, u.id, u.display_name FROM invite_uses iu"
                " JOIN users u ON u.id = iu.user_id ORDER BY iu.created_at"
            )
        )
    ).all():
        accepted.setdefault(str(invite_id), []).append(
            {"id": str(user_id), "display_name": str(display_name or "")}
        )
    visible_scope = actor.scope_for_any(("invite.create", "invite.revoke"))
    inside = await _manageable(session, actor)
    visible: list[dict[str, Any]] = []
    for invite_id, tags, max_uses, used_count, expires_at, revoked_at, by, by_name in rows:
        if not inside(list(tags or [])):
            continue
        if not actor.is_superadmin and visible_scope is not None:
            if {display_tag(t) for t in tags or []}.isdisjoint(visible_scope):
                continue
        visible.append(
            {
                "id": str(invite_id),
                "course_tags": [display_tag(t) for t in tags or []],
                "course_titles": await course_titles(session, list(tags or [])),
                "max_uses": max_uses,
                "used_count": int(used_count),
                "expires_at": expires_at.isoformat() if expires_at else None,
                "revoked_at": revoked_at.isoformat() if revoked_at else None,
                "created_by": ({"id": str(by), "display_name": str(by_name or "")} if by else None),
                "accepted_by": accepted.get(str(invite_id), []),
            }
        )
    return visible


async def course_titles(session: AsyncSession, tags: list[str]) -> list[str]:
    """Titles of the courses an invite's tags open (a course id or a group tag)."""
    rows = (
        await session.execute(
            sa.text(
                "SELECT title FROM courses WHERE id::text = ANY(:tags) OR EXISTS ("
                "SELECT 1 FROM unnest(scope_tags) t"
                " WHERE (owner_id::text || ':' || t) = ANY(:tags)) ORDER BY title"
            ),
            {"tags": list(tags)},
        )
    ).all()
    return [str(row[0]) for row in rows]


async def invite_preview(session: AsyncSession, *, token_hash: str) -> dict[str, Any]:
    """What the signed-out accept page may show: course titles and whether it can be used.

    No creator, no seat count, no ids: the token holder learns only the
    course names and why the link is unusable, when it is.
    """
    row = (
        await session.execute(
            sa.text(
                "SELECT course_tags, max_uses, used_count, expires_at, revoked_at"
                " FROM invites WHERE token_hash = :hash"
            ),
            {"hash": token_hash},
        )
    ).first()
    if row is None:
        raise NotFoundError("invite not found")
    tags, max_uses, used_count, expires_at, revoked_at = row
    reason: str | None = None
    if revoked_at is not None:
        reason = "revoked"
    elif expires_at is not None and (
        expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=UTC)
    ) <= datetime.now(UTC):
        reason = "expired"
    elif max_uses is not None and int(used_count) >= int(max_uses):
        reason = "no_seats"
    return {
        "course_titles": await course_titles(session, list(tags or [])),
        "usable": reason is None,
        "reason": reason,
    }


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
    stored = list(row[1] or [])
    require_scope(actor, [display_tag(t) for t in stored], code="invite.revoke")
    if not (await _manageable(session, actor))(stored):
        raise NotFoundError("invite not found")
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
        return
    for code in STUDENT_PERMISSION_CODES:
        await session.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission_code) VALUES (:role, :code)"
                " ON CONFLICT DO NOTHING"
            ),
            {"role": str(exists[0]), "code": code},
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
