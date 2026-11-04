"""Build the whole course: one operation drives plan, answers, generation and rewrite.

A build is a `build_course` operation, not a run kind. Its worker body works
out the next stage from the database (plan present? questions open? draft
blocks? held blocks?), drives the ordinary child runs through the same
functions the manual buttons use, and ends `done` (ready for the author) or
`failed` (stopped, with a plain reason). The spend cap is enforced by the
provider through `runs.spend_ceiling_usd`, set here before every drive.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any, NoReturn
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ConflictError, UnprocessableError, ValidationError
from app.rbac.principal import Principal
from app.rbac.require import require
from app.runs.stop_text import CAP_REACHED, EMPTY_PLAN, MODEL_DOWN
from app.services.audit import write_audit

OPERATION_KIND_BUILD = "build_course"
BUILD_LOCK_PREFIX = "build:"
BUILD_REWRITE_ROUNDS = 1
MAX_GENERATION_RUNS = 3
MAX_EVENTS = 5
MODEL_DOWN_BACKOFF = (60, 180, 540)
BUSY_WAIT_SECONDS = 30
REVIVE_AFTER_SECONDS = 120
ASSUMED_SENTENCE = (
    "Not answered \N{EM DASH} the plan follows the sources and the profile as they stand"
)
STATE_OF_STATUS = {
    "queued": "building",
    "running": "building",
    "done": "ready",
    "failed": "stopped",
}


class BuildStopped(Exception):
    """The build cannot go on; the message is the plain reason shown to the author."""

    def __init__(self, reason: str, code: str) -> None:
        super().__init__(reason)
        self.code = code


async def _now_iso(session: AsyncSession) -> str:
    return str((await session.execute(sa.text("SELECT now()"))).scalar_one().isoformat())


async def _modules(session: AsyncSession, course_id: UUID) -> int:
    row = await session.execute(
        sa.text("SELECT count(*) FROM modules WHERE course_id = :c"), {"c": str(course_id)}
    )
    return int(row.scalar_one())


async def _blocks_in(session: AsyncSession, course_id: UUID, status: str) -> int:
    row = await session.execute(
        sa.text(
            "SELECT count(*) FROM blocks b JOIN topics t ON t.id = b.topic_id"
            " JOIN modules m ON m.id = t.module_id WHERE m.course_id = :c AND b.status = :s"
        ),
        {"c": str(course_id), "s": status},
    )
    return int(row.scalar_one())


async def build_spent(session: AsyncSession, *, course_id: UUID, started_at: str) -> Decimal:
    """What the course's runs spent since the build started (the cap's measure)."""
    row = await session.execute(
        sa.text(
            "SELECT COALESCE(SUM(l.cost_usd), 0) FROM llm_calls l JOIN runs r ON r.id = l.run_id"
            " WHERE r.course_id = :c AND l.created_at >= CAST(CAST(:s AS text) AS timestamptz)"
        ),
        {"c": str(course_id), "s": started_at},
    )
    return Decimal(str(row.scalar_one()))


async def submit_build(
    session: AsyncSession,
    *,
    actor: Principal,
    course_id: UUID,
    queries: list[str],
    cap_usd: Decimal,
    idempotency_key: str | None = None,
    continue_build: bool = False,
    defer: Callable[[UUID], Awaitable[None]] | None = None,
) -> UUID:
    """Queue a build (202): validate, refuse while other work is live, persist, enqueue.

    `continue_build` re-queues the last stopped build with a new cap; the cap is
    counted from that build's original start, so a raised cap never forgets spend.
    """
    from app.services import courses as course_service
    from app.services.planruns import (
        _approved_passage_count,
        generation_blockers,
        live_work_on,
    )

    require(actor, "course.plan")
    require(actor, "course.generate")
    if cap_usd <= 0:
        raise ValidationError("the spending cap must be more than zero")
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
    if await live_work_on(session, course_id=course_id):
        raise ConflictError("A run is already going")
    started_at = await _now_iso(session)
    extra: dict[str, Any] = {}
    cleaned = [query.strip() for query in queries if query.strip()]
    if continue_build:
        last = (
            await session.execute(
                sa.text(
                    "SELECT id, status, params FROM operations WHERE kind = :k"
                    " AND params->>'course_id' = :c ORDER BY created_at DESC LIMIT 1"
                ),
                {"k": OPERATION_KIND_BUILD, "c": str(course_id)},
            )
        ).first()
        if last is None or last[1] != "failed":
            raise UnprocessableError("There is no stopped build to continue.")
        started_at = str(last[2]["started_at"])
        cleaned = cleaned or [str(q) for q in last[2].get("queries", [])]
        extra["continue_of"] = str(last[0])
    if await _modules(session, course_id) == 0:
        course = await course_service.get_course(session, course_id=course_id)
        if course["profile"] is None:
            raise ValidationError("set the course profile before building the course")
        if not cleaned:
            raise ValidationError("queries must be a non-empty list of non-empty strings")
        if await _approved_passage_count(session, course_id=course_id) == 0:
            raise UnprocessableError("Add and approve at least one source before building.")
    else:
        blocker = await generation_blockers(session, course_id=course_id)
        if isinstance(blocker, UnprocessableError):
            raise blocker
    row = (
        await session.execute(
            sa.text(
                "INSERT INTO operations (kind, status, params, progress, actor_id, idempotency_key)"
                " VALUES (:kind, 'queued', CAST(:params AS jsonb), 0, :actor, :key)"
                " RETURNING id"
            ),
            {
                "kind": OPERATION_KIND_BUILD,
                "params": json.dumps(
                    {
                        "course_id": str(course_id),
                        "queries": cleaned,
                        "cap_usd": str(cap_usd),
                        "started_at": started_at,
                        **extra,
                    }
                ),
                "actor": str(actor.user_id),
                "key": idempotency_key,
            },
        )
    ).one()
    operation_id = UUID(str(row[0]))
    await write_audit(
        session,
        actor_id=actor.user_id,
        action=f"{OPERATION_KIND_BUILD}.submit",
        entity="operation",
        entity_id=str(operation_id),
        meta={"course_id": str(course_id), "cap_usd": str(cap_usd)},
    )
    await session.commit()
    if defer is not None:
        await defer(operation_id)
    return operation_id


class _Build:
    """One build's loop: the state lives in the database, never in this object."""

    def __init__(
        self,
        *,
        operation_id: UUID,
        course_id: UUID,
        queries: list[str],
        cap: Decimal,
        started_at: str,
        provider_factory: Any,
        session_factory: Any,
    ) -> None:
        self.op = operation_id
        self.course_id = course_id
        self.queries = queries
        self.cap = cap
        self.started_at = started_at
        self.pf = provider_factory
        self.factory = session_factory
        self.resumes: dict[str, int] = {}
        self.last_failed: str | None = None

    async def note(self, *, event: str | None = None, **fields: Any) -> None:
        """Merge progress into operations.result; the update also stamps updated_at."""
        async with self.factory() as session:
            current = (
                await session.execute(
                    sa.text("SELECT result FROM operations WHERE id = :op"), {"op": str(self.op)}
                )
            ).scalar_one() or {}
            events = list(current.get("events", []))
            if event:
                events = [*events, event][-MAX_EVENTS:]
            merged = {**current, **fields, "events": events}
            await session.execute(
                sa.text(
                    "UPDATE operations SET result = CAST(:r AS jsonb), updated_at = now()"
                    " WHERE id = :op"
                ),
                {"r": json.dumps(merged), "op": str(self.op)},
            )
            await session.commit()

    async def spent(self) -> Decimal:
        async with self.factory() as session:
            return await build_spent(session, course_id=self.course_id, started_at=self.started_at)

    async def stop(self, reason: str, code: str, *, event: str | None = None) -> NoReturn:
        await self.note(stop_code=code, event=event or reason)
        raise BuildStopped(reason, code)

    async def check_cap(self) -> Decimal:
        """Raise the stop when the cap is spent; else the amount still available."""
        spent = await self.spent()
        if spent >= self.cap:
            await self.stop(f"{CAP_REACHED} Spent ${spent:.2f} of ${self.cap:.2f}.", "cap")
        return self.cap - spent

    async def facts(self) -> dict[str, Any]:
        from app.services.courses import blocking_questions

        async with self.factory() as session:
            return {
                "modules": await _modules(session, self.course_id),
                "drafts": await _blocks_in(session, self.course_id, "draft"),
                "held": await _blocks_in(session, self.course_id, "held"),
                "questions": await blocking_questions(session, course_id=self.course_id),
            }

    async def newest_run(self, kind: str, *, since: str | None = None) -> tuple[UUID, str] | None:
        async with self.factory() as session:
            row = (
                await session.execute(
                    sa.text(
                        "SELECT id, status FROM runs WHERE course_id = :c AND kind = :k"
                        " AND (CAST(CAST(:since AS text) AS timestamptz) IS NULL"
                        " OR created_at >= CAST(CAST(:since AS text) AS timestamptz))"
                        " ORDER BY created_at DESC, id LIMIT 1"
                    ),
                    {"c": str(self.course_id), "k": kind, "since": since},
                )
            ).first()
        return (UUID(str(row[0])), str(row[1])) if row is not None else None

    async def count_runs_since(self, kind: str) -> int:
        async with self.factory() as session:
            row = await session.execute(
                sa.text(
                    "SELECT count(*) FROM runs WHERE course_id = :c AND kind = :k"
                    " AND created_at >= CAST(CAST(:s AS text) AS timestamptz)"
                ),
                {"c": str(self.course_id), "k": kind, "s": self.started_at},
            )
            return int(row.scalar_one())

    async def set_ceiling(self, run_id: UUID, remaining: Decimal) -> None:
        """The run may spend what it has spent plus what the cap still allows."""
        async with self.factory() as session:
            await session.execute(
                sa.text(
                    "UPDATE runs SET spend_ceiling_usd = COALESCE((SELECT SUM(cost_usd)"
                    " FROM llm_calls WHERE run_id = :r), 0) + CAST(:rem AS numeric)"
                    " WHERE id = :r"
                ),
                {"r": str(run_id), "rem": str(remaining)},
            )
            await session.commit()

    async def drive(self, stage: str, run_id: UUID) -> dict[str, Any]:
        """Drive one child run to done, resuming it under the stop policy."""
        from app.services.planruns import drive_and_close, reopen_run

        while True:
            remaining = await self.check_cap()
            await self.set_ceiling(run_id, remaining)
            await self.note(stage=stage, current_run_id=str(run_id), run_id=str(run_id))
            final: dict[str, Any] = await drive_and_close(self.factory, self.pf, run_id)
            if final["status"] == "done":
                self.last_failed = None
                return final
            if final["status"] == "running":
                await asyncio.sleep(BUSY_WAIT_SECONDS)
                continue
            marker = str(final.get("stop_marker") or "")
            code = final.get("stop_code")
            if marker == "cancel requested":
                await self.stop("The build was stopped by you.", "cancelled")
            if code == "cap_reached":
                spent = await self.spent()
                await self.stop(f"{CAP_REACHED} Spent ${spent:.2f} of ${self.cap:.2f}.", "cap")
            if code == "empty_plan":
                await self.stop(EMPTY_PLAN, "empty_plan")
            failed = [s["step_key"] for s in final["steps"] if s["status"] == "failed"]
            step = str(failed[0]) if failed else marker
            if code == "model_down":
                tried = self.resumes.get(stage, 0)
                if tried >= len(MODEL_DOWN_BACKOFF):
                    await self.stop(
                        f"{MODEL_DOWN} The build waited and tried {tried} times.", "model_down"
                    )
                wait = MODEL_DOWN_BACKOFF[tried]
                self.resumes[stage] = tried + 1
                await self.note(
                    event=f"The model did not answer; trying again in {wait} seconds.",
                    resumes=sum(self.resumes.values()),
                )
                await asyncio.sleep(wait)
            else:
                if step == self.last_failed:
                    await self.stop(marker or "A step stopped twice in a row.", "step_failed")
                self.last_failed = step
                await self.note(
                    event="A step stopped; the build resumes it.",
                    resumes=sum(self.resumes.values()) + 1,
                )
            async with self.factory() as session:
                await reopen_run(session, run_id=run_id)
                await session.commit()

    async def run_for(
        self,
        stage: str,
        kind: str,
        start: Callable[[AsyncSession], Awaitable[UUID]],
        *,
        since: str | None = None,
    ) -> UUID:
        """Resume the newest unfinished run of a kind, else start one; drive it to done."""
        from app.services.planruns import reopen_run

        newest = await self.newest_run(kind, since=since)
        if newest is not None and newest[1] != "done":
            run_id = newest[0]
            async with self.factory() as session:
                await reopen_run(session, run_id=run_id)
                await session.commit()
        else:
            await self.check_cap()
            async with self.factory() as session:
                run_id = await start(session)
        await self.drive(stage, run_id)
        return run_id

    async def plan_stage(self) -> None:
        from app.services.planruns import finish_plan_run, start_plan_run

        await self.note(
            stage="plan", event="Planning: reading the sources and outlining the course."
        )
        run_id = await self.run_for(
            "plan",
            "plan",
            lambda session: start_plan_run(session, course_id=self.course_id, queries=self.queries),
        )
        await finish_plan_run(self.factory, course_id=self.course_id, run_id=run_id)
        async with self.factory() as session:
            if await _modules(session, self.course_id) == 0:
                await self.stop(EMPTY_PLAN, "empty_plan")

    async def answer_stage(self, questions: list[dict[str, Any]]) -> None:
        from app.services.courses import edit_plan

        assumable = [q for q in questions if q["kind"] == "author_question"]
        if not assumable:
            keys = ", ".join(q["key"] for q in questions)
            await self.stop(f"Answer the open questions to go on: {keys}.", "questions")
        async with self.factory() as session:
            await edit_plan(
                session,
                course_id=self.course_id,
                operations=[
                    {
                        "op": "set_constraint",
                        "key": q["key"],
                        "answer": {"value": ASSUMED_SENTENCE, "assumed": True},
                    }
                    for q in assumable
                ],
            )
            await session.commit()
        await self.note(
            stage="answers", event=f"{len(assumable)} open questions were assumed, not answered."
        )

    async def generate_stage(self) -> None:
        from app.services.planruns import (
            finish_blocks_run,
            generation_blockers,
            start_generation_run,
        )

        newest = await self.newest_run("generation")
        if newest is None or newest[1] == "done":
            if await self.count_runs_since("generation") >= MAX_GENERATION_RUNS:
                await self.stop(
                    "Some blocks could not be written. Open the last run.", "step_failed"
                )
            async with self.factory() as session:
                blocker = await generation_blockers(session, course_id=self.course_id)
            if blocker is not None:
                code = "tree" if isinstance(blocker, UnprocessableError) else "questions"
                await self.stop(str(blocker), code)
        await self.note(stage="generate", event="Writing the blocks and reading them back.")
        await self.run_for(
            "generate",
            "generation",
            lambda session: start_generation_run(session, course_id=self.course_id),
        )
        await finish_blocks_run(self.factory, course_id=self.course_id)

    async def released_by_rework(self) -> int:
        """Blocks the newest generation run's rework step rewrote that are no longer held."""
        async with self.factory() as session:
            row = await session.execute(
                sa.text(
                    "SELECT count(*) FROM blocks b JOIN topics t ON t.id = b.topic_id"
                    " JOIN modules m ON m.id = t.module_id WHERE m.course_id = :c"
                    " AND b.status <> 'held' AND b.plan_node_id IN ("
                    " SELECT jsonb_array_elements_text(rs.output -> 'reworked')"
                    " FROM run_steps rs WHERE rs.step_key LIKE 'rework:%'"
                    " AND jsonb_typeof(rs.output -> 'reworked') = 'array'"
                    " AND rs.run_id = (SELECT r.id FROM runs r WHERE r.course_id = :c"
                    " AND r.kind = 'generation' ORDER BY r.created_at DESC, r.id LIMIT 1))"
                ),
                {"c": str(self.course_id)},
            )
            return int(row.scalar_one())

    async def rewrite_stage(self) -> None:
        from app.services.planruns import finish_blocks_run, start_rewrite_run

        async def start(session: AsyncSession) -> UUID:
            ids = (
                await session.execute(
                    sa.text(
                        "SELECT b.id FROM blocks b JOIN topics t ON t.id = b.topic_id"
                        " JOIN modules m ON m.id = t.module_id"
                        " WHERE m.course_id = :c AND b.status = 'held'"
                        " ORDER BY m.ordinal, t.ordinal, b.ordinal"
                    ),
                    {"c": str(self.course_id)},
                )
            ).all()
            return await start_rewrite_run(
                session, course_id=self.course_id, block_ids=[str(row[0]) for row in ids]
            )

        await self.note(stage="rewrite", event="Rewriting the blocks that were held.")
        await self.run_for("rewrite", "rewrite", start, since=self.started_at)
        await finish_blocks_run(self.factory, course_id=self.course_id)

    async def run(self) -> dict[str, Any]:
        await self.note(stage="starting", cap_usd=str(self.cap), started_at=self.started_at)
        while True:
            facts = await self.facts()
            if facts["modules"] == 0:
                await self.check_cap()
                await self.plan_stage()
                continue
            if facts["questions"]:
                await self.answer_stage(facts["questions"])
                continue
            if facts["drafts"] > 0:
                await self.check_cap()
                await self.generate_stage()
                continue
            rewrites = await self.newest_run("rewrite", since=self.started_at)
            if rewrites is not None and rewrites[1] != "done":
                await self.rewrite_stage()
                continue
            if (
                facts["held"] > 0
                and await self.count_runs_since("rewrite") < BUILD_REWRITE_ROUNDS
                and await self.released_by_rework() >= 1
            ):
                await self.check_cap()
                await self.rewrite_stage()
                continue
            break
        await self.note(
            stage="ready",
            held_count=facts["held"],
            event="The course is built.",
            state="ready",
        )
        async with self.factory() as session:
            result = (
                await session.execute(
                    sa.text("SELECT result FROM operations WHERE id = :op"), {"op": str(self.op)}
                )
            ).scalar_one()
        return dict(result)


