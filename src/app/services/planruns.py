"""Plan and generate operations: submit_plan / submit_generate + resume.

Both submit paths validate, authorise, persist the operation with a
per-actor idempotency key, commit, enqueue, and answer 202. The worker
runs them through the runs executor; generate refuses while a required
question is open. resume_run continues a stopped run from its last
good step and never repays a done step.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import NotFoundError, ValidationError
from app.rbac.principal import Principal
from app.rbac.require import require
from app.services.audit import write_audit

OPERATION_KIND_SUBMIT_PLAN = "submit_plan"
OPERATION_KIND_SUBMIT_GENERATE = "submit_generate"


async def _submit(
    session: AsyncSession,
    *,
    actor: Principal,
    kind: str,
    course_id: UUID,
    params: dict[str, Any],
    idempotency_key: str | None,
    defer: Callable[[UUID], Awaitable[None]] | None,
    required_code: str,
) -> UUID:
    require(actor, required_code)
    if idempotency_key:
        existing = (
            await session.execute(
                sa.text(
                    "SELECT id FROM operations WHERE actor_id = :actor AND idempotency_key = :key"
                ),
                {"actor": str(actor.user_id), "key": idempotency_key},
            )
        ).first()
        if existing is not None:
            return UUID(str(existing[0]))
    row = (
        await session.execute(
            sa.text(
                "INSERT INTO operations"
                " (kind, status, params, progress, actor_id, idempotency_key)"
                " VALUES (:kind, 'queued', CAST(:params AS jsonb), 0, :actor, :key)"
                " RETURNING id"
            ),
            {
                "kind": kind,
                "params": json.dumps({**params, "course_id": str(course_id)}),
                "actor": str(actor.user_id),
                "key": idempotency_key,
            },
        )
    ).one()
    operation_id = UUID(str(row[0]))
    await write_audit(
        session,
        actor_id=actor.user_id,
        action=f"{kind}.submit",
        entity="operation",
        entity_id=str(operation_id),
        meta={"course_id": str(course_id)},
    )
    await session.commit()
    if defer is not None:
        await defer(operation_id)
    return operation_id


def researcher_node_id(query: str) -> str:
    """Stable plan node id for one researcher query (`<agent>:<plan_node_id>`).

    Researcher steps are addressed by their query, not by ordinal, so
    reordered queries keep their step keys and their reuse digests.
    """
    return "q" + hashlib.sha256(query.strip().encode("utf-8")).hexdigest()[:12]


async def submit_plan(
    session: AsyncSession,
    *,
    actor: Principal,
    course_id: UUID,
    queries: list[str],
    idempotency_key: str | None = None,
    defer: Callable[[UUID], Awaitable[None]] | None = None,
) -> UUID:
    """Submit plan generation: researcher passes plus the architect."""
    require(actor, "course.plan")
    if not queries or not all(query.strip() for query in queries):
        raise ValidationError("queries must be a non-empty list of non-empty strings")
    return await _submit(
        session,
        actor=actor,
        kind=OPERATION_KIND_SUBMIT_PLAN,
        course_id=course_id,
        params={"queries": queries},
        idempotency_key=idempotency_key,
        defer=defer,
        required_code="course.plan",
    )


async def submit_generate(
    session: AsyncSession,
    *,
    actor: Principal,
    course_id: UUID,
    idempotency_key: str | None = None,
    defer: Callable[[UUID], Awaitable[None]] | None = None,
) -> UUID:
    """Submit block generation; refused while a required question is open."""
    from app.errors import ConflictError
    from app.services.courses import blocking_questions

    require(actor, "course.generate")
    open_questions = await blocking_questions(session, course_id=course_id)
    if open_questions:
        raise ValidationError(
            "generation refused: answer required questions first: "
            + ", ".join(question["key"] for question in open_questions)
        )
    raise ConflictError("block generation is not available until the generation pipeline lands")


OPERATION_KIND_RESUME_RUN = "resume_run"


async def _no_driver_holds(session: AsyncSession, *, run_id: UUID) -> bool:
    """True when no live driver holds the run's session-level lock.

    The lock is acquired and released inside this call on the
    request's own session, so a pooled connection never keeps it.
    """
    from app.runs.executor import RUN_LOCK_PREFIX

    acquired = (
        await session.execute(
            sa.text("SELECT pg_try_advisory_lock(hashtext(:key))"),
            {"key": f"{RUN_LOCK_PREFIX}{run_id}"},
        )
    ).scalar()
    if acquired:
        await session.execute(
            sa.text("SELECT pg_advisory_unlock(hashtext(:key))"),
            {"key": f"{RUN_LOCK_PREFIX}{run_id}"},
        )
    return bool(acquired)


def plan_handlers() -> dict[str, Any]:
    """Handler map for plan/resume drives (the worker entry points)."""
    from app.runs import executor

    return {
        "researcher": executor.plan_researcher_step,
        "architect": executor.plan_architect_step,
    }


class _RealProviderFactory:
    """Production provider factory sharing one ledger engine.

    Every provider built by one drive shares a single ledger engine
    (one pool, not one per model call); `aclose` disposes it when the
    drive is done. `ledger()` hands out ledger sessions for probes.
    """

    def __init__(self, settings: Any) -> None:
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from app.db import create_engine

        self._settings = settings
        self._engine = create_engine(settings.database_url)
        self._ledger = async_sessionmaker(self._engine, class_=AsyncSession, expire_on_commit=False)

    def __call__(self, session: AsyncSession) -> Any:
        from app.providers.llm import MetaProvider

        return MetaProvider(session, self._settings, None, self._ledger)

    def ledger(self) -> Any:
        """Borrow the shared ledger factory's session (context manager)."""
        return self._ledger()

    async def aclose(self) -> None:
        """Dispose the shared engine; the drive is done paying."""
        await self._engine.dispose()


