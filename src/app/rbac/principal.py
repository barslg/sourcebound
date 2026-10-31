"""Principal: the frozen identity snapshot every check reads.

Principals are loaded fresh from the database on every call: there is
no in-process cache, so the API and the worker can never act on stale
or banned rights. Scope is kept per role grant and per permission, so a
grant carrying one permission never widens another; the union stays for
visibility overlap checks.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
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
    permission_scopes: dict[str, frozenset[str] | None] = field(default_factory=dict)

    def has(self, code: str) -> bool:
        """Superadmin implies every code; otherwise membership decides."""
        return self.is_superadmin or code in self.permissions

    def scope_for(self, code: str) -> frozenset[str] | None:
        """Tags carrying `code`; None means globally scoped for this permission.

        A held code is global when any grant carrying it has no tags. A
        principal built without per-grant detail (tests) falls back to the
        legacy union: no tags means global, otherwise the union.
        """
        if self.is_superadmin:
            return None
        if code not in self.permissions:
            return frozenset()
        if not self.permission_scopes:
            if not self.scope_tags:
                return None
            return frozenset(self.scope_tags)
        return self.permission_scopes.get(code, frozenset())

    def scope_for_any(self, codes: Collection[str]) -> frozenset[str] | None:
        """Union of scope_for over codes; None when any of them is global."""
        union: set[str] = set()
        for code in codes:
            scope = self.scope_for(code)
            if scope is None:
                return None
            union |= scope
        return frozenset(union)


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
    code_grants = (
        await session.execute(
            sa.text(
                "SELECT r.name, rp.permission_code FROM roles r"
                " JOIN user_roles ur ON ur.role_id = r.id"
                " JOIN role_permissions rp ON rp.role_id = r.id"
                " WHERE ur.user_id = :user"
            ),
            {"user": str(user_id)},
        )
    ).all()
    tags_by_role = {str(name): set(stags or []) for name, stags in role_rows}
    scopes: dict[str, frozenset[str] | None] = {}
    for role_name, code in code_grants:
        carrying = tags_by_role.get(str(role_name), set())
        if not carrying:
            scopes[str(code)] = None
        elif str(code) not in scopes:
            scopes[str(code)] = frozenset(carrying)
        elif scopes[str(code)] is not None:
            current = scopes[str(code)]
            assert current is not None
            scopes[str(code)] = current | carrying
    return Principal(
        user_id=user_id,
        email=str(user[0]),
        roles=tuple(sorted(str(name) for name, _ in role_rows)),
        permissions=frozenset(str(code) for (code,) in code_rows),
        scope_tags=tuple(sorted(tags)),
        is_superadmin=bool(user[1]),
        grants=tuple(sorted(grants, key=lambda grant: grant.role)),
        permission_scopes=scopes,
    )
