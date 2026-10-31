"""Auth controller: provider lookup, pending invites, session tokens."""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import Request

from app.auth.ctx import RequestContext, require_auth
from app.auth.oidc import GoogleProvider, Provider
from app.auth.session import create_session, destroy_session, set_pending_invite
from app.config import Settings
from app.errors import ValidationError
from app.schemas import t02 as dto


def provider_for(settings: Settings, name: str, request: Request | None = None) -> Provider:
    """Resolve a provider by name; registries on the app win for tests."""
    registry: dict[str, Provider] | None = None
    if request is not None:
        registry = getattr(request.app.state, "oidc_providers", None)
    if registry and name in registry:
        return registry[name]
    if name == "google":
        client_secret = settings.google_oauth_client_secret
        return GoogleProvider(
            client_id=settings.google_oauth_client_id,
            client_secret=client_secret.get_secret_value() if client_secret else "",
            redirect_url=settings.oidc_redirect_url,
            issuer=settings.oidc_issuer,
            allowed_domains=settings.oidc_allowed_domain_set,
            http_timeout_s=settings.oidc_http_timeout_s,
        )
    raise ValidationError(f"unknown provider {name!r}")


async def start_login(
    ctx: RequestContext,
    *,
    provider_name: str,
    next_url: str | None,
    request: Request | None = None,
) -> tuple[str, str | None]:
    """Begin a login; return (provider_url, fresh_session_token_or_None)."""
    from app.services.auth import begin_login

    provider = provider_for(ctx.settings, provider_name, request)
    token: str | None = None
    if ctx.login_session is None:
        token, _ = await create_session(ctx.db, None, ttl_s=ctx.settings.session_ttl_s)
    url, _ = await begin_login(ctx.db, provider, next_url=next_url)
    return url, token


async def finish_login(
    ctx: RequestContext,
    *,
    provider_name: str,
    state: str,
    code: str,
    request: Request | None = None,
) -> tuple[dto.LoginDoneResponse, str]:
    """Finish a login; return (payload, fresh_session_token_for_cookie)."""
    from app.services.auth import finish_login as finish_use_case

    provider = provider_for(ctx.settings, provider_name, request)
    pending = ctx.login_session.pending_invite_token if ctx.login_session else None
    user, outcome = await finish_use_case(
        ctx.db,
        provider,
        state=state,
        code=code,
        superadmin_emails=ctx.settings.superadmin_email_set,
        pending_invite_token=pending,
    )
    token, _ = await create_session(ctx.db, user.id, ttl_s=ctx.settings.session_ttl_s)
    if ctx.login_session is not None:
        await set_pending_invite(ctx.db, ctx.login_session.id, None)
        if ctx.session_token:
            await destroy_session(ctx.db, ctx.session_token)
    return (
        dto.LoginDoneResponse(
            id=str(user.id),
            email=user.email,
            display_name=user.display_name,
            is_new=outcome.is_new,
            applied_invite_scopes=list(outcome.applied_invite_scopes or [])
            if outcome.applied_invite_scopes is not None
            else None,
        ),
        token,
    )


async def logout(ctx: RequestContext) -> None:
    """Destroy the current session; the route clears the cookie."""
    require_auth(ctx)
    if ctx.session_token:
        await destroy_session(ctx.db, ctx.session_token)


async def me(ctx: RequestContext) -> dto.MeResponse:
    """The signed-in account with roles, permissions, and scope tags."""
    principal = require_auth(ctx)
    row = (
        await ctx.db.execute(
            sa.text("SELECT display_name, avatar_url FROM users WHERE id = :user"),
            {"user": str(principal.user_id)},
        )
    ).first()
    return dto.MeResponse(
        id=str(principal.user_id),
        email=principal.email,
        display_name=str(row[0]) if row else "",
        avatar_url=str(row[1]) if row else "",
        is_superadmin=principal.is_superadmin,
        roles=list(principal.roles),
        permissions=sorted(principal.permissions),
        scope_tags=list(principal.scope_tags),
    )