async def run_build_operation(
    session: AsyncSession,
    operation_id: UUID,
    *,
    provider_factory: Any,
    session_factory: Any,
) -> dict[str, Any]:
    """Worker body for build_course; one driver per course holds `build:<course_id>`."""
    params = (
        await session.execute(
            sa.text("SELECT params FROM operations WHERE id = :op"), {"op": str(operation_id)}
        )
    ).scalar_one()
    await session.commit()
    course_id = UUID(str(params["course_id"]))
    engine = session_factory.kw.get("bind")
    if engine is None:  # pragma: no cover - every caller binds an engine
        raise ValidationError("run_build_operation needs a session factory bound to an engine")
    key = f"{BUILD_LOCK_PREFIX}{course_id}"
    async with engine.connect() as lock_conn:
        acquired = (
            await lock_conn.execute(
                sa.text("SELECT pg_try_advisory_lock(hashtext(:key))"), {"key": key}
            )
        ).scalar()
        if not acquired:
            return {"state": "duplicate"}
        try:
            return await _Build(
                operation_id=operation_id,
                course_id=course_id,
                queries=[str(q) for q in params.get("queries", [])],
                cap=Decimal(str(params["cap_usd"])),
                started_at=str(params["started_at"]),
                provider_factory=provider_factory,
                session_factory=session_factory,
            ).run()
        finally:
            await lock_conn.execute(
                sa.text("SELECT pg_advisory_unlock(hashtext(:key))"), {"key": key}
            )


