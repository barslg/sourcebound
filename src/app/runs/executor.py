"""Run executor: the only place that calls the model for generation.

Claim + commit each step as running BEFORE the model call (so the
paid llm_calls row with step_id survives any later rollback), check
the output in code, then write blocks and mark the step done in one
transaction. One driver per run holds the `run:<id>` advisory lock.
A 4xx/5xx that exhausts the retry policy stops the run with a marker;
resume_run() continues from the last good step and never repays one.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.errors import ValidationError
from app.llm.registry import resolve_step
from app.runs.stop_text import EMPTY_PLAN, MODEL_DOWN, plain_marker, plain_step_error, plain_stop

RUN_LOCK_PREFIX = "run:"
# Whole approved source the architect may see (BRIEF §3: ≤200k tokens).
ARCHITECT_SOURCE_CHAR_BUDGET = 800_000
TS_CONFIG_TO_LANGUAGE = {"english": "en", "norwegian": "nb", "simple": "uk"}

PLAN_HANDLERS: dict[str, str] = {
    "researcher": "plan_researcher_step",
    "architect": "plan_architect_step",
}


def input_digest_of(payload: dict[str, Any]) -> str:
    """Stable digest of one step's input for reuse detection."""
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:32]


def step_digest(*, agent: str, prompt: str, upstream: dict[str, Any] | None = None) -> str:
    """Digest of one step's real inputs: prompt, upstream outputs, model.

    The digest carries the assembled prompt, the outputs of upstream
    steps (so a changed researcher output reruns the architect), and
    the resolved model, effort, and max_output_tokens from the
    registry — never a constant.
    """
    model, effort, max_tokens = resolve_step(agent, None, None, None)
    return input_digest_of(
        {
            "agent": agent,
            "prompt": prompt,
            "upstream": upstream or {},
            "model": model,
            "effort": effort,
            "max_output_tokens": max_tokens,
        }
    )


def address_sort_key(address_key: str) -> tuple[Any, ...]:
    """Numeric sort for passage addresses: para2 before para10.

    Text sorting would put para10 before para2; the architect must see
    the source in reading order, so page/paragraph numbers compare
    numerically with a stable fallback for unknown shapes.
    """
    import re

    match = re.fullmatch(r"p(\d+)/para(\d+)", address_key)
    if match:
        return (0, int(match.group(1)), int(match.group(2)))
    match = re.fullmatch(r"h:(.*)/para(\d+)", address_key)
    if match:
        return (1, match.group(1), int(match.group(2)))
    match = re.fullmatch(r"t([0-9:.]+)-([0-9:.]+)", address_key)
    if match:
        return (2, match.group(1), match.group(2))
    return (3, address_key)


