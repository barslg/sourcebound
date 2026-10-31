"""Per-route request context: session cookie, CSRF, principal, audit actor.

Exactly one `ctx` dependency per route. Unsafe methods demand the
session's CSRF token in the X-CSRF-Token header; the principal is the
frozen snapshot for this request; the audit actor is its user id.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import db as db_module
from app.auth.session import SessionRow, load_session
from app.config import Settings
from app.errors import AuthError, ForbiddenError
from app.rbac.principal import Principal, load_principal

CSRF_HEADER = "X-CSRF-Token"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass
class RequestContext:
    """Everything a route needs: principal, session, actor, and the session."""

    db: AsyncSession
    settings: Settings
    session_token: str | None
    login_session: SessionRow | None
    principal: Principal | None

    @property
    def actor_id(self) -> UUID | None:
        """The audit actor: the signed-in user, if any."""
        return self.principal.user_id if self.principal else None


def _settings_of(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        from app.config import get_settings

        return get_settings()
    return settings  # type: ignore[no-any-return]


async def get_ctx(
    request: Request,
    db: AsyncSession = Depends(db_module.get_session),  # noqa: B008 - FastAPI dependency injection idiom
) -> RequestContext:
    """Build the request context from the session cookie."""
    settings = _settings_of(request)
    token = request.cookies.get(settings.session_cookie_name)
    login_session: SessionRow | None = None
    principal: Principal | None = None
    if token:
        login_session = await load_session(db, token, ttl_s=settings.session_ttl_s)
        if login_session is not None and login_session.user_id is not None:
            try:
                principal = await load_principal(db, login_session.user_id)
            except LookupError:
                principal = None
    if request.method in UNSAFE_METHODS:
        header = request.headers.get(CSRF_HEADER, "")
        if (
            login_session is None
            or not header
            or not secrets.compare_digest(header, login_session.csrf_token)
        ):
            raise ForbiddenError("CSRF token missing or invalid.")
    return RequestContext(
        db=db,
        settings=settings,
        session_token=token,
        login_session=login_session,
        principal=principal,
    )


def require_auth(ctx: RequestContext) -> Principal:
    """Demand a signed-in principal; anonymous callers get 401."""
    if ctx.principal is None:
        raise AuthError("sign-in required")
    return ctx.principal
