"""Lecture-first plan steps: the blueprint, every lecture at once, the code-made block plan.

Each step is one claim, one or two model calls through the reply store,
one corrector pass, one finish. Nothing here holds or re-asks: code lists
its remarks, the corrector decides, the result stands.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import blueprint as blueprint_agent
from app.agents import corrector as corrector_agent
from app.agents import lecture as lecture_agent
from app.agents.outline import normalise_ids
from app.course.handles import numbered_passages
from app.course.tree import BlueprintOutput, LectureOutput
from app.db import get_session_factory
from app.pipelines import lecture_first as pipeline
from app.runs.executor import (
    _persist_block,
    claim_step,
    finish_step,
    step_digest,
    write_plan_citations,
)

PARALLEL_LECTURES = 4


def _schema_only(model: Any) -> Callable[[dict[str, Any]], list[str]]:
    def check(reply: dict[str, Any]) -> list[str]:
        try:
            model.model_validate(reply)
        except (
            Exception
        ) as exc:  # the corrector gets the remarks; only an unusable shape is re-asked
            return [str(exc)[:300]]
        return []

    return check


async def _correct(
    provider: Any,
    *,
    stage: str,
    context: dict[str, Any],
    draft: dict[str, Any],
    remarks: list[str],
    stage_schema: dict[str, Any],
    language: str,
    run_id: UUID,
    step_id: UUID,
    cache_key: str | None = None,
) -> tuple[dict[str, Any], str]:
    """One corrector pass: the draft to keep and the corrector's note."""
    reply = await provider.generate_json(
        agent="corrector",
        prompt=corrector_agent.build_prompt(
            stage=stage, context=context, draft=draft, remarks=remarks, course_language=language
        ),
        schema=corrector_agent.schema(stage_schema),
        cache_key=cache_key,
        run_id=run_id,
        step_id=step_id,
        variant=0,
        check=corrector_agent.check_output,
    )
    return corrector_agent.apply(reply, draft), str(reply.get("notes", ""))


async def _step_output(session: AsyncSession, *, run_id: UUID, step_key: str) -> dict[str, Any]:
    """The stored output of one done step of this run, whichever step asks for it."""
    row = (
        await session.execute(
            sa.text(
                "SELECT output FROM run_steps WHERE run_id = :run AND step_key = :key"
                " AND status = 'done'"
            ),
            {"run": str(run_id), "key": step_key},
        )
    ).first()
    if row is None or not isinstance(row[0], dict):
        from app.errors import ValidationError

        raise ValidationError(f"step {step_key} has no stored output yet")
    return dict(row[0])


async def blueprint_step(
    session: AsyncSession,
    *,
    run_id: UUID,
    entry: dict[str, Any],
    provider_factory: Callable[[AsyncSession], Any],
) -> None:
    """The brief in, the course blueprint out: one writer call, one corrector pass."""
    language = str(entry.get("language", "en"))
    profile = dict(entry.get("profile", {}))
    brief = blueprint_agent.brief_of(list(entry.get("constraints", [])))
    prompt = blueprint_agent.build_prompt(profile, brief, course_language=language)
    digest = step_digest(agent="blueprint", prompt=prompt)
    claim = await claim_step(
        session, run_id=run_id, step_key=str(entry["step_key"]), input_digest=digest
    )
    if claim["reused_output"] is None:
        provider = provider_factory(session)
        reply = await provider.generate_json(
            agent="blueprint",
            prompt=prompt,
            schema=blueprint_agent.schema(),
            run_id=run_id,
            step_id=claim["step_id"],
            variant=0,
            check=_schema_only(BlueprintOutput),
        )
        plan = normalise_ids(reply)
        kept, note = await _correct(
            provider,
            stage="blueprint",
            context={"brief": brief},
            draft=plan,
            remarks=blueprint_agent.check_output(plan),
            stage_schema=blueprint_agent.schema(),
            language=language,
            run_id=run_id,
            step_id=claim["step_id"],
        )
        kept = normalise_ids(kept)
        if blueprint_agent.check_output(kept):
            kept = plan
        await finish_step(
            session, step_id=claim["step_id"], output={**kept, "brief": brief, "corrector": note}
        )
    await session.commit()


