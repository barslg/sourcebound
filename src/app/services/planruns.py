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

from app.errors import ConflictError, NotFoundError, UnprocessableError, ValidationError
from app.rbac.principal import Principal
from app.rbac.require import require
from app.services.audit import write_audit

OPERATION_KIND_SUBMIT_PLAN = "submit_plan"
OPERATION_KIND_SUBMIT_GENERATE = "submit_generate"


LIVE_OPERATION_KINDS = ("submit_plan", "submit_generate", "submit_translate", "build_course")


async def live_work_on(session: AsyncSession, *, course_id: UUID) -> bool:
    """True while a run of the course is queued or running, or a plan, generate,
    translate or build operation of it is waiting or going."""
    live_run = (
        await session.execute(
            sa.text("SELECT id FROM runs WHERE course_id = :c AND status IN ('queued', 'running')"),
            {"c": str(course_id)},
        )
    ).first()
    live_op = (
        await session.execute(
            sa.text(
                "SELECT id FROM operations WHERE kind = ANY(:kinds)"
                " AND status IN ('queued', 'running') AND params->>'course_id' = :c"
            ),
            {"kinds": list(LIVE_OPERATION_KINDS), "c": str(course_id)},
        )
    ).first()
    return live_run is not None or live_op is not None


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
    if await live_work_on(session, course_id=course_id):
        raise ConflictError("A run is already going")
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


def frozen_plan(tree: dict[str, Any]) -> dict[str, Any]:
    """The plan skeleton prompts may see: ids, titles, roles, briefs, handles.

    Never status, hold reasons, current versions, or DB ids — mutable
    state in a prompt changes every digest after run 1 and the second
    identical run pays again.
    """
    modules: list[dict[str, Any]] = []
    for module in tree.get("modules", []):
        topics: list[dict[str, Any]] = []
        for topic in module.get("topics", []):
            blocks: list[dict[str, Any]] = []
            for block in topic.get("blocks", []):
                handles = block.get("source_handles") or [
                    str(ref.get("handle", ""))
                    for ref in block.get("source_refs", [])
                    if isinstance(ref, dict)
                ]
                spec = (
                    block.get("example_spec") if isinstance(block.get("example_spec"), dict) else {}
                )
                blocks.append(
                    {
                        "plan_node_id": str(block.get("plan_node_id", "")),
                        "role": str(block.get("role", "")),
                        "block_type": str(block.get("block_type", "")),
                        "brief": str(block.get("brief", "")),
                        "source_handles": [str(h) for h in handles if h],
                        "example_spec": {
                            k: str(spec.get(k, "")) for k in ("input", "action", "result")
                        },
                    }
                )
            row = topic.get("scenario") if isinstance(topic.get("scenario"), dict) else {}
            topics.append(
                {
                    "plan_node_id": str(topic.get("plan_node_id", "")),
                    "kind": str(topic.get("kind", "")),
                    "title": str(topic.get("title", "")),
                    "objectives": [str(o) for o in (topic.get("objectives") or [])],
                    "annotation": str(row.get("annotation", "")),
                    "introduces_terms": [str(t) for t in row.get("introduces_terms", [])],
                    "prepares_for": str(row.get("prepares_for", "")),
                    "requires": [str(r) for r in row.get("requires", [])],
                    "misconception": str(row.get("misconception", "")),
                    "blocks": blocks,
                }
            )
        modules.append(
            {
                "plan_node_id": str(module.get("plan_node_id", "")),
                "title": str(module.get("title", "")),
                "topics": topics,
            }
        )
    raw_through = tree.get("through_line")
    through: dict[str, Any] = raw_through if isinstance(raw_through, dict) else {}
    return {
        "learner": str(through.get("learner", "")),
        "case": str(through.get("case", "")),
        "modules": modules,
    }


async def _approved_passage_count(session: AsyncSession, *, course_id: UUID) -> int:
    """Approved passages of one course (the submit guard's fact)."""
    row = (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM passages p"
                " JOIN course_sources cs ON cs.source_id = p.source_id"
                " AND cs.course_id = :c AND cs.approval = 'approved'"
                " JOIN sources s ON s.id = p.source_id AND s.approval = 'approved'"
            ),
            {"c": str(course_id)},
        )
    ).scalar()
    return int(row or 0)


