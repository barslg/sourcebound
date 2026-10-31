"""Principal: the frozen identity snapshot every check reads.

Principals are loaded fresh from the database on every call: there is
no in-process cache, so the API and the worker can never act on stale
or banned rights. Scope is kept per role grant; the union stays for
overlap checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class RoleGrant:
    """One role grant: the role name plus the tags it is scoped to."""

    role: str
    scope_tags: tuple[str, ...]


@dataclass(frozen=True)
class Principal:
    """Who acts: user id, roles, permission codes, scope tags, superadmin flag."""

    user_id: UUID
    email: str
    roles: tuple[str, ...]
    permissions: frozenset[str]
    scope_tags: tuple[str, ...]
    is_superadmin: bool
    grants: tuple[RoleGrant, ...] = ()

    def has(self, code: str) -> bool:
        """Superadmin implies every code; otherwise membership decides."""
        return self.is_superadmin or code in self.permissions


async def load_principal(session: AsyncSession, user_id: UUID) -> Principal:
    """Build the principal for a user, always from the live database rows.

    Banned and legacy-holder accounts have no usable principal: the
    worker and the request path refuse them alike.
    """
    from app.errors import AuthError

    user = (
        await session.execute(
            sa.text(
                "SELECT email, is_superadmin, banned_at, is_legacy FROM users WHERE id = :user"
            ),
            {"user": str(user_id)},
        )
    ).first()
    if user is None:
        raise LookupError(f"unknown user {user_id}")
    if user[2] is not None or bool(user[3]):
        raise AuthError("this account cannot sign in")
    role_rows = (
        await session.execute(
            sa.text(
                "SELECT r.name, ur.scope_tags FROM roles r"
                " JOIN user_roles ur ON ur.role_id = r.id"
                " WHERE ur.user_id = :user"
            ),
            {"user": str(user_id)},
        )
    ).all()
    code_rows = (
        await session.execute(
            sa.text(
                "SELECT DISTINCT rp.permission_code FROM role_permissions rp"
                " JOIN user_roles ur ON ur.role_id = rp.role_id"
                " WHERE ur.user_id = :user"
            ),
            {"user": str(user_id)},
        )
    ).all()
    tags: set[str] = set()
    grants: list[RoleGrant] = []
    for name, scope_tags in role_rows:
        grant_tags = tuple(sorted(scope_tags or []))
        tags.update(grant_tags)
        grants.append(RoleGrant(role=str(name), scope_tags=grant_tags))
    return Principal(
        user_id=user_id,
        email=str(user[0]),
        roles=tuple(sorted(str(name) for name, _ in role_rows)),
        permissions=frozenset(str(code) for (code,) in code_rows),
        scope_tags=tuple(sorted(tags)),
        is_superadmin=bool(user[1]),
        grants=tuple(sorted(grants, key=lambda grant: grant.role)),
    )
