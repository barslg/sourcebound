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
from app.controllers import t02 as controller
from app.schemas import t02 as dto

invites_accept_router = APIRouter(prefix="/invites", tags=["invites"])
operations_router = APIRouter(prefix="/operations", tags=["operations"])
events_router = APIRouter(tags=["events"])


@invites_accept_router.post("/{token}/accept", response_model=None)
async def accept_invite(token: str, ctx: RequestContext = Depends(get_ctx)) -> Response:  # noqa: B008 - FastAPI dependency injection idiom
    """Accept an invite.

    Signed in: grant the scoped student role now. Signed out: stash
    the invite id on an anonymous session and 303 at sign-in with the
    course page as the return address; the callback applies the invite
    server-side after login.
    """
    if ctx.principal is None:
        from app.auth.session import set_session_cookie

        target, fresh_token = await controller.stash_pending_invite(ctx, token=token)
        redirect = RedirectResponse(target, status_code=303)
        if fresh_token:
            secret = (
                ctx.settings.session_secret_key.get_secret_value()
                if ctx.settings.session_secret_key
                else None
            )
            set_session_cookie(
                redirect,
                app_env=ctx.settings.app_env,
                cookie_name=ctx.settings.session_cookie_name,
                secret=secret,
                token=fresh_token,
                ttl_s=ctx.settings.session_ttl_s,
                secure_configured=ctx.settings.session_secure_cookie,
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
    operation_id = await controller.submit_echo(
        ctx, message=body.message, idempotency_key=body.idempotency_key
    )
    return {"operation_id": operation_id}


@operations_router.get("/{operation_id}", response_model=dto.OperationResponse)
async def get_operation(
    operation_id: str,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dto.OperationResponse:
    """One operation for its owner; anyone else's answers 404."""
    return await controller.get_operation(ctx, operation_id=operation_id)


MAX_STREAMS_PER_USER = 5
SESSION_RECHECK_S = 15.0


async def _session_still_valid(database_url: str, *, token_hash: str, user_id: UUID) -> bool:
    """True when the session row exists and its owner may still sign in."""
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            row = (
                await session.execute(
                    sa.text("SELECT user_id FROM sessions WHERE token_hash = :hash"),
                    {"hash": token_hash},
                )
            ).first()
            if row is None or str(row[0]) != str(user_id):
                return False
            user = (
                await session.execute(
                    sa.text("SELECT banned_at, is_legacy FROM users WHERE id = :user"),
                    {"user": str(user_id)},
                )
            ).first()
            if user is None or user[0] is not None or bool(user[1]):
                return False
            return True
    finally:
        await engine.dispose()


async def sse_stream(
    user_id: UUID,
    *,
    ping_s: float = 20.0,
    session_token_hash: str | None = None,
    database_url: str | None = None,
    recheck_s: float = SESSION_RECHECK_S,
) -> AsyncIterator[str]:
    """Yield SSE data frames for a user; heartbeat every ping_s seconds.

    The heartbeat uses asyncio.wait so a quiet interval never cancels
    the subscription itself; teardown cancels the pending read and
    unsubscribes in finally. When a session hash and database URL are
    given, the session and the ban flag are re-checked every recheck_s
    seconds and the stream ends once they fail.
    """
    import time

    from app.services.events import subscribe

    stream = subscribe(user_id)
    pending: asyncio.Task[dict[str, Any]] | None = None
    last_check = time.monotonic()
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
            if session_token_hash and database_url:
                now = time.monotonic()
                if now - last_check >= recheck_s:
                    last_check = now
                    valid = await _session_still_valid(
                        database_url, token_hash=session_token_hash, user_id=user_id
                    )
                    if not valid:
                        return
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            try:
                await pending
            except BaseException:
                pass
        await stream.aclose()


@events_router.get("/events")
async def events(ctx: RequestContext = Depends(get_ctx)) -> StreamingResponse:  # noqa: B008 - FastAPI dependency injection idiom
    """Per-user SSE stream; the DB session is closed before streaming."""
    from app.auth.session import hash_token
    from app.services.events import subscriber_count

    principal = require_auth(ctx)
    if subscriber_count(principal.user_id) >= MAX_STREAMS_PER_USER:
        return JSONResponse(status_code=429, content={"message": "too many open streams"})  # type: ignore[return-value]
    token_hash = hash_token(ctx.session_token) if ctx.session_token else None
    database_url = ctx.settings.database_url
    await ctx.db.commit()
    await ctx.db.close()
    return StreamingResponse(
        sse_stream(
            principal.user_id,
            ping_s=ctx.settings.sse_ping_s,
            session_token_hash=token_hash,
            database_url=database_url,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
