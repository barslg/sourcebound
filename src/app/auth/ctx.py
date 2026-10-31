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
from app.auth.session import (
    SessionRow,
    cookie_name_for,
    load_session,
    parse_session_cookie,
)
from app.config import Settings
from app.errors import AuthError, ForbiddenError
from app.rbac.principal import Principal, load_principal

CSRF_HEADER = "X-CSRF-Token"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _anonymous_accept_request(request: Request) -> bool:
    """POST /invites/{token}/accept without a session only mints an anonymous one.

    No authenticated authority is exercised — the handler creates a fresh
    anonymous session and stashes the invite hash there — so there is no
    session to forge a request for. Signed-in accepts still need CSRF.
    """
    return (
        request.method == "POST"
        and (request.scope.get("route") is not None)
        and getattr(request.scope.get("route"), "path", "").endswith("/accept")
    )


def _origin_allowed(request: Request) -> bool:
    """Unsafe requests carrying Origin/Referer must come from this host.

    A sandboxed document sends `Origin: null`; it carries no host to
    compare, so it is never allowed.
    """
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        return True
    if origin.strip().lower() == "null":
        return False
    from urllib.parse import urlparse

    try:
        claimed = urlparse(origin).netloc.lower()
    except ValueError:
        return False
    if not claimed:
        return False
    own = request.url.netloc.lower()
    return claimed == own


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
    cookie_name = cookie_name_for(settings.app_env, settings.session_cookie_name)
    raw_cookie = request.cookies.get(cookie_name)
    secret = settings.session_secret_key.get_secret_value() if settings.session_secret_key else None
    token = parse_session_cookie(raw_cookie, secret=secret) if raw_cookie else None
    login_session: SessionRow | None = None
    principal: Principal | None = None
    if token:
        login_session = await load_session(db, token, ttl_s=settings.session_ttl_s)
        if login_session is not None and login_session.user_id is not None:
            try:
                principal = await load_principal(db, login_session.user_id)
            except (LookupError, AuthError):
                principal = None
    if request.method in UNSAFE_METHODS:
        if not _origin_allowed(request):
            raise ForbiddenError("Cross-origin requests are not allowed.")
        if login_session is None and _anonymous_accept_request(request):
            pass
        else:
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
