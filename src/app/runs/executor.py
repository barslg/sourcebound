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

from app.errors import LLMError, ValidationError

RUN_LOCK_PREFIX = "run:"

PLAN_HANDLERS: dict[str, str] = {
    "researcher": "plan_researcher_step",
    "architect": "plan_architect_step",
}


def input_digest_of(payload: dict[str, Any]) -> str:
    """Stable digest of one step's input for reuse detection."""
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:32]


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
                "INSERT INTO runs (status, compiled_dag, dag_digest, estimate)"
                " VALUES ('running', CAST(:dag AS jsonb), :digest,"
                " CAST(:estimate AS jsonb)) RETURNING id"
            ),
            {
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

    Returns {"step_id", "reused_output"}: done steps with the same
    input digest reuse their output (never repaid); done steps with a
    changed digest reset to pending so downstream reruns.
    """
    existing = (
        await session.execute(
            sa.text(
                "SELECT id, status, input_digest, output FROM run_steps"
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
        return {"step_id": UUID(str(row[0])), "reused_output": None}
    step_id, status, digest, output = existing
    if status == "done" and digest == input_digest:
        return {"step_id": UUID(str(step_id)), "reused_output": output}
    await session.execute(
        sa.text(
            "UPDATE run_steps SET status = 'running', input_digest = :digest,"
            " output = NULL, error = NULL, attempt = attempt + 1,"
            " started_at = now() WHERE id = :step"
        ),
        {"digest": input_digest, "step": str(step_id)},
    )
    await session.commit()
    return {"step_id": UUID(str(step_id)), "reused_output": None}


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


async def get_run(session: AsyncSession, *, run_id: UUID) -> dict[str, Any]:
    """One run with its steps in DAG order."""
    row = (
        await session.execute(
            sa.text(
                "SELECT status, compiled_dag, dag_digest, estimate, stop_marker"
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
    return {
        "id": str(run_id),
        "status": row[0],
        "compiled_dag": row[1],
        "dag_digest": row[2],
        "estimate": row[3],
        "stop_marker": row[4],
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
    """
    async with factory() as session:
        acquired = (
            await session.execute(
                sa.text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
                {"key": f"{RUN_LOCK_PREFIX}{run_id}"},
            )
        ).scalar()
        if not acquired:
            return await get_run(session, run_id=run_id)
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
            except LLMError as exc:
                await session.rollback()
                async with factory() as failed:
                    await failed.execute(
                        sa.text(
                            "UPDATE run_steps SET status = 'failed', error = :error,"
                            " finished_at = now() WHERE run_id = :run AND step_key = :key"
                        ),
                        {"error": str(exc)[:2000], "run": str(run_id), "key": step_key},
                    )
                    await stop_run(
                        failed,
                        run_id=run_id,
                        marker=f"retry policy exhausted at {step_key}: {exc}",
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
    language = str(entry.get("language", "en"))
    ts_config = str(entry.get("ts_config", "english"))
    retriever = get_retriever(str(entry.get("retriever", "fts")))
    found = await retriever.search(session, query=query, ts_config=ts_config, limit=8)
    handle_to_passage = {f"P{index}": item.passage_id for index, item in enumerate(found, 1)}
    handles = [
        {
            "handle": f"P{index}",
            "address_key": item.address_key,
            "text": item.text[:500],
        }
        for index, item in enumerate(found, 1)
    ]
    rephrased = researcher.rephrase_queries(query)
    prompt = researcher.build_prompt(query, rephrased, handles)
    digest = input_digest_of({"prompt": prompt, "query": query})
    claim = await claim_step(session, run_id=run_id, step_key=step_key, input_digest=digest)
    provider = provider_factory(session)
    if claim["reused_output"] is not None:
        output = dict(claim["reused_output"])
    else:
        output = await provider.generate_json(
            agent="researcher",
            prompt=prompt,
            schema=researcher.schema(),
            run_id=run_id,
            step_id=claim["step_id"],
        )
        errors = researcher.check_output(output, set(handle_to_passage))
        if errors:
            raise ValidationError(f"researcher output rejected: {errors[0]}")
        await researcher.record_pairs(
            session,
            query=query,
            query_lang=language,
            ranked=output["ranked"],
            handle_to_passage=handle_to_passage,
            ranker=retriever.name,
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
    profile = entry["profile"]
    constraints = entry.get("constraints", [])
    prescription = prescription_for(list(profile.get("methodology_codes", [])))
    rows = (
        await session.execute(
            sa.text(
                "SELECT id, address_key, text FROM passages WHERE source_id IN"
                " (SELECT id FROM sources WHERE approval = 'approved')"
                " ORDER BY source_id, address_key LIMIT 60"
            )
        )
    ).all()
    handle_to_passage = {f"P{index}": row[0] for index, row in enumerate(rows, 1)}
    passages = [
        {"handle": f"P{index}", "address_key": row[1], "text": str(row[2])[:500]}
        for index, row in enumerate(rows, 1)
    ]
    prompt = architect.build_prompt(profile, constraints, prescription, passages)
    digest = input_digest_of({"prompt": prompt})
    claim = await claim_step(session, run_id=run_id, step_key=step_key, input_digest=digest)
    provider = provider_factory(session)
    if claim["reused_output"] is not None:
        output = dict(claim["reused_output"])
    else:
        output = await provider.generate_json(
            agent="architect",
            prompt=prompt,
            schema=architect.schema(),
            run_id=run_id,
            step_id=claim["step_id"],
        )
        errors = architect.check_output(output, prescription)
        if errors:
            raise ValidationError(f"architect output rejected: {errors[0]}")
        parsed = ArchitectOutput.model_validate(output)
        resolved = _resolve_handles(parsed, handle_to_passage)
        await write_tree(
            session,
            course_id=course_id,
            tree=CourseTree(modules=parsed.modules),
            plan_digest=digest,
        )
        await finish_step(session, step_id=claim["step_id"], output=resolved)
    await session.commit()


def _resolve_handles(parsed: Any, handle_to_passage: dict[str, Any]) -> dict[str, Any]:
    """Map P-handles to passage ids for the stored step output."""
    dump: dict[str, Any] = json.loads(parsed.model_dump_json())
    for module in dump["modules"]:
        for topic in module["topics"]:
            for block in topic["blocks"]:
                block["source_refs"] = [
                    {"passage_id": str(handle_to_passage[ref["handle"]])}
                    for ref in block["source_refs"]
                ]
    return dump