def real_provider_factory(settings: Any) -> _RealProviderFactory:
    """Production provider factory: Meta calls with ledger on the app DB."""
    return _RealProviderFactory(settings)


async def _close_factory(provider_factory: Any) -> None:
    """Dispose a shared-engine factory; plain callables have nothing to close."""
    aclose = getattr(provider_factory, "aclose", None)
    if aclose is not None:
        await aclose()


async def resume_run(
    session: AsyncSession,
    *,
    actor: Principal,
    run_id: UUID,
    defer: Callable[[UUID], Awaitable[None]] | None = None,
) -> UUID:
    """Resume a stopped (or stranded running) run: enqueue a resume operation.

    Stopped runs always resume. A running run resumes only when no
    driver holds its session-level lock — a queue failure after commit
    or a dead worker can strand a run in `running` with nobody
    driving it, and the lock proves it. A running run with a live
    driver still refuses. The worker — never the request — drives the
    resumed run, so resume pays nothing for done steps.
    """
    require(actor, "run.retry")

    row = (
        await session.execute(
            sa.text("SELECT status FROM runs WHERE id = :run"),
            {"run": str(run_id)},
        )
    ).first()
    if row is None:
        raise NotFoundError(f"run {run_id} not found")
    if row[0] == "running" and not await _no_driver_holds(session, run_id=run_id):
        raise ValidationError(f"run {run_id} is running; only stopped runs resume")
    if row[0] not in ("stopped", "running"):
        raise ValidationError(f"run {run_id} is {row[0]}; only stopped runs resume")
    await session.execute(
        sa.text("UPDATE runs SET status = 'running', stop_marker = NULL WHERE id = :run"),
        {"run": str(run_id)},
    )
    op_row = (
        await session.execute(
            sa.text(
                "INSERT INTO operations (kind, status, params, progress, actor_id)"
                " VALUES (:kind, 'queued', CAST(:params AS jsonb), 0, :actor)"
                " RETURNING id"
            ),
            {
                "kind": OPERATION_KIND_RESUME_RUN,
                "params": json.dumps({"run_id": str(run_id)}),
                "actor": str(actor.user_id),
            },
        )
    ).one()
    operation_id = UUID(str(op_row[0]))
    await session.commit()
    if defer is not None:
        await defer(operation_id)
    return run_id