async def _invalid_tree_errors(session: AsyncSession, *, course_id: UUID) -> list[str]:
    """Blocking tree errors, or [] when there is no plan tree yet to judge.

    An empty course (no modules) has nothing invalid: building the plan
    is exactly how it gains one. A course WITH a plan whose topics fail
    the contract (empty topics, wrong roles, bad refs) must not reach
    generation until the author fixes or deletes them.
    """
    from app.services.courses import validate_course_tree

    modules = (
        await session.execute(
            sa.text("SELECT count(*) FROM modules WHERE course_id = :c"),
            {"c": str(course_id)},
        )
    ).scalar()
    if not int(modules or 0):
        return []
    return await validate_course_tree(session, course_id=course_id)


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
    if await _approved_passage_count(session, course_id=course_id) == 0:
        raise UnprocessableError("Add and approve at least one source before building a plan.")
    # an invalid stored plan is no reason to refuse a new plan: the run replaces the
    # tree, and re-planning is the author's way out of a plan a newer rule rejects
    # (the Muse API course was locked out on 2026-11-02 by the reading-role rule)
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


async def generation_blockers(
    session: AsyncSession, *, course_id: UUID
) -> ValidationError | UnprocessableError | None:
    """The first reason block generation cannot start now, as the error to raise, or None."""
    from app.services.courses import blocking_questions

    open_questions = await blocking_questions(session, course_id=course_id)
    if open_questions:
        return ValidationError(
            "generation refused: answer required questions first: "
            + ", ".join(question["key"] for question in open_questions)
        )
    block_count = (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM blocks b JOIN topics t ON t.id = b.topic_id"
                " JOIN modules m ON m.id = t.module_id WHERE m.course_id = :c"
            ),
            {"c": str(course_id)},
        )
    ).scalar()
    if int(block_count or 0) == 0:
        return UnprocessableError("Build a plan before generating blocks.")
    if await _approved_passage_count(session, course_id=course_id) == 0:
        return UnprocessableError("Add and approve at least one source before generating blocks.")
    tree_errors = await _invalid_tree_errors(session, course_id=course_id)
    if tree_errors:
        return UnprocessableError("the plan is invalid: " + "; ".join(tree_errors))
    return None


async def submit_generate(
    session: AsyncSession,
    *,
    actor: Principal,
    course_id: UUID,
    idempotency_key: str | None = None,
    defer: Callable[[UUID], Awaitable[None]] | None = None,
) -> UUID:
    """Submit block generation; refused while a required question is open."""
    require(actor, "course.generate")
    blocker = await generation_blockers(session, course_id=course_id)
    if blocker is not None:
        raise blocker
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


OPERATION_KIND_SUBMIT_TRANSLATE = "submit_translate"


async def submit_translate(
    session: AsyncSession,
    *,
    actor: Principal,
    course_id: UUID,
    languages: list[str],
    glossary: dict[str, str] | None = None,
    idempotency_key: str | None = None,
    defer: Callable[[UUID], Awaitable[None]] | None = None,
) -> UUID:
    """Submit course translation as an operation (202; worker drives it)."""
    require(actor, "course.generate")
    cleaned = [str(lang).strip() for lang in languages if str(lang).strip()]
    if not cleaned:
        raise ValidationError("languages must be a non-empty list")
    return await _submit(
        session,
        actor=actor,
        kind=OPERATION_KIND_SUBMIT_TRANSLATE,
        course_id=course_id,
        params={"languages": cleaned, "glossary": dict(glossary or {})},
        idempotency_key=idempotency_key,
        defer=defer,
        required_code="course.generate",
    )


async def _latest_version_id(session: AsyncSession, *, course_id: UUID) -> UUID:
    """Published version when present, else the newest snapshot."""
    from app.services import courses as course_service

    course = await course_service.get_course(session, course_id=course_id)
    if course.get("published_version_id"):
        return UUID(str(course["published_version_id"]))
    row = (
        await session.execute(
            sa.text(
                "SELECT id FROM course_versions WHERE course_id = :course"
                " ORDER BY created_at DESC LIMIT 1"
            ),
            {"course": str(course_id)},
        )
    ).first()
    if row is not None:
        return UUID(str(row[0]))
    version_id = await course_service.snapshot_version(session, course_id=course_id)
    await session.commit()
    return version_id


async def _course_blocks(session: AsyncSession, *, course_id: UUID) -> list[dict[str, Any]]:
    """Current block contents and answer keys of one course, in plan order."""
    rows = (
        await session.execute(
            sa.text(
                "SELECT b.plan_node_id, b.block_type, bv.content, bv.answer_key"
                " FROM blocks b JOIN topics t ON t.id = b.topic_id"
                " JOIN modules m ON m.id = t.module_id"
                " LEFT JOIN block_versions bv ON bv.id = b.current_version_id"
                " WHERE m.course_id = :course ORDER BY m.ordinal, t.ordinal, b.ordinal"
            ),
            {"course": str(course_id)},
        )
    ).all()
    return [
        {
            "plan_node_id": str(row[0]),
            "block_type": str(row[1]),
            "content": dict(row[2] or {}),
            "answer_key": dict(row[3] or {}),
        }
        for row in rows
        if row[2]
    ]


