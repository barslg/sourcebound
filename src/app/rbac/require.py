"""Authorisation checks: the one 403 and the scope 404."""

from __future__ import annotations

from collections.abc import Collection

from app.errors import ForbiddenError, NotFoundError
from app.rbac.principal import Principal

PERMISSION_DENIED_MESSAGE = "You do not have permission for this action."


def require(principal: Principal | None, code: str) -> Principal:
    """Demand a permission code; raise the one 403 error when it is missing."""
    if principal is None or not principal.has(code):
        raise ForbiddenError(PERMISSION_DENIED_MESSAGE)
    return principal


def require_scope(principal: Principal, tags: Collection[str]) -> None:
    """Demand scope overlap; an object outside scope answers 404.

    A superadmin and a globally scoped principal (no tags) see
    everything; anyone else must share at least one tag with the object.
    """
    wanted = set(tags)
    if principal.is_superadmin or not principal.scope_tags or not wanted:
        return
    if wanted.isdisjoint(principal.scope_tags):
        raise NotFoundError("object not found")
