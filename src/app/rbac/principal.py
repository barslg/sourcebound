"""Principal: the frozen identity snapshot every check reads."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

_VERSION = 0
_CACHE: dict[str, tuple[int, Principal]] = {}


def current_version() -> int:
    """The global principal-cache version key."""
    return _VERSION


def bump_version() -> int:
    """Invalidate every cached principal; call after roles/users/invites change."""
    global _VERSION
    _CACHE.clear()
    _VERSION += 1
    return _VERSION


@dataclass(frozen=True)
class Principal:
    """Who acts: user id, roles, permission codes, scope tags, superadmin flag."""

    user_id: UUID
    email: str
    roles: tuple[str, ...]
    permissions: frozenset[str]
    scope_tags: tuple[str, ...]
    is_superadmin: bool

    def has(self, code: str) -> bool:
        """Superadmin implies every code; otherwise membership decides."""
        return self.is_superadmin or code in self.permissions


async def load_principal(session: AsyncSession, user_id: UUID) -> Principal:
    """Build (and cache for the current version) the principal for a user."""
    key = str(user_id)
    cached = _CACHE.get(key)
    if cached is not None and cached[0] == _VERSION:
        return cached[1]
    user = (
        await session.execute(
            sa.text("SELECT email, is_superadmin FROM users WHERE id = :user"),
            {"user": str(user_id)},
        )
    ).first()
    if user is None:
        raise LookupError(f"unknown user {user_id}")
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
    for _, scope_tags in role_rows:
        tags.update(scope_tags or [])
    principal = Principal(
        user_id=user_id,
        email=str(user[0]),
        roles=tuple(sorted(str(name) for name, _ in role_rows)),
        permissions=frozenset(str(code) for (code,) in code_rows),
        scope_tags=tuple(sorted(tags)),
        is_superadmin=bool(user[1]),
    )
    _CACHE[key] = (_VERSION, principal)
    return principal