async def new_run(
    session: AsyncSession,
    *,
    course_id: UUID,
    kind: str,
    dag: list[dict[str, Any]],
    estimate: dict[str, Any] | None = None,
) -> UUID:
    """Persist a run with its compiled DAG; return the run id."""
    digest = hashlib.sha256(
        json.dumps(dag, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:32]
    row = (
        await session.execute(
            sa.text(
                "INSERT INTO runs (status, course_id, compiled_dag, dag_digest, estimate, kind)"
                " VALUES ('running', :course, CAST(:dag AS jsonb), :digest,"
                " CAST(:estimate AS jsonb), :kind) RETURNING id"
            ),
            {
                "course": str(course_id),
                "dag": json.dumps(dag),
                "digest": digest,
                "estimate": json.dumps(estimate) if estimate is not None else None,
                "kind": kind,
            },
        )
    ).one()
    return UUID(str(row[0]))


async def claim_step(
    session: AsyncSession, *, run_id: UUID, step_key: str, input_digest: str
) -> dict[str, Any]:
    """Claim a step and commit BEFORE the model call.

    Returns {"step_id", "reused_output", "attempt"}: done steps with
    the same input digest reuse their output (never repaid); done
    steps with a changed digest — and every other non-done state —
    reset to running with attempt + 1. The caller passes
    variant=attempt-1 to the model, so an explicit retry pays for a
    fresh draft under a new request hash instead of re-serving a
    rejected reply at $0.
    """
    existing = (
        await session.execute(
            sa.text(
                "SELECT id, status, input_digest, output, attempt FROM run_steps"
                " WHERE run_id = :run AND step_key = :key"
            ),
            {"run": str(run_id), "key": step_key},
        )
    ).first()
    if existing is None:
        row = (
            await session.execute(
                sa.text(
                    "INSERT INTO run_steps"
                    " (run_id, name, status, step_key, depends_on, input_digest, attempt)"
                    " VALUES (:run, :name, 'running', :key, '[]', :digest, 1)"
                    " RETURNING id"
                ),
                {"run": str(run_id), "name": step_key, "key": step_key, "digest": input_digest},
            )
        ).one()
        await session.commit()
        return {"step_id": UUID(str(row[0])), "reused_output": None, "attempt": 1}
    step_id, status, digest, output, attempt = existing
    if status == "done" and digest == input_digest:
        return {"step_id": UUID(str(step_id)), "reused_output": output, "attempt": int(attempt)}
    row = (
        await session.execute(
            sa.text(
                "UPDATE run_steps SET status = 'running', input_digest = :digest,"
                " output = NULL, error = NULL, attempt = attempt + 1,"
                " started_at = now() WHERE id = :step RETURNING attempt"
            ),
            {"digest": input_digest, "step": str(step_id)},
        )
    ).one()
    await session.commit()
    return {"step_id": UUID(str(step_id)), "reused_output": None, "attempt": int(row[0])}


async def finish_step(session: AsyncSession, *, step_id: UUID, output: dict[str, Any]) -> None:
    """Mark one step done with its checked output (same transaction as writes)."""
    await session.execute(
        sa.text(
            "UPDATE run_steps SET status = 'done', output = CAST(:output AS jsonb),"
            " finished_at = now() WHERE id = :step"
        ),
        {"output": json.dumps(output), "step": str(step_id)},
    )


def safe_stop_marker(marker: str) -> str:
    """Stop markers are a step + exception class, or an already plain sentence from plain_stop."""
    import re as _re

    clean = marker.replace("\x00", "")
    m = _re.match(r"stopped at ([A-Za-z_]+:[A-Za-z0-9_-]+): ([A-Za-z]+)", clean)
    if m:
        return f"stopped at {m.group(1)}: {m.group(2)}"
    if clean.startswith("no handler for step"):
        return clean[:80]
    if clean == "cancel requested":
        return clean
    return clean[:500]


async def stop_run(session: AsyncSession, *, run_id: UUID, marker: str) -> None:
    """Stop a run with an exhausted-retry marker; resume continues later."""
    marker = safe_stop_marker(marker)
    await session.execute(
        sa.text(
            "UPDATE runs SET status = 'stopped', stop_marker = :marker,"
            " updated_at = now() WHERE id = :run"
        ),
        {"marker": marker, "run": str(run_id)},
    )
    await session.commit()


async def complete_run(session: AsyncSession, *, run_id: UUID) -> None:
    """Mark a run done once every DAG step is done."""
    await session.execute(
        sa.text(
            "UPDATE runs SET status = 'done', stop_marker = NULL, updated_at = now()"
            " WHERE id = :run"
        ),
        {"run": str(run_id)},
    )
    await session.commit()


async def run_course_id(session: AsyncSession, *, run_id: UUID) -> UUID | None:
    """The course a run belongs to (scope checks for reads and resume)."""
    row = (
        await session.execute(
            sa.text("SELECT course_id FROM runs WHERE id = :run"),
            {"run": str(run_id)},
        )
    ).first()
    if row is None:
        from app.errors import NotFoundError

        raise NotFoundError(f"run {run_id} not found")
    return UUID(str(row[0])) if row[0] is not None else None


async def get_run(session: AsyncSession, *, run_id: UUID) -> dict[str, Any]:
    """One run with its steps in DAG order, ledger cost, and per-topic status."""
    row = (
        await session.execute(
            sa.text(
                "SELECT status, course_id, compiled_dag, dag_digest, estimate, stop_marker, kind"
                " FROM runs WHERE id = :run"
            ),
            {"run": str(run_id)},
        )
    ).first()
    if row is None:
        from app.errors import NotFoundError

        raise NotFoundError(f"run {run_id} not found")
    steps = (
        await session.execute(
            sa.text(
                "SELECT step_key, status, input_digest, attempt, error"
                " FROM run_steps WHERE run_id = :run ORDER BY name"
            ),
            {"run": str(run_id)},
        )
    ).all()
    spent = (
        await session.execute(
            sa.text("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE run_id = :run"),
            {"run": str(run_id)},
        )
    ).scalar_one()
    safe_dag = [
        {
            "step_key": str(e.get("step_key", "")),
            "handler": str(e.get("handler", "")),
            "depends_on": list(e.get("depends_on", [])),
        }
        for e in (row[2] or [])
        if isinstance(e, dict)
    ]
    return {
        "id": str(run_id),
        "status": row[0],
        "course_id": str(row[1]) if row[1] is not None else None,
        "compiled_dag": safe_dag,
        "dag_digest": row[3],
        "estimate": row[4],
        "stop_marker": plain_marker(row[5])[0],
        "stop_code": plain_marker(row[5])[1] or _code_of_plain(row[5]),
        "kind": row[6] if len(row) > 6 else None,
        "cost_so_far": str(spent),
        "topics": await _run_topics(session, course_id=row[1]),
        "steps": [
            {
                "step_key": step[0],
                "status": step[1],
                "input_digest": step[2],
                "attempt": step[3],
                "error": plain_step_error(str(step[0]), step[4]),
            }
            for step in steps
        ],
    }


async def _run_topics(session: AsyncSession, *, course_id: Any) -> list[dict[str, Any]]:
    """Per-topic generation status: held wins, then checking, then accepted."""
    if course_id is None:
        return []
    topics = (
        await session.execute(
            sa.text(
                "SELECT t.id, t.title FROM topics t JOIN modules m ON m.id = t.module_id"
                " WHERE m.course_id = :course ORDER BY m.ordinal, t.ordinal"
            ),
            {"course": str(course_id)},
        )
    ).all()
    if not topics:
        return []
    states = (
        await session.execute(
            sa.text(
                "SELECT b.topic_id, b.status, b.role FROM blocks b"
                " WHERE b.topic_id IN (SELECT t.id FROM topics t"
                " JOIN modules m ON m.id = t.module_id WHERE m.course_id = :course)"
            ),
            {"course": str(course_id)},
        )
    ).all()
    by_topic: dict[str, list[tuple[str, str]]] = {}
    for topic_id, status, role in states:
        by_topic.setdefault(str(topic_id), []).append((str(status), str(role)))
    result: list[dict[str, Any]] = []
    for topic_id, title in topics:
        members = by_topic.get(str(topic_id), [])
        if any(status == "held" for status, _ in members):
            state = "held"
        elif any(status != "accepted" and role == "check" for status, role in members):
            state = "checking"
        elif members and all(status == "accepted" for status, _ in members):
            state = "accepted"
        else:
            state = "draft"
        result.append({"topic_id": str(topic_id), "title": title, "status": state})
    return result


async def cancel_run(session: AsyncSession, *, run_id: UUID) -> None:
    """Cooperative cancel: plant a stop marker and commit it.

    The in-flight model call finishes and pays normally; the driver
    starts no new step once the marker is set (see _drive_locked).
    Only running runs cancel; anything else is a client error.
    """
    row = (
        await session.execute(
            sa.text("SELECT status FROM runs WHERE id = :run"),
            {"run": str(run_id)},
        )
    ).first()
    if row is None:
        from app.errors import NotFoundError

        raise NotFoundError(f"run {run_id} not found")
    if row[0] != "running":
        from app.errors import ValidationError

        raise ValidationError(f"run {run_id} is {row[0]}; only running runs cancel")
    await session.execute(
        sa.text(
            "UPDATE runs SET stop_marker = 'cancel requested', updated_at = now() WHERE id = :run"
        ),
        {"run": str(run_id)},
    )
    await session.commit()


def verify_citations(citations: list[dict[str, Any]], passages: dict[str, str]) -> list[str]:
    """Hold reasons for citations whose quote is not verbatim in the passage.

    passages maps passage id (or handle) to its text. A quote must
    appear verbatim; quote_start/end must match its position.
    """
    holds: list[str] = []
    for citation in citations:
        passage_id = str(citation.get("passage_id", citation.get("handle", "")))
        text = passages.get(passage_id, "")
        quote = str(citation.get("quote", ""))
        claim = str(citation.get("claim_path", "?"))
        if not quote or quote not in text:
            holds.append(f"claim {claim}: quote is not verbatim in its passage")
            continue
        start = text.index(quote)
        if int(citation.get("quote_start", start)) != start or int(
            citation.get("quote_end", start + len(quote))
        ) != start + len(quote):
            holds.append(f"claim {claim}: quote offsets do not match the passage")
    return holds


async def drive_run(
    factory: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    provider_factory: Callable[[AsyncSession], Any],
    handlers: dict[str, Callable[..., Any]],
) -> dict[str, Any]:
    """Drive one run to done or stopped; one driver holds `run:<id>`.

    Steps run in compiled-DAG order, skipping satisfied dependencies;
    done steps with an unchanged input digest are reused without a
    model call. Returns the final run record.

    The lock is session-level (`pg_try_advisory_lock`) on a dedicated
    connection held for the whole drive: the old transaction-scoped
    lock was released by the first commit inside claim_step, so two
    drivers could enter the same run. Any exception in a handler —
    LLM errors and code bugs alike — fails the step and stops the run
    with a marker instead of leaving it running.
    """
    engine = factory.kw.get("bind")
    if engine is None:  # pragma: no cover - every caller binds an engine
        raise ValidationError("drive_run needs a session factory bound to an engine")
    async with engine.connect() as lock_conn:
        acquired = (
            await lock_conn.execute(
                sa.text("SELECT pg_try_advisory_lock(hashtext(:key))"),
                {"key": f"{RUN_LOCK_PREFIX}{run_id}"},
            )
        ).scalar()
        if not acquired:
            async with factory() as busy:
                return await get_run(busy, run_id=run_id)
        try:
            return await _drive_locked(
                factory,
                run_id=run_id,
                provider_factory=provider_factory,
                handlers=handlers,
            )
        finally:
            await lock_conn.execute(
                sa.text("SELECT pg_advisory_unlock(hashtext(:key))"),
                {"key": f"{RUN_LOCK_PREFIX}{run_id}"},
            )


async def _drive_locked(
    factory: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    provider_factory: Callable[[AsyncSession], Any],
    handlers: dict[str, Callable[..., Any]],
) -> dict[str, Any]:
    """One drive pass; the caller holds the session-level run lock."""
    async with factory() as session:
        dag_row = (
            await session.execute(
                sa.text("SELECT compiled_dag, status FROM runs WHERE id = :run"),
                {"run": str(run_id)},
            )
        ).first()
        if dag_row is None:
            from app.errors import NotFoundError

            raise NotFoundError(f"run {run_id} not found")
        if dag_row[1] == "done":
            return await get_run(session, run_id=run_id)
        dag = dag_row[0] or []
        done_keys: set[str] = {
            str(row[0])
            for row in (
                await session.execute(
                    sa.text(
                        "SELECT step_key FROM run_steps WHERE run_id = :run AND status = 'done'"
                    ),
                    {"run": str(run_id)},
                )
            ).all()
        }
        for entry in dag:
            step_key = str(entry["step_key"])
            marker = (
                await session.execute(
                    sa.text("SELECT stop_marker FROM runs WHERE id = :run"),
                    {"run": str(run_id)},
                )
            ).scalar()
            if marker:
                await stop_run(session, run_id=run_id, marker=str(marker))
                return await get_run(session, run_id=run_id)
            if not all(dep in done_keys for dep in entry.get("depends_on", [])):
                continue
            if step_key in done_keys and entry.get("input_digest"):
                existing = (
                    await session.execute(
                        sa.text(
                            "SELECT input_digest FROM run_steps"
                            " WHERE run_id = :run AND step_key = :key"
                        ),
                        {"run": str(run_id), "key": step_key},
                    )
                ).first()
                if existing is not None and existing[0] == entry["input_digest"]:
                    continue
            handler = handlers.get(str(entry.get("handler", step_key)))
            if handler is None:
                await stop_run(session, run_id=run_id, marker=f"no handler for step {step_key}")
                return await get_run(session, run_id=run_id)
            try:
                await handler(
                    session, run_id=run_id, entry=entry, provider_factory=provider_factory
                )
            except Exception as exc:
                await session.rollback()
                async with factory() as failed:
                    await failed.execute(
                        sa.text(
                            "UPDATE run_steps SET status = 'failed', error = :error,"
                            " finished_at = now() WHERE run_id = :run AND step_key = :key"
                        ),
                        {
                            "error": f"{type(exc).__name__}: {exc}"[:2000],
                            "run": str(run_id),
                            "key": step_key,
                        },
                    )
                    await stop_run(
                        failed,
                        run_id=run_id,
                        marker=plain_stop(step_key, type(exc).__name__, str(exc))[0],
                    )
                async with factory() as fresh:
                    return await get_run(fresh, run_id=run_id)
            done_keys.add(step_key)
        remaining = (
            await session.execute(
                sa.text("SELECT count(*) FROM run_steps WHERE run_id = :run AND status <> 'done'"),
                {"run": str(run_id)},
            )
        ).scalar()
        if not remaining:
            await session.execute(
                sa.text("UPDATE runs SET status = 'running' WHERE id = :run"),
                {"run": str(run_id)},
            )
            await complete_run(session, run_id=run_id)
        return await get_run(session, run_id=run_id)


async def plan_researcher_step(
    session: AsyncSession,
    *,
    run_id: UUID,
    entry: dict[str, Any],
    provider_factory: Callable[[AsyncSession], Any],
) -> None:
    """Researcher step: FTS first, model ranks, pairs recorded.

    The single model-call site for plan runs lives here; every other
    agent call in T03 flows through drive_run handlers the same way.
    """
    from app.agents import researcher
    from app.providers.retrieval import get_retriever

    step_key = str(entry["step_key"])
    query = str(entry["query"])
    course_id = UUID(str(entry["course_id"]))
    language = str(entry.get("language", "en"))
    retriever = get_retriever(str(entry.get("retriever", "fts")))
    found = await retriever.search_course(session, query=query, course_id=course_id, limit=8)
    handle_to_passage = {f"P{index}": item.passage_id for index, item in enumerate(found, 1)}
    handles = [
        {
            "handle": f"P{index}",
            "address_key": item.address_key,
            "text": item.text[:500],
        }
        for index, item in enumerate(found, 1)
    ]
    source_languages = sorted(
        {
            TS_CONFIG_TO_LANGUAGE.get(item.ts_config, item.ts_config)
            for item in found
            if item.ts_config
        }
    )
    rephrased = researcher.rephrase_queries(query)
    prompt = researcher.build_prompt(
        query, rephrased, handles, course_language=language, source_languages=source_languages
    )
    digest = step_digest(
        agent="researcher",
        prompt=prompt,
        upstream={"query": query, "course_id": str(course_id)},
    )
    claim = await claim_step(session, run_id=run_id, step_key=step_key, input_digest=digest)
    provider = provider_factory(session)
    if claim["reused_output"] is not None:
        output = dict(claim["reused_output"])
    else:
        known_handles = set(handle_to_passage)
        output = await provider.generate_json(
            agent="researcher",
            prompt=prompt,
            schema=researcher.schema(),
            run_id=run_id,
            step_id=claim["step_id"],
            variant=int(claim["attempt"]) - 1,
            check=lambda reply: researcher.check_output(reply, known_handles),
        )
        await researcher.record_pairs(
            session,
            query=query,
            query_lang=language,
            ranked=output["ranked"],
            handle_to_passage=handle_to_passage,
            ranker="model",
            fts_order=[item.passage_id for item in found],
        )
        for candidate in output.get("web_candidates", []):
            from app.services.library import add_web_candidate

            await add_web_candidate(
                session,
                course_id=UUID(str(entry["course_id"])),
                query=query,
                url=str(candidate["url"]),
                title=str(candidate.get("title", "")),
                snippet=str(candidate.get("why", "")),
            )
        await finish_step(session, step_id=claim["step_id"], output=output)
    await session.commit()


async def plan_architect_step(
    session: AsyncSession,
    *,
    run_id: UUID,
    entry: dict[str, Any],
    provider_factory: Callable[[AsyncSession], Any],
) -> None:
    """Architect step: profile plus passage handles in, plan tree out."""
    from app.agents import architect
    from app.course.methodologies import prescription_for
    from app.course.tree import ArchitectOutput, CourseTree
    from app.services.courses import write_tree

    step_key = str(entry["step_key"])
    course_id = UUID(str(entry["course_id"]))
    language = str(entry.get("language", "en"))
    profile = entry["profile"]
    constraints = entry.get("constraints", [])
    prescription = prescription_for(list(profile.get("methodology_codes", [])))
    rows = (
        await session.execute(
            sa.text(
                "SELECT passages.id, passages.address_key, passages.text,"
                " passages.source_id, passages.ts_config FROM passages"
                " JOIN course_sources cs ON cs.source_id = passages.source_id"
                " AND cs.course_id = :course AND cs.approval = 'approved'"
                " JOIN sources s ON s.id = passages.source_id"
                " AND s.approval = 'approved'"
            ),
            {"course": str(course_id)},
        )
    ).all()
    ordered = sorted(rows, key=lambda row: (str(row[3]), address_sort_key(str(row[1]))))
    picked: list[Any] = []
    budget = 0
    for row in ordered:
        size = len(str(row[2]))
        if picked and budget + size > ARCHITECT_SOURCE_CHAR_BUDGET:
            break
        picked.append(row)
        budget += size
    handle_to_passage = {f"P{index}": row[0] for index, row in enumerate(picked, 1)}
    passages = [
        {"handle": f"P{index}", "address_key": row[1], "text": str(row[2])}
        for index, row in enumerate(picked, 1)
    ]
    source_languages = sorted(
        {TS_CONFIG_TO_LANGUAGE.get(str(row[4]), str(row[4])) for row in picked if row[4]}
    )
    prompt = architect.build_prompt(
        profile,
        constraints,
        prescription,
        passages,
        course_language=language,
        source_languages=source_languages,
    )
    upstream_rows = (
        await session.execute(
            sa.text(
                "SELECT step_key, output FROM run_steps WHERE run_id = :run AND status = 'done'"
            ),
            {"run": str(run_id)},
        )
    ).all()
    digest = step_digest(
        agent="architect",
        prompt=prompt,
        upstream={str(key): output for key, output in upstream_rows},
    )
    claim = await claim_step(session, run_id=run_id, step_key=step_key, input_digest=digest)
    provider = provider_factory(session)
    if claim["reused_output"] is not None:
        output = dict(claim["reused_output"])
    else:
        handles = set(handle_to_passage)
        output = await provider.generate_json(
            agent="architect",
            prompt=prompt,
            schema=architect.schema(),
            run_id=run_id,
            step_id=claim["step_id"],
            variant=int(claim["attempt"]) - 1,
            check=lambda reply: _rejectable_architect_errors(reply, prescription, handles),
        )
        parsed = ArchitectOutput.model_validate(output)
        resolved, held = _resolve_handles_tolerant(parsed, handle_to_passage)
        refs = _passage_refs(resolved)
        version_map = await write_tree(
            session,
            course_id=course_id,
            tree=CourseTree(modules=parsed.modules, through_line=parsed.through_line),
            plan_digest=digest,
            run_step_id=claim["step_id"],
            held=held,
        )
        await write_plan_citations(session, version_map=version_map, refs=refs)
        await finish_step(session, step_id=claim["step_id"], output=resolved)
    await session.commit()


def _rejectable_architect_errors(
    reply: dict[str, Any], prescription: dict[str, list[str]], handles: set[str]
) -> list[str]:
    """Architect errors that stop the run; unknown handles hold instead.

    A reply whose only errors are unknown source handles is accepted
    here (the reply is stored) and its blocks are held with a reason
    by _resolve_handles_tolerant; every other error rejects the reply
    so it never poisons the store.
    """
    from app.agents import architect

    errors = architect.check_output(reply, prescription, handles)
    if errors and all("unknown handle" in error for error in errors):
        return []
    return errors


def _resolve_handles_tolerant(
    parsed: Any, handle_to_passage: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Map P-handles to passage ids; unknown handles hold their block.

    Returns the resolved output plus plan_node_id to hold reason for
    blocks citing handles outside this step. Known refs of a held
    block are kept (so the reader still sees the passage); a block
    with no known ref keeps an empty ref list and is still held.
    """
    dump: dict[str, Any] = json.loads(parsed.model_dump_json())
    held: dict[str, str] = {}
    for module in dump["modules"]:
        for topic in module["topics"]:
            for block in topic["blocks"]:
                resolved_refs = []
                unknown: list[str] = []
                for ref in block["source_refs"]:
                    passage_id = handle_to_passage.get(ref["handle"])
                    if passage_id is None:
                        unknown.append(str(ref["handle"]))
                    else:
                        resolved_refs.append({"passage_id": str(passage_id)})
                block["source_refs"] = resolved_refs
                if unknown:
                    quoted = ", ".join(sorted(set(unknown)))
                    held[str(block["plan_node_id"])] = f"unknown handle {quoted}"
    return dump, held


def _passage_refs(resolved: dict[str, Any]) -> dict[str, list[str]]:
    """Block plan_node_id to cited passage ids from resolved output."""
    refs: dict[str, list[str]] = {}
    for module in resolved["modules"]:
        for topic in module["topics"]:
            for block in topic["blocks"]:
                refs[str(block["plan_node_id"])] = [
                    str(ref["passage_id"]) for ref in block["source_refs"]
                ]
    return refs


async def _stored_passage_texts(session: AsyncSession, *, passage_ids: list[str]) -> dict[str, str]:
    """Passage id to its stored text for every cited passage."""
    if not passage_ids:
        return {}
    rows = (
        await session.execute(
            sa.text("SELECT id, text FROM passages WHERE id::text = ANY(:ids)"),
            {"ids": passage_ids},
        )
    ).all()
    return {str(row[0]): str(row[1]) for row in rows}


async def write_plan_citations(
    session: AsyncSession,
    *,
    version_map: dict[str, dict[str, str]],
    refs: dict[str, list[str]],
    citations: dict[str, list[dict[str, Any]]] | None = None,
) -> int:
    """Write block→passage citation rows beside the block versions.

    Plan-time links carry no quote yet (verified_by='plan'); rows with
    a quote (generation time, T05) are verified verbatim in code
    first against the STORED passage text — caller-supplied text never
    decides verification. A bad quote raises ValidationError (the
    generation layer turns it into a held block) instead of crashing
    the run. Passage is RESTRICT: cited passages cannot be deleted
    out from under their blocks.
    """
    wanted: list[str] = []
    for passage_ids in refs.values():
        wanted.extend(passage_ids)
    stored = await _stored_passage_texts(session, passage_ids=list(dict.fromkeys(wanted)))
    count = 0
    for node_id, passage_ids in refs.items():
        target = version_map.get(node_id)
        if target is None:
            continue
        quoted = {item.get("passage_id"): item for item in (citations or {}).get(node_id, [])}
        for passage_id in passage_ids:
            quote_item = quoted.get(passage_id, {})
            quote = str(quote_item.get("quote", ""))
            if quote:
                holds = verify_citations(
                    [
                        {
                            "passage_id": passage_id,
                            "claim_path": f"blocks.{node_id}",
                            "quote": quote,
                            "quote_start": quote_item.get("quote_start", 0),
                            "quote_end": quote_item.get("quote_end", 0),
                        }
                    ],
                    {passage_id: stored.get(passage_id, "")},
                )
                if holds:
                    raise ValidationError(f"citation rejected: {holds[0]}")
            await session.execute(
                sa.text(
                    "INSERT INTO citations (block_version_id, passage_id, claim_path,"
                    " quote, quote_start, quote_end, verified_by)"
                    " VALUES (:version, :passage, :claim, :quote, :start, :end,"
                    " :verified)"
                ),
                {
                    "version": target["version_id"],
                    "passage": passage_id,
                    "claim": f"plan:{node_id}",
                    "quote": quote,
                    "start": int(quote_item.get("quote_start", 0)),
                    "end": int(quote_item.get("quote_end", 0)),
                    "verified": "code" if quote else "plan",
                },
            )
            count += 1
    return count


async def _upstream_outputs(
    session: AsyncSession, *, run_id: UUID, entry: dict[str, Any]
) -> list[dict[str, Any]]:
    """Outputs of done dependency steps (block texts wave B and readers see)."""
    deps = [str(d) for d in entry.get("depends_on", [])]
    if not deps:
        return []
    rows = (
        await session.execute(
            sa.text(
                "SELECT step_key, output FROM run_steps WHERE run_id = :run AND status = 'done'"
                " AND step_key = ANY(:deps)"
            ),
            {"run": str(run_id), "deps": deps},
        )
    ).all()
    out = []
    for key, output in rows:
        if isinstance(output, dict):
            out.append(
                {
                    "step_key": str(key),
                    "plan_node_id": str(key).partition(":")[2],
                    "text": str(output.get("text", "")),
                    "output": output,
                }
            )
    return out


async def _persist_block(
    session: AsyncSession,
    *,
    course_id: str,
    node_id: str,
    content: dict[str, Any],
    answer_key: dict[str, Any] | None,
    step_id: UUID,
    status: str = "draft",
    hold_reason: str | None = None,
) -> None:
    """Write one block version; content never carries the answer key."""
    brow = (
        await session.execute(
            sa.text(
                "SELECT b.id FROM blocks b JOIN topics tp ON tp.id = b.topic_id"
                " JOIN modules m ON m.id = tp.module_id"
                " WHERE m.course_id = :c AND b.plan_node_id = :n"
            ),
            {"c": course_id, "n": node_id},
        )
    ).first()
    if brow is None:
        return
    block_id = str(brow[0])
    nxt = (
        await session.execute(
            sa.text(
                "SELECT COALESCE(MAX(version_no), 0) + 1 FROM block_versions WHERE block_id = :b"
            ),
            {"b": block_id},
        )
    ).scalar_one()
    vid = (
        await session.execute(
            sa.text(
                "INSERT INTO block_versions (block_id, version_no, content, answer_key,"
                " schema_version, run_step_id) VALUES (:b, :n, CAST(:content AS jsonb),"
                " CAST(:key AS jsonb), '1', :step) RETURNING id"
            ),
            {
                "b": block_id,
                "n": int(nxt),
                "content": json.dumps(content),
                "key": json.dumps(answer_key) if answer_key is not None else None,
                "step": str(step_id),
            },
        )
    ).one()[0]
    await session.execute(
        sa.text(
            "UPDATE blocks SET current_version_id = :v, status = :s, hold_reason = :r WHERE id = :b"
        ),
        {"v": str(vid), "s": status, "r": hold_reason, "b": block_id},
    )


async def _plant_spot_block(
    session: AsyncSession,
    *,
    course_id: str,
    node_id: str,
    handle_to_passage: dict[str, str],
    step_id: UUID,
    mutated: str | None = None,
    kind: str | None = None,
) -> None:
    """Code-planted spot-the-error: exercise from verified passage sentences."""
    import hashlib as _hl

    from app.course.errorspot import plant_error as _plant

    stored_all = await _stored_passage_texts(session, passage_ids=list(handle_to_passage.values()))
    verified: list[dict[str, Any]] = []
    for handle, pid in handle_to_passage.items():
        for sent in stored_all.get(pid, "").split(". "):
            if sent.strip():
                verified.append({"sentence": sent.strip().rstrip(".") + ".", "passage": handle})
    exercise = _plant(
        verified=verified,
        reader_holds=[],
        seed=int(_hl.sha256(node_id.encode()).hexdigest()[:8], 16),
        mutated=mutated,
        kind=kind,
    )
    pid = handle_to_passage.get(str(exercise.get("passage", "P1")), "")
    quote = str(exercise.get("true_sentence", ""))[:120]
    output: dict[str, Any] = {
        "block_type": "spot_the_error",
        "text": str(exercise.get("planted_sentence", "")),
        "sentences": exercise["sentences"],
        "citations": [{"passage_id": str(exercise.get("passage", "P1")), "quote": quote}],
        "answer": "",
    }
    key: dict[str, Any] = {
        "planted_sentence": exercise["planted_sentence"],
        "planted_index": exercise["planted_index"],
        "kind": exercise["kind"],
        "passage": exercise["passage"],
        "true_sentence": exercise["true_sentence"],
        "fully_correct": exercise["fully_correct"],
    }
    await _persist_block(
        session,
        course_id=course_id,
        node_id=node_id,
        content={"sentences": exercise["sentences"]},
        answer_key=key,
        step_id=step_id,
        status="draft",
    )
    brow = (
        await session.execute(
            sa.text("SELECT current_version_id FROM blocks WHERE plan_node_id = :n"),
            {"n": node_id},
        )
    ).first()
    if brow is not None and brow[0] is not None and pid and quote in stored_all.get(pid, ""):
        start = stored_all[pid].index(quote)
        await session.execute(
            sa.text(
                "INSERT INTO citations (block_version_id, passage_id, claim_path, quote,"
                " quote_start, quote_end, verified_by) VALUES (:v, :p, :c, :q, :s, :e, 'code')"
            ),
            {
                "v": str(brow[0]),
                "p": pid,
                "c": f"blocks.{node_id}",
                "q": quote,
                "s": start,
                "e": start + len(quote),
            },
        )
    await finish_step(session, step_id=step_id, output=output)


async def generate_block_step(
    session: AsyncSession,
    *,
    run_id: UUID,
    entry: dict[str, Any],
    provider_factory: Callable[[AsyncSession], Any],
) -> None:
    """One block generator call: upstream texts in prompt and digest, verdicts in code."""
    from app.agents import generate_block
    from app.helpers.language import check_language

    step_key = str(entry["step_key"])
    agent, _, node_id = step_key.partition(":")
    block_type = str(entry.get("block_type", "reading"))
    course_id = str(entry.get("course_id", ""))
    upstream = await _upstream_outputs(session, run_id=run_id, entry=entry)
    sources = list(entry.get("sources", []))
    handle_to_passage: dict[str, str] = {}
    for index, item in enumerate(generate_block.ordered_sources(sources), 1):
        pid = str(item.get("passage_id", ""))
        if pid:
            handle_to_passage[f"P{index}"] = pid
    prompt = generate_block.build_generation_prompt(
        agent=agent,
        block_type=block_type,
        course_id=course_id,
        course_language=str(entry.get("course_language", "en")),
        sources=sources,
        plan=dict(entry.get("plan", {})),
        dossier=dict(entry.get("dossier", {})),
        skeleton=dict(entry.get("skeleton", {"plan_node_id": node_id})),
        upstream_texts=[{"plan_node_id": u["plan_node_id"], "text": u["text"]} for u in upstream],
    )
    digest = step_digest(
        agent=agent,
        prompt=prompt,
        upstream={"node": node_id, "upstream": [u["output"] for u in upstream]},
    )
    claim = await claim_step(session, run_id=run_id, step_key=step_key, input_digest=digest)
    if block_type == "spot_the_error" and claim["reused_output"] is None:
        sab: dict[str, Any] = next(
            (u["output"] for u in upstream if u["step_key"].startswith("saboteur:")), {}
        )
        await _plant_spot_block(
            session,
            course_id=course_id,
            node_id=node_id,
            handle_to_passage=handle_to_passage,
            step_id=claim["step_id"],
            mutated=str(sab["mutated"]) if sab.get("mutated") else None,
            kind=str(sab["kind"]) if sab.get("kind") else None,
        )
        await session.commit()
        return
    provider = provider_factory(session)
    if claim["reused_output"] is not None:
        output = dict(claim["reused_output"])
    else:
        from app.llm.prompts import cache_key

        output = await provider.generate_json(
            agent=agent,
            prompt=prompt,
            schema=generate_block.schema_for(block_type),
            cache_key=cache_key(agent, block_type, course_id),
            run_id=run_id,
            step_id=claim["step_id"],
            variant=int(claim["attempt"]) - 1,
            check=lambda reply: generate_block.check_block_output(reply, block_type),
        )
        # code verdicts before acceptance: verbatim citations + language
        text = str(output.get("text", ""))
        stored = await _stored_passage_texts(session, passage_ids=list(handle_to_passage.values()))
        holds: list[str] = []
        resolved_citations = []
        for cit in output.get("citations", []):
            handle = str(cit.get("passage_id", cit.get("handle", "")))
            pid = handle_to_passage.get(handle, handle)
            quote = str(cit.get("quote", ""))
            passage_text = stored.get(pid, "")
            if not quote or quote not in passage_text:
                holds.append(f"quote is not verbatim in its passage ({handle})")
            else:
                start = passage_text.index(quote)
                resolved_citations.append(
                    {
                        "passage_id": pid,
                        "handle": handle,
                        "quote": quote,
                        "quote_start": start,
                        "quote_end": start + len(quote),
                    }
                )
        holds += check_language(text, course_language=str(entry.get("course_language", "en")))
        if holds:
            await _persist_block(
                session,
                course_id=course_id,
                node_id=node_id,
                content={"text": text},
                answer_key=None,
                step_id=claim["step_id"],
                status="held",
                hold_reason="; ".join(holds),
            )
            output = {**output, "held": True, "hold_reason": "; ".join(holds)}
            await finish_step(session, step_id=claim["step_id"], output=output)
            await session.commit()
            return
        # persist learner content (no key) + key column + verified citations
        content: dict[str, Any]
        key: dict[str, Any]
        if block_type == "choice" and isinstance(output.get("options"), list):
            content = {
                "stem": text,
                "options": [
                    str(o.get("text", "")) if isinstance(o, dict) else str(o)
                    for o in output["options"]
                ],
                "citations": resolved_citations,
            }
            key = {
                "correct": [
                    i
                    for i, o in enumerate(output["options"])
                    if isinstance(o, dict) and o.get("correct")
                ],
                "answer": str(output.get("answer", "")),
            }
        elif block_type == "spot_the_error":
            planted = entry.get("planted") or {}
            sentences = [str(s) for s in output.get("sentences", [])] or [text]
            content = {"sentences": sentences}
            key = {
                "planted_sentence": str(planted.get("planted_sentence", "")),
                "planted_index": int(planted.get("planted_index", -1)),
                "kind": str(planted.get("kind", "negation")),
                "passage": str(planted.get("passage", "")),
                "true_sentence": str(planted.get("true_sentence", "")),
                "fully_correct": bool(planted.get("fully_correct", False)),
                "answer": str(output.get("answer", "")),
            }
        else:
            content = {"text": text, "body": text, "citations": resolved_citations}
            key = {"answer": str(output.get("answer", ""))}
        await _persist_block(
            session,
            course_id=course_id,
            node_id=node_id,
            content=content,
            answer_key=key,
            step_id=claim["step_id"],
            status="draft",
        )
        # citation rows verified against stored passage text
        brow = (
            await session.execute(
                sa.text("SELECT current_version_id FROM blocks WHERE plan_node_id = :n"),
                {"n": node_id},
            )
        ).first()
        if brow is not None and brow[0] is not None:
            for cit in resolved_citations:
                await session.execute(
                    sa.text(
                        "INSERT INTO citations (block_version_id, passage_id, claim_path, quote,"
                        " quote_start, quote_end, verified_by)"
                        " VALUES (:v, :p, :c, :q, :s, :e, 'code')"
                    ),
                    {
                        "v": str(brow[0]),
                        "p": cit["passage_id"],
                        "c": f"blocks.{node_id}",
                        "q": cit["quote"],
                        "s": cit["quote_start"],
                        "e": cit["quote_end"],
                    },
                )
        await finish_step(session, step_id=claim["step_id"], output=output)
    await session.commit()


async def read_topic_step(
    session: AsyncSession,
    *,
    run_id: UUID,
    entry: dict[str, Any],
    provider_factory: Callable[[AsyncSession], Any],
) -> None:
    """One reader call per topic; holds land on blocks with the reason."""
    from app.agents import reader
    from app.llm.prompts import cache_key

    step_key = str(entry["step_key"])
    _, _, topic_id = step_key.partition(":")
    upstream = await _upstream_outputs(session, run_id=run_id, entry=entry)
    by_node = {u["plan_node_id"]: u for u in upstream}
    blocks = []
    for item in entry.get("blocks", []):
        node = str(item.get("plan_node_id", ""))
        up = by_node.get(node, {})
        blocks.append({**item, "text": up.get("text", str(item.get("text", "")))})
    node_ids = {b["plan_node_id"] for b in blocks if b.get("plan_node_id")}
    prompt = reader.build_reader_prompt(
        topic_plan_node_id=topic_id,
        course_language=str(entry.get("course_language", "en")),
        sources=list(entry.get("sources", [])),
        plan=dict(entry.get("plan", {})),
        dossier=dict(entry.get("dossier", {})),
        blocks=blocks,
    )
    digest = step_digest(
        agent="reader",
        prompt=prompt,
        upstream={"topic": topic_id, "blocks": [u["output"] for u in upstream]},
    )
    claim = await claim_step(session, run_id=run_id, step_key=step_key, input_digest=digest)
    provider = provider_factory(session)
    if claim["reused_output"] is not None:
        output = dict(claim["reused_output"])
    else:
        output = await provider.generate_json(
            agent="reader",
            prompt=prompt,
            schema=reader.schema(),
            cache_key=cache_key("reader", topic_id, str(entry.get("course_id", ""))),
            run_id=run_id,
            step_id=claim["step_id"],
            variant=int(claim["attempt"]) - 1,
            check=lambda reply: reader.check_reader_output(reply, node_ids=node_ids),
        )
        for verdict in output.get("verdicts", []):
            node = str(verdict.get("plan_node_id", ""))
            items = verdict.get("items", {}) if isinstance(verdict.get("items"), dict) else {}
            reasons = [
                f"{k}: {v.get('evidence', '')}"
                for k, v in items.items()
                if isinstance(v, dict) and v.get("verdict") == "hold"
            ]
            if verdict.get("verdict") == "hold" or reasons:
                reason = "; ".join(reasons) or "reader hold"
                await session.execute(
                    sa.text(
                        "UPDATE blocks SET status = 'held', hold_reason = :r"
                        " WHERE plan_node_id = :n"
                    ),
                    {"r": reason, "n": node},
                )
            elif verdict.get("verdict") == "pass":
                await session.execute(
                    sa.text(
                        "UPDATE blocks SET status = 'accepted', hold_reason = NULL"
                        " WHERE plan_node_id = :n AND status <> 'held'"
                    ),
                    {"n": node},
                )
        await finish_step(session, step_id=claim["step_id"], output=output)
    await session.commit()


async def read_continuity_step(
    session: AsyncSession,
    *,
    run_id: UUID,
    entry: dict[str, Any],
    provider_factory: Callable[[AsyncSession], Any],
) -> None:
    """One continuity reader call per module over topic handoffs."""
    from app.agents import continuity
    from app.llm.prompts import cache_key

    step_key = str(entry["step_key"])
    _, _, module_id = step_key.partition(":")
    prompt = continuity.build_continuity_prompt(
        module_plan_node_id=module_id,
        course_language=str(entry.get("course_language", "en")),
        handoffs=list(entry.get("handoffs", [])),
        dossier=dict(entry.get("dossier", {})),
    )
    digest = step_digest(agent="continuity", prompt=prompt, upstream={"module": module_id})
    claim = await claim_step(session, run_id=run_id, step_key=step_key, input_digest=digest)
    provider = provider_factory(session)
    if claim["reused_output"] is not None:
        output = dict(claim["reused_output"])
    else:
        output = await provider.generate_json(
            agent="continuity",
            prompt=prompt,
            schema=continuity.schema(),
            cache_key=cache_key("continuity", module_id, str(entry.get("course_id", ""))),
            run_id=run_id,
            step_id=claim["step_id"],
            variant=int(claim["attempt"]) - 1,
            check=continuity.check_continuity_output,
        )
        await finish_step(session, step_id=claim["step_id"], output=output)
    await session.commit()


async def saboteur_step(
    session: AsyncSession,
    *,
    run_id: UUID,
    entry: dict[str, Any],
    provider_factory: Callable[[AsyncSession], Any],
) -> None:
    """Saboteur rewrite for non-numeric kinds only (code owns numbers)."""
    from app.agents import saboteur
    from app.llm.prompts import cache_key

    step_key = str(entry["step_key"])
    sentence = str(entry.get("sentence", ""))
    if not sentence:
        sources = list(entry.get("sources", []))
        if sources:
            sentence = str(sources[0].get("text", "")).split(". ")[0].strip()
            if sentence and not sentence.endswith("."):
                sentence += "."
    prompt = saboteur.build_saboteur_prompt(
        sentence=sentence,
        kind=str(entry.get("kind", "entity_swap")),
        course_language=str(entry.get("course_language", "en")),
    )
    digest = step_digest(agent="saboteur", prompt=prompt, upstream={"key": step_key})
    claim = await claim_step(session, run_id=run_id, step_key=step_key, input_digest=digest)
    provider = provider_factory(session)
    if claim["reused_output"] is not None:
        output = dict(claim["reused_output"])
    else:
        output = await provider.generate_json(
            agent="saboteur",
            prompt=prompt,
            schema=saboteur.schema(),
            cache_key=cache_key(
                "saboteur", str(entry.get("kind", "")), str(entry.get("course_id", ""))
            ),
            run_id=run_id,
            step_id=claim["step_id"],
            variant=int(claim["attempt"]) - 1,
            check=saboteur.check_saboteur_output,
        )
        await finish_step(session, step_id=claim["step_id"], output=output)
    await session.commit()


async def rework_blocks_step(
    session: AsyncSession,
    *,
    run_id: UUID,
    entry: dict[str, Any],
    provider_factory: Callable[[AsyncSession], Any],
) -> None:
    """Rework only named blocks, with the hold reason in the prompt."""
    step_key = str(entry["step_key"])
    named = [str(n) for n in entry.get("named", [])]
    if not named:
        # named = blocks the reader held in this run
        rows = (
            await session.execute(
                sa.text(
                    "SELECT plan_node_id, hold_reason FROM blocks WHERE status = 'held'"
                    " AND topic_id IN (SELECT t.id FROM topics t"
                    " JOIN modules m ON m.id = t.module_id"
                    " JOIN runs r ON r.course_id = m.course_id WHERE r.id = :run)"
                ),
                {"run": str(run_id)},
            )
        ).all()
        named = [str(r[0]) for r in rows]
        reasons = {str(r[0]): str(r[1] or "") for r in rows}
    else:
        reasons = {str(k): str(v) for k, v in dict(entry.get("reasons", {})).items()}
    digest = input_digest_of({"kind": "rework", "named": named, "reasons": reasons})
    claim = await claim_step(session, run_id=run_id, step_key=step_key, input_digest=digest)
    if claim["reused_output"] is None:
        dag_row = (
            await session.execute(
                sa.text("SELECT compiled_dag FROM runs WHERE id = :run"), {"run": str(run_id)}
            )
        ).first()
        dag_list = dag_row[0] if dag_row is not None else []
        originals = {
            str(e.get("step_key", "")).partition(":")[2]: e
            for e in (dag_list or [])
            if isinstance(e, dict)
        }
        reworked = []
        for node in named:
            original = originals.get(node)
            if original is None:
                continue
            gen_entry = dict(original)
            skeleton = dict(gen_entry.get("skeleton", {"plan_node_id": node}))
            skeleton["hold_reason"] = reasons.get(node, "")
            skeleton["brief"] = f"{skeleton.get('brief', '')} Rewrite: {reasons.get(node, '')}"
            gen_entry["skeleton"] = skeleton
            try:
                await generate_block_step(
                    session, run_id=run_id, entry=gen_entry, provider_factory=provider_factory
                )
                reworked.append(node)
            except Exception:
                # a failed rewrite leaves the block held; the run still completes
                await session.rollback()
        await finish_step(session, step_id=claim["step_id"], output={"reworked": reworked})
    await session.commit()


async def translate_glossary_step(
    session: AsyncSession,
    *,
    run_id: UUID,
    entry: dict[str, Any],
    provider_factory: Callable[[AsyncSession], Any],
) -> None:
    """Glossary-first pass: translate bare terms before any block.

    The only model-call site for term tables; the per-block steps read
    this output and the translator must use its terms. Code checks term
    use per segment after the run.
    """
    from app.agents import translator
    from app.llm.prompts import cache_key

    step_key = str(entry["step_key"])
    source_language = str(entry.get("source_language", "en"))
    target_language = str(entry["target_language"])
    course_id = str(entry.get("course_id", ""))
    terms = dict(entry.get("terms", {}))
    if not terms:
        claim = await claim_step(
            session,
            run_id=run_id,
            step_key=step_key,
            input_digest=input_digest_of({"kind": "glossary", "terms": {}}),
        )
        if claim["reused_output"] is None:
            await finish_step(session, step_id=claim["step_id"], output={"terms": {}})
        await session.commit()
        return
    prompt = translator.build_glossary_prompt(
        terms=terms,
        source_language=source_language,
        target_language=target_language,
    )
    digest = step_digest(agent="translator", prompt=prompt, upstream={"glossary": target_language})
    claim = await claim_step(session, run_id=run_id, step_key=step_key, input_digest=digest)
    provider = provider_factory(session)
    if claim["reused_output"] is not None:
        output = dict(claim["reused_output"])
    else:
        expected = set(terms)
        try:
            reply = await provider.generate_json(
                agent="translator",
                prompt=prompt,
                schema=translator.schema(),
                cache_key=cache_key("translator", "glossary", course_id or target_language),
                run_id=run_id,
                step_id=claim["step_id"],
                variant=int(claim["attempt"]) - 1,
                check=lambda answer: translator.check_translator_output(
                    answer, expected_ids=expected
                ),
            )
        except Exception as exc:  # one bad reply holds, never fails the run
            await finish_step(
                session,
                step_id=claim["step_id"],
                output={"terms": {}, "error": f"{type(exc).__name__}: {exc}"[:500]},
            )
            await session.commit()
            return
        output = {
            "terms": {str(item["id"]): str(item["text"]) for item in reply.get("segments", [])}
        }
        await finish_step(session, step_id=claim["step_id"], output=output)
    await session.commit()


async def translate_block_step(
    session: AsyncSession,
    *,
    run_id: UUID,
    entry: dict[str, Any],
    provider_factory: Callable[[AsyncSession], Any],
) -> None:
    """One translator call per block: plain segments in, segments out.

    Translation-memory hits and the planted sentence's exclusion are
    resolved here, before the call, so unchanged blocks never repay.
    Structure, citations, answer keys, and checks stay in
    app/translation; this step only pays for the model call.
    """
    from app.agents import translator
    from app.llm.prompts import cache_key
    from app.translation import memory, segments

    step_key = str(entry["step_key"])
    block_type = str(entry.get("block_type", "reading"))
    content = dict(entry.get("content", {}))
    source_language = str(entry.get("source_language", "en"))
    target_language = str(entry["target_language"])
    course_id = str(entry.get("course_id", ""))
    node_id = str(entry.get("plan_node_id", step_key))
    glossary = await _translation_glossary(session, run_id=run_id, target_language=target_language)
    wanted = segments.extract_segments(block_type, content)
    key_segs = segments.key_segments(block_type, dict(entry.get("answer_key", {})))
    planted_id = segments.planted_segment_id(content, dict(entry.get("answer_key", {})))
    cached: dict[str, str] = {}
    missing: list[dict[str, str]] = []
    for segment in wanted:
        if segment["id"] == planted_id:
            continue
        hit = await memory.tm_lookup(
            session,
            source_text=segment["text"],
            source_language=source_language,
            target_language=target_language,
            course_id=course_id,
        )
        if hit is None:
            missing.append(segment)
        else:
            cached[segment["id"]] = hit
    batches = [missing] if missing else []
    if planted_id is not None:
        batches.append([seg for seg in wanted if seg["id"] == planted_id])
    if key_segs:
        batches.append(key_segs)
    # A block held by an earlier translation run re-asks the model with
    # variant+1: the reply store would otherwise re-serve the held reply.
    variant_offset = 0
    if entry.get("version_id"):
        held_row = (
            await session.execute(
                sa.text(
                    "SELECT tb.status FROM translation_blocks tb"
                    " JOIN course_translations ct ON ct.id = tb.translation_id"
                    " WHERE ct.course_version_id = :version AND ct.language = :lang"
                    " AND tb.plan_node_id = :node"
                ),
                {
                    "version": str(entry["version_id"]),
                    "lang": target_language,
                    "node": node_id,
                },
            )
        ).first()
        if held_row is not None and str(held_row[0]) in ("held", "failed"):
            variant_offset = 1
    if not batches:
        claim = await claim_step(
            session,
            run_id=run_id,
            step_key=step_key,
            input_digest=input_digest_of(
                {"kind": "translate", "node": node_id, "lang": target_language}
            ),
        )
        if claim["reused_output"] is None:
            await finish_step(
                session,
                step_id=claim["step_id"],
                output={
                    "translations": cached,
                    "planted_ids": [planted_id] if planted_id else [],
                },
            )
        await session.commit()
        return
    prompts = [
        translator.build_translator_prompt(
            segments=batch,
            source_language=source_language,
            target_language=target_language,
            glossary=glossary,
            course_id=course_id,
        )
        for batch in batches
    ]
    digest = step_digest(
        agent="translator",
        prompt=json.dumps(prompts, ensure_ascii=False, sort_keys=True),
        upstream={"node": node_id},
    )
    claim = await claim_step(session, run_id=run_id, step_key=step_key, input_digest=digest)
    provider = provider_factory(session)
    if claim["reused_output"] is not None:
        output = dict(claim["reused_output"])
    else:
        fresh: dict[str, str] = {}

        def _check_batch(answer: dict[str, Any], expected: set[str]) -> list[str]:
            return translator.check_translator_output(answer, expected_ids=expected)

        try:
            for batch, prompt in zip(batches, prompts, strict=True):
                expected = {segment["id"] for segment in batch}
                reply = await provider.generate_json(
                    agent="translator",
                    prompt=prompt,
                    schema=translator.schema(),
                    cache_key=cache_key("translator", block_type, course_id or node_id),
                    run_id=run_id,
                    step_id=claim["step_id"],
                    variant=int(claim["attempt"]) - 1 + variant_offset,
                    check=lambda answer, want=expected: _check_batch(answer, want),
                )
                for item in reply.get("segments", []):
                    fresh[str(item["id"])] = str(item["text"])
        except Exception as exc:  # a bad reply holds this block only
            await finish_step(
                session,
                step_id=claim["step_id"],
                output={
                    "translations": {**cached, **fresh},
                    "planted_ids": [planted_id] if planted_id else [],
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                },
            )
            await session.commit()
            return
        output = {
            "translations": {**cached, **fresh},
            "planted_ids": [planted_id] if planted_id else [],
        }
        await finish_step(session, step_id=claim["step_id"], output=output)
    await session.commit()


async def _translation_glossary(
    session: AsyncSession, *, run_id: UUID, target_language: str
) -> dict[str, str]:
    """Term table of this run's glossary step (empty when not done yet)."""
    row = (
        await session.execute(
            sa.text(
                "SELECT output FROM run_steps"
                " WHERE run_id = :run AND step_key = :key AND status = 'done'"
            ),
            {"run": str(run_id), "key": f"translator_glossary:{target_language}"},
        )
    ).first()
    if row is None or not isinstance(row[0], dict):
        return {}
    terms = row[0].get("terms")
    return {str(k): str(v) for k, v in terms.items()} if isinstance(terms, dict) else {}


GENERATION_HANDLERS: dict[str, str] = {
    "generate_block": "generate_block_step",
    "read_topic": "read_topic_step",
    "read_continuity": "read_continuity_step",
    "rework_blocks": "rework_blocks_step",
    "saboteur": "saboteur_step",
}


def _code_of_plain(marker: str | None) -> str | None:
    """Machine code for a marker already written as a plain sentence."""
    return {EMPTY_PLAN: "empty_plan", MODEL_DOWN: "model_down"}.get(marker or "")


def generation_handlers() -> dict[str, Any]:
    """Resolved generation handler map (worker drives generation with these)."""
    return {
        "generate_block": generate_block_step,
        "read_topic": read_topic_step,
        "read_continuity": read_continuity_step,
        "rework_blocks": rework_blocks_step,
        "saboteur": saboteur_step,
    }


TRANSLATION_HANDLERS: dict[str, str] = {
    "translate_block": "translate_block_step",
    "translate_glossary": "translate_glossary_step",
}


def translation_handlers() -> dict[str, Any]:
    """Resolved translation handler map (worker + resume drive these)."""
    return {
        "translate_block": translate_block_step,
        "translate_glossary": translate_glossary_step,
    }
