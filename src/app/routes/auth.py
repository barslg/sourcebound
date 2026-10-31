"""Auth routes: login start, callback, logout, CSRF, me."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse

from app.auth.ctx import RequestContext, get_ctx, require_auth
from app.controllers import auth as controller
from app.schemas import t02 as dto

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookie(response: Response, ctx: RequestContext, token: str) -> None:
    """Attach the backend session cookie: HttpOnly, Lax, Secure from config."""
    response.set_cookie(
        ctx.settings.session_cookie_name,
        token,
        httponly=True,
        samesite="lax",
        secure=ctx.settings.session_secure_cookie,
        max_age=ctx.settings.session_ttl_s,
        path="/",
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


@router.get("/callback", response_model=dto.LoginDoneResponse)
async def callback(
    request: Request,
    response: Response,
    state: str,
    code: str,
    provider: str = "google",
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dto.LoginDoneResponse:
    """Finish a login: create the backend session and apply a pending invite."""
    payload, token = await controller.finish_login(
        ctx, provider_name=provider, state=state, code=code, request=request
    )
    _set_session_cookie(response, ctx, token)
    return payload


@router.post("/logout")
async def logout(response: Response, ctx: RequestContext = Depends(get_ctx)) -> dict[str, bool]:  # noqa: B008 - FastAPI dependency injection idiom
    """Destroy the backend session and clear its cookie."""
    await controller.logout(ctx)
    response.delete_cookie(ctx.settings.session_cookie_name, path="/")
    return {"ok": True}


@router.get("/csrf", response_model=dto.CsrfResponse)
async def csrf(ctx: RequestContext = Depends(get_ctx)) -> dto.CsrfResponse:  # noqa: B008 - FastAPI dependency injection idiom
    """Hand the session's CSRF token to the frontend (no tokens in storage)."""
    require_auth(ctx)
    assert ctx.login_session is not None
    return dto.CsrfResponse(csrf_token=ctx.login_session.csrf_token)


@router.get("/me", response_model=dto.MeResponse)
async def me(ctx: RequestContext = Depends(get_ctx)) -> dto.MeResponse:  # noqa: B008 - FastAPI dependency injection idiom
    """The signed-in account with its permissions for the UI guards."""
    return await controller.me(ctx)
