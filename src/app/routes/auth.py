"""Auth routes: login start, callback, logout, CSRF, me."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse

from app.auth.ctx import RequestContext, get_ctx, require_auth
from app.controllers import auth as controller
from app.schemas import t02 as dto

router = APIRouter(prefix="/auth", tags=["auth"])
public_router = APIRouter(prefix="/config", tags=["config"])


@public_router.get("/public")
def public_config(request: Request, lang: str | None = None) -> dict[str, str]:
    """Signed-out config: the privacy line in ?lang= or Accept-Language."""
    locale = lang
    if locale is None:
        header = str(request.headers.get("accept-language", ""))
        locale = header.split(",")[0].strip().split("-")[0] if header else "en"
    return controller.public_config(locale=locale or "en")


def _set_session_cookie(response: Response, ctx: RequestContext, token: str) -> None:
    """Attach the backend session cookie: HttpOnly, Lax, Secure outside local."""
    from app.auth.session import set_session_cookie

    secret = (
        ctx.settings.session_secret_key.get_secret_value()
        if ctx.settings.session_secret_key
        else None
    )
    set_session_cookie(
        response,
        app_env=ctx.settings.app_env,
        cookie_name=ctx.settings.session_cookie_name,
        secret=secret,
        token=token,
        ttl_s=ctx.settings.session_ttl_s,
        secure_configured=ctx.settings.session_secure_cookie,
    )


@router.get("/login")
async def login(
    request: Request,
    response: Response,
    provider: str = "google",
    next: str | None = None,  # noqa: A002 - `next` is the invite-flow return address
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> RedirectResponse:
    """Start a login: redirect to the provider, minting an anonymous session."""
    url, fresh_token = await controller.start_login(
        ctx, provider_name=provider, next_url=next, request=request
    )
    redirect = RedirectResponse(url, status_code=307)
    if fresh_token:
        _set_session_cookie(redirect, ctx, fresh_token)
    return redirect


@router.get("/callback")
async def callback(
    request: Request,
    state: str,
    code: str,
    provider: str = "google",
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> RedirectResponse:
    """Finish a login, then redirect the browser to the frontend return path."""
    done = await controller.finish_login(
        ctx, provider_name=provider, state=state, code=code, request=request
    )
    if done is None:
        return RedirectResponse("/", status_code=303)
    payload, token = done
    target = payload.next_url or "/"
    redirect = RedirectResponse(target, status_code=303)
    _set_session_cookie(redirect, ctx, token)
    return redirect


@router.post("/logout")
async def logout(response: Response, ctx: RequestContext = Depends(get_ctx)) -> dict[str, bool]:  # noqa: B008 - FastAPI dependency injection idiom
    """Destroy the backend session and clear its cookie."""
    from app.auth.session import cookie_name_for

    await controller.logout(ctx)
    response.delete_cookie(
        cookie_name_for(ctx.settings.app_env, ctx.settings.session_cookie_name), path="/"
    )
    return {"ok": True}


@router.get("/csrf", response_model=dto.CsrfResponse)
async def csrf(ctx: RequestContext = Depends(get_ctx)) -> dto.CsrfResponse:  # noqa: B008 - FastAPI dependency injection idiom
    """Hand the session's CSRF token to the frontend (no tokens in storage)."""
    require_auth(ctx)
    assert ctx.login_session is not None
    return dto.CsrfResponse(csrf_token=ctx.login_session.csrf_token)


@router.get("/me", response_model=dto.MeResponse)
async def me(
    request: Request,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dto.MeResponse:
    """The signed-in account with its permissions for the UI guards."""
    header = request.headers.get("accept-language", "en")
    locale = header.split(",")[0].strip().split("-")[0] or "en"
    return await controller.me(ctx, locale=locale)