async def _one_lecture(
    provider_factory: Callable[[AsyncSession], Any],
    *,
    run_id: UUID,
    step_id: UUID,
    language: str,
    brief: dict[str, Any],
    picture: dict[str, Any],
    topics: list[dict[str, Any]],
    own: dict[str, Any],
    previous_steps: list[dict[str, Any]],
    gate: asyncio.Semaphore,
) -> dict[str, Any]:
    node = str(own["plan_node_id"])
    async with gate, get_session_factory()() as session:
        provider = provider_factory(session)
        prompt = lecture_agent.build_prompt(
            brief=brief,
            picture=picture,
            topics=topics,
            own=own,
            previous_steps=previous_steps,
            course_language=language,
        )
        reply = await provider.generate_json(
            agent="lecture",
            prompt=prompt,
            schema=lecture_agent.schema(),
            cache_key=f"lecture:{run_id}",
            run_id=run_id,
            step_id=step_id,
            variant=0,
            check=_schema_only(LectureOutput),
        )
        kept, note = await _correct(
            provider,
            stage="lecture",
            context={"brief": brief, **picture, "topic": own},
            draft=reply,
            remarks=lecture_agent.check_output(reply, node),
            stage_schema=lecture_agent.schema(),
            language=language,
            run_id=run_id,
            step_id=step_id,
            cache_key=f"corrector:{run_id}",
        )
        if _schema_only(LectureOutput)(kept):
            kept = reply
        return {**kept, "plan_node_id": node, "corrector": note}


async def lectures_step(
    session: AsyncSession,
    *,
    run_id: UUID,
    entry: dict[str, Any],
    provider_factory: Callable[[AsyncSession], Any],
) -> None:
    """Every topic's lecture at once from the blueprint alone; each with its corrector pass."""
    language = str(entry.get("language", "en"))
    plan = await _step_output(session, run_id=run_id, step_key=pipeline.BLUEPRINT_STEP)
    brief = dict(plan.get("brief", {}))
    picture = {"learner": str(plan.get("learner", "")), "case": str(plan.get("case", ""))}
    topics = blueprint_agent.topics_of(plan)
    digest = step_digest(
        agent="lecture", prompt=json.dumps({"plan": plan, "language": language}, sort_keys=True)
    )
    claim = await claim_step(
        session, run_id=run_id, step_key=str(entry["step_key"]), input_digest=digest
    )
    if claim["reused_output"] is None:
        gate = asyncio.Semaphore(PARALLEL_LECTURES)
        jobs = []
        for index, topic in enumerate(topics):
            previous_steps = [
                {
                    "plan_node_id": str(t.get("plan_node_id", "")),
                    "artefact_step": str(t.get("artefact_step", "")),
                }
                for t in topics[:index]
            ]
            jobs.append(
                _one_lecture(
                    provider_factory,
                    run_id=run_id,
                    step_id=claim["step_id"],
                    language=language,
                    brief=brief,
                    picture=picture,
                    topics=topics,
                    own={k: v for k, v in topic.items() if k != "module"},
                    previous_steps=previous_steps,
                    gate=gate,
                )
            )
        lectures = await asyncio.gather(*jobs)
        await finish_step(
            session,
            step_id=claim["step_id"],
            output={"lectures": {str(lec["plan_node_id"]): lec for lec in lectures}},
        )
    await session.commit()


async def _lecture_source(session: AsyncSession, *, course_id: UUID) -> UUID:
    """The course's one approved Lectures source, emptied of earlier paragraphs."""
    sha = pipeline.lecture_source_sha(str(course_id))
    source_id = (
        await session.execute(
            sa.text(
                "INSERT INTO sources (content_sha256, kind, media, title, approval)"
                " VALUES (:sha, 'upload', 'markdown', :title, 'approved')"
                " ON CONFLICT (content_sha256) DO UPDATE SET approval = 'approved' RETURNING id"
            ),
            {"sha": sha, "title": pipeline.LECTURE_SOURCE_TITLE},
        )
    ).scalar_one()
    await session.execute(
        sa.text("DELETE FROM modules WHERE course_id = :course"), {"course": str(course_id)}
    )
    await session.execute(
        sa.text("DELETE FROM passages WHERE source_id = :source"), {"source": str(source_id)}
    )
    await session.execute(
        sa.text(
            "INSERT INTO course_sources (course_id, source_id, approval)"
            " VALUES (:course, :source, 'approved')"
            " ON CONFLICT (course_id, source_id) DO UPDATE SET approval = 'approved'"
        ),
        {"course": str(course_id), "source": str(source_id)},
    )
    return UUID(str(source_id))


