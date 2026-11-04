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
import re
from collections.abc import Callable
from typing import Any, NamedTuple
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.course.grounding import locate_verbatim
from app.course.handles import address_sort_key, handle_map
from app.errors import LLMError, UnparseableReplyError, ValidationError
from app.llm.registry import resolve_step
from app.runs.stop_text import (
    CAP_REACHED,
    EMPTY_PLAN,
    MODEL_DOWN,
    plain_marker,
    plain_step_error,
    plain_stop,
)

RUN_LOCK_PREFIX = "run:"
MAX_WEB_SEARCHES_PER_QUERY = 3
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
    the resolved model and effort from the registry — never a constant.
    The output budget stays out: a finished reply is not less valid
    because the next call may be longer (raising the reader budget
    mid-run re-read twenty finished topics on the 2026-11-02 resume).
    """
    model, effort, _max_tokens = resolve_step(agent, None, None, None)
    return input_digest_of(
        {
            "agent": agent,
            "prompt": prompt,
            "upstream": upstream or {},
            "model": model,
            "effort": effort,
        }
    )


__all__ = ["address_sort_key"]


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
    m = _re.match(r"stopped at ([A-Za-z_]+:[A-Za-z0-9_-]+):", clean)
    if m:
        return f"The run stopped at step {m.group(1)}. Resume to continue from there."
    if clean.startswith("no handler for step"):
        step = clean.removeprefix("no handler for step ").strip()
        return f"The run stopped at step {step}. Resume to continue from there."
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
    rows = (
        await session.execute(
            sa.text(
                "SELECT step_key, status, input_digest, attempt, error"
                " FROM run_steps WHERE run_id = :run ORDER BY name"
            ),
            {"run": str(run_id)},
        )
    ).all()
    # DAG order, never name order: "reader:m1-test" sorts before "reader:t1" by name
    order = {
        str(e.get("step_key", "")): i for i, e in enumerate(row[2] or []) if isinstance(e, dict)
    }
    steps = sorted(rows, key=lambda step: (order.get(str(step[0]), len(order)), str(step[0])))
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


async def list_runs(session: AsyncSession, *, course_id: UUID) -> list[dict[str, Any]]:
    """Runs of one course, newest first: id, status, kind, cost, started, plain stop."""
    rows = (
        await session.execute(
            sa.text(
                "SELECT r.id, r.status, r.kind, r.created_at, r.stop_marker,"
                " COALESCE((SELECT SUM(l.cost_usd) FROM llm_calls l WHERE l.run_id = r.id), 0)"
                " FROM runs r WHERE r.course_id = :course ORDER BY r.created_at DESC, r.id"
            ),
            {"course": str(course_id)},
        )
    ).all()
    return [
        {
            "id": str(row[0]),
            "status": row[1],
            "kind": row[2],
            "created_at": row[3].isoformat(),
            "stop_marker": plain_marker(row[4])[0],
            "cost_so_far": str(row[5]),
        }
        for row in rows
    ]


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
        span = locate_verbatim(text, quote)
        if span is None:
            holds.append(f"claim {claim}: quote is not verbatim in its passage")
            continue
        start, end = span
        if (
            int(citation.get("quote_start", start)) != start
            or int(citation.get("quote_end", end)) != end
        ):
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


def dag_block_nodes(dag: list[dict[str, Any]]) -> list[str]:
    """Plan node ids of the blocks a run's own DAG generates (the no-draft gate reads these)."""
    return sorted(
        {
            str((entry.get("skeleton") or {}).get("plan_node_id", ""))
            for entry in dag
            if entry.get("handler") == "generate_block" and entry.get("skeleton")
        }
        - {""}
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
            # a generation run cannot end done while a block is still draft
            run_row = (
                await session.execute(
                    sa.text("SELECT kind, course_id FROM runs WHERE id = :run"),
                    {"run": str(run_id)},
                )
            ).first()
            if run_row is not None and run_row[0] == "generation" and run_row[1] is not None:
                own_nodes = dag_block_nodes(dag)
                drafts = (
                    await session.execute(
                        sa.text(
                            "SELECT count(*) FROM blocks b JOIN topics t ON t.id = b.topic_id"
                            " JOIN modules m ON m.id = t.module_id"
                            " WHERE m.course_id = :c AND b.status = 'draft'"
                            " AND b.plan_node_id = ANY(:nodes)"
                        ),
                        {"c": str(run_row[1]), "nodes": own_nodes},
                    )
                ).scalar()
                if drafts:
                    await stop_run(
                        session,
                        run_id=run_id,
                        marker="The run stopped: some blocks were never read. "
                        "Resume to continue from there.",
                    )
                    async with factory() as fresh:
                        return await get_run(fresh, run_id=run_id)
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
        from app.services.library import add_web_candidate

        seen_urls: set[str] = set()
        for gap in [str(g) for g in output["gaps"]][:MAX_WEB_SEARCHES_PER_QUERY]:
            # a gap is answered by a real search: every candidate URL comes from the
            # provider's web_search results, never from the model's memory
            for hit in await provider.search_web(
                agent="web_search", query=gap, run_id=run_id, step_id=claim["step_id"]
            ):
                if hit["url"] in seen_urls:
                    continue
                seen_urls.add(hit["url"])
                await add_web_candidate(
                    session,
                    course_id=UUID(str(entry["course_id"])),
                    query=gap,
                    url=hit["url"],
                    title=hit["title"],
                    snippet=hit["snippet"],
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
                " passages.source_id, passages.ts_config, passages.is_content, passages.ordinal"
                " FROM passages"
                " JOIN course_sources cs ON cs.source_id = passages.source_id"
                " AND cs.course_id = :course AND cs.approval = 'approved'"
                " JOIN sources s ON s.id = passages.source_id"
                " AND s.approval = 'approved'"
            ),
            {"course": str(course_id)},
        )
    ).all()
    from app.course.handles import numbered_passages

    numbered = numbered_passages(
        [
            {
                "passage_id": str(row[0]),
                "address": str(row[1]),
                "text": str(row[2]),
                "source_id": str(row[3]),
                "ts_config": str(row[4] or ""),
                "is_content": bool(row[5]) if len(row) > 5 else True,
                "ordinal": row[6] if len(row) > 6 else None,
            }
            for row in rows
        ]
    )
    picked: list[dict[str, Any]] = []
    budget = 0
    for passage in numbered:
        size = len(passage["text"])
        if picked and budget + size > ARCHITECT_SOURCE_CHAR_BUDGET:
            break
        picked.append(passage)
        budget += size
    if not picked:
        raise ValidationError("Add and approve at least one source before building a plan.")
    handle_to_passage = {passage["handle"]: passage["passage_id"] for passage in picked}
    passages = [
        {
            "handle": passage["handle"],
            "address_key": passage["address"],
            "text": passage["text"],
            "is_content": bool(passage.get("is_content", True)),
        }
        for passage in picked
    ]
    source_languages = sorted(
        {
            TS_CONFIG_TO_LANGUAGE.get(passage["ts_config"], passage["ts_config"])
            for passage in picked
            if passage.get("ts_config")
        }
    )
    prompt = outline_prompt_of(profile, constraints, passages, language)
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
        output = await _plan_output(
            provider,
            run_id=run_id,
            step_id=claim["step_id"],
            variant=int(claim["attempt"]) - 1,
            profile=profile,
            constraints=constraints,
            prescription=prescription,
            passages=passages,
            language=language,
            source_languages=source_languages,
            handle_to_passage=handle_to_passage,
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
            uncovered=_uncovered_with_addresses(parsed, passages),
            scenario=dict(output.get("scenario", {})),
        )
        await write_plan_citations(session, version_map=version_map, refs=refs)
        from app.services.courses import record_source_passages, set_author_questions

        await record_source_passages(
            session,
            version_map=version_map,
            handles=_handle_passages(parsed, handle_to_passage),
        )
        await set_author_questions(
            session, course_id=course_id, questions=list(output.get("questions", []))
        )
        await finish_step(session, step_id=claim["step_id"], output=resolved)
    await session.commit()


MODULE_PLAN_OUTPUT_TOKENS = 20000


def outline_prompt_of(
    profile: dict[str, Any],
    constraints: list[dict[str, Any]],
    passages: list[dict[str, Any]],
    language: str,
) -> str:
    """The outline prompt of a plan step: what the step digest is computed over."""
    from app.agents import outline
    from app.course.sections import public_sections, sections_of

    return outline.build_prompt(
        profile, constraints, public_sections(sections_of(passages)), course_language=language
    )