async def build_status(session: AsyncSession, *, course_id: UUID) -> dict[str, Any]:
    """The course's latest build for the panel: state, stage, spend, events, exits."""
    row = (
        await session.execute(
            sa.text(
                "SELECT id, status, result, error, params FROM operations WHERE kind = :k"
                " AND params->>'course_id' = :c ORDER BY created_at DESC LIMIT 1"
            ),
            {"k": OPERATION_KIND_BUILD, "c": str(course_id)},
        )
    ).first()
    if row is None:
        return {"build": None}
    result = dict(row[2] or {})
    params = dict(row[4])
    spent = await build_spent(session, course_id=course_id, started_at=str(params["started_at"]))
    current = result.get("current_run_id")
    live = False
    if current:
        live = (
            await session.execute(
                sa.text("SELECT status = 'running' FROM runs WHERE id = :r"), {"r": str(current)}
            )
        ).scalar() is True
    assumed = (
        await session.execute(
            sa.text(
                "SELECT key, question, answer FROM profile_constraints"
                " WHERE course_id = :c AND answer ->> 'assumed' = 'true' ORDER BY key"
            ),
            {"c": str(course_id)},
        )
    ).all()
    estimate = (
        await session.execute(
            sa.text(
                "SELECT estimate FROM runs WHERE course_id = :c AND estimate IS NOT NULL"
                " ORDER BY created_at DESC LIMIT 1"
            ),
            {"c": str(course_id)},
        )
    ).scalar()
    return {
        "build": {
            "operation_id": str(row[0]),
            "state": STATE_OF_STATUS.get(str(row[1]), "building"),
            "stage": str(result.get("stage", "starting")),
            "cap_usd": str(params["cap_usd"]),
            "spent_usd": str(spent),
            "started_at": str(params["started_at"]),
            "current_run_id": str(current) if current else None,
            "can_stop": live and row[1] in ("queued", "running"),
            "events": [str(e) for e in result.get("events", [])],
            "assumed": [
                {"key": a[0], "question": a[1], "value": str((a[2] or {}).get("value", ""))}
                for a in assumed
            ],
            "held_count": int(result.get("held_count", 0)),
            "forecast": estimate,
            "error": row[3],
            "stop_code": result.get("stop_code"),
            "resumes": int(result.get("resumes", 0)),
        }
    }