async def blockplan_step(
    session: AsyncSession,
    *,
    run_id: UUID,
    entry: dict[str, Any],
    provider_factory: Callable[[AsyncSession], Any],
) -> None:
    """Paragraphs become passages, the lecture the teach block; code types the exercises."""
    from app.course.methodologies import prescription_for
    from app.course.tree import validate_tree
    from app.errors import ValidationError
    from app.services.courses import record_source_passages, write_tree
    from app.services.library import ingest_passages

    _ = provider_factory
    course_id = UUID(str(entry["course_id"]))
    plan = await _step_output(session, run_id=run_id, step_key=pipeline.BLUEPRINT_STEP)
    lectures = dict(
        (await _step_output(session, run_id=run_id, step_key=pipeline.LECTURES_STEP))["lectures"]
    )
    digest = step_digest(
        agent="blockplan", prompt=json.dumps({"plan": plan, "lectures": lectures}, sort_keys=True)
    )
    claim = await claim_step(
        session, run_id=run_id, step_key=str(entry["step_key"]), input_digest=digest
    )
    if claim["reused_output"] is not None:
        await session.commit()
        return
    source_id = await _lecture_source(session, course_id=course_id)
    rows: list[dict[str, Any]] = []
    for topic in blueprint_agent.topics_of(plan):
        node = str(topic.get("plan_node_id", ""))
        for index, text in enumerate(lectures.get(node, {}).get("paragraphs", [])):
            if not str(text).strip():
                continue
            rows.append(
                {
                    "text": str(text),
                    "address": {
                        "kind": "md",
                        "heading_path": str(topic.get("title", "")).replace(">", "\u203a"),
                        "para": index + 1,
                        "topic": node,
                    },
                    "address_key": f"{node}/p{index + 1:03d}",
                    "is_content": True,
                }
            )
    await ingest_passages(session, source_id=source_id, passages=rows)
    stored = (
        await session.execute(
            sa.text(
                "SELECT id, address_key, text, ordinal, address FROM passages WHERE source_id = :s"
            ),
            {"s": str(source_id)},
        )
    ).all()
    numbered = numbered_passages(
        [
            {
                "passage_id": str(r[0]),
                "source_id": str(source_id),
                "address": str(r[1]),
                "text": str(r[2]),
                "ordinal": r[3],
                "is_content": True,
            }
            for r in stored
        ]
    )
    topic_of_pid = {str(r[0]): str(dict(r[4]).get("topic", "")) for r in stored}
    handles_by_topic: dict[str, list[str]] = {}
    handle_to_passage: dict[str, str] = {}
    text_of_handle: dict[str, str] = {}
    for passage in numbered:
        handle = str(passage["handle"])
        handle_to_passage[handle] = str(passage["passage_id"])
        text_of_handle[handle] = str(passage["text"])
        handles_by_topic.setdefault(topic_of_pid[str(passage["passage_id"])], []).append(handle)
    tree, scenario = pipeline.build_tree(plan, lectures, handles_by_topic)
    errors = validate_tree(tree, prescription_for([pipeline.METHODOLOGY]))
    if errors:
        raise ValidationError("block plan rejected: " + "; ".join(errors)[:800])
    for row in scenario.values():
        row["passages"] = {h: handle_to_passage[h] for h in row["handles"]}
    version_map = await write_tree(
        session,
        course_id=course_id,
        tree=tree,
        plan_digest=digest,
        run_step_id=claim["step_id"],
        scenario=scenario,
    )
    refs: dict[str, list[str]] = {}
    handles: dict[str, dict[str, str]] = {}
    for module_node in tree.modules:
        for topic_node in module_node.topics:
            for block in topic_node.blocks:
                refs[block.plan_node_id] = [handle_to_passage[r.handle] for r in block.source_refs]
                handles[block.plan_node_id] = {
                    r.handle: handle_to_passage[r.handle] for r in block.source_refs
                }
    await write_plan_citations(session, version_map=version_map, refs=refs)
    await record_source_passages(session, version_map=version_map, handles=handles)
    for module_node in tree.modules:
        for topic_node in module_node.topics:
            for block in topic_node.blocks:
                if block.role != "teach":
                    continue
                await _write_lecture_block(
                    session,
                    course_id=str(course_id),
                    node_id=block.plan_node_id,
                    handles=[r.handle for r in block.source_refs],
                    text_of_handle=text_of_handle,
                    handle_to_passage=handle_to_passage,
                    artefact_state=str(
                        lectures.get(topic_node.plan_node_id, {}).get("artefact_state", "")
                    ),
                    step_id=claim["step_id"],
                )
    await finish_step(
        session,
        step_id=claim["step_id"],
        output=json.loads(tree.model_dump_json()),
    )
    await session.commit()