def _scenario_lines(scenario: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Every topic of the course as one line: id, module, title, annotation."""
    return [
        {
            "plan_node_id": node,
            "module": row["module"],
            "title": row["title"],
            "annotation": row["annotation"],
        }
        for node, row in scenario.items()
    ]


async def _plan_output(
    provider: Any,
    *,
    run_id: UUID,
    step_id: UUID,
    variant: int,
    profile: dict[str, Any],
    constraints: list[dict[str, Any]],
    prescription: dict[str, list[str]],
    passages: list[dict[str, Any]],
    language: str,
    source_languages: list[str],
    handle_to_passage: dict[str, Any],
) -> dict[str, Any]:
    """The plan tree in two layers: the scenario, then one architect call per module.

    The outline call sees sections (titles, sizes, kinds, previews) and
    writes the scenario: learner, case, modules, annotated topics with
    their sections, leftovers and questions for the author. Each module
    is then planned by the architect over its own passages, each
    passage marked with the topic whose sections hold it, with the whole
    scenario in view; the module trees are merged. Every model call is
    a ledger row of its own; a repeat of this step replays done calls at
    no cost through the reply store.
    """
    from app.agents import architect, outline
    from app.course.sections import module_handles, public_sections, sections_of

    codes = list(profile.get("methodology_codes", []))
    handles = {str(p["handle"]) for p in passages}
    # the outline and every module call keep variant 0: an accepted reply is
    # served by the reply store on the next attempt, a rejected one was evicted
    variant = 0
    sections = sections_of(passages)
    section_ids = [s["id"] for s in sections]
    plan = await provider.generate_json(
        agent="outline",
        prompt=outline.build_prompt(
            profile, constraints, public_sections(sections), course_language=language
        ),
        schema=outline.schema(),
        run_id=run_id,
        step_id=step_id,
        variant=variant,
        check=lambda reply: outline.check_output(reply, section_ids),
    )
    plan = outline.normalise_ids(plan)
    scenario = outline.scenario_of(plan)
    by_handle = {str(p["handle"]): p for p in passages}
    for row in scenario.values():
        row["handles"] = module_handles(sections, list(row["sections"]))
        row["passages"] = {h: str(handle_to_passage[h]) for h in row["handles"]}
    modules = [m for m in plan.get("modules", []) if isinstance(m, dict)]
    merged_modules: list[dict[str, Any]] = []
    handoffs: list[dict[str, Any]] = []
    uncovered: list[dict[str, Any]] = []
    estimate = {"blocks": 0, "calls": 0, "tokens": 0}
    lines = _scenario_lines(scenario)
    for index, module in enumerate(modules):
        module_id = str(module.get("plan_node_id", f"m{index + 1}"))
        topic_rows = [
            {"plan_node_id": node, **row}
            for node, row in scenario.items()
            if row["module"] == module_id
        ]
        owner = {h: node for node, row in scenario.items() for h in row["handles"]}
        own_handles_list = module_handles(sections, list(module.get("sections", [])))
        own = [{**by_handle[h], "topic": owner.get(h, "")} for h in own_handles_list]
        if not own:
            continue
        scope = {
            "plan_node_id": module_id,
            "title": str(module.get("title", "")),
            "purpose": str(module.get("purpose", "")),
            "entry_knowledge": str(module.get("entry_knowledge", "")),
            "exit_knowledge": str(module.get("exit_knowledge", "")),
            "position": f"{index + 1} of {len(modules)}",
            "case": str(plan.get("case", "")),
            "learner": str(plan.get("learner", "")),
            "next_module": str(modules[index + 1].get("title", ""))
            if index + 1 < len(modules)
            else "",
            "topics": [
                {
                    "plan_node_id": row["plan_node_id"],
                    "title": row["title"],
                    "annotation": row["annotation"],
                    "introduces_terms": row["introduces_terms"],
                    "prepares_for": row["prepares_for"],
                    "requires": row["requires"],
                    "handles": row["handles"],
                }
                for row in topic_rows
            ],
        }
        own_handles = {str(p["handle"]) for p in own}
        # a module plan is 8–16k tokens of JSON: Spark with a 20k budget writes it in one
        # go for a fifth of Wonder's price (Wonder cut 8 of 8 module replies at 8k).
        # A rejected module plan is asked again with the failed checks as a Fix
        # list, up to CODE_REASKS times, instead of stopping the whole run.
        last_errors: list[str] = []

        def _check(
            reply: dict[str, Any],
            scope: Any = scope,
            own: Any = own,
            own_handles: Any = own_handles,
            sink: list[str] = last_errors,
        ) -> list[str]:
            sink[:] = _rejectable_architect_errors(
                reply,
                prescription,
                own_handles,
                methodology_codes=codes,
                passages=own,
                module_scope=scope,
            )
            return list(sink)

        fix_list: list[str] | None = None
        for reask in range(CODE_REASKS + 1):
            try:
                reply = await provider.generate_json(
                    agent="architect",
                    max_output_tokens=MODULE_PLAN_OUTPUT_TOKENS,
                    prompt=architect.build_prompt(
                        profile,
                        constraints,
                        prescription,
                        own,
                        course_language=language,
                        source_languages=source_languages,
                        module_scope=scope,
                        scenario=lines,
                        fix_list=fix_list,
                    ),
                    schema=architect.schema(),
                    run_id=run_id,
                    step_id=step_id,
                    variant=variant,
                    check=_check,
                )
                break
            except ValidationError:
                if reask == CODE_REASKS or not last_errors:
                    raise
                fix_list = list(last_errors)[:12]
        for reply_module in reply.get("modules", []):
            if not isinstance(reply_module, dict):
                continue
            merged_modules.append(reply_module)
            for topic in reply_module.get("topics", []):
                node = str(topic.get("plan_node_id", ""))
                if node not in scenario and str(topic.get("kind", "")) == "module_test":
                    scenario[node] = {
                        "module": module_id,
                        "title": str(topic.get("title", "")),
                        "annotation": "",
                        "introduces_terms": [],
                        "prepares_for": "",
                        "requires": [],
                        "sections": list(module.get("sections", [])),
                        "handles": list(own_handles_list),
                        "passages": {h: str(handle_to_passage[h]) for h in own_handles_list},
                    }
        handoffs += list((reply.get("through_line") or {}).get("handoffs", []))
        uncovered += list(reply.get("uncovered", []))
        for key in estimate:
            estimate[key] += int((reply.get("estimate_inputs") or {}).get(key, 0) or 0)
    for item in plan.get("uncovered", []):
        if not isinstance(item, dict):
            continue
        for handle in module_handles(sections, [str(item.get("section", ""))]):
            uncovered.append({"handle": handle, "reason": str(item.get("reason", ""))})
    # the 60 % rule was enforced per module over that module's passages; what no
    # module cites after that is the planner's declared leftover
    cited = _cited_handles(merged_modules)
    declared = _declared_handles(uncovered)
    for passage in passages:
        handle = str(passage["handle"])
        if passage.get("is_content", True) and handle not in cited and handle not in declared:
            uncovered.append({"handle": handle, "reason": "not cited by any module's plan"})
    merged = {
        "modules": merged_modules,
        "estimate_inputs": {k: max(v, 1) for k, v in estimate.items()},
        "through_line": {
            "case": str(plan.get("case", "")),
            "learner": str(plan.get("learner", "")),
            "handoffs": handoffs,
        },
        "uncovered": uncovered,
        "scenario": scenario,
        "questions": [
            {"key": str(q.get("key", "")), "question": str(q.get("question", ""))}
            for q in plan.get("questions", [])
            if isinstance(q, dict)
        ],
    }
    errors = _rejectable_architect_errors(merged, prescription, handles, methodology_codes=codes)
    if errors:
        raise ValidationError("architect output rejected: " + "; ".join(errors)[:800])
    return merged


def _fill_handoffs_from_notes(reply: dict[str, Any], module_scope: dict[str, Any] | None) -> None:
    """Every topic's through_line_note becomes its handoff when the list lacks one.

    The schema asks for the note on every topic; Wonder then omits the
    separate handoffs list for some topics (8 of 8 module replies on
    2026-11-02 cost $2.80 in rejections). The note is the same fact, so
    code copies it instead of paying again; a scoped module's last topic
    without any note hands the case to the next module by name.
    """
    topics = [
        t
        for m in reply.get("modules", [])
        if isinstance(m, dict)
        for t in m.get("topics", [])
        if isinstance(t, dict)
    ]
    if not topics:
        return
    through = reply.get("through_line")
    if not isinstance(through, dict):
        through = {"case": str((module_scope or {}).get("case", "")), "handoffs": []}
        reply["through_line"] = through
    handoffs = through.setdefault("handoffs", [])
    present = {
        str(h.get("topic_plan_node_id", ""))
        for h in handoffs
        if isinstance(h, dict) and str(h.get("note", "")).strip()
    }
    nxt = str((module_scope or {}).get("next_module", "")).strip()
    for index, topic in enumerate(topics):
        node = str(topic.get("plan_node_id", ""))
        if node in present:
            continue
        note = str(topic.get("through_line_note", "")).strip()
        if not note and module_scope is not None and index == len(topics) - 1:
            note = f"The case continues in the next module: {nxt}." if nxt else ""
        if note:
            handoffs.append({"topic_plan_node_id": node, "note": note})


def _block_signature(block: dict[str, Any]) -> tuple[str, str, tuple[str, ...]]:
    refs = tuple(
        sorted(
            str(r.get("handle", "")) for r in block.get("source_refs", []) if isinstance(r, dict)
        )
    )
    return (str(block.get("block_type", "")), " ".join(str(block.get("brief", "")).split()), refs)


def _drop_restated_blocks(reply: dict[str, Any], module_scope: dict[str, Any] | None) -> None:
    """A block restated in a topic that does not own its passages is dropped.

    The 2026-11-03 Python plan wrote module 3's first lesson as the
    whole module: the blocks of its three sibling lessons and of the
    module test again, same briefs, same passages. A block whose
    passages belong to another topic of the scope and whose type,
    brief and passages equal a block of that topic is the echo; the
    copy in the owning topic stays.
    """
    if not module_scope:
        return
    owner_of: dict[str, str] = {}
    for row in module_scope.get("topics", []) if isinstance(module_scope, dict) else []:
        for handle in row.get("handles", []) if isinstance(row, dict) else []:
            owner_of[str(handle)] = str(row.get("plan_node_id", ""))
    topics = [
        t
        for m in reply.get("modules", [])
        if isinstance(m, dict)
        for t in m.get("topics", [])
        if isinstance(t, dict)
    ]
    signatures = {
        str(t.get("plan_node_id", "")): {
            _block_signature(b) for b in t.get("blocks", []) if isinstance(b, dict)
        }
        for t in topics
    }
    for topic in topics:
        if str(topic.get("kind", "")) != "lesson":
            continue  # a module test cites lesson passages by design
        node = str(topic.get("plan_node_id", ""))
        kept: list[Any] = []
        for block in topic.get("blocks", []):
            if not isinstance(block, dict):
                continue
            signature = _block_signature(block)
            owners = {owner_of.get(h, "") for h in signature[2]} - {""}
            elsewhere = [o for o in owners if o != node and signature in signatures.get(o, set())]
            test_echo = str(topic.get("kind", "")) == "lesson" and any(
                signature in sig and str(t.get("kind", "")) == "module_test"
                for t in topics
                for sig in [signatures.get(str(t.get("plan_node_id", "")), set())]
            )
            if (owners and node not in owners and elsewhere) or test_echo:
                continue
            kept.append(block)
        if kept:
            topic["blocks"] = kept


_ROLE_BY_KIND = {
    "lesson": {"test": "check"},
    "module_test": {"check": "test", "practice": "test"},
}


def _repair_roles(reply: dict[str, Any]) -> None:
    """A role follows its topic kind: a lesson's `test` is a check, a test's `check` a test.

    The 2026-11-03 Python plan lost a $0.10 module reply to one lesson
    block typed `test`; the block was a check in every other respect.
    The prescription check still runs after this on what the model
    left out.
    """
    for module in reply.get("modules", []):
        if not isinstance(module, dict):
            continue
        for topic in module.get("topics", []):
            if not isinstance(topic, dict):
                continue
            table = _ROLE_BY_KIND.get(str(topic.get("kind", "")), {})
            for block in topic.get("blocks", []):
                if isinstance(block, dict):
                    block["role"] = table.get(
                        str(block.get("role", "")), str(block.get("role", ""))
                    )


def _renumber_blocks(reply: dict[str, Any]) -> None:
    """Block ids are `<topic id>-b<n>` in order, written by code: a reply never duplicates one.

    The 2026-11-02 Muse API plan lost a $0.16 module reply to one repeated
    block id; nothing at plan time refers to a block id but the tree.
    """
    for module in reply.get("modules", []):
        if not isinstance(module, dict):
            continue
        for topic in module.get("topics", []):
            if not isinstance(topic, dict):
                continue
            prefix = str(topic.get("plan_node_id", "")).strip()
            for index, block in enumerate(topic.get("blocks", []), 1):
                if isinstance(block, dict) and prefix:
                    block["plan_node_id"] = f"{prefix}-b{index}"


def _declared_handles(uncovered: list[dict[str, Any]]) -> set[str]:
    """Every handle an uncovered list names."""
    return {str(item.get("handle", "")) for item in uncovered if isinstance(item, dict)}


def _cited_handles(modules: list[dict[str, Any]]) -> set[str]:
    """Every source handle the module trees cite."""
    return {
        str(ref.get("handle", ""))
        for module in modules
        for topic in module.get("topics", [])
        for block in topic.get("blocks", [])
        for ref in block.get("source_refs", [])
        if isinstance(ref, dict)
    }


def _uncovered_with_addresses(parsed: Any, passages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The uncovered list with each passage's address and first words, never a bare handle."""
    by_handle = {str(p.get("handle", "")): p for p in passages}
    out: list[dict[str, Any]] = []
    for item in parsed.uncovered:
        passage = by_handle.get(item.handle, {})
        out.append(
            {
                "handle": item.handle,
                "reason": item.reason,
                "address": str(passage.get("address_key", "")),
                "excerpt": " ".join(str(passage.get("text", "")).split())[:80],
            }
        )
    return out


def _rejectable_architect_errors(
    reply: dict[str, Any],
    prescription: dict[str, list[str]],
    handles: set[str],
    methodology_codes: list[str] | None = None,
    passages: list[dict[str, Any]] | None = None,
    module_scope: dict[str, Any] | None = None,
) -> list[str]:
    """Architect errors that stop the run; unknown handles hold instead.

    A reply whose only errors are unknown source handles is accepted
    here (the reply is stored) and its blocks are held with a reason
    by _resolve_handles_tolerant; every other error rejects the reply
    so it never poisons the store. The Pydantic contract runs HERE,
    inside the provider check, so a reply it rejects (empty modules,
    invented block type) is never stored or replayed either.
    """
    from app.agents import architect
    from app.course.tree import ArchitectOutput

    _fill_handoffs_from_notes(reply, module_scope)
    _drop_restated_blocks(reply, module_scope)
    _renumber_blocks(reply)
    _repair_roles(reply)
    errors = architect.check_output(
        reply, prescription, handles, passages=passages, module_scope=module_scope
    )
    if errors and all("unknown handle" in error for error in errors):
        return []
    if errors:
        return errors
    try:
        ArchitectOutput.model_validate(reply)
    except Exception as exc:
        return [f"architect contract: {exc}"]
    if methodology_codes and "merrill_first_principles" in methodology_codes:
        types = [
            str(block.get("block_type", ""))
            for module in reply.get("modules", [])
            for topic in module.get("topics", [])
            for block in topic.get("blocks", [])
        ]
        if "spot_the_error" not in types:
            return ["Merrill plan needs a spot_the_error integration block"]
    return []


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


def _handle_passages(parsed: Any, handle_to_passage: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Block plan_node_id to {handle: passage id} for the handles this step knows."""
    out: dict[str, dict[str, str]] = {}
    for module in parsed.modules:
        for topic in module.topics:
            for block in topic.blocks:
                out[block.plan_node_id] = {
                    ref.handle: str(handle_to_passage[ref.handle])
                    for ref in block.source_refs
                    if ref.handle in handle_to_passage
                }
    return out


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


async def _written_by_this_run(session: AsyncSession, *, run_id: UUID, node_id: str) -> bool:
    """True when the block's current version was written by a done step of this run.

    Resume semantics: a block this run already wrote is finished for the main
    pass, whatever happened to its step digest since (a rework rewrote it under
    the same step key, a prompt or budget changed). Only the rework pays again.
    """
    row = (
        await session.execute(
            sa.text(
                "SELECT 1 FROM blocks b JOIN block_versions bv ON bv.id = b.current_version_id"
                " JOIN run_steps rs ON rs.id = bv.run_step_id AND rs.run_id = :run"
                " AND rs.status = 'done'"
                " JOIN topics t ON t.id = b.topic_id JOIN modules m ON m.id = t.module_id"
                " JOIN runs r ON r.id = rs.run_id AND r.course_id = m.course_id"
                " WHERE b.plan_node_id = :n LIMIT 1"
            ),
            {"run": str(run_id), "n": node_id},
        )
    ).first()
    return row is not None


async def _read_after_last_version(
    session: AsyncSession, *, run_id: UUID, step_key: str, topic_id: str
) -> bool:
    """True when this run's done reader step finished after every current version of the topic."""
    row = (
        await session.execute(
            sa.text(
                "SELECT 1 FROM run_steps rs JOIN runs r ON r.id = rs.run_id"
                " WHERE rs.run_id = :run AND rs.step_key = :key AND rs.status = 'done'"
                " AND rs.finished_at >= COALESCE((SELECT max(bv.created_at) FROM blocks b"
                "  JOIN block_versions bv ON bv.id = b.current_version_id"
                "  JOIN topics t ON t.id = b.topic_id JOIN modules m ON m.id = t.module_id"
                "  WHERE m.course_id = r.course_id AND t.plan_node_id = :t), rs.finished_at)"
            ),
            {"run": str(run_id), "key": step_key, "t": topic_id},
        )
    ).first()
    return row is not None


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
    # DAG order, never row order: an unordered read flipped the order of
    # upstream texts between runs, changed every downstream prompt digest
    # and made the second identical run pay again.
    by_key = {str(key): output for key, output in rows}
    for key in sorted(by_key, key=deps.index):
        output = by_key[key]
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
    force_new: bool = False,
) -> tuple[str, str, bool] | None:
    """Write one block version; content never carries the answer key.

    Returns (block_id, version_id, created) of the course-scoped block
    so callers attach citations to THE version they just wrote, in
    THEIR course — never to whichever course shares the plan node id.
    A write equal to the block's current version (content and key) is
    not persisted: created is False, the version, its status and its
    citations stay as they are, so an identical re-run neither
    re-versions every block nor resets an author's accept. A rewrite
    (`force_new`) never skips: the author asked for a new answer, so an
    identical reply still becomes a new draft version that the reader
    then judges, and the block leaves `held` through that verdict.
    """
    brow = (
        await session.execute(
            sa.text(
                "SELECT b.id, b.current_version_id FROM blocks b"
                " JOIN topics tp ON tp.id = b.topic_id"
                " JOIN modules m ON m.id = tp.module_id"
                " WHERE m.course_id = :c AND b.plan_node_id = :n"
            ),
            {"c": course_id, "n": node_id},
        )
    ).first()
    if brow is None:
        return None
    block_id = str(brow[0])
    if brow[1] is not None:
        current = (
            await session.execute(
                sa.text(
                    "SELECT content = CAST(:content AS jsonb),"
                    " answer_key IS NOT DISTINCT FROM CAST(:key AS jsonb)"
                    " FROM block_versions WHERE id = :v"
                ),
                {
                    "content": json.dumps(content),
                    "key": json.dumps(answer_key) if answer_key is not None else None,
                    "v": str(brow[1]),
                },
            )
        ).one()
        if current[0] and current[1] and status == "draft" and not force_new:
            return block_id, str(brow[1]), False
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
    return block_id, str(vid), True


async def _mark_reader_stale(
    session: AsyncSession, *, run_id: UUID, course_id: str, node_id: str
) -> None:
    """A regenerated draft block makes its topic's finished read stale.

    The reader step's digest cannot see a block that was rewritten with
    identical text; resetting the stored digest forces the next drive
    to read the block again, so no regenerated block ends `draft`.
    """
    await session.execute(
        sa.text(
            "UPDATE run_steps SET input_digest = 'stale' WHERE run_id = :run"
            " AND status = 'done' AND step_key IN ("
            " SELECT 'reader:' || t.plan_node_id FROM blocks b"
            " JOIN topics t ON t.id = b.topic_id JOIN modules m ON m.id = t.module_id"
            " WHERE m.course_id = :c AND b.plan_node_id = :n)"
        ),
        {"run": str(run_id), "c": course_id, "n": node_id},
    )


def _spot_seed(node_id: str, variant: int = 0) -> int:
    """One seed per spot block and variant: picker, saboteur, and key all derive from it.

    The first generation is variant 0; every rewrite or rework passes
    the block's version count, so the seed advances: a new sentence, a
    new kind, a new position, still from ONE picker.
    """
    import hashlib as _hl

    return int(_hl.sha256(node_id.encode()).hexdigest()[:8], 16) + int(variant)


async def _verified_sentences(
    session: AsyncSession, *, handle_to_passage: dict[str, str]
) -> list[dict[str, Any]]:
    """Content-only verified sentences in handle order (picker pool).

    Non-content passages (reuse notice, source line) are dropped by
    their stored ingest flag and again by the text classifier.
    """
    from app.course.errorspot import is_content_text

    stored_all = await _stored_passage_texts(session, passage_ids=list(handle_to_passage.values()))
    flags: dict[str, bool] = {}
    if handle_to_passage:
        rows = (
            await session.execute(
                sa.text("SELECT id, is_content FROM passages WHERE id::text = ANY(:ids)"),
                {"ids": list(handle_to_passage.values())},
            )
        ).all()
        flags = {str(r[0]): bool(r[1]) for r in rows}
    verified: list[dict[str, Any]] = []
    for handle, pid in handle_to_passage.items():
        text = stored_all.get(pid, "")
        if not flags.get(pid, True) or not is_content_text(text):
            continue
        for sent in text.split(". "):
            if sent.strip():
                verified.append(
                    {
                        "sentence": sent.strip().rstrip(".") + ".",
                        "passage": handle,
                        "is_content": True,
                    }
                )
    return verified


async def _plant_spot_block(
    session: AsyncSession,
    *,
    course_id: str,
    node_id: str,
    handle_to_passage: dict[str, str],
    step_id: UUID,
    mutated: str | None = None,
    kind: str | None = None,
    pick: dict[str, Any] | None = None,
    run_id: UUID | None = None,
    variant: int = 0,
    force_new: bool = False,
    allow_fully_correct: bool = True,
    topic_terms: list[str] | None = None,
) -> None:
    """Code-planted spot-the-error from the ONE picker's pick.

    `pick` is the saboteur step's pick ({sentence, kind, passage});
    the key's true_sentence and passage are that pick's, by construction.
    Without a saboteur pick the planter seeds fully_correct itself.
    A planting failure or a forbidden fully_correct (the only task of
    a module test) is regenerated with a new seed inside the run,
    never left held as the module test's only task.
    """
    from app.course.errorspot import pick_sentence
    from app.course.errorspot import plant_error as _plant

    stored_all = await _stored_passage_texts(session, passage_ids=list(handle_to_passage.values()))
    verified = await _verified_sentences(session, handle_to_passage=handle_to_passage)
    exercise: dict[str, Any] | None = None
    last_exc: ValueError | None = None
    for attempt in range(4):
        seed = _spot_seed(node_id, variant + attempt)
        use_pick = pick
        if attempt > 0:
            # a retry is a new pick from the same topic-scoped pool, topic terms kept
            try:
                use_pick = pick_sentence(verified, seed=seed, topic_terms=topic_terms or None)
                use_pick["fully_correct"] = False
            except ValueError as exc:
                last_exc = exc
                continue
        try:
            candidate = _plant(
                verified=verified,
                reader_holds=[],
                seed=seed,
                mutated=mutated if attempt == 0 else None,
                kind=kind if attempt == 0 else None,
                pick=use_pick,
                fully_correct=(
                    bool(use_pick.get("fully_correct"))
                    if use_pick is not None
                    else (None if allow_fully_correct else False)
                ),
            )
        except ValueError as exc:
            last_exc = exc
            continue
        if candidate.get("fully_correct") and not allow_fully_correct:
            last_exc = ValueError("module test needs a planted error, not fully_correct")
            pick = None
            mutated = None
            kind = None
            continue
        exercise = candidate
        break
    if exercise is None:
        failure = last_exc or ValueError("no content sentence available to plant")
        reason = f"the exercise could not be planted: {failure}"[:500]
        await _persist_block(
            session,
            course_id=course_id,
            node_id=node_id,
            content={"text": ""},
            answer_key=None,
            step_id=step_id,
            status="held",
            hold_reason=reason,
        )
        await finish_step(
            session,
            step_id=step_id,
            output={"block_type": "spot_the_error", "held": True, "hold_reason": reason},
        )
        return
    pid = handle_to_passage.get(str(exercise.get("passage", "P1")), "")
    quote = str(exercise.get("true_sentence") or (pick or {}).get("sentence", ""))[:120]
    output: dict[str, Any] = {
        "block_type": "spot_the_error",
        "text": str(exercise.get("planted_sentence", "")),
        "sentences": exercise["sentences"],
        "citations": [{"handle": str(exercise.get("passage", "P1")), "quote": quote}],
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
    persisted = await _persist_block(
        session,
        course_id=course_id,
        node_id=node_id,
        content={"sentences": exercise["sentences"]},
        answer_key=key,
        step_id=step_id,
        status="draft",
        force_new=force_new,
    )
    if (
        persisted is not None
        and persisted[2]
        and pid
        and quote
        and quote in stored_all.get(pid, "")
    ):
        version_id = persisted[1]
        start = stored_all[pid].index(quote)
        await session.execute(
            sa.text(
                "INSERT INTO citations (block_version_id, passage_id, claim_path, quote,"
                " quote_start, quote_end, verified_by) VALUES (:v, :p, :c, :q, :s, :e, 'code')"
            ),
            {
                "v": version_id,
                "p": pid,
                "c": f"blocks.{node_id}",
                "q": quote,
                "s": start,
                "e": start + len(quote),
            },
        )
    if run_id is not None and persisted is not None and persisted[2]:
        await _mark_reader_stale(session, run_id=run_id, course_id=course_id, node_id=node_id)
    await finish_step(session, step_id=step_id, output=output)


def _typed_content(
    output: dict[str, Any], *, block_type: str, citations: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Persisted content + key per block type, validated by its schema.

    Content comes from the type's own fields (never collapsed to
    {text, body}); the key comes from the type's key fields and lives
    only in block_versions.answer_key. A reply that does not validate
    against its type schema returns errors — the caller holds it.
    """
    from importlib import import_module

    text = str(output.get("text", ""))
    answer = str(output.get("answer", ""))
    content: dict[str, Any] = {}
    key: dict[str, Any] = {"answer": answer}
    if str(output.get("block_type", "")) not in ("", block_type):
        return {}, {}, [f"reply block_type {output.get('block_type')!r} != {block_type!r}"]
    if block_type == "reading":
        # leftovers travel with the block: the author sees what the writer left out and why
        content = {
            "body": text,
            "citations": citations,
            "leftovers": [
                {"handle": str(x.get("handle", "")), "reason": str(x.get("reason", ""))}
                for x in output.get("leftovers", [])
                if isinstance(x, dict) and x.get("handle")
            ],
        }
    elif block_type == "figure":
        numbers = [n for n in output.get("numbers", []) if isinstance(n, (int, float))]
        content = {
            "caption": text,
            "numbers": numbers,
            "citation": citations[0] if citations else {},
        }
        key["source_numbers"] = numbers
    elif block_type == "diagram":
        raw_nodes: Any = output.get("nodes")
        nodes = [n for n in raw_nodes if isinstance(n, dict)] if isinstance(raw_nodes, list) else []
        node_id = {str(n.get("id", "")): f"n{i}" for i, n in enumerate(nodes)}
        raw_edges: Any = output.get("edges")
        edges = [e for e in raw_edges if isinstance(e, dict)] if isinstance(raw_edges, list) else []
        content = {
            "caption": text,
            "nodes": [
                {"id": f"n{i}", "label": str(n.get("label", ""))} for i, n in enumerate(nodes)
            ],
            "edges": [
                {
                    "from": node_id.get(str(e.get("from", "")), str(e.get("from", ""))),
                    "to": node_id.get(str(e.get("to", "")), str(e.get("to", ""))),
                    "label": str(e.get("label", "")),
                }
                for e in edges
            ],
            "citations": citations,
        }
    elif block_type == "choice":
        raw_options: Any = output.get("options")
        options: list[Any] = raw_options if isinstance(raw_options, list) else []
        content = {
            "stem": text,
            "options": [str(o.get("text", "")) if isinstance(o, dict) else str(o) for o in options],
            "citations": citations,
        }
        key["correct"] = [
            i for i, o in enumerate(options) if isinstance(o, dict) and o.get("correct")
        ]
        # "select all that apply" is part of the question, not of the key
        content["multiple"] = len(key["correct"]) > 1
    elif block_type == "matching":
        raw_pairs: Any = output.get("pairs")
        pairs: list[Any] = raw_pairs if isinstance(raw_pairs, list) else []
        content = {"pairs": pairs, "citations": citations}
        key["mapping"] = {
            str(p.get("left", "")): str(p.get("right", "")) for p in pairs if isinstance(p, dict)
        }
        rights = [str(p.get("right", "")) for p in pairs if isinstance(p, dict)]
        key["mapping_by_index"] = {
            str(i): rights.index(str(p.get("right", "")))
            for i, p in enumerate(pairs)
            if isinstance(p, dict)
        }
    elif block_type == "cloze":
        raw_blanks: Any = output.get("blanks")
        blanks = [str(b) for b in raw_blanks] if isinstance(raw_blanks, list) else []
        content = {"text": text, "blanks": ["___"] * len(blanks)}
        key["blanks"] = blanks
    elif block_type == "ordering":
        raw_steps: Any = output.get("steps")
        steps = [str(s) for s in raw_steps] if isinstance(raw_steps, list) else []
        content = {"prompt": text, "items": steps, "citations": citations}
        key["order_by_index"] = list(range(len(steps)))
    elif block_type == "scenario_practice":
        raw_items: Any = output.get("items")
        items: list[Any] = raw_items if isinstance(raw_items, list) else []
        content = {
            "scenario": text,
            "items": [
                {
                    "text": str(it.get("text", "")),
                    "handle": str(it.get("passage_id", it.get("handle", ""))),
                }
                for it in items
                if isinstance(it, dict)
            ],
            "citations": citations,
        }
        key["labels"] = {
            str(it.get("text", "")): str(it.get("tier", "")) for it in items if isinstance(it, dict)
        }
        key["labels_by_index"] = {
            str(i): str(it.get("tier", "")) for i, it in enumerate(items) if isinstance(it, dict)
        }
        key["passages_by_index"] = {
            str(i): str(it.get("passage_id", it.get("handle", "")))
            for i, it in enumerate(items)
            if isinstance(it, dict)
        }
    elif block_type == "spot_the_error":
        sentences = [str(s) for s in output.get("sentences", [])] or [text]
        content = {"sentences": sentences}
    else:
        content = {"body": text, "citations": citations}
    try:
        validate = import_module(f"app.blocks.{block_type}.schema").validate
    except Exception:
        return content, key, []
    return content, key, [*validate(content), *_key_errors(block_type, content, key)]


def _key_errors(block_type: str, content: dict[str, Any], key: dict[str, Any]) -> list[str]:
    """Per-type answer-key validation: a key a grader would misread holds the block.

    A choice with no correct option would score 1.0 for picking
    nothing; duplicate matching rights collapse the index key; a cloze
    whose blanks do not match its gaps, or a scenario item without a
    tier, grade against a key that is not the exercise.
    """
    if block_type == "choice":
        options = content.get("options")
        count = len(options) if isinstance(options, list) else 0
        correct = key.get("correct")
        if not isinstance(correct, list) or not correct:
            return ["choice key: at least one option must be correct"]
        if any(not isinstance(i, int) or not 0 <= i < count for i in correct):
            return ["choice key: a correct option is outside the options"]
    elif block_type == "matching":
        pairs = [p for p in content.get("pairs", []) if isinstance(p, dict)]
        rights = [str(p.get("right", "")).strip() for p in pairs]
        lefts = [str(p.get("left", "")).strip() for p in pairs]
        if len(set(rights)) != len(rights) or len(set(lefts)) != len(lefts):
            return ["matching key: each left and each right item must be unique"]
    elif block_type == "cloze":
        gaps = len(re.findall(r"_{3,}", str(content.get("text", ""))))
        blanks = key.get("blanks")
        if (
            not isinstance(blanks, list)
            or len(blanks) != gaps
            or any(not str(b).strip() for b in blanks)
        ):
            return [f"cloze key: {gaps} gaps need {gaps} non-empty blanks"]
    elif block_type == "ordering":
        from app.blocks.ordering.schema import MIN_STEPS

        steps = [str(s).strip() for s in content.get("items", [])]
        if len(steps) < MIN_STEPS or len(set(steps)) != len(steps) or any(not s for s in steps):
            return [f"ordering key: at least {MIN_STEPS} distinct non-empty steps are required"]
        if key.get("order_by_index") != list(range(len(steps))):
            return ["ordering key: the key must list every step once in authored order"]
    elif block_type == "scenario_practice":
        items = [i for i in content.get("items", []) if isinstance(i, dict)]
        labels = key.get("labels_by_index")
        if (
            not isinstance(labels, dict)
            or len(labels) != len(items)
            or any(not str(v).strip() for v in labels.values())
        ):
            return ["scenario key: every item needs a tier"]
    return []


def _own_topic_blocks(plan: dict[str, Any], node_id: str) -> set[str]:
    """Block ids of the topic that holds `node_id` in a scenario view (never a sibling topic's)."""
    for module in plan.get("modules", []) if isinstance(plan, dict) else []:
        for topic in module.get("topics", []) if isinstance(module, dict) else []:
            ids = {str(b.get("plan_node_id", "")) for b in topic.get("blocks", [])}
            if node_id in ids:
                return ids
    return {node_id}


async def _previous_handoffs(
    session: AsyncSession, *, course_id: str, topic_id: str
) -> list[dict[str, Any]]:
    """Handoffs the teach blocks of earlier topics of the same module wrote, in course order."""
    rows = (
        await session.execute(
            sa.text(
                "SELECT b.plan_node_id, bv.content ->> 'handoff' FROM blocks b"
                " JOIN topics t ON t.id = b.topic_id JOIN modules m ON m.id = t.module_id"
                " JOIN block_versions bv ON bv.id = b.current_version_id"
                " WHERE m.course_id = :c AND b.role = 'teach'"
                " AND m.id = (SELECT t2.module_id FROM topics t2 JOIN modules m2"
                "  ON m2.id = t2.module_id WHERE m2.course_id = :c AND t2.plan_node_id = :t)"
                " AND t.ordinal < (SELECT t3.ordinal FROM topics t3 JOIN modules m3"
                "  ON m3.id = t3.module_id WHERE m3.course_id = :c AND t3.plan_node_id = :t)"
                " ORDER BY t.ordinal, b.ordinal"
            ),
            {"c": course_id, "t": topic_id},
        )
    ).all()
    return [{"plan_node_id": str(node), "handoff": str(handoff or "")} for node, handoff in rows]


CODE_REASKS = 2
"""Re-asks a writer gets inside its step when a code check holds its draft."""


class _CodeVerdict(NamedTuple):
    holds: list[str]
    resolved_citations: list[dict[str, Any]]
    citation_pids: list[str]
    content: dict[str, Any]
    key: dict[str, Any] | None
    text: str
    handoff: str


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
    if not entry.get("rewrite") and await _written_by_this_run(
        session, run_id=run_id, node_id=node_id
    ):
        return
    block_type = str(entry.get("block_type", "reading"))
    course_id = str(entry.get("course_id", ""))
    upstream = await _upstream_outputs(session, run_id=run_id, entry=entry)
    sources = generate_block.ordered_sources(list(entry.get("sources", [])))
    handle_to_passage: dict[str, str] = handle_map(sources)
    merged_texts = {str(item.get("passage_id", "")): str(item.get("text", "")) for item in sources}
    # R2: a check/practice/test generator receives only the passages its
    # topic taught (module tests: any teach block of the module). The
    # subset keeps every passage's handle: the frozen plan names them.
    from app.course.grounding import taught_handles_for

    role = str(dict(entry.get("skeleton", {})).get("role", ""))
    is_question = agent == "block_exercise" or role in ("check", "practice", "test")
    taught_pids: list[str] = []
    if is_question:
        found = taught_handles_for(dict(entry.get("plan", {})), node_id)
        if found is not None:
            taught_pids = [handle_to_passage[h] for h in found[0] if h in handle_to_passage]
            if taught_pids:
                sources = [
                    item for item in sources if str(item.get("passage_id", "")) in set(taught_pids)
                ]
                handle_to_passage = handle_map(sources)
    own_nodes = _own_topic_blocks(dict(entry.get("plan", {})), node_id)
    previously = [
        {"plan_node_id": u["plan_node_id"], "handoff": str(u["output"].get("handoff", ""))}
        for u in upstream
        if u["step_key"].startswith("block_teach:") and u["plan_node_id"] not in own_nodes
    ]
    prompt_input: dict[str, Any] = {
        "agent": agent,
        "block_type": block_type,
        "course_id": course_id,
        "course_language": str(entry.get("course_language", "en")),
        "sources": sources,
        "plan": dict(entry.get("plan", {})),
        "previously": previously,
        "skeleton": dict(entry.get("skeleton", {"plan_node_id": node_id})),
        "upstream_texts": [
            {"plan_node_id": u["plan_node_id"], "text": u["text"]}
            for u in upstream
            if u["plan_node_id"] in own_nodes
        ],
    }
    prompt = generate_block.build_generation_prompt(**prompt_input)
    digest = step_digest(
        agent=agent,
        prompt=prompt,
        upstream={"node": node_id, "upstream": [u["output"] for u in upstream]},
    )
    claim = await claim_step(session, run_id=run_id, step_key=step_key, input_digest=digest)
    if block_type == "spot_the_error" and claim["reused_output"] is None:
        cited_ids = {str(pid) for pid in entry.get("cited_passage_ids", [])}
        if cited_ids:
            handle_to_passage = {h: p for h, p in handle_to_passage.items() if p in cited_ids}
        sab: dict[str, Any] = next(
            (u["output"] for u in upstream if u["step_key"].startswith("saboteur:")), {}
        )
        await _plant_spot_block(
            session,
            course_id=course_id,
            node_id=node_id,
            handle_to_passage=handle_to_passage,
            step_id=claim["step_id"],
            run_id=run_id,
            variant=int(entry.get("variant_offset", 0)),
            force_new=bool(entry.get("rewrite")),
            allow_fully_correct=bool(entry.get("allow_fully_correct", True)),
            topic_terms=_topic_terms(entry),
            mutated=str(sab["mutated"]) if sab.get("mutated") else None,
            kind=str(sab["kind"]) if sab.get("kind") else None,
            pick=(
                {
                    "sentence": str(sab["true_sentence"]),
                    "kind": str(sab["kind"]),
                    "passage": str(sab["passage"]),
                    "fully_correct": bool(sab.get("fully_correct")),
                }
                if sab.get("true_sentence")
                else None
            ),
        )
        await session.commit()
        return
    provider = provider_factory(session)
    if claim["reused_output"] is not None:
        output = dict(claim["reused_output"])
    else:
        from app.llm.prompts import cache_key

        # a first reply that fails the schema check or cannot be parsed is asked
        # again under the next variant (up to CODE_REASKS times); a model that
        # is down (LLMError) still stops the run, a cap (SpendCapReached) too
        output = None
        unusable: Exception | None = None
        for first_ask in range(CODE_REASKS + 1):
            try:
                output = await provider.generate_json(
                    agent=agent,
                    prompt=prompt,
                    schema=generate_block.schema_for(block_type),
                    cache_key=cache_key(agent, block_type, course_id),
                    run_id=run_id,
                    step_id=claim["step_id"],
                    variant=int(claim["attempt"])
                    - 1
                    + int(entry.get("variant_offset", 0))
                    + first_ask,
                    check=lambda reply: generate_block.check_block_output(reply, block_type),
                )
                break
            except (ValidationError, UnparseableReplyError) as bad:
                unusable = bad
        if output is None:
            if entry.get("rewrite") and unusable is not None:
                raise unusable
            reason = f"The writer returned no usable result after {CODE_REASKS + 1} tries."
            await _persist_block(
                session,
                course_id=course_id,
                node_id=node_id,
                content={"text": "", "handoff": ""},
                answer_key=None,
                step_id=claim["step_id"],
                status="held",
                hold_reason=reason,
            )
            await finish_step(
                session,
                step_id=claim["step_id"],
                output={"held": True, "hold_reason": reason, "error": str(unusable)[:500]},
            )
            await session.commit()
            return

        # code verdicts before acceptance: verbatim citations, coverage,
        # adjacency, assignment, language, shape. A draft that fails one is
        # not stored: the writer gets the lines back in the same step (a
        # Fix list) up to CODE_REASKS times; only what passes code reaches
        # the reader, who judges meaning, never a line code already knew.
        async def judge(output: dict[str, Any]) -> _CodeVerdict:
            from app.course.text_checks import decode_escapes_in, narration_holds, python_code_holds

            # a literal \\uXXXX in the prose is repaired, never a reason to ask again
            output = {
                **decode_escapes_in({k: v for k, v in output.items() if k != "citations"}),
                "citations": output.get("citations", []),
            }
            text = str(output.get("text", ""))
            stored = {
                **await _stored_passage_texts(
                    session, passage_ids=list(handle_to_passage.values())
                ),
                **{pid: merged for pid, merged in merged_texts.items() if merged},
            }
            holds: list[str] = []
            resolved_citations: list[dict[str, Any]] = []
            citation_pids: list[str] = []
            cited_handles: list[str] = []
            handle_of_pid = {pid: handle for handle, pid in handle_to_passage.items()}
            for cit in output.get("citations", []):
                handle = str(cit.get("passage_id", ""))
                pid = handle_to_passage.get(handle, handle)
                quote = str(cit.get("quote", ""))
                passage_text = stored.get(pid, "")
                span = locate_verbatim(passage_text, quote)
                if span is None:
                    holds.append(
                        _misplaced_quote_line(
                            handle, quote, stored=stored, handle_of_pid=handle_of_pid
                        )
                    )
                else:
                    start, end = span
                    # stored content carries the public handle only, never a UUID;
                    # the quote is the passage's own span (whitespace as the passage has it)
                    resolved_citations.append(
                        {
                            "handle": handle,
                            "quote": passage_text[start:end],
                            "quote_start": start,
                            "quote_end": end,
                        }
                    )
                    citation_pids.append(pid)
                    cited_handles.append(handle)
            # R1/R3: teach/example blocks carry the complete claim of every
            # cited passage (its list items) and never join two passages
            # into one paragraph without a handle marker between them.
            role = str(dict(entry.get("skeleton", {})).get("role", ""))
            is_reading = agent in ("block_teach", "block_example") or role in ("teach", "example")
            if is_reading and block_type == "diagram":
                from app.course.grounding import diagram_holds

                holds += diagram_holds(output, [stored.get(pid, "") for pid in citation_pids])
            if is_reading and block_type not in ("figure", "diagram"):
                # a figure or a diagram is drawn from a passage; its caption is not a reading
                from app.course.grounding import adjacency_holds, assignment_holds, coverage_holds

                cited_texts = {
                    handle: stored.get(pid, "")
                    for handle, pid in zip(cited_handles, citation_pids, strict=True)
                }
                holds += coverage_holds(text, cited_texts)
                holds += adjacency_holds(text, resolved_citations)
                holds += assignment_holds(
                    [str(h) for h in dict(entry.get("skeleton", {})).get("source_handles", [])],
                    cited=[str(c.get("passage_id", "")) for c in output.get("citations", [])],
                    leftovers=[x for x in output.get("leftovers", []) if isinstance(x, dict)],
                )
            if is_reading and block_type not in ("figure", "diagram"):
                holds += narration_holds(text)
            if block_type != "cloze":
                holds += python_code_holds(text)
            if block_type == "figure":
                from app.course.grounding import passage_kind

                cited_kinds = {passage_kind(stored.get(pid, "")) for pid in citation_pids}
                if cited_kinds and cited_kinds <= {"prose"}:
                    holds.append("figure: cited passage has no numbers or structure to draw")
            if is_question and block_type != "spot_the_error":
                from app.course.exercise_shape import exercise_holds
                from app.course.grounding import taught_holds

                if taught_pids:
                    taught_texts = [stored.get(pid, "") for pid in taught_pids]
                    outside = sorted(
                        {
                            h
                            for h, pid in zip(cited_handles, citation_pids, strict=True)
                            if pid not in taught_pids
                        }
                    )
                    if outside:
                        holds.append(
                            "not taught: cites passages outside what this topic taught"
                            f" ({', '.join(outside)})"
                        )
                else:
                    taught_texts = [stored.get(pid, "") for pid in citation_pids]
                holds += taught_holds(output, taught_texts, block_type)
                holds += exercise_holds(output, block_type, taught_texts)
            holds += check_language(text, course_language=str(entry.get("course_language", "en")))
            content, key, type_errors = _typed_content(
                output, block_type=block_type, citations=resolved_citations
            )
            holds += type_errors
            handoff = " ".join(str(output.get("handoff", "")).split())
            if content:
                content["handoff"] = handoff
            return _CodeVerdict(
                holds=list(dict.fromkeys(holds)),
                resolved_citations=resolved_citations,
                citation_pids=citation_pids,
                content=content,
                key=key,
                text=text,
                handoff=handoff,
            )

        verdict = await judge(output)
        reasks = 0
        while verdict.holds and reasks < CODE_REASKS:
            reasks += 1
            try:
                output = await provider.generate_json(
                    agent=agent,
                    prompt=generate_block.build_generation_prompt(
                        **prompt_input, fix_list=list(verdict.holds)
                    ),
                    schema=generate_block.schema_for(block_type),
                    cache_key=cache_key(agent, block_type, course_id),
                    run_id=run_id,
                    step_id=claim["step_id"],
                    variant=int(claim["attempt"])
                    - 1
                    + int(entry.get("variant_offset", 0))
                    + reasks,
                    check=lambda reply: generate_block.check_block_output(reply, block_type),
                )
            except ValidationError:
                # a re-ask that returns nothing usable never replaces the draft
                # it was asked to fix: the draft stays, held by its own lines
                break
            verdict = await judge(output)
        holds = verdict.holds
        resolved_citations = verdict.resolved_citations
        citation_pids = verdict.citation_pids
        content, key, text, handoff = verdict.content, verdict.key, verdict.text, verdict.handoff
        if reasks:
            output = {**output, "reasks": reasks}
        if holds:
            await _persist_block(
                session,
                course_id=course_id,
                node_id=node_id,
                content=content if content else {"text": text, "handoff": handoff},
                answer_key=None,
                step_id=claim["step_id"],
                status="held",
                hold_reason="; ".join(holds),
            )
            output = {**output, "held": True, "hold_reason": "; ".join(holds)}
            await finish_step(session, step_id=claim["step_id"], output=output)
            await session.commit()
            return
        persisted = await _persist_block(
            session,
            course_id=course_id,
            node_id=node_id,
            content=content,
            answer_key=key,
            step_id=claim["step_id"],
            status="draft",
            force_new=bool(entry.get("rewrite")),
        )
        # citation rows on THE version just written, in THIS course
        if persisted is not None and persisted[2]:
            version_id = persisted[1]
            for cit, pid in zip(resolved_citations, citation_pids, strict=True):
                await session.execute(
                    sa.text(
                        "INSERT INTO citations (block_version_id, passage_id, claim_path, quote,"
                        " quote_start, quote_end, verified_by)"
                        " VALUES (:v, :p, :c, :q, :s, :e, 'code')"
                    ),
                    {
                        "v": version_id,
                        "p": pid,
                        "c": f"blocks.{node_id}",
                        "q": cit["quote"],
                        "s": cit["quote_start"],
                        "e": cit["quote_end"],
                    },
                )
        if persisted is not None and persisted[2]:
            await _mark_reader_stale(session, run_id=run_id, course_id=course_id, node_id=node_id)
        await finish_step(session, step_id=claim["step_id"], output=output)
    await session.commit()


def _undecided_nodes(output: dict[str, Any]) -> set[str]:
    """Nodes the reader could not decide: no hold anywhere, yet not a clean pass.

    A block verdict of `unknown`, or a `pass` that leaves any rubric
    item `unknown`, is not a pass: it is asked once more, then held.
    """
    undecided: set[str] = set()
    for verdict in output.get("verdicts", []):
        items = verdict.get("items", {}) if isinstance(verdict.get("items"), dict) else {}
        verdicts = [v.get("verdict") for v in items.values() if isinstance(v, dict)]
        if verdict.get("verdict") == "hold" or "hold" in verdicts:
            continue
        if verdict.get("verdict") != "pass" or any(v != "pass" for v in verdicts):
            undecided.add(str(verdict.get("plan_node_id", "")))
    return undecided


def _merge_undecided(
    first: dict[str, Any], second: dict[str, Any], nodes: set[str]
) -> dict[str, Any]:
    """Second reply decides the undecided nodes; still-unknown ones become holds."""
    again = {str(v.get("plan_node_id", "")): v for v in second.get("verdicts", [])}
    merged: list[dict[str, Any]] = []
    for verdict in first.get("verdicts", []):
        node = str(verdict.get("plan_node_id", ""))
        if node not in nodes:
            merged.append(verdict)
            continue
        replacement = again.get(node, verdict)
        if node in _undecided_nodes({"verdicts": [replacement]}):
            items = (
                replacement.get("items", {}) if isinstance(replacement.get("items"), dict) else {}
            )
            evidence = next(
                (
                    f"{k}: {v.get('evidence', '')}"
                    for k, v in items.items()
                    if isinstance(v, dict) and v.get("verdict") != "pass"
                ),
                "",
            )
            replacement = {
                **replacement,
                "verdict": "hold",
                "hold_reason": f"reader could not decide (unknown twice): {evidence}"[:500],
            }
        merged.append(replacement)
    return {**first, "verdicts": merged}


def _assigned_handles(plan: dict[str, Any]) -> dict[str, list[str]]:
    """plan_node_id -> the handles the plan assigned to that block (the scenario view)."""
    out: dict[str, list[str]] = {}
    for module in plan.get("modules", []) if isinstance(plan, dict) else []:
        for topic in module.get("topics", []) if isinstance(module, dict) else []:
            for block in topic.get("blocks", []) if isinstance(topic, dict) else []:
                if isinstance(block, dict) and block.get("plan_node_id"):
                    out[str(block["plan_node_id"])] = [
                        str(h) for h in block.get("source_handles", []) if h
                    ]
    return out


def _misplaced_quote_line(
    handle: str, quote: str, *, stored: dict[str, str], handle_of_pid: dict[str, str]
) -> str:
    """The non-verbatim hold, naming the one passage that does carry the quote.

    8 of the Muse API course's 3,904 citations (2026-11-03) were real words
    under the wrong handle; the writer fixes a handle only when told which.
    """
    line = f"quote is not verbatim in its passage ({handle})"
    owners = sorted(
        {
            handle_of_pid[pid]
            for pid, text in stored.items()
            if pid in handle_of_pid and locate_verbatim(text, quote) is not None
        }
    )
    if len(owners) == 1 and owners[0] != handle:
        return f"{line}; it is verbatim in {owners[0]}: cite that handle"
    return line


def _hold_line(item_id: str, item: dict[str, Any]) -> str:
    """One hold reason the writer can act on: the reader's note, then its verbatim evidence."""
    note = " ".join(str(item.get("note", "")).split())
    evidence = str(item.get("evidence", ""))
    if note:
        return f"{item_id}: {note} (evidence: {evidence})"
    return f"{item_id}: {evidence}"


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
    if await _read_after_last_version(session, run_id=run_id, step_key=step_key, topic_id=topic_id):
        return
    upstream = await _upstream_outputs(session, run_id=run_id, entry=entry)
    course_id = str(entry.get("course_id", ""))
    if not course_id:
        run_course = await run_course_id(session, run_id=run_id)
        course_id = str(run_course) if run_course else ""
    # The reader judges the FULL stored block: learner content + answer
    # key (it grades answer_key), read course-scoped from the DB.
    db_blocks = (
        await session.execute(
            sa.text(
                "SELECT b.plan_node_id, b.block_type, bv.content, bv.answer_key"
                " FROM blocks b JOIN topics t ON t.id = b.topic_id"
                " JOIN modules m ON m.id = t.module_id"
                " LEFT JOIN block_versions bv ON bv.id = b.current_version_id"
                " WHERE m.course_id = :c AND t.plan_node_id = :t ORDER BY b.ordinal"
            ),
            {"c": course_id, "t": topic_id},
        )
    ).all()
    assigned = _assigned_handles(dict(entry.get("plan", {})))
    blocks: list[dict[str, Any]] = []
    for node, btype, content, answer_key in db_blocks:
        block: dict[str, Any] = {
            "plan_node_id": str(node),
            "block_type": str(btype),
            "source_handles": assigned.get(str(node), []),
            "content": dict(content or {}),
            "answer_key": dict(answer_key or {}),
        }
        if str(btype) == "spot_the_error":
            block["planted_index"] = int(dict(answer_key or {}).get("planted_index", -1))
        blocks.append(block)
    if not blocks:
        by_node = {u["plan_node_id"]: u for u in upstream}
        for item in entry.get("blocks", []):
            node = str(item.get("plan_node_id", ""))
            up = by_node.get(node, {})
            blocks.append({**item, "text": up.get("text", str(item.get("text", "")))})
    if entry.get("reread"):
        for block in blocks:
            block["reread_after_rework"] = True
    node_ids = {b["plan_node_id"] for b in blocks if b.get("plan_node_id")}
    run_dag = (
        await session.execute(
            sa.text("SELECT compiled_dag FROM runs WHERE id = :run"), {"run": str(run_id)}
        )
    ).scalar()
    own_nodes = set(dag_block_nodes([e for e in (run_dag or []) if isinstance(e, dict)]))
    prompt = reader.build_reader_prompt(
        topic_plan_node_id=topic_id,
        course_language=str(entry.get("course_language", "en")),
        sources=list(entry.get("sources", [])),
        plan=dict(entry.get("plan", {})),
        previously=await _previous_handoffs(session, course_id=course_id, topic_id=topic_id),
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
        # an item's evidence must exist verbatim in the block or a passage; else unknown
        topic_passages = list(entry.get("sources", []))
        output = reader.verify_evidence(output, blocks=blocks, passages=topic_passages)
        undecided = _undecided_nodes(output) & own_nodes if own_nodes else _undecided_nodes(output)
        if undecided:
            # `unknown` is not a verdict: ask once more with variant+1, then hold.
            second = await provider.generate_json(
                agent="reader",
                prompt=prompt,
                schema=reader.schema(),
                cache_key=cache_key("reader", topic_id, str(entry.get("course_id", ""))),
                run_id=run_id,
                step_id=claim["step_id"],
                variant=int(claim["attempt"]),
                check=lambda reply: reader.check_reader_output(reply, node_ids=node_ids),
            )
            second = reader.verify_evidence(second, blocks=blocks, passages=topic_passages)
            output = _merge_undecided(output, second, undecided)
        for verdict in output.get("verdicts", []):
            node = str(verdict.get("plan_node_id", ""))
            if own_nodes and node not in own_nodes:
                continue
            items = verdict.get("items", {}) if isinstance(verdict.get("items"), dict) else {}
            reasons = [
                _hold_line(k, v)
                for k, v in items.items()
                if isinstance(v, dict) and v.get("verdict") == "hold"
            ]
            if verdict.get("verdict") == "hold" or reasons:
                reason = "; ".join(reasons) or str(verdict.get("hold_reason") or "reader hold")
                await session.execute(
                    sa.text(
                        "UPDATE blocks SET status = 'held', hold_reason = :r"
                        " WHERE id IN (SELECT b.id FROM blocks b"
                        " JOIN topics t ON t.id = b.topic_id"
                        " JOIN modules m ON m.id = t.module_id"
                        " WHERE m.course_id = :c AND b.plan_node_id = :n)"
                        " AND (accepted_version_id IS NULL"
                        " OR accepted_version_id <> current_version_id)"
                    ),
                    {"r": reason, "c": course_id, "n": node},
                )
            elif verdict.get("verdict") == "pass":
                await session.execute(
                    sa.text(
                        "UPDATE blocks SET status = 'accepted', hold_reason = NULL"
                        " WHERE id IN (SELECT b.id FROM blocks b"
                        " JOIN topics t ON t.id = b.topic_id"
                        " JOIN modules m ON m.id = t.module_id"
                        " WHERE m.course_id = :c AND b.plan_node_id = :n)"
                        " AND status <> 'held'"
                    ),
                    {"c": course_id, "n": node},
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
    """Saboteur rewrite for non-numeric kinds only (code owns numbers).

    The ONE picker chooses sentence + kind by seed from content
    passages; the saboteur receives exactly that sentence and kind,
    and the answer key is later built from the same pick.
    """
    from app.agents import generate_block, saboteur
    from app.course.errorspot import mutate_claim, pick_sentence
    from app.llm.prompts import cache_key

    step_key = str(entry["step_key"])
    _, _, node_id = step_key.partition(":")
    if not entry.get("rewrite") and await _written_by_this_run(
        session, run_id=run_id, node_id=node_id
    ):
        return
    sources = generate_block.ordered_sources(list(entry.get("sources", [])))
    handle_to_passage: dict[str, str] = handle_map(sources)
    cited_ids = {str(pid) for pid in entry.get("cited_passage_ids", [])}
    if cited_ids:
        handle_to_passage = {h: p for h, p in handle_to_passage.items() if p in cited_ids}
    verified = await _verified_sentences(session, handle_to_passage=handle_to_passage)
    if entry.get("sentence"):
        pick = {
            "sentence": str(entry["sentence"]),
            "kind": str(entry.get("kind", "entity_swap")),
            "passage": str(entry.get("passage", "P1")),
        }
    else:
        topic_terms = _topic_terms(entry)
        base_seed = _spot_seed(node_id, int(entry.get("variant_offset", 0)))
        pick = pick_sentence(verified, seed=base_seed, topic_terms=topic_terms or None)
        if pick.get("fully_correct") and not bool(entry.get("allow_fully_correct", True)):
            for offset in range(1, 9):
                repick = pick_sentence(
                    verified, seed=base_seed + offset, topic_terms=topic_terms or None
                )
                if not repick.get("fully_correct"):
                    pick = repick
                    break
    sentence = str(pick["sentence"])
    kind = str(pick["kind"])
    digest = step_digest(
        agent="saboteur",
        prompt=f"{sentence}|{kind}",
        upstream={"key": step_key, "pick": pick},
    )
    claim = await claim_step(session, run_id=run_id, step_key=step_key, input_digest=digest)
    provider = provider_factory(session)
    if claim["reused_output"] is not None:
        output = dict(claim["reused_output"])
    elif pick.get("fully_correct"):
        # seeded fully-correct exercise: nothing to mutate, no model call
        output = {
            "sentence": sentence,
            "kind": kind,
            "mutated": "",
            "true_sentence": sentence,
            "passage": str(pick["passage"]),
            "fully_correct": True,
        }
        await finish_step(session, step_id=claim["step_id"], output=output)
    elif kind == "number_date":
        # code owns number/date swaps: no model call, same pick
        output = {
            "sentence": sentence,
            "kind": kind,
            "mutated": mutate_claim(sentence, kind="number_date"),
            "true_sentence": sentence,
            "passage": str(pick["passage"]),
        }
        await finish_step(session, step_id=claim["step_id"], output=output)
    else:
        prompt = saboteur.build_saboteur_prompt(
            sentence=sentence,
            kind=kind,
            course_language=str(entry.get("course_language", "en")),
            hold_reason=str(entry.get("hold_reason", "")),
        )

        def _check_pick(reply: dict[str, Any]) -> list[str]:
            errors = saboteur.check_saboteur_output(reply)
            if str(reply.get("sentence", "")) != sentence:
                errors.append("saboteur reply sentence differs from the pick")
            if str(reply.get("kind", "")) != kind:
                errors.append("saboteur reply kind differs from the pick")
            return errors

        output = await provider.generate_json(
            agent="saboteur",
            prompt=prompt,
            schema=saboteur.schema(),
            cache_key=cache_key("saboteur", kind, str(entry.get("course_id", ""))),
            run_id=run_id,
            step_id=claim["step_id"],
            variant=int(claim["attempt"]) - 1,
            check=_check_pick,
        )
        output = {**output, "true_sentence": sentence, "passage": str(pick["passage"])}
        await finish_step(session, step_id=claim["step_id"], output=output)
    await session.commit()


def _topic_terms(entry: dict[str, Any]) -> list[str]:
    """Content terms of the step's topic title and purpose (the picker's preference)."""
    from app.course.grounding import content_terms

    return content_terms(f"{entry.get('topic_title', '')} {entry.get('topic_purpose', '')}")


async def _version_count(session: AsyncSession, *, course_id: str, node: str) -> int:
    """How many versions a course's block already has (the next variant offset)."""
    return int(
        (
            await session.execute(
                sa.text(
                    "SELECT count(*) FROM block_versions bv JOIN blocks b ON b.id = bv.block_id"
                    " JOIN topics t ON t.id = b.topic_id JOIN modules m ON m.id = t.module_id"
                    " WHERE m.course_id = :c AND b.plan_node_id = :n"
                ),
                {"c": course_id, "n": node},
            )
        ).scalar_one()
    )


async def _held_blocks(session: AsyncSession, *, run_id: UUID) -> tuple[list[str], dict[str, str]]:
    """Plan node ids and reasons of the blocks held in the run's course."""
    rows = (
        await session.execute(
            sa.text(
                "SELECT plan_node_id, hold_reason FROM blocks WHERE status = 'held'"
                " AND topic_id IN (SELECT t.id FROM topics t"
                " JOIN modules m ON m.id = t.module_id"
                " JOIN runs r ON r.course_id = m.course_id WHERE r.id = :run)"
                " ORDER BY plan_node_id"
            ),
            {"run": str(run_id)},
        )
    ).all()
    return [str(r[0]) for r in rows], {str(r[0]): str(r[1] or "") for r in rows}


async def rework_blocks_step(
    session: AsyncSession,
    *,
    run_id: UUID,
    entry: dict[str, Any],
    provider_factory: Callable[[AsyncSession], Any],
) -> None:
    """Rework the held blocks, with the hold reason in the prompt.

    Order: the current code checks run over the held blocks first ($0: a
    hold from a check fixed since the write is not a reason to pay), then
    what is still held is rewritten, then every topic with a draft block is
    read again: the rewrites of this pass and any draft an earlier, stopped
    pass left unread.
    """
    from app.services.recheck import recheck_held

    step_key = str(entry["step_key"])
    named = [str(n) for n in entry.get("named", [])]
    course_of_run = await run_course_id(session, run_id=run_id)
    if not named:
        named, reasons = await _held_blocks(session, run_id=run_id)
    else:
        reasons = {str(k): str(v) for k, v in dict(entry.get("reasons", {})).items()}
    digest = input_digest_of({"kind": "rework", "named": named, "reasons": reasons})
    claim = await claim_step(session, run_id=run_id, step_key=step_key, input_digest=digest)
    if claim["reused_output"] is None:
        if course_of_run is not None and not entry.get("named"):
            await recheck_held(session, course_id=course_of_run)
            await session.commit()
            named, reasons = await _held_blocks(session, run_id=run_id)
        dag_row = (
            await session.execute(
                sa.text("SELECT compiled_dag FROM runs WHERE id = :run"), {"run": str(run_id)}
            )
        ).first()
        dag_list = dag_row[0] if dag_row is not None else []
        originals = {
            str(e.get("step_key", "")).partition(":")[2]: e
            for e in (dag_list or [])
            if isinstance(e, dict) and str(e.get("handler", "")) == "generate_block"
        }
        saboteurs = {
            str(e.get("step_key", "")).partition(":")[2]: e
            for e in (dag_list or [])
            if isinstance(e, dict) and str(e.get("handler", "")) == "saboteur"
        }
        reworked = []
        for node in named:
            original = originals.get(node)
            if original is None:
                continue
            gen_entry = {**original, "rewrite": True}
            skeleton = dict(gen_entry.get("skeleton", {"plan_node_id": node}))
            skeleton["hold_reason"] = reasons.get(node, "")
            skeleton["brief"] = f"{skeleton.get('brief', '')} Rewrite: {reasons.get(node, '')}"
            gen_entry["skeleton"] = skeleton
            failing = str(original.get("step_key", ""))
            try:
                if node in saboteurs:
                    # a reworked exercise is a NEW pick: the seed advances with the version count
                    versions = await _version_count(
                        session, course_id=str(course_of_run), node=node
                    )
                    gen_entry["variant_offset"] = versions
                    failing = str(saboteurs[node].get("step_key", ""))
                    await saboteur_step(
                        session,
                        run_id=run_id,
                        entry={
                            **saboteurs[node],
                            "rewrite": True,
                            "variant_offset": versions,
                            "hold_reason": reasons.get(node, ""),
                        },
                        provider_factory=provider_factory,
                    )
                    failing = str(original.get("step_key", ""))
                await generate_block_step(
                    session, run_id=run_id, entry=gen_entry, provider_factory=provider_factory
                )
                reworked.append(node)
            except (ValidationError, LLMError) as exc:
                # a rewrite the checks reject (no citation, no JSON after the
                # re-ask) holds THIS block with the reason and the rework goes
                # on; the 2026-11-02 big course stopped its whole rework step
                # and left 24 rewritten blocks unread over one such reply
                await session.rollback()
                reason = f"the rewrite did not pass the checks: {exc}"[:500]
                await session.execute(
                    sa.text(
                        "UPDATE blocks SET status = 'held', hold_reason = :r"
                        " WHERE id IN (SELECT b.id FROM blocks b"
                        " JOIN topics t ON t.id = b.topic_id"
                        " JOIN modules m ON m.id = t.module_id"
                        " WHERE m.course_id = :c AND b.plan_node_id = :n)"
                    ),
                    {"r": reason, "c": str(course_of_run), "n": node},
                )
                await session.execute(
                    sa.text(
                        "UPDATE run_steps SET status = 'done', output = CAST(:o AS jsonb),"
                        " error = NULL, finished_at = now() WHERE run_id = :run AND step_key = :key"
                    ),
                    {
                        "o": json.dumps({"held": True, "hold_reason": reason}),
                        "run": str(run_id),
                        "key": failing,
                    },
                )
                await session.commit()
                continue
            except Exception as exc:
                # the claimed step must not strand at running: mark it
                # failed with the marker, then let the driver stop the
                # (resumable) run instead of swallowing the error.
                await session.rollback()
                await session.execute(
                    sa.text(
                        "UPDATE run_steps SET status = 'failed', error = :error,"
                        " finished_at = now() WHERE run_id = :run AND step_key = :key"
                    ),
                    {
                        "error": f"{type(exc).__name__}: {exc}"[:2000],
                        "run": str(run_id),
                        "key": failing,
                    },
                )
                await session.commit()
                raise
        # every reworked block, and every draft an earlier pass left
        # unread, is read again before the run ends
        reread_topics: list[str] = []
        if course_of_run is not None:
            topic_rows = (
                await session.execute(
                    sa.text(
                        "SELECT DISTINCT t.plan_node_id FROM blocks b"
                        " JOIN topics t ON t.id = b.topic_id"
                        " JOIN modules m ON m.id = t.module_id"
                        " WHERE m.course_id = :c"
                        " AND (b.status = 'draft' OR b.plan_node_id = ANY(:nodes))"
                        " ORDER BY t.plan_node_id"
                    ),
                    {"c": str(course_of_run), "nodes": reworked},
                )
            ).all()
            reader_entries = {
                str(e.get("step_key", "")).partition(":")[2]: e
                for e in (dag_list or [])
                if isinstance(e, dict) and str(e.get("handler", "")) == "read_topic"
            }
            for (topic_node,) in topic_rows:
                reader_entry = reader_entries.get(str(topic_node))
                if reader_entry is None:
                    continue
                await read_topic_step(
                    session,
                    run_id=run_id,
                    entry={**reader_entry, "reread": True},
                    provider_factory=provider_factory,
                )
                reread_topics.append(str(topic_node))
        await finish_step(
            session,
            step_id=claim["step_id"],
            output={"reworked": reworked, "reread_topics": reread_topics},
        )
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
    from app.translation.variants import block_digest, latest_block_row

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
    # The variant that produced the block's ok translation is reused (the
    # reply store serves it at $0); a held or failed row re-asks with
    # variant+1 so a bad reply is never served again.
    variant_offset = 0
    previous = (
        await latest_block_row(
            session,
            course_id=UUID(course_id),
            language=target_language,
            plan_node_id=node_id,
        )
        if course_id
        else None
    )
    if previous is not None and previous["source_digest"] == block_digest(
        content, dict(entry.get("answer_key", {}))
    ):
        variant_offset = previous["variant"] + (0 if previous["status"] == "ok" else 1)
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
                    "variant": variant_offset,
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
                    "variant": variant_offset,
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                },
            )
            await session.commit()
            return
        output = {
            "translations": {**cached, **fresh},
            "planted_ids": [planted_id] if planted_id else [],
            "variant": variant_offset,
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
    return {
        EMPTY_PLAN: "empty_plan",
        MODEL_DOWN: "model_down",
        CAP_REACHED: "cap_reached",
    }.get(marker or "")


def generation_handlers() -> dict[str, Any]:
    """Resolved generation handler map (worker drives generation with these)."""
    return {
        "generate_block": generate_block_step,
        "read_topic": read_topic_step,
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


async def tutor_call(
    provider: Any,
    *,
    prompt: str,
    schema: dict[str, Any],
    check: Any,
    subject_user_id: UUID,
) -> dict[str, Any]:
    """The learner tutor's one model call (outside any run, logged with its subject).

    Caller: services/tutor.py ask. The reply is never stored for reuse
    (a subject-attributed call is wiped with the learner), and no run
    step exists: the tutor is a request, not a pipeline stage.
    """
    reply: dict[str, Any] = await provider.generate_json(
        agent="tutor",
        prompt=prompt,
        schema=schema,
        subject_user_id=subject_user_id,
        check=check,
    )
    return reply