async def revive_stranded_builds(
    session: AsyncSession, *, defer: Callable[[UUID], Awaitable[None]]
) -> list[UUID]:
    """Re-queue builds that say running while no worker drives them.

    Needs status running, no touch for REVIVE_AFTER_SECONDS, and the course's
    build lock free: the lock is held for the whole job, so a free lock proves
    nobody is driving. The row is touched so the next minute does not queue it again.
    """
    rows = (
        await session.execute(
            sa.text(
                "SELECT id, params->>'course_id' FROM operations WHERE kind = :k"
                " AND status = 'running'"
                " AND updated_at < now() - make_interval(secs => :age) ORDER BY created_at"
            ),
            {"k": OPERATION_KIND_BUILD, "age": REVIVE_AFTER_SECONDS},
        )
    ).all()
    revived: list[UUID] = []
    for operation_id, course_id in rows:
        key = f"{BUILD_LOCK_PREFIX}{course_id}"
        free = (
            await session.execute(
                sa.text("SELECT pg_try_advisory_lock(hashtext(:key))"), {"key": key}
            )
        ).scalar()
        if not free:
            continue
        await session.execute(sa.text("SELECT pg_advisory_unlock(hashtext(:key))"), {"key": key})
        await session.execute(
            sa.text("UPDATE operations SET updated_at = now() WHERE id = :op"),
            {"op": str(operation_id)},
        )
        await session.commit()
        await defer(UUID(str(operation_id)))
        revived.append(UUID(str(operation_id)))
    return revived
