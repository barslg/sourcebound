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


def require_scope(principal: Principal, tags: Collection[str], *, code: str) -> None:
    """Demand scope containment for one permission; outside scope answers 404.

    The scope is read from the grants carrying `code`, never from the
    union across grants. A superadmin, or a principal holding `code`
    globally (a grant with no tags), sees everything. Every other caller
    must cover every tag of the object; a target with no tags is in scope
    for a global actor only.
    """
    if principal.is_superadmin:
        return
    scope = principal.scope_for(code)
    if scope is None:
        return
    wanted = set(tags)
    if not wanted or wanted - scope:
        raise NotFoundError("object not found")