async def _write_lecture_block(
    session: AsyncSession,
    *,
    course_id: str,
    node_id: str,
    handles: list[str],
    text_of_handle: dict[str, str],
    handle_to_passage: dict[str, str],
    artefact_state: str,
    step_id: UUID,
) -> None:
    """The lecture itself as the topic's accepted reading block, every paragraph cited verbatim."""
    citations: list[dict[str, Any]] = [
        {
            "handle": handle,
            "quote": text_of_handle[handle],
            "quote_start": 0,
            "quote_end": len(text_of_handle[handle]),
        }
        for handle in handles
    ]
    content = {
        "body": "\n\n".join(text_of_handle[h] for h in handles),
        "citations": citations,
        "leftovers": [],
        "handoff": " ".join(artefact_state.split())[:600],
    }
    persisted = await _persist_block(
        session,
        course_id=course_id,
        node_id=node_id,
        content=content,
        answer_key={"answer": ""},
        step_id=step_id,
        status="accepted",
        force_new=True,
    )
    if persisted is None:
        return
    for citation in citations:
        await session.execute(
            sa.text(
                "INSERT INTO citations (block_version_id, passage_id, claim_path, quote,"
                " quote_start, quote_end, verified_by)"
                " VALUES (:v, :p, :c, :q, :s, :e, 'code')"
            ),
            {
                "v": persisted[1],
                "p": handle_to_passage[citation["handle"]],
                "c": f"blocks.{node_id}",
                "q": citation["quote"],
                "s": citation["quote_start"],
                "e": citation["quote_end"],
            },
        )


def illustration_file(store_root: str, course_id: str, node: str) -> Path:
    """Where a topic's picture lives: <store>/illustrations/<course>/<topic>.png."""
    return Path(store_root) / "illustrations" / course_id / f"{node}.png"


async def _one_illustration(
    provider_factory: Callable[[AsyncSession], Any],
    *,
    run_id: UUID,
    step_id: UUID,
    course_id: str,
    node: str,
    prompt: str,
    store_root: str,
    gate: asyncio.Semaphore,
) -> tuple[str, str]:
    target = illustration_file(store_root, course_id, node)
    if await asyncio.to_thread(target.exists):
        return node, str(target)
    async with gate, get_session_factory()() as session:
        image = await provider_factory(session).generate_image(
            agent="illustrator", prompt=prompt, run_id=run_id, step_id=step_id
        )
    await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(target.write_bytes, image)
    return node, str(target)


async def illustrations_step(
    session: AsyncSession,
    *,
    run_id: UUID,
    entry: dict[str, Any],
    provider_factory: Callable[[AsyncSession], Any],
) -> None:
    """One simple picture per topic, all at once; the path lands in the topic's scenario row."""
    from app.config import get_settings

    course_id = str(entry["course_id"])
    plan = await _step_output(session, run_id=run_id, step_key=pipeline.BLUEPRINT_STEP)
    topics = blueprint_agent.topics_of(plan)
    nodes = [str(t.get("plan_node_id", "")) for t in topics]
    digest = step_digest(
        agent="illustrator", prompt=json.dumps({"course": course_id, "topics": nodes})
    )
    claim = await claim_step(
        session, run_id=run_id, step_key=str(entry["step_key"]), input_digest=digest
    )
    if claim["reused_output"] is None:
        store_root = str(get_settings().source_store_path)
        gate = asyncio.Semaphore(PARALLEL_LECTURES)
        jobs = []
        for topic in topics:
            prompt = pipeline.illustration_prompt(
                str(topic.get("title", "")), str(plan.get("case", ""))
            )
            jobs.append(
                _one_illustration(
                    provider_factory,
                    run_id=run_id,
                    step_id=claim["step_id"],
                    course_id=course_id,
                    node=str(topic.get("plan_node_id", "")),
                    prompt=prompt,
                    store_root=store_root,
                    gate=gate,
                )
            )
        done = await asyncio.gather(*jobs)
        for node, path in done:
            await session.execute(
                sa.text(
                    "UPDATE topics SET scenario = scenario"
                    " || jsonb_build_object('illustration', CAST(:p AS text))"
                    " WHERE plan_node_id = :n"
                    " AND module_id IN (SELECT id FROM modules WHERE course_id = :c)"
                ),
                {"p": path, "n": node, "c": course_id},
            )
        await finish_step(session, step_id=claim["step_id"], output={"illustrations": dict(done)})
    await session.commit()


def handlers() -> dict[str, Any]:
    """The lecture-first plan handlers, by DAG handler name."""
    return {
        "blueprint": blueprint_step,
        "lectures": lectures_step,
        "blockplan": blockplan_step,
        "illustrations": illustrations_step,
    }
