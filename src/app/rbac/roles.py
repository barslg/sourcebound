"""Role use cases: CRUD, assignment, and anti-escalation.

Every change writes its audit row in the same session so an audit
failure rolls the change back. Principals load fresh from the database,
so every change takes effect immediately in all processes.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ConflictError, NotFoundError, ValidationError
from app.rbac.principal import Principal
from app.rbac.require import require
from app.services.audit import write_audit


async def _known_codes(session: AsyncSession) -> set[str]:
    rows = (await session.execute(sa.text("SELECT code FROM permissions"))).all()
    return {str(row[0]) for row in rows}


async def _reject_unknown(session: AsyncSession, codes: Collection[str]) -> None:
    known = await _known_codes(session)
    unknown = sorted(set(codes) - known)
    if unknown:
        raise ValidationError(f"unknown permission codes: {', '.join(unknown)}")


CRUD_VERBS = frozenset({"create", "read", "update", "delete"})


def _check_escalation(actor: Principal | None, codes: Collection[str], role_name: str) -> None:
    """Nobody grants what they lack, nor edits their own role."""
    if actor is None or actor.is_superadmin:
        return
    missing = sorted(set(codes) - actor.permissions)
    if missing:
        from app.errors import ForbiddenError
        from app.rbac.require import PERMISSION_DENIED_MESSAGE

        raise ForbiddenError(PERMISSION_DENIED_MESSAGE)
    if role_name in actor.roles:
        from app.errors import ForbiddenError
        from app.rbac.require import PERMISSION_DENIED_MESSAGE

        raise ForbiddenError(PERMISSION_DENIED_MESSAGE)


def _check_assignment(
    actor: Principal | None,
    *,
    target_id: UUID,
    role_codes: Collection[str],
    scope_tags: Collection[str],
) -> None:
    """Anti-escalation for grants: codes and tags ⊆ the actor's, never oneself.

    Tags are read from the grants carrying role.assign, never from the
    union across grants; an empty (global) grant needs a global actor.
    """
    if actor is None or actor.is_superadmin:
        return
    from app.errors import ForbiddenError
    from app.rbac.require import PERMISSION_DENIED_MESSAGE

    if target_id == actor.user_id:
        raise ForbiddenError(PERMISSION_DENIED_MESSAGE)
    if sorted(set(role_codes) - actor.permissions):
        raise ForbiddenError(PERMISSION_DENIED_MESSAGE)
    scope = actor.scope_for("role.assign")
    if scope is None:
        return
    wanted = set(scope_tags)
    if not wanted or wanted - scope:
        raise ForbiddenError(PERMISSION_DENIED_MESSAGE)


async def _target_tags(session: AsyncSession, user_id: UUID) -> list[str]:
    """Every scope tag granted to the target account."""
    rows = (
        await session.execute(
            sa.text("SELECT ur.scope_tags FROM user_roles ur WHERE ur.user_id = :user"),
            {"user": str(user_id)},
        )
    ).all()
    return sorted({tag for (tags,) in rows for tag in (tags or [])})


def _validate_crud(crud: Mapping[str, Collection[str]] | None) -> dict[str, list[str]]:
    """CRUD matrix values are verbs only; anything else is refused."""
    cleaned: dict[str, list[str]] = {}
    for record_type, verbs in dict(crud or {}).items():
        if not record_type or not isinstance(record_type, str):
            raise ValidationError("crud record types must be non-empty strings")
        unknown = sorted(set(verbs) - CRUD_VERBS)
        if unknown:
            raise ValidationError(f"unknown crud verbs for {record_type!r}: {', '.join(unknown)}")
        cleaned[record_type] = sorted(set(verbs))
    return cleaned


def _validate_scope_tags(scope_tags: Collection[str] | None) -> list[str] | None:
    """Scope tags are plain non-empty strings when given."""
    if scope_tags is None:
        return None
    cleaned = [str(tag) for tag in scope_tags]
    if any(not tag for tag in cleaned):
        raise ValidationError("scope tags must be non-empty strings")
    return sorted(set(cleaned))


async def _role_id(session: AsyncSession, name: str) -> UUID:
    row = (
        await session.execute(sa.text("SELECT id FROM roles WHERE name = :name"), {"name": name})
    ).first()
    if row is None:
        raise NotFoundError("role not found")
    return UUID(str(row[0]))


async def _role_codes(session: AsyncSession, role_id: UUID) -> set[str]:
    rows = (
        await session.execute(
            sa.text("SELECT permission_code FROM role_permissions WHERE role_id = :role"),
            {"role": str(role_id)},
        )
    ).all()
    return {str(row[0]) for row in rows}


async def create_role(
    session: AsyncSession,
    *,
    name: str,
    permission_codes: Collection[str],
    crud: Mapping[str, Collection[str]] | None = None,
    scope_tags: Collection[str] = (),
    actor: Principal | None = None,
    audit_meta: Mapping[str, Any] | None = None,
) -> UUID:
    """Create a role after validating codes and anti-escalation."""
    if actor is not None:
        require(actor, "role.edit")
    await _reject_unknown(session, permission_codes)
    _check_escalation(actor, permission_codes, name)
    cleaned_crud = _validate_crud(crud)
    cleaned_tags = _validate_scope_tags(scope_tags) or []
    try:
        row = (
            await session.execute(
                sa.text(
                    "INSERT INTO roles (name, crud, scope_tags)"
                    " VALUES (:name, CAST(:crud AS jsonb), :tags) RETURNING id"
                ),
                {
                    "name": name,
                    "crud": json.dumps(cleaned_crud),
                    "tags": cleaned_tags,
                },
            )
        ).one()
    except Exception as exc:
        raise ConflictError(f"role {name!r} already exists") from exc
    role_id = UUID(str(row[0]))
    for code in sorted(set(permission_codes)):
        await session.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission_code)"
                " VALUES (:role, :code) ON CONFLICT DO NOTHING"
            ),
            {"role": str(role_id), "code": code},
        )
    meta = {"name": name, **dict(audit_meta or {})}
    await write_audit(
        session,
        actor_id=actor.user_id if actor else None,
        action="role.create",
        entity="role",
        entity_id=str(role_id),
        meta=meta,
    )
    return role_id


async def update_role(
    session: AsyncSession,
    *,
    name: str,
    permission_codes: Collection[str] | None = None,
    crud: Mapping[str, Collection[str]] | None = None,
    scope_tags: Collection[str] | None = None,
    actor: Principal | None = None,
) -> None:
    """Change a role's grants, matrix, or default scope.

    The administrator role is immutable except by a superadmin, and it
    can never lose permission codes, not even to a superadmin.
    """
    if actor is not None:
        require(actor, "role.edit")
    from app.rbac.catalogue import ADMIN_ROLE_NAME

    if name == ADMIN_ROLE_NAME and actor is not None and not actor.is_superadmin:
        from app.errors import ForbiddenError
        from app.rbac.require import PERMISSION_DENIED_MESSAGE

        raise ForbiddenError(PERMISSION_DENIED_MESSAGE)
    role_id = await _role_id(session, name)
    if permission_codes is not None:
        if name == ADMIN_ROLE_NAME and not set(permission_codes):
            raise ValidationError("the administrator role must keep its permissions")
        if name == ADMIN_ROLE_NAME:
            removed = sorted(await _role_codes(session, role_id) - set(permission_codes))
            if removed:
                raise ValidationError(
                    f"the administrator role cannot lose permissions: {', '.join(removed)}"
                )
        await _reject_unknown(session, permission_codes)
        _check_escalation(actor, permission_codes, name)
        await session.execute(
            sa.text("DELETE FROM role_permissions WHERE role_id = :role"),
            {"role": str(role_id)},
        )
        for code in sorted(set(permission_codes)):
            await session.execute(
                sa.text(
                    "INSERT INTO role_permissions (role_id, permission_code)"
                    " VALUES (:role, :code) ON CONFLICT DO NOTHING"
                ),
                {"role": str(role_id), "code": code},
            )
    elif actor is not None and not actor.is_superadmin and name in actor.roles:
        from app.errors import ForbiddenError
        from app.rbac.require import PERMISSION_DENIED_MESSAGE

        raise ForbiddenError(PERMISSION_DENIED_MESSAGE)
    if crud is not None:
        await session.execute(
            sa.text("UPDATE roles SET crud = CAST(:crud AS jsonb) WHERE id = :role"),
            {"crud": json.dumps(_validate_crud(crud)), "role": str(role_id)},
        )
    if scope_tags is not None:
        await session.execute(
            sa.text("UPDATE roles SET scope_tags = :tags WHERE id = :role"),
            {"tags": _validate_scope_tags(scope_tags) or [], "role": str(role_id)},
        )
    await write_audit(
        session,
        actor_id=actor.user_id if actor else None,
        action="role.update",
        entity="role",
        entity_id=str(role_id),
        meta={"name": name},
    )


async def delete_role(session: AsyncSession, *, name: str, actor: Principal | None) -> None:
    """Delete a role; grants and assignments cascade in the schema.

    The administrator role cannot be deleted: removing it would lose
    every permission code it carries.
    """
    if actor is not None:
        require(actor, "role.edit")
    from app.rbac.catalogue import ADMIN_ROLE_NAME

    if name == ADMIN_ROLE_NAME:
        if actor is not None and not actor.is_superadmin:
            from app.errors import ForbiddenError
            from app.rbac.require import PERMISSION_DENIED_MESSAGE

            raise ForbiddenError(PERMISSION_DENIED_MESSAGE)
        raise ValidationError("the administrator role cannot be deleted")
    role_id = await _role_id(session, name)
    if actor is not None and not actor.is_superadmin and name in actor.roles:
        from app.errors import ForbiddenError
        from app.rbac.require import PERMISSION_DENIED_MESSAGE

        raise ForbiddenError(PERMISSION_DENIED_MESSAGE)
    await session.execute(sa.text("DELETE FROM roles WHERE id = :role"), {"role": str(role_id)})
    await write_audit(
        session,
        actor_id=actor.user_id if actor else None,
        action="role.delete",
        entity="role",
        entity_id=str(role_id),
        meta={"name": name},
    )


async def list_roles(session: AsyncSession) -> list[dict[str, Any]]:
    """Every role with its permission codes, CRUD matrix, and scope tags."""
    rows = (
        await session.execute(sa.text("SELECT id, name, crud, scope_tags FROM roles ORDER BY name"))
    ).all()
    grants = (
        await session.execute(sa.text("SELECT role_id, permission_code FROM role_permissions"))
    ).all()
    by_role: dict[str, list[str]] = {}
    for role_id, code in grants:
        by_role.setdefault(str(role_id), []).append(str(code))
    return [
        {
            "name": str(name),
            "permission_codes": sorted(by_role.get(str(role_id), [])),
            "crud": dict(crud or {}),
            "scope_tags": list(tags or []),
        }
        for role_id, name, crud, tags in rows
    ]


async def assign_role(
    session: AsyncSession,
    user_id: UUID,
    role_name: str,
    *,
    scope_tags: Collection[str] = (),
    actor: Principal | None = None,
) -> None:
    """Grant a role scoped to tags; a second grant unions the scopes."""
    if actor is not None:
        require(actor, "role.assign")
    role_id = await _role_id(session, role_name)
    cleaned = _validate_scope_tags(scope_tags) or []
    _check_assignment(
        actor, target_id=user_id, role_codes=await _role_codes(session, role_id), scope_tags=cleaned
    )
    existing = (
        await session.execute(
            sa.text("SELECT scope_tags FROM user_roles WHERE user_id = :user AND role_id = :role"),
            {"user": str(user_id), "role": str(role_id)},
        )
    ).first()
    merged = sorted(set(cleaned) | set(existing[0] or [])) if existing else cleaned
    await session.execute(
        sa.text(
            "INSERT INTO user_roles (user_id, role_id, scope_tags)"
            " VALUES (:user, :role, :tags)"
            " ON CONFLICT (user_id, role_id) DO UPDATE SET scope_tags = EXCLUDED.scope_tags"
        ),
        {"user": str(user_id), "role": str(role_id), "tags": merged},
    )
    await write_audit(
        session,
        actor_id=actor.user_id if actor else None,
        action="role.assign",
        entity="user",
        entity_id=str(user_id),
        meta={"role": role_name, "scope_tags": list(scope_tags)},
    )


async def revoke_role(
    session: AsyncSession,
    user_id: UUID,
    role_name: str,
    *,
    actor: Principal | None = None,
) -> None:
    """Remove a role grant from a user, with the same check as the grant."""
    if actor is not None:
        require(actor, "role.assign")
    role_id = await _role_id(session, role_name)
    _check_assignment(
        actor,
        target_id=user_id,
        role_codes=await _role_codes(session, role_id),
        scope_tags=await _target_tags(session, user_id),
    )
    await session.execute(
        sa.text("DELETE FROM user_roles WHERE user_id = :user AND role_id = :role"),
        {"user": str(user_id), "role": str(role_id)},
    )
    await write_audit(
        session,
        actor_id=actor.user_id if actor else None,
        action="role.revoke",
        entity="user",
        entity_id=str(user_id),
        meta={"role": role_name},
    )
