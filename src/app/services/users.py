"""User administration: ban and delete with heir or legacy holder.

Bans persist in the bans table (normalised email plus identity
digests), so deleting a banned row never lets the person sign in
fresh. Deleting with owned objects moves invite ownership to a vetted
heir (or strips the account into a legacy holder); the user's queued
operations are cancelled, never reassigned.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.rbac.principal import Principal, load_principal
from app.rbac.require import PERMISSION_DENIED_MESSAGE, require, require_scope
from app.rbac.tags import display_tag, display_tags
from app.services.audit import write_audit
from app.services.auth import identity_digest, is_banned, normalize_email


async def _target_row(session: AsyncSession, user_id: UUID) -> Any:
    row = (
        await session.execute(
            sa.text(
                "SELECT id, email, is_superadmin, banned_at, is_legacy FROM users WHERE id = :user"
            ),
            {"user": str(user_id)},
        )
    ).first()
    if row is None:
        raise NotFoundError("user not found")
    return row


def _refuse_superadmin_target(actor: Principal, *, is_superadmin: bool) -> None:
    """A non-superadmin never acts on a superadmin account."""
    if is_superadmin and not actor.is_superadmin:
        raise ForbiddenError(PERMISSION_DENIED_MESSAGE)


async def _target_tags(session: AsyncSession, user_id: UUID) -> list[str]:
    """Every scope tag granted to the target account, as display tags.

    A student's group grants are owner-qualified; scope checks compare
    them with the plain names a staff grant carries.
    """
    rows = (
        await session.execute(
            sa.text("SELECT ur.scope_tags FROM user_roles ur WHERE ur.user_id = :user"),
            {"user": str(user_id)},
        )
    ).all()
    return display_tags({tag for (tags,) in rows for tag in (tags or [])})


async def list_users(session: AsyncSession, *, actor: Principal) -> list[dict[str, Any]]:
    """The user directory: identity flags plus role grants, without secrets.

    Scoped callers see only users overlapping their scope; superadmin
    rows are visible to superadmins only.
    """
    require(actor, "user.list")
    rows = (
        await session.execute(
            sa.text(
                "SELECT id, email, display_name, is_superadmin, is_legacy,"
                " banned_at, banned_reason, created_at"
                " FROM users ORDER BY created_at"
            )
        )
    ).all()
    grant_rows = (
        await session.execute(
            sa.text(
                "SELECT ur.user_id, r.name, ur.scope_tags FROM user_roles ur"
                " JOIN roles r ON r.id = ur.role_id"
            )
        )
    ).all()
    grants: dict[str, list[dict[str, Any]]] = {}
    for owner, name, tags in grant_rows:
        grants.setdefault(str(owner), []).append(
            {"name": str(name), "scope_tags": sorted(tags or [])}
        )
    directory: list[dict[str, Any]] = []
    visible_scope = actor.scope_for("user.list")
    for user_id, email, name, admin, legacy, banned, reason, created in rows:
        if bool(admin) and not actor.is_superadmin:
            continue
        if not actor.is_superadmin and visible_scope is not None:
            user_tags = {
                display_tag(tag)
                for grant in grants.get(str(user_id), ())
                for tag in grant["scope_tags"]
            }
            if not user_tags or user_tags.isdisjoint(visible_scope):
                continue
        directory.append(
            {
                "id": str(user_id),
                "email": email,
                "display_name": name,
                "is_superadmin": bool(admin),
                "is_legacy": bool(legacy),
                "banned_at": banned.isoformat() if banned else None,
                "banned_reason": reason,
                "created_at": created.isoformat() if created else None,
                "roles": sorted(grants.get(str(user_id), ()), key=lambda grant: grant["name"]),
            }
        )
    return directory


async def _record_ban(session: AsyncSession, *, email: str, user_id: UUID, reason: str) -> None:
    """Persist the ban as one row per federated identity, email attached.

    A fallback email-only row covers accounts with no recorded
    identity; identity rows upsert on their digest.
    """
    identities = (
        await session.execute(
            sa.text("SELECT provider, subject FROM identities WHERE user_id = :user"),
            {"user": str(user_id)},
        )
    ).all()
    if not identities:
        existing = (
            await session.execute(
                sa.text("SELECT id FROM bans WHERE email = :email LIMIT 1"),
                {"email": normalize_email(email)},
            )
        ).first()
        if existing is None:
            await session.execute(
                sa.text("INSERT INTO bans (email, reason) VALUES (:email, :reason)"),
                {"email": normalize_email(email), "reason": reason},
            )
        return
    for provider, subject in identities:
        await session.execute(
            sa.text(
                "INSERT INTO bans (email, provider, subject_digest, reason)"
                " VALUES (:email, :provider, :digest, :reason)"
                " ON CONFLICT (provider, subject_digest)"
                " DO UPDATE SET email = EXCLUDED.email, reason = EXCLUDED.reason"
            ),
            {
                "email": normalize_email(email),
                "provider": str(provider),
                "digest": identity_digest(str(provider), str(subject)),
                "reason": reason,
            },
        )


async def ban_user(session: AsyncSession, *, actor: Principal, user_id: UUID, reason: str) -> None:
    """Ban an account: stamp it, persist the ban, revoke sessions and invites."""
    require(actor, "user.ban")
    target = await _target_row(session, user_id)
    _refuse_superadmin_target(actor, is_superadmin=bool(target[2]))
    require_scope(actor, await _target_tags(session, user_id), code="user.ban")
    await _record_ban(session, email=str(target[1]), user_id=user_id, reason=reason)
    await session.execute(
        sa.text("UPDATE users SET banned_at = now(), banned_reason = :reason WHERE id = :user"),
        {"reason": reason, "user": str(user_id)},
    )
    from app.auth.session import revoke_user_sessions

    await revoke_user_sessions(session, user_id)
    await session.execute(
        sa.text(
            "UPDATE invites AS invite SET used_count = GREATEST(invite.used_count - freed.n, 0)"
            " FROM (SELECT invite_id, count(*) AS n FROM invite_uses"
            " WHERE user_id = :user GROUP BY invite_id) AS freed"
            " WHERE invite.id = freed.invite_id"
        ),
        {"user": str(user_id)},
    )
    await session.execute(
        sa.text("DELETE FROM invite_uses WHERE user_id = :user"), {"user": str(user_id)}
    )
    await session.execute(
        sa.text("UPDATE invites SET revoked_at = now() WHERE created_by = :user"),
        {"user": str(user_id)},
    )
    await session.execute(
        sa.text(
            "UPDATE operations SET status = 'cancelled', error = 'account banned',"
            " updated_at = now() WHERE actor_id = :user AND status IN ('queued', 'running')"
        ),
        {"user": str(user_id)},
    )
    await write_audit(
        session,
        actor_id=actor.user_id,
        action="user.ban",
        entity="user",
        entity_id=str(user_id),
        meta={"reason": reason},
    )


async def _owned_invites(session: AsyncSession, user_id: UUID) -> list[Any]:
    rows = (
        await session.execute(
            sa.text("SELECT id, course_tags FROM invites WHERE created_by = :user"),
            {"user": str(user_id)},
        )
    ).all()
    return list(rows)


async def _owned_operation_count(session: AsyncSession, user_id: UUID) -> int:
    return int(
        (
            await session.execute(
                sa.text("SELECT count(*) FROM operations WHERE actor_id = :user"),
                {"user": str(user_id)},
            )
        ).scalar_one()
    )


async def _vet_heir(session: AsyncSession, *, heir_id: UUID) -> Principal:
    """The heir must exist and be usable, with rights covering the handover."""
    heir = (
        await session.execute(
            sa.text("SELECT id, email, banned_at, is_legacy FROM users WHERE id = :heir"),
            {"heir": str(heir_id)},
        )
    ).first()
    if heir is None:
        raise NotFoundError("heir not found")
    if heir[2] is not None or bool(heir[3]):
        raise ValidationError("the heir account cannot sign in")
    if await is_banned(session, email=str(heir[1]), provider="", subject=""):
        raise ValidationError("the heir account is banned")
    try:
        return await load_principal(session, heir_id)
    except Exception as exc:
        raise ValidationError("the heir account cannot sign in") from exc


async def delete_user(
    session: AsyncSession,
    *,
    actor: Principal,
    user_id: UUID,
    heir_id: UUID | None = None,
    legacy: bool = False,
) -> None:
    """Delete an account and cascade its personal data.

    Invite ownership moves to a vetted heir (or stays with a stripped
    legacy holder); the account's operations are cancelled, never
    reassigned. Sessions, identities, and invite uses cascade.
    """
    require(actor, "user.delete")
    target = await _target_row(session, user_id)
    _refuse_superadmin_target(actor, is_superadmin=bool(target[2]))
    require_scope(actor, await _target_tags(session, user_id), code="user.delete")
    owned_invites = await _owned_invites(session, user_id)
    owned_operations = await _owned_operation_count(session, user_id)
    owned = len(owned_invites) + owned_operations
    if owned and heir_id is None and not legacy:
        raise ConflictError("this account owns objects: name an heir or delete with legacy=true")
    heir: Principal | None = None
    if heir_id is not None:
        if heir_id == user_id:
            raise ValidationError("an account cannot inherit its own objects")
        heir = await _vet_heir(session, heir_id=heir_id)
        for _, tags in owned_invites:
            if tags:
                try:
                    require_scope(heir, display_tags(tags), code="course.edit")
                except NotFoundError as exc:
                    raise ValidationError(
                        "the heir must hold edit rights in the scope of every object"
                    ) from exc
    if heir is not None:
        await session.execute(
            sa.text("UPDATE invites SET created_by = :heir WHERE created_by = :user"),
            {"heir": str(heir_id), "user": str(user_id)},
        )
    await session.execute(
        sa.text(
            "UPDATE operations SET status = 'cancelled', error = 'account deleted',"
            " updated_at = now() WHERE actor_id = :user AND status IN ('queued', 'running')"
        ),
        {"user": str(user_id)},
    )
    if legacy:
        await session.execute(
            sa.text("DELETE FROM identities WHERE user_id = :user"), {"user": str(user_id)}
        )
        await session.execute(
            sa.text("DELETE FROM user_roles WHERE user_id = :user"), {"user": str(user_id)}
        )
        await session.execute(
            sa.text(
                "UPDATE users SET is_legacy = true, display_name = '', avatar_url = '',"
                " is_superadmin = false, banned_reason = '', email = :anon WHERE id = :user"
            ),
            {"anon": f"legacy-{user_id}@invalid", "user": str(user_id)},
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
        return
    await write_audit(
        session,
        actor_id=actor.user_id,
        action="user.delete",
        entity="user",
        entity_id=str(user_id),
        meta={"heir_id": str(heir_id) if heir_id else None},
    )
    # Learner subject rows keep their cost but lose request/response text.
    await session.execute(
        sa.text(
            "UPDATE llm_calls SET request = '{\"wiped\": true}', response = NULL,"
            " subject_user_id = NULL WHERE subject_user_id = :user"
        ),
        {"user": str(user_id)},
    )
    await session.execute(sa.text("DELETE FROM users WHERE id = :user"), {"user": str(user_id)})