async def run_translate_operation(
    session: AsyncSession,
    operation_id: UUID,
    *,
    provider_factory: Any | None = None,
    session_factory: Any | None = None,
) -> dict[str, Any]:
    """Worker body for submit_translate: run, drive, judge, store."""
    from app.translation.pipeline import translate_course

    params = (
        await session.execute(
            sa.text("SELECT params FROM operations WHERE id = :op"),
            {"op": str(operation_id)},
        )
    ).scalar_one()
    course_id = UUID(str(params["course_id"]))
    languages = [str(lang) for lang in params.get("languages", [])]
    from app.services import courses as course_service

    course = await course_service.get_course(session, course_id=course_id)
    version_id = await _latest_version_id(session, course_id=course_id)
    blocks = await _course_blocks(session, course_id=course_id)

    async def _driver(run_id: UUID) -> Any:
        if provider_factory is None or session_factory is None:
            return None
        from app.runs.executor import drive_run

        try:
            return await drive_run(
                session_factory,
                run_id=run_id,
                provider_factory=provider_factory,
                handlers=plan_handlers(),
            )
        finally:
            await _close_factory(provider_factory)

    from app.translation.glossary import seed_glossary

    run_id = await translate_course(
        session,
        None,
        course_id=course_id,
        version_id=version_id,
        source_language=str(course.get("language", "en")),
        target_languages=languages,
        blocks=blocks,
        glossary=seed_glossary(
            blocks, {str(k): str(v) for k, v in dict(params.get("glossary", {})).items()}
        ),
        driver=_driver,
    )
    return {"run_id": str(run_id)}


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
        **executor.generation_handlers(),
        **executor.translation_handlers(),
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


async def _drop_orphan_steps(session: AsyncSession, *, run_id: UUID) -> int:
    """Rebuild a resumed run's pending steps against the current plan.

    Steps whose topic/block no longer exists (the author deleted them
    while the run was stopped) are dropped from the compiled DAG and
    recorded done with a skipped output: they are never re-run, never
    repaid, and their dependents proceed. Returns the dropped count.
    """
    from app.services import courses as course_service

    row = (
        await session.execute(
            sa.text("SELECT kind, course_id, compiled_dag FROM runs WHERE id = :run"),
            {"run": str(run_id)},
        )
    ).first()
    if row is None or row[0] not in ("generation", "rewrite") or row[1] is None:
        return 0
    course_id = UUID(str(row[1]))
    dag = [e for e in (row[2] or []) if isinstance(e, dict)]
    if not dag:
        return 0
    tree = await course_service.read_tree(session, course_id=course_id)
    topics = {
        str(t.get("plan_node_id", "")) for m in tree.get("modules", []) for t in m.get("topics", [])
    }
    blocks = {
        str(b.get("plan_node_id", ""))
        for m in tree.get("modules", [])
        for t in m.get("topics", [])
        for b in t.get("blocks", [])
    }
    done_keys = {
        str(r[0])
        for r in (
            await session.execute(
                sa.text("SELECT step_key FROM run_steps WHERE run_id = :run AND status = 'done'"),
                {"run": str(run_id)},
            )
        ).all()
    }
    orphans: list[dict[str, Any]] = []
    for entry in dag:
        key = str(entry.get("step_key", ""))
        if key in done_keys:
            continue
        handler = str(entry.get("handler", ""))
        node = key.partition(":")[2]
        if handler == "read_topic" and node not in topics:
            orphans.append(entry)
        elif handler in ("generate_block", "saboteur") and node not in blocks:
            orphans.append(entry)
    for entry in orphans:
        key = str(entry.get("step_key", ""))
        dag = [e for e in dag if str(e.get("step_key", "")) != key]
        digest = entry.get("input_digest")
        skipped = json.dumps({"skipped": True, "reason": "topic or block no longer in the plan"})
        await session.execute(
            sa.text(
                "INSERT INTO run_steps (run_id, name, status, step_key, depends_on,"
                " input_digest, output, attempt, finished_at)"
                " VALUES (:run, CAST(:name AS varchar), 'done', CAST(:key AS text), '[]',"
                " :digest, CAST(:output AS jsonb), 1, now())"
                " ON CONFLICT (run_id, step_key) DO UPDATE SET"
                " status = 'done', output = EXCLUDED.output, finished_at = now()"
            ),
            {
                "run": str(run_id),
                "name": key,
                "key": key,
                "digest": str(digest) if digest is not None else None,
                "output": skipped,
            },
        )
    if orphans:
        await session.execute(
            sa.text("UPDATE runs SET compiled_dag = CAST(:dag AS jsonb) WHERE id = :run"),
            {"dag": json.dumps(dag), "run": str(run_id)},
        )
    return len(orphans)


