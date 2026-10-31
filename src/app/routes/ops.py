"""Invite accept, operation, and event routes."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

from app.auth.ctx import RequestContext, get_ctx, require_auth
from app.auth.session import create_session, set_pending_invite
from app.controllers import t02 as controller
from app.schemas import t02 as dto

invites_accept_router = APIRouter(prefix="/invites", tags=["invites"])
operations_router = APIRouter(prefix="/operations", tags=["operations"])
events_router = APIRouter(tags=["events"])


@invites_accept_router.get("/{token}/accept", response_model=None)
async def accept_invite(token: str, ctx: RequestContext = Depends(get_ctx)) -> Response:  # noqa: B008 - FastAPI dependency injection idiom
    """Accept an invite.

    Signed in: grant the scoped student role now. Signed out: stash
    the invite on an anonymous session and redirect to sign-in; the
    callback applies it.
    """
    if ctx.principal is None:
        from app.auth.session import session_id_for_token

        fresh_token: str | None = None
        session_id = ctx.login_session.id if ctx.login_session else None
        if session_id is None:
            fresh_token, _ = await create_session(ctx.db, None, ttl_s=ctx.settings.session_ttl_s)
            session_id = await session_id_for_token(ctx.db, fresh_token)
        assert session_id is not None
        await set_pending_invite(ctx.db, session_id, token)
        target = f"/api/v1/auth/login?provider=google&next=/api/v1/invites/{token}/accept"
        redirect = RedirectResponse(target, status_code=307)
        if fresh_token:
            redirect.set_cookie(
                ctx.settings.session_cookie_name,
                fresh_token,
                httponly=True,
                samesite="lax",
                secure=ctx.settings.session_secure_cookie,
                max_age=ctx.settings.session_ttl_s,
                path="/",
            )
        return redirect
    scopes = await controller.accept_invite(ctx, token=token)
    return JSONResponse({"scopes": scopes})


@operations_router.post("/echo", status_code=202)
async def submit_echo(
    body: dto.EchoRequest,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, str]:
    """Submit a long write: validate, persist, enqueue, answer 202."""
    operation_id = await controller.submit_echo(ctx, message=body.message)
    return {"operation_id": operation_id}


@operations_router.get("/{operation_id}", response_model=dto.OperationResponse)
async def get_operation(
    operation_id: str,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dto.OperationResponse:
    """One operation for its owner; anyone else's answers 404."""
    return await controller.get_operation(ctx, operation_id=operation_id)


async def sse_stream(user_id: UUID, *, ping_s: float = 20.0) -> AsyncIterator[str]:
    """Yield SSE data frames for a user; heartbeat every ping_s seconds.

    The heartbeat uses asyncio.wait so a quiet interval never cancels
    the subscription itself; teardown cancels the pending read and
    unsubscribes in finally.
    """
    from app.services.events import subscribe

    stream = subscribe(user_id)
    pending: asyncio.Task[dict[str, Any]] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(anext(stream))
            done, _ = await asyncio.wait({pending}, timeout=ping_s)
            if pending in done:
                event = pending.result()
                pending = None
                yield f"data: {json.dumps(event)}\n\n"
            else:
                yield ": ping\n\n"
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
        await stream.aclose()


@events_router.get("/events")
async def events(ctx: RequestContext = Depends(get_ctx)) -> StreamingResponse:  # noqa: B008 - FastAPI dependency injection idiom
    """Per-user SSE stream; the DB session is closed before streaming."""
    principal = require_auth(ctx)
    await ctx.db.commit()
    await ctx.db.close()
    return StreamingResponse(
        sse_stream(principal.user_id, ping_s=ctx.settings.sse_ping_s),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
