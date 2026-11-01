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
    live_run = (
        await session.execute(
            sa.text("SELECT id FROM runs WHERE course_id = :c AND status IN ('queued', 'running')"),
            {"c": str(course_id)},
        )
    ).first()
    live_op = (
        await session.execute(
            sa.text(
                "SELECT id FROM operations WHERE kind IN"
                " ('submit_plan', 'submit_generate', 'submit_translate')"
                " AND status IN ('queued', 'running') AND params->>'course_id' = :c"
            ),
            {"c": str(course_id)},
        )
    ).first()
    if live_run is not None or live_op is not None:
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
                blocks.append(
                    {
                        "plan_node_id": str(block.get("plan_node_id", "")),
                        "role": str(block.get("role", "")),
                        "block_type": str(block.get("block_type", "")),
                        "brief": str(block.get("brief", "")),
                        "source_handles": [str(h) for h in handles if h],
                    }
                )
            topics.append(
                {
                    "plan_node_id": str(topic.get("plan_node_id", "")),
                    "kind": str(topic.get("kind", "")),
                    "title": str(topic.get("title", "")),
                    "objectives": [str(o) for o in (topic.get("objectives") or [])],
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
    return {"modules": modules}


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
    tree_errors = await _invalid_tree_errors(session, course_id=course_id)
    if tree_errors:
        raise UnprocessableError("the plan is invalid: " + "; ".join(tree_errors))
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

    require(actor, "course.generate")
    open_questions = await blocking_questions(session, course_id=course_id)
    if open_questions:
        raise ValidationError(
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
        raise UnprocessableError("Build a plan before generating blocks.")
    if await _approved_passage_count(session, course_id=course_id) == 0:
        raise UnprocessableError("Add and approve at least one source before generating blocks.")
    tree_errors = await _invalid_tree_errors(session, course_id=course_id)
    if tree_errors:
        raise UnprocessableError("the plan is invalid: " + "; ".join(tree_errors))
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


async def learner_course_view(
    session: AsyncSession, *, course_id: UUID, language: str
) -> dict[str, Any]:
    """Learner view of one course in the chosen language.

    Every block goes through learner_block: translated ok blocks serve
    their translation, everything else serves the source with a
    localised notice (none when the chosen language is the source).
    The answer key learner_block returns for the grader is dropped
    here, and the content goes through the type's learner serializer
    (shuffled matching, gapped cloze text): the owner previews exactly
    what a learner is served and never a key. Citation chips carry the
    address formatted for the locale.
    """
    from app.blocks.registry import learner_serializers
    from app.i18n.citations import format_citation_address
    from app.translation.variants import available_languages, learner_block

    course = await _course_for_learner(session, course_id=course_id)
    source_language = str(course.get("language", "en"))
    blocks = await _course_blocks(session, course_id=course_id)
    out_blocks: list[dict[str, Any]] = []
    for block in blocks:
        resolved = await learner_block(
            session,
            course_id=course_id,
            language=language,
            block=block,
            source_language=source_language,
        )
        raw_content = dict(resolved["content"])
        citations = await _citation_chips(
            session, content=raw_content, locale=language, fmt=format_citation_address
        )
        content = dict(learner_serializers()["card"](str(block["block_type"]), raw_content, {}))
        out_blocks.append(
            {
                "plan_node_id": str(block["plan_node_id"]),
                "block_type": str(block["block_type"]),
                "content": content,
                "citations": citations,
                "language": resolved["language"],
                "fallback_notice": resolved["fallback_notice"],
            }
        )
    return {
        "course_id": str(course_id),
        "language": language,
        "source_language": source_language,
        "available_languages": await available_languages(session, course_id=course_id),
        "blocks": out_blocks,
    }


async def _course_for_learner(session: AsyncSession, *, course_id: UUID) -> dict[str, Any]:
    from app.services import courses as course_service

    return await course_service.get_course(session, course_id=course_id)


async def _citation_chips(
    session: AsyncSession, *, content: dict[str, Any], locale: str, fmt: Any
) -> list[dict[str, Any]]:
    """Citation chips: passage quote plus its address in the locale."""
    chips: list[dict[str, Any]] = []
    raw = content.get("citations")
    if isinstance(content.get("citation"), dict):
        raw = [content["citation"]]
    if not isinstance(raw, list):
        return chips
    for item in raw:
        if not isinstance(item, dict):
            continue
        passage_id = str(item.get("passage_id", ""))
        address: dict[str, Any] = {}
        if passage_id:
            row = (
                await session.execute(
                    sa.text("SELECT address, address_key FROM passages WHERE id::text = :pid"),
                    {"pid": passage_id},
                )
            ).first()
            if row is not None and isinstance(row[0], dict):
                address = dict(row[0])
                address.setdefault("address_key", row[1])
        chips.append(
            {
                "passage_id": passage_id,
                "quote": str(item.get("quote", "")),
                "address": fmt(address, locale) if address else str(item.get("address", "")),
            }
        )
    return chips


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
    await session.execute(
        sa.text("UPDATE runs SET status = 'running', stop_marker = NULL WHERE id = :run"),
        {"run": str(run_id)},
    )
    await _drop_orphan_steps(session, run_id=run_id)
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
            await course_service.mark_generated_if_complete(fresh, course_id=course_id)
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
            for block in topic.get("blocks", []):
                for ref in block.get("source_refs", []):
                    pid = ref.get("passage_id") if isinstance(ref, dict) else None
                    if pid and str(pid) not in ids:
                        ids.append(str(pid))
            spots = sum(
                1
                for b in topic.get("blocks", [])
                if str(b.get("block_type", "")) == "spot_the_error"
            )
            kind = str(topic.get("kind", ""))
            index[str(topic.get("plan_node_id", ""))] = {
                "cited_passage_ids": ids,
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


def scoped_plan(tree: dict[str, Any], topic_node: str) -> dict[str, Any]:
    """The frozen plan a step sees: its own module in full, the other modules as titles.

    Every block prompt carried the whole plan; on a big course that is
    the largest part of every call and it is never prefix-cached (the
    sources section before it differs per topic).
    """
    modules: list[dict[str, Any]] = []
    for module in tree.get("modules", []):
        own = any(str(t.get("plan_node_id", "")) == topic_node for t in module.get("topics", []))
        if own:
            modules.append(module)
        else:
            modules.append(
                {
                    "plan_node_id": str(module.get("plan_node_id", "")),
                    "title": str(module.get("title", "")),
                    "topics": [
                        {
                            "plan_node_id": str(t.get("plan_node_id", "")),
                            "title": str(t.get("title", "")),
                        }
                        for t in module.get("topics", [])
                    ],
                }
            )
    return {**tree, "modules": modules}


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
    """Per topic, the passages its generators and reader see: cited ones plus retrieval.

    A big source set never travels whole into a block prompt: the topic
    gets the passages its plan cites (a module test: the module's), plus
    the course's best full-text matches for its title and objectives.
    Handles stay the course-wide ones. An empty selection falls back to
    every passage, so a plan without refs still generates.
    """
    from app.course.handles import handle_number
    from app.providers.retrieval import get_retriever

    owner: dict[str, dict[str, Any]] = {}
    for passage in sources:
        for pid in [passage["passage_id"], *passage.get("merged_ids", [])]:
            owner.setdefault(str(pid), passage)
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
        wanted = list(meta["cited_passage_ids"])
        if meta["topic_kind"] == "module_test":
            wanted += module_cited.get(topic_node, [])
        query = f"{meta['topic_title']} {meta['topic_purpose']}".strip()
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


async def run_rewrite_operation(
    session: AsyncSession,
    operation_id: UUID,
    *,
    provider_factory: Any | None = None,
    session_factory: Any | None = None,
) -> dict[str, Any]:
    """Worker body for rewrite_block / rewrite_held_batch.

    Regenerates ONLY the named blocks: variant = how many generation
    runs already touched the block (a counter that grows on every
    rewrite, so an unchanged reply can never freeze it; a fresh draft,
    never the reply store's old one), the block is always re-versioned,
    the hold reason rides in the skeleton prompt, and the affected
    topics are read again. Other blocks keep their versions.
    """
    from app.pipelines.merrill import _role_agent
    from app.services import courses as course_service

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
    dossier = {"thesis": "", "glossary": {}, "handoffs": []}
    dag: list[dict[str, Any]] = []
    gen_keys_by_topic: dict[str, list[str]] = {}
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
                "plan": scoped_plan(tree, str(topic_node)),
                "dossier": dossier,
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
                "plan": scoped_plan(tree, topic_node),
                "dossier": dossier,
                "sources": by_topic.get(topic_node, sources),
                "blocks": [],
            }
        )
    from app.runs.executor import drive_run, new_run

    run_id = await new_run(session, course_id=course_id, kind="rewrite", dag=dag)
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
            await course_service.mark_generated_if_complete(fresh, course_id=course_id)
            await fresh.commit()
    return {"run_id": str(run_id)}


async def run_generate_operation(
    session: AsyncSession,
    operation_id: UUID,
    *,
    provider_factory: Any | None = None,
    session_factory: Any | None = None,
) -> dict[str, Any]:
    """Worker body for submit_generate: compile the DAG, create the run, drive it."""
    from app.pipelines.merrill import compile_generation_dag
    from app.services import courses as course_service

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
    dossier = {"thesis": "", "glossary": {}, "handoffs": []}
    try:
        tl = await course_service.read_through_line(session, course_id=course_id)
        dossier = {
            "thesis": str(tl.get("case", "")),
            "glossary": {},
            "handoffs": list(tl.get("handoffs", [])),
        }
    except Exception:
        pass
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
        e["plan"] = tree
        e["dossier"] = dossier
        if handler == "generate_block":
            node = str(entry["step_key"]).partition(":")[2]
            skel = dict(blocks_by_id.get(node, {"plan_node_id": node}))
            e["block_type"] = str(skel.get("block_type", "reading"))
            e["sources"] = by_topic.get(_topic_of(topic_index, node), sources)
            e["plan"] = scoped_plan(tree, _topic_of(topic_index, node))
            e["skeleton"] = {
                "plan_node_id": node,
                "brief": str(skel.get("brief", "")),
                "role": str(skel.get("role", "")),
                "block_type": e["block_type"],
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
            e["plan"] = scoped_plan(tree, topic)
            e["blocks"] = [
                {"plan_node_id": b["plan_node_id"]} for b in blocks_by_topic.get(topic, [])
            ]
        elif handler == "read_continuity":
            e["handoffs"] = dossier["handoffs"]
        elif handler == "rework_blocks":
            e["named"] = []
        dag.append(e)
    from app.runs.executor import drive_run, new_run

    run_id = await new_run(session, course_id=course_id, kind="generation", dag=dag)
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
            await course_service.mark_generated_if_complete(fresh, course_id=course_id)
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
