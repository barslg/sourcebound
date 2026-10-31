"""Plan and generate operations: submit_plan / submit_generate + resume.

Both submit paths validate, authorise, persist the operation with a
per-actor idempotency key, commit, enqueue, and answer 202. The worker
runs them through the runs executor; generate refuses while a required
question is open. resume_run continues a stopped run from its last
good step and never repays a done step.
"""

from __future__ import annotations

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
    from app.services.courses import blocking_questions

    open_questions = await blocking_questions(session, course_id=course_id)
    if open_questions:
        raise ValidationError(
            "generation refused: answer required questions first: "
            + ", ".join(question["key"] for question in open_questions)
        )
    return await _submit(
        session,
        actor=actor,
        kind=OPERATION_KIND_SUBMIT_GENERATE,
        course_id=course_id,
        params={},
        idempotency_key=idempotency_key,
        defer=defer,
        required_code="course.generate",
    )


async def resume_run(
    session: AsyncSession,
    *,
    actor: Principal,
    run_id: UUID,
    defer: Callable[[UUID], Awaitable[None]] | None = None,
) -> UUID:
    """Resume a stopped run from its last good step (never repaid)."""
    require(actor, "run.retry")

    row = (
        await session.execute(
            sa.text("SELECT status FROM runs WHERE id = :run"),
            {"run": str(run_id)},
        )
    ).first()
    if row is None:
        raise NotFoundError(f"run {run_id} not found")
    if row[0] not in ("stopped", "running"):
        raise ValidationError(f"run {run_id} is {row[0]}; only stopped runs resume")
    await session.execute(
        sa.text("UPDATE runs SET status = 'running' WHERE id = :run"),
        {"run": str(run_id)},
    )
    await session.commit()
    if defer is not None:
        await defer(run_id)
    return run_id


async def run_plan_operation(session: AsyncSession, operation_id: UUID) -> dict[str, Any]:
    """Worker body for submit_plan: drive researcher steps plus architect."""
    from app.services import courses as course_service
    from app.services.library import ts_config_for_language

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
    ts_config = ts_config_for_language(course["language"])
    dag: list[dict[str, Any]] = [
        {
            "step_key": f"researcher:{index}",
            "handler": "researcher",
            "depends_on": [],
            "query": query,
            "language": course["language"],
            "ts_config": ts_config,
            "course_id": str(course_id),
        }
        for index, query in enumerate(params["queries"])
    ]
    dag.append(
        {
            "step_key": "architect:plan",
            "handler": "architect",
            "depends_on": [entry["step_key"] for entry in dag],
            "course_id": str(course_id),
            "profile": course["profile"],
            "constraints": course["constraints"],
        }
    )
    from app.runs.executor import new_run

    run_id = await new_run(session, course_id=course_id, kind="plan", dag=dag)
    await session.commit()
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