async def run_plan_operation(
    session: AsyncSession,
    operation_id: UUID,
    *,
    provider_factory: Any | None = None,
    session_factory: Any | None = None,
) -> dict[str, Any]:
    """Worker body for submit_plan: create the run, then drive it.

    The worker drives the run to done or stopped (never the request:
    without a provider factory this only creates the run, which is
    what request-scoped tests use before driving explicitly).
    """
    from app.services import courses as course_service

    params = (
        await session.execute(
            sa.text("SELECT params FROM operations WHERE id = :op"),
            {"op": str(operation_id)},
        )
    ).scalar_one()
    course_id = UUID(str(params["course_id"]))
    course = await course_service.get_course(session, course_id=course_id)
    if course["profile"] is None:
        raise ValidationError("set the course profile before submitting a plan")
    dag: list[dict[str, Any]] = [
        {
            "step_key": f"researcher:{researcher_node_id(query)}",
            "handler": "researcher",
            "depends_on": [],
            "query": query,
            "language": course["language"],
            "course_id": str(course_id),
        }
        for query in params["queries"]
    ]
    dag.append(
        {
            "step_key": "architect:plan",
            "handler": "architect",
            "depends_on": [entry["step_key"] for entry in dag],
            "course_id": str(course_id),
            "language": course["language"],
            "profile": course["profile"],
            "constraints": course["constraints"],
        }
    )
    from app.runs.executor import drive_run, new_run

    run_id = await new_run(session, course_id=course_id, kind="plan", dag=dag)
    await session.commit()
    if provider_factory is not None and session_factory is not None:
        try:
            await drive_run(
                session_factory,
                run_id=run_id,
                provider_factory=provider_factory,
                handlers=plan_handlers(),
            )
        finally:
            await _close_factory(provider_factory)
        async with session_factory() as fresh:
            await refresh_plan_estimate(fresh, run_id=run_id)
        async with session_factory() as fresh:
            await course_service.snapshot_version(fresh, course_id=course_id)
            await fresh.commit()
    return {"run_id": str(run_id)}


async def run_resume_operation(
    session: AsyncSession,
    operation_id: UUID,
    *,
    provider_factory: Any | None = None,
    session_factory: Any | None = None,
) -> dict[str, Any]:
    """Worker body for resume_run: drive the run to done, then refresh it.

    Like the plan path, the resumed run gets its forecast refreshed
    and its plan snapshotted — resume is a full continuation, not a
    bare drive.
    """
    from app.services import courses as course_service

    params = (
        await session.execute(
            sa.text("SELECT params FROM operations WHERE id = :op"),
            {"op": str(operation_id)},
        )
    ).scalar_one()
    run_id = UUID(str(params["run_id"]))
    if provider_factory is None or session_factory is None:  # pragma: no cover - worker wires these
        raise ValidationError("resume needs a provider and session factory")
    from app.runs.executor import drive_run, run_course_id

    try:
        await drive_run(
            session_factory,
            run_id=run_id,
            provider_factory=provider_factory,
            handlers=plan_handlers(),
        )
    finally:
        await _close_factory(provider_factory)
    async with session_factory() as fresh:
        await refresh_plan_estimate(fresh, run_id=run_id)
    async with session_factory() as fresh:
        course_id = await run_course_id(fresh, run_id=run_id)
        if course_id is not None:
            await course_service.snapshot_version(fresh, course_id=course_id)
            await fresh.commit()
    return {"run_id": str(run_id)}


async def refresh_plan_estimate(session: AsyncSession, *, run_id: UUID) -> None:
    """Store the forecast from the architect's estimate inputs on the run."""
    import json as json_lib

    from app.agents.estimator import forecast

    row = (
        await session.execute(
            sa.text(
                "SELECT output FROM run_steps WHERE run_id = :run AND step_key = 'architect:plan'"
            ),
            {"run": str(run_id)},
        )
    ).first()
    if row is None or not row[0]:
        return
    estimate = forecast((row[0] or {}).get("estimate_inputs", {}))
    await session.execute(
        sa.text("UPDATE runs SET estimate = CAST(:estimate AS jsonb) WHERE id = :run"),
        {"estimate": json_lib.dumps(estimate), "run": str(run_id)},
    )
    await session.commit()