async def reopen_run(session: AsyncSession, *, run_id: UUID) -> None:
    """Mark a stopped (or stranded) run running again and drop steps whose node is gone.

    The caller commits; driving the run is the caller's job.
    """
    await session.execute(
        sa.text("UPDATE runs SET status = 'running', stop_marker = NULL WHERE id = :run"),
        {"run": str(run_id)},
    )
    await _drop_orphan_steps(session, run_id=run_id)


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
            sa.text("SELECT status, course_id FROM runs WHERE id = :run"),
            {"run": str(run_id)},
        )
    ).first()
    if row is None:
        raise NotFoundError(f"run {run_id} not found")
    if row[0] == "running" and not await _no_driver_holds(session, run_id=run_id):
        raise ValidationError(f"run {run_id} is running; only stopped runs resume")
    if row[0] not in ("stopped", "running"):
        raise ValidationError(f"run {run_id} is {row[0]}; only stopped runs resume")
    if row[1] is not None:
        from app.services.courses import blocking_questions

        open_questions = await blocking_questions(session, course_id=UUID(str(row[1])))
        if open_questions:
            raise ConflictError(
                "resume refused: answer the open questions first: "
                + ", ".join(question["key"] for question in open_questions)
            )
    await reopen_run(session, run_id=run_id)
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


async def start_plan_run(session: AsyncSession, *, course_id: UUID, queries: list[str]) -> UUID:
    """Create (and commit) the plan run for a course: researchers per query, then the architect."""
    from app.runs.executor import new_run
    from app.services import courses as course_service

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
        for query in queries
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
    run_id = await new_run(session, course_id=course_id, kind="plan", dag=dag)
    await session.commit()
    return run_id


async def finish_plan_run(session_factory: Any, *, course_id: UUID, run_id: UUID) -> None:
    """After a plan drive: the forecast on the run, a snapshot of the plan, the generated flag."""
    from app.services import courses as course_service

    async with session_factory() as fresh:
        await refresh_plan_estimate(fresh, run_id=run_id)
    async with session_factory() as fresh:
        await course_service.snapshot_version(fresh, course_id=course_id)
        await course_service.mark_generated_if_complete(fresh, course_id=course_id)
        await fresh.commit()


async def drive_and_close(session_factory: Any, provider_factory: Any, run_id: UUID) -> Any:
    """Drive one run with the plan handlers, then dispose the provider factory's engine."""
    from app.runs.executor import drive_run

    try:
        return await drive_run(
            session_factory,
            run_id=run_id,
            provider_factory=provider_factory,
            handlers=plan_handlers(),
        )
    finally:
        await _close_factory(provider_factory)


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
    params = (
        await session.execute(
            sa.text("SELECT params FROM operations WHERE id = :op"),
            {"op": str(operation_id)},
        )
    ).scalar_one()
    course_id = UUID(str(params["course_id"]))
    run_id = await start_plan_run(session, course_id=course_id, queries=list(params["queries"]))
    if provider_factory is not None and session_factory is not None:
        await drive_and_close(session_factory, provider_factory, run_id)
        await finish_plan_run(session_factory, course_id=course_id, run_id=run_id)
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
        run_row = (
            await fresh.execute(
                sa.text("SELECT kind FROM runs WHERE id = :run"), {"run": str(run_id)}
            )
        ).first()
    if run_row is not None and run_row[0] == "translation":
        from app.translation.pipeline import finalize_translation_run

        async with session_factory() as fresh:
            await finalize_translation_run(fresh, run_id=run_id)
        return {"run_id": str(run_id)}
    async with session_factory() as fresh:
        await refresh_plan_estimate(fresh, run_id=run_id)
    async with session_factory() as fresh:
        course_id = await run_course_id(fresh, run_id=run_id)
        if course_id is not None:
            await course_service.snapshot_version(fresh, course_id=course_id)
            await fresh.commit()
    return {"run_id": str(run_id)}


