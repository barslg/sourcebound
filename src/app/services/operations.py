"""Long writes are operations: submit, run in the worker, stream progress.

submit_<x> validates, authorises, persists the operations row,
commits, enqueues a Procrastinate job, and answers 202 with the
operation id. The worker re-authorises with the stored actor, updates
progress, writes audit, persists a notification row, and emits the
event. T02 ships one real operation (echo, the backbone proof); later
slices add their own submit_<x> on this framework.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.errors import NotFoundError, ValidationError
from app.rbac.principal import Principal, load_principal
from app.rbac.require import require, require_scope
from app.services.audit import write_audit

OPERATION_KIND_ECHO = "echo"
OPERATION_KIND_TRANSCRIBE_AUDIO = "transcribe_audio"

OPERATION_CODES = {
    "submit_plan": "course.plan",
    "submit_generate": "course.generate",
    "submit_translate": "course.generate",
    "resume_run": "run.retry",
    "rewrite_block": "course.generate",
    "rewrite_held_batch": "course.generate",
    OPERATION_KIND_TRANSCRIBE_AUDIO: "source.upload",
    OPERATION_KIND_ECHO: "operation.run",
}


async def submit_echo(
    session: AsyncSession,
    *,
    actor: Principal,
    payload: dict[str, Any],
    defer: Callable[[UUID], Awaitable[None]] | None,
    idempotency_key: str | None = None,
) -> UUID:
    """Validate, authorise, persist, and enqueue an echo operation.

    A repeated submit with the same idempotency key returns the first
    operation instead of starting a duplicate.
    """
    require(actor, "operation.run")
    message = payload.get("message", "")
    if not isinstance(message, str) or not message.strip():
        raise ValidationError("payload.message must be a non-empty string")
    if len(message) > 10000:
        raise ValidationError("payload.message is too long")
    if idempotency_key:
        existing = (
            await session.execute(
                sa.text(
                    "SELECT id FROM operations WHERE actor_id = :actor"
                    " AND idempotency_key = :key LIMIT 1"
                ),
                {"actor": str(actor.user_id), "key": idempotency_key},
            )
        ).first()
        if existing is not None:
            return UUID(str(existing[0]))
    row = (
        await session.execute(
            sa.text(
                "INSERT INTO operations (kind, status, params, progress, actor_id, idempotency_key)"
                " VALUES (:kind, 'queued', CAST(:params AS jsonb), 0, :actor, :key)"
                " RETURNING id"
            ),
            {
                "kind": OPERATION_KIND_ECHO,
                "params": json.dumps({"message": message}),
                "actor": str(actor.user_id),
                "key": idempotency_key,
            },
        )
    ).one()
    operation_id = UUID(str(row[0]))
    await write_audit(
        session,
        actor_id=actor.user_id,
        action="operation.submit",
        entity="operation",
        entity_id=str(operation_id),
        meta={"kind": OPERATION_KIND_ECHO},
    )
    await session.commit()
    if defer is not None:
        await defer(operation_id)
    return operation_id


async def _owner_tags(session: AsyncSession, owner: UUID | None) -> list[str]:
    """Every scope tag granted to the operation's owner (none when ownerless)."""
    if owner is None:
        return []
    rows = (
        await session.execute(
            sa.text("SELECT ur.scope_tags FROM user_roles ur WHERE ur.user_id = :user"),
            {"user": str(owner)},
        )
    ).all()
    return sorted({tag for (tags,) in rows for tag in (tags or [])})


async def get_operation(
    session: AsyncSession, *, actor: Principal, operation_id: UUID
) -> dict[str, Any]:
    """One operation for its owner; anyone else's answers 404.

    An operation.view holder reads only operations whose owner lies in
    their operation.view scope; anything else answers 404.
    """
    row = (
        await session.execute(
            sa.text(
                "SELECT id, kind, status, progress, result, error, actor_id,"
                " created_at, updated_at FROM operations WHERE id = :op"
            ),
            {"op": str(operation_id)},
        )
    ).first()
    if row is None:
        raise NotFoundError("operation not found")
    values = tuple(row)
    owner = str(values[6]) if values[6] is not None else None
    if not actor.is_superadmin and owner != str(actor.user_id):
        from app.errors import ForbiddenError

        try:
            require(actor, "operation.view")
        except ForbiddenError as exc:
            raise NotFoundError("operation not found") from exc
        require_scope(
            actor,
            await _owner_tags(session, UUID(owner) if owner is not None else None),
            code="operation.view",
        )
    return {
        "operation_id": str(values[0]),
        "kind": values[1],
        "status": values[2],
        "progress": float(values[3]),
        "result": values[4],
        "error": values[5],
    }


