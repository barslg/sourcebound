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
                "INSERT INTO runs (status, course_id, compiled_dag, dag_digest, estimate)"
                " VALUES ('running', :course, CAST(:dag AS jsonb), :digest,"
                " CAST(:estimate AS jsonb)) RETURNING id"
            ),
            {
                "course": str(course_id),
                "dag": json.dumps(dag),
                "digest": digest,
                "estimate": json.dumps(estimate) if estimate is not None else None,
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


async def stop_run(session: AsyncSession, *, run_id: UUID, marker: str) -> None:
    """Stop a run with an exhausted-retry marker; resume continues later."""
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
                "SELECT status, course_id, compiled_dag, dag_digest, estimate, stop_marker"
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
    return {
        "id": str(run_id),
        "status": row[0],
        "course_id": str(row[1]) if row[1] is not None else None,
        "compiled_dag": row[2],
        "dag_digest": row[3],
        "estimate": row[4],
        "stop_marker": row[5],
        "cost_so_far": str(spent),
        "topics": await _run_topics(session, course_id=row[1]),
        "steps": [
            {
                "step_key": step[0],
                "status": step[1],
                "input_digest": step[2],
                "attempt": step[3],
                "error": step[4],
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
                        marker=f"stopped at {step_key}: {type(exc).__name__}: {exc}"[:2000],
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