def _topic_index(raw_tree: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per topic: cited passage ids, title, purpose, kind, spot count and the fully-correct rule."""
    index: dict[str, dict[str, Any]] = {}
    for module in raw_tree.get("modules", []):
        for topic in module.get("topics", []):
            ids: list[str] = []
            handles: list[str] = []
            for block in topic.get("blocks", []):
                for ref in block.get("source_refs", []):
                    pid = ref.get("passage_id") if isinstance(ref, dict) else None
                    if pid and str(pid) not in ids:
                        ids.append(str(pid))
                    handle = ref.get("handle") if isinstance(ref, dict) else None
                    if handle and str(handle) not in handles:
                        handles.append(str(handle))
            spots = sum(
                1
                for b in topic.get("blocks", [])
                if str(b.get("block_type", "")) == "spot_the_error"
            )
            kind = str(topic.get("kind", ""))
            raw_row = topic.get("scenario")
            row: dict[str, Any] = raw_row if isinstance(raw_row, dict) else {}
            raw_passages = row.get("passages")
            section_passages: dict[str, Any] = (
                raw_passages if isinstance(raw_passages, dict) else {}
            )
            index[str(topic.get("plan_node_id", ""))] = {
                "cited_passage_ids": ids,
                "cited_handles": handles,
                "section_passage_ids": [str(p) for p in section_passages.values()],
                "terms": [str(t) for t in row.get("introduces_terms", [])],
                "topic_title": str(topic.get("title", "")),
                "topic_purpose": " ".join(str(o) for o in (topic.get("objectives") or [])),
                "topic_kind": kind,
                # a module test whose only task is the exercise must get a planted error
                "allow_fully_correct": not (kind == "module_test" and spots < 2),
                "blocks": [str(b.get("plan_node_id", "")) for b in topic.get("blocks", [])],
            }
    return index


def _spot_fields(topic_index: dict[str, dict[str, Any]], node: str) -> dict[str, Any]:
    """The fields every saboteur and spot generator entry carries, from its topic."""
    for meta in topic_index.values():
        if node in meta["blocks"]:
            return {
                "cited_passage_ids": list(meta["cited_passage_ids"]),
                "topic_title": meta["topic_title"],
                "topic_purpose": meta["topic_purpose"],
                "topic_kind": meta["topic_kind"],
                "allow_fully_correct": bool(meta["allow_fully_correct"]),
            }
    return {
        "cited_passage_ids": [],
        "topic_title": "",
        "topic_purpose": "",
        "topic_kind": "",
        "allow_fully_correct": False,
    }


def _plan_handle_map(raw_tree: dict[str, Any]) -> dict[str, str]:
    """handle -> passage id as the stored plan cites them."""
    mapping: dict[str, str] = {}
    for module in raw_tree.get("modules", []):
        for topic in module.get("topics", []):
            for block in topic.get("blocks", []):
                for ref in block.get("source_refs", []):
                    if isinstance(ref, dict) and ref.get("passage_id") and ref.get("handle"):
                        mapping.setdefault(str(ref["handle"]), str(ref["passage_id"]))
    return mapping


RETRIEVED_PER_TOPIC = 8


def scenario_view(tree: dict[str, Any], topic_node: str) -> dict[str, Any]:
    """What a step sees of the plan: learner, case, every topic as one line, its own topic in full.

    A module test also sees its module's lesson blocks (handles only),
    so the taught-passage pool of the test is the whole module. The
    whole plan never travels into a block prompt: on a big course it was
    the largest part of every call and never prefix-cached.
    """
    lines: list[dict[str, Any]] = []
    own_module: dict[str, Any] | None = None
    own_topic: dict[str, Any] | None = None
    for module in tree.get("modules", []):
        for topic in module.get("topics", []):
            node = str(topic.get("plan_node_id", ""))
            lines.append(
                {
                    "plan_node_id": node,
                    "module": str(module.get("title", "")),
                    "title": str(topic.get("title", "")),
                    "annotation": str(topic.get("annotation", "")),
                }
            )
            if node == topic_node:
                own_module, own_topic = module, topic
    if own_module is None or own_topic is None:
        return {"learner": tree.get("learner", ""), "case": tree.get("case", ""), "topics": lines}
    siblings: list[dict[str, Any]] = []
    if str(own_topic.get("kind", "")) == "module_test":
        siblings = [
            {
                "plan_node_id": str(t.get("plan_node_id", "")),
                "kind": str(t.get("kind", "")),
                "title": str(t.get("title", "")),
                "blocks": [
                    {
                        "plan_node_id": str(b.get("plan_node_id", "")),
                        "role": str(b.get("role", "")),
                        "source_handles": list(b.get("source_handles", [])),
                    }
                    for b in t.get("blocks", [])
                ],
            }
            for t in own_module.get("topics", [])
            if str(t.get("plan_node_id", "")) != topic_node
        ]
    return {
        "learner": str(tree.get("learner", "")),
        "case": str(tree.get("case", "")),
        "topics": lines,
        "modules": [
            {
                "plan_node_id": str(own_module.get("plan_node_id", "")),
                "title": str(own_module.get("title", "")),
                "topics": [*siblings, own_topic],
            }
        ],
    }


def _topic_of(topic_index: dict[str, dict[str, Any]], node: str) -> str:
    return next((t for t, meta in topic_index.items() if node in meta["blocks"]), "")


async def _topic_sources(
    session: AsyncSession,
    *,
    course_id: UUID,
    raw_tree: dict[str, Any],
    sources: list[dict[str, Any]],
    topic_index: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Per topic, the passages its generators and reader see: sections, citations, retrieval.

    A big source set never travels whole into a block prompt: the topic
    gets every passage of its own sections (the scenario's assignment),
    the passages its plan cites (a module test: the module's), plus the
    course's best full-text matches for the terms it introduces, so a
    definition the section refers to but does not give is in view.
    Handles stay the course-wide ones. An empty selection falls back to
    every passage, so a plan without refs still generates.
    """
    from app.course.handles import handle_number
    from app.providers.retrieval import get_retriever

    owner: dict[str, dict[str, Any]] = {}
    by_handle: dict[str, dict[str, Any]] = {}
    for passage in sources:
        for pid in [passage["passage_id"], *passage.get("merged_ids", [])]:
            owner.setdefault(str(pid), passage)
        by_handle[str(passage.get("handle", ""))] = passage
    module_cited: dict[str, list[str]] = {}
    for module in raw_tree.get("modules", []):
        ids: list[str] = []
        for topic in module.get("topics", []):
            ids += topic_index.get(str(topic.get("plan_node_id", "")), {}).get(
                "cited_passage_ids", []
            )
        for topic in module.get("topics", []):
            module_cited[str(topic.get("plan_node_id", ""))] = ids
    retriever = get_retriever("fts")
    out: dict[str, list[dict[str, Any]]] = {}
    for topic_node, meta in topic_index.items():
        wanted = [*meta["section_passage_ids"], *meta["cited_passage_ids"]]
        if meta["topic_kind"] == "module_test":
            wanted += module_cited.get(topic_node, [])
        query = " ".join([meta["topic_title"], *meta["terms"]]).strip()
        if query:
            try:
                found = await retriever.search_course(
                    session, query=query, course_id=course_id, limit=RETRIEVED_PER_TOPIC
                )
                wanted += [str(item.passage_id) for item in found]
            except Exception:  # retrieval is an enrichment, never a stop
                pass
        chosen: dict[str, dict[str, Any]] = {}
        for pid in wanted:
            found_passage = owner.get(str(pid))
            if found_passage is not None:
                chosen.setdefault(found_passage["passage_id"], found_passage)
        # a plan ref that names a handle without a passage id (older plans)
        # still puts that passage in front of the writer who must cite it
        for handle in meta.get("cited_handles", []):
            found_passage = by_handle.get(str(handle))
            if found_passage is not None:
                chosen.setdefault(found_passage["passage_id"], found_passage)
        out[topic_node] = sorted(
            chosen.values(), key=lambda p: handle_number(str(p.get("handle", "")))
        ) or list(sources)
    return out


async def _course_sources(
    session: AsyncSession, *, course_id: UUID, raw_tree: dict[str, Any]
) -> list[dict[str, Any]]:
    """Approved passages numbered exactly as the plan cites them (one numbering)."""
    from app.course.handles import numbered_passages, reconcile_handles

    rows = (
        await session.execute(
            sa.text(
                "SELECT p.id, p.source_id, p.address_key, p.text, p.is_content, p.ordinal"
                " FROM passages p"
                " JOIN course_sources cs ON cs.source_id = p.source_id AND cs.course_id = :c"
                " AND cs.approval = 'approved' JOIN sources s ON s.id = p.source_id"
                " AND s.approval = 'approved'"
            ),
            {"c": str(course_id)},
        )
    ).all()
    numbered = numbered_passages(
        [
            {
                "passage_id": str(r[0]),
                "source_id": str(r[1]),
                "address": str(r[2]),
                "text": str(r[3]),
                "is_content": bool(r[4]),
                "ordinal": r[5],
            }
            for r in rows
        ]
    )
    return reconcile_handles(numbered, _plan_handle_map(raw_tree))


async def start_rewrite_run(
    session: AsyncSession, *, course_id: UUID, block_ids: list[str]
) -> UUID:
    """Create (and commit) the rewrite run for the named blocks of a course.

    Regenerates ONLY the named blocks: variant = how many generation
    runs already touched the block (a counter that grows on every
    rewrite, so an unchanged reply can never freeze it; a fresh draft,
    never the reply store's old one), the block is always re-versioned,
    the hold reason rides in the skeleton prompt, and the affected
    topics are read again. Other blocks keep their versions.
    """
    from app.pipelines.merrill import _role_agent
    from app.services import courses as course_service

    wanted = [str(b) for b in block_ids]
    if not wanted:
        raise ValidationError("name at least one block to rewrite")
    course = await course_service.get_course(session, course_id=course_id)
    raw_tree = await course_service.read_tree(session, course_id=course_id)
    tree = frozen_plan(raw_tree)
    topic_index = _topic_index(raw_tree)
    rows = (
        await session.execute(
            sa.text(
                "SELECT b.id, b.plan_node_id, b.block_type, b.role, b.brief,"
                " b.hold_reason, t.plan_node_id,"
                " (SELECT count(*) FROM run_steps rs JOIN runs r ON r.id = rs.run_id"
                " WHERE r.course_id = m.course_id"
                " AND split_part(rs.step_key, ':', 2) = b.plan_node_id"
                " AND split_part(rs.step_key, ':', 1) LIKE 'block\\_%')"
                " FROM blocks b JOIN topics t ON t.id = b.topic_id"
                " JOIN modules m ON m.id = t.module_id"
                " WHERE m.course_id = :c AND b.id::text = ANY(:ids)"
            ),
            {"c": str(course_id), "ids": wanted},
        )
    ).all()
    if not rows:
        raise NotFoundError("no named blocks found for rewrite")
    sources = await _course_sources(session, course_id=course_id, raw_tree=raw_tree)
    by_topic = await _topic_sources(
        session, course_id=course_id, raw_tree=raw_tree, sources=sources, topic_index=topic_index
    )
    dag: list[dict[str, Any]] = []
    gen_keys_by_topic: dict[str, list[str]] = {}
    specs = {
        str(b.get("plan_node_id", "")): dict(b.get("example_spec", {}))
        for m in tree.get("modules", [])
        for t in m.get("topics", [])
        for b in t.get("blocks", [])
    }
    assigned = {
        str(b.get("plan_node_id", "")): [str(h) for h in b.get("source_handles", [])]
        for m in tree.get("modules", [])
        for t in m.get("topics", [])
        for b in t.get("blocks", [])
    }
    for _bid, node, btype, role, brief, hold_reason, topic_node, versions in rows:
        agent = _role_agent(str(role))
        key = f"{agent}:{node}"
        scoped = by_topic.get(str(topic_node), sources)
        gen_keys_by_topic.setdefault(str(topic_node), []).append(key)
        reason = str(hold_reason or "")
        depends: list[str] = []
        if str(btype) == "spot_the_error":
            # the planted exercise needs its picker + saboteur step, with a seed
            # that advances with the rewrite counter: a rewrite is a NEW exercise
            saboteur_key = f"saboteur:{node}"
            depends.append(saboteur_key)
            dag.append(
                {
                    "step_key": saboteur_key,
                    "handler": "saboteur",
                    "depends_on": [],
                    "course_id": str(course_id),
                    "course_language": str(course.get("language", "en")),
                    "sources": scoped,
                    "variant_offset": int(versions or 0),
                    "rewrite": True,
                    "hold_reason": reason,
                    **_spot_fields(topic_index, str(node)),
                }
            )
        dag.append(
            {
                "step_key": key,
                "handler": "generate_block",
                "depends_on": depends,
                "course_id": str(course_id),
                "course_language": str(course.get("language", "en")),
                "plan": scenario_view(tree, str(topic_node)),
                "block_type": str(btype),
                "sources": scoped,
                "variant_offset": int(versions or 0),
                "rewrite": True,
                **(_spot_fields(topic_index, str(node)) if str(btype) == "spot_the_error" else {}),
                "skeleton": {
                    "plan_node_id": str(node),
                    "brief": f"{brief} Rewrite: {reason}" if reason else str(brief),
                    "role": str(role),
                    "block_type": str(btype),
                    "hold_reason": reason,
                    "example_spec": specs.get(str(node), {}),
                    "source_handles": assigned.get(str(node), []),
                },
            }
        )
    for topic_node, keys in gen_keys_by_topic.items():
        dag.append(
            {
                "step_key": f"reader:{topic_node}",
                "handler": "read_topic",
                "depends_on": keys,
                "course_id": str(course_id),
                "course_language": str(course.get("language", "en")),
                "plan": scenario_view(tree, topic_node),
                "sources": by_topic.get(topic_node, sources),
                "blocks": [],
            }
        )
    from app.runs.executor import new_run

    run_id = await new_run(session, course_id=course_id, kind="rewrite", dag=dag)
    await session.commit()
    return run_id


async def finish_blocks_run(session_factory: Any, *, course_id: UUID) -> None:
    """After a rewrite or generation drive: the generated flag when every block has content."""
    from app.services import courses as course_service

    async with session_factory() as fresh:
        await course_service.mark_generated_if_complete(fresh, course_id=course_id)
        await fresh.commit()


async def run_rewrite_operation(
    session: AsyncSession,
    operation_id: UUID,
    *,
    provider_factory: Any | None = None,
    session_factory: Any | None = None,
) -> dict[str, Any]:
    """Worker body for rewrite_block / rewrite_held_batch: create the run, then drive it."""
    params = (
        await session.execute(
            sa.text("SELECT params FROM operations WHERE id = :op"),
            {"op": str(operation_id)},
        )
    ).scalar_one()
    course_id = UUID(str(params["course_id"]))
    wanted: list[str] = []
    if params.get("block_id"):
        wanted.append(str(params["block_id"]))
    wanted.extend(str(b) for b in params.get("block_ids", []))
    run_id = await start_rewrite_run(session, course_id=course_id, block_ids=wanted)
    if provider_factory is not None and session_factory is not None:
        await drive_and_close(session_factory, provider_factory, run_id)
        await finish_blocks_run(session_factory, course_id=course_id)
    return {"run_id": str(run_id)}


async def start_generation_run(session: AsyncSession, *, course_id: UUID) -> UUID:
    """Compile the generation DAG of a course's plan, create (and commit) the run."""
    from app.pipelines.merrill import compile_generation_dag
    from app.services import courses as course_service

    course = await course_service.get_course(session, course_id=course_id)
    raw_tree = await course_service.read_tree(session, course_id=course_id)
    tree = frozen_plan(raw_tree)
    topic_index = _topic_index(raw_tree)
    profile = course.get("profile") or {}
    sources = await _course_sources(session, course_id=course_id, raw_tree=raw_tree)
    by_topic = await _topic_sources(
        session, course_id=course_id, raw_tree=raw_tree, sources=sources, topic_index=topic_index
    )
    raw = compile_generation_dag(
        profile={
            "methodology_codes": list(profile.get("methodology_codes", [])),
            "bloom_level": str(profile.get("bloom_level", "apply")),
        },
        tree=tree,
        course_id=str(course_id),
        methodology=(
            list(profile.get("methodology_codes", ["merrill_first_principles"]))
            or ["merrill_first_principles"]
        )[0],
    )
    blocks_by_id = {
        b["plan_node_id"]: b
        for m in tree.get("modules", [])
        for tp in m.get("topics", [])
        for b in tp.get("blocks", [])
    }
    blocks_by_topic = {
        tp["plan_node_id"]: tp.get("blocks", [])
        for m in tree.get("modules", [])
        for tp in m.get("topics", [])
    }
    dag = []
    for entry in raw:
        handler = str(entry["handler"])
        if handler in ("generation_research", "generation_plan"):
            continue
        e = dict(entry)
        e["depends_on"] = [
            d for d in entry.get("depends_on", []) if d not in ("plan:plan", "research:plan")
        ]
        e["course_id"] = str(course_id)
        e["course_language"] = str(course.get("language", "en"))
        if handler == "generate_block":
            node = str(entry["step_key"]).partition(":")[2]
            skel = dict(blocks_by_id.get(node, {"plan_node_id": node}))
            e["block_type"] = str(skel.get("block_type", "reading"))
            e["sources"] = by_topic.get(_topic_of(topic_index, node), sources)
            e["plan"] = scenario_view(tree, _topic_of(topic_index, node))
            e["skeleton"] = {
                "plan_node_id": node,
                "brief": str(skel.get("brief", "")),
                "role": str(skel.get("role", "")),
                "block_type": e["block_type"],
                "example_spec": dict(skel.get("example_spec", {})),
                "source_handles": [str(h) for h in skel.get("source_handles", [])],
            }
            if e["block_type"] == "spot_the_error":
                e.update(_spot_fields(topic_index, node))
        elif handler == "saboteur":
            node = str(entry["step_key"]).partition(":")[2]
            e["sources"] = by_topic.get(_topic_of(topic_index, node), sources)
            e.update(_spot_fields(topic_index, node))
        elif handler == "read_topic":
            topic = str(entry["step_key"]).partition(":")[2]
            e["sources"] = by_topic.get(topic, sources)
            e["plan"] = scenario_view(tree, topic)
            e["blocks"] = [
                {"plan_node_id": b["plan_node_id"]} for b in blocks_by_topic.get(topic, [])
            ]
        elif handler == "rework_blocks":
            e["named"] = []
        dag.append(e)
    from app.runs.executor import new_run

    run_id = await new_run(session, course_id=course_id, kind="generation", dag=dag)
    await session.commit()
    return run_id


async def run_generate_operation(
    session: AsyncSession,
    operation_id: UUID,
    *,
    provider_factory: Any | None = None,
    session_factory: Any | None = None,
) -> dict[str, Any]:
    """Worker body for submit_generate: compile the DAG, create the run, drive it."""
    params = (
        await session.execute(
            sa.text("SELECT params FROM operations WHERE id = :op"), {"op": str(operation_id)}
        )
    ).scalar_one()
    course_id = UUID(str(params["course_id"]))
    if "block_id" in params or "block_ids" in params:
        return await run_rewrite_operation(
            session,
            operation_id,
            provider_factory=provider_factory,
            session_factory=session_factory,
        )
    run_id = await start_generation_run(session, course_id=course_id)
    if provider_factory is not None and session_factory is not None:
        await drive_and_close(session_factory, provider_factory, run_id)
        await finish_blocks_run(session_factory, course_id=course_id)
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