async def _set_status(
    session: AsyncSession,
    operation_id: UUID,
    *,
    status: str,
    progress: float,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    await session.execute(
        sa.text(
            "UPDATE operations SET status = :status, progress = :progress,"
            " result = CAST(:result AS jsonb), error = :error, updated_at = now()"
            " WHERE id = :op"
        ),
        {
            "status": status,
            "progress": progress,
            "result": json.dumps(result) if result is not None else None,
            "error": error,
            "op": str(operation_id),
        },
    )


async def execute_operation(
    operation_id: UUID,
    *,
    database_url: str | None = None,
    required_code: str = "operation.run",
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Run one operation to completion: the worker entry point.

    Opens a fresh session (jobs never reuse request sessions),
    re-authorises the stored actor, advances progress, writes audit,
    persists the notification row, issues pg_notify, and publishes
    to the in-process bus for local SSE subscribers.
    """
    from app.services.events import publish_event

    engine = None
    if session_factory is None:
        engine = create_async_engine(
            database_url or get_settings().database_url, pool_pre_ping=True
        )
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    else:
        factory = session_factory
    try:
        async with factory() as session:
            row = (
                await session.execute(
                    sa.text("SELECT kind, status, actor_id FROM operations WHERE id = :op"),
                    {"op": str(operation_id)},
                )
            ).first()
            if row is None:
                return
            kind, _, actor_id = row
            await _set_status(session, operation_id, status="running", progress=0.1)
            await session.commit()
            try:
                actor = (
                    await load_principal(session, UUID(str(actor_id)))
                    if actor_id is not None
                    else None
                )
                require(actor, OPERATION_CODES.get(str(kind), required_code))
                await _set_status(session, operation_id, status="running", progress=0.5)
                await session.commit()
                detail = await _run_kind(session, str(kind), operation_id, factory=factory)
                await _set_status(session, operation_id, status="done", progress=1.0, result=detail)
                target = UUID(str(actor_id)) if actor_id is not None else None
                if target is not None:
                    await session.execute(
                        sa.text(
                            "INSERT INTO notifications (user_id, kind, payload)"
                            " VALUES (:user, 'operation.done', CAST(:payload AS jsonb))"
                        ),
                        {
                            "user": str(target),
                            "payload": json.dumps(
                                {"operation_id": str(operation_id), "status": "done"}
                            ),
                        },
                    )
                if actor is not None:
                    await write_audit(
                        session,
                        actor_id=actor.user_id,
                        action="operation.done",
                        entity="operation",
                        entity_id=str(operation_id),
                        meta={"kind": str(kind)},
                    )
                await session.execute(
                    sa.text("SELECT pg_notify('muse_events', :payload)"),
                    {
                        "payload": json.dumps(
                            {
                                "operation_id": str(operation_id),
                                "status": "done",
                                "user_id": str(target) if target is not None else None,
                            }
                        )
                    },
                )
                await session.commit()
                if target is not None:
                    await publish_event(
                        target, {"operation_id": str(operation_id), "status": "done"}
                    )
            except Exception as exc:
                await session.rollback()
                async with factory() as failed:
                    await _set_status(
                        failed,
                        operation_id,
                        status="failed",
                        progress=1.0,
                        error=str(exc)[:2000],
                    )
                    await failed.commit()
    finally:
        if engine is not None:
            await engine.dispose()


async def _run_kind(
    session: AsyncSession,
    kind: str,
    operation_id: UUID,
    *,
    factory: async_sessionmaker[AsyncSession] | None = None,
) -> dict[str, Any]:
    """Do the kind-specific work; echo returns its submitted message."""
    if kind == "submit_plan":
        from app.services.planruns import real_provider_factory, run_plan_operation

        return await run_plan_operation(
            session,
            operation_id,
            provider_factory=real_provider_factory(get_settings()),
            session_factory=factory,
        )
    if kind == "submit_generate":
        from app.services.planruns import real_provider_factory, run_generate_operation

        return await run_generate_operation(
            session,
            operation_id,
            provider_factory=real_provider_factory(get_settings()),
            session_factory=factory,
        )
    if kind == "submit_translate":
        from app.services.planruns import real_provider_factory, run_translate_operation

        return await run_translate_operation(
            session,
            operation_id,
            provider_factory=real_provider_factory(get_settings()),
            session_factory=factory,
        )
    if kind == "resume_run":
        from app.services.planruns import real_provider_factory, run_resume_operation

        return await run_resume_operation(
            session,
            operation_id,
            provider_factory=real_provider_factory(get_settings()),
            session_factory=factory,
        )
    if kind in ("rewrite_block", "rewrite_held_batch"):
        from app.services.planruns import real_provider_factory, run_generate_operation

        return await run_generate_operation(
            session,
            operation_id,
            provider_factory=real_provider_factory(get_settings()),
            session_factory=factory,
        )
    if kind == OPERATION_KIND_TRANSCRIBE_AUDIO:
        from app.services.library import transcribe_source

        params = (
            await session.execute(
                sa.text("SELECT params FROM operations WHERE id = :op"),
                {"op": str(operation_id)},
            )
        ).scalar_one()
        count = await transcribe_source(
            session,
            source_id=UUID(str((params or {})["source_id"])),
            settings=get_settings(),
        )
        return {"source_id": str((params or {})["source_id"]), "passages": count}
    if kind == OPERATION_KIND_ECHO:
        params = (
            await session.execute(
                sa.text("SELECT params FROM operations WHERE id = :op"),
                {"op": str(operation_id)},
            )
        ).scalar_one()
        return {"echo": (params or {}).get("message", "")}
    raise NotFoundError(f"unknown operation kind {kind!r}")
