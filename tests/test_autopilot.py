"""Autopilot probes: the cap, the re-ask-then-hold rule, the 409s, the revive and the build loop."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from typing import Any
from uuid import UUID

import demo_t05
import httpx2
import pytest
import sqlalchemy as sa
from conftest import test_database_url as _db_url
from sqlalchemy.ext.asyncio import AsyncSession
from t06_helpers import cookie_for, http
from test_jobs_smoke import (
    _plan_actor,
    _plan_completed,
    _plan_course_with_source,
    _plan_mock_handler,
    _plan_settings,
)
from transport_guard import guarded_client

from app.db import create_session_factory
from app.errors import ConflictError, SpendCapReached
from app.providers.llm import MetaProvider
from app.runs import executor
from app.runs.stop_text import CAP_REACHED, plain_stop
from app.services import build, courses, planruns

GENERATOR_REPLY = {"block_type": "reading", "text": "x", "citations": [], "answer": "k"}


def _provider(session: AsyncSession, handler: Any) -> tuple[MetaProvider, Any]:
    settings = _plan_settings()
    client = guarded_client(settings, inner=httpx2.MockTransport(handler))
    return MetaProvider(session, settings, client, create_session_factory(_db_url())), settings


async def _spent(session: AsyncSession, run_id: UUID) -> Any:
    return (
        await session.execute(
            sa.text("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE run_id = :r"),
            {"r": str(run_id)},
        )
    ).scalar_one()


async def test_a_run_at_its_ceiling_makes_no_paid_call_but_still_replays_the_reply_store(
    t03_session: AsyncSession,
) -> None:
    from app.agents import generate_block

    _, course_id = await _plan_course_with_source(t03_session)
    run_id = await executor.new_run(t03_session, course_id=course_id, kind="generation", dag=[])
    await t03_session.commit()
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append("call")
        return httpx2.Response(200, json=_plan_completed(json.dumps(GENERATOR_REPLY)))

    provider, _ = _provider(t03_session, handler)
    schema = generate_block.schema_for("reading")
    first = await provider.generate_json(
        agent="block_teach", prompt="alpha", schema=schema, run_id=run_id
    )
    spent = await _spent(t03_session, run_id)
    assert spent > 0 and len(seen) == 1
    await t03_session.execute(
        sa.text("UPDATE runs SET spend_ceiling_usd = :c WHERE id = :r"),
        {"c": spent, "r": str(run_id)},
    )
    await t03_session.commit()
    with pytest.raises(SpendCapReached):
        await provider.generate_json(
            agent="block_teach", prompt="beta", schema=schema, run_id=run_id
        )
    assert len(seen) == 1
    replayed = await provider.generate_json(
        agent="block_teach", prompt="alpha", schema=schema, run_id=run_id
    )
    assert replayed == first and len(seen) == 1


async def test_a_run_below_its_ceiling_still_pays(t03_session: AsyncSession) -> None:
    from app.agents import generate_block

    _, course_id = await _plan_course_with_source(t03_session)
    run_id = await executor.new_run(t03_session, course_id=course_id, kind="generation", dag=[])
    await t03_session.execute(
        sa.text("UPDATE runs SET spend_ceiling_usd = 1 WHERE id = :r"), {"r": str(run_id)}
    )
    await t03_session.commit()
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append("call")
        return httpx2.Response(200, json=_plan_completed(json.dumps(GENERATOR_REPLY)))

    provider, _ = _provider(t03_session, handler)
    await provider.generate_json(
        agent="block_teach",
        prompt="gamma",
        schema=generate_block.schema_for("reading"),
        run_id=run_id,
    )
    assert seen == ["call"]


def test_the_cap_has_its_own_plain_sentence_and_code() -> None:
    assert plain_stop("block_teach:b1", "SpendCapReached", "raw") == (CAP_REACHED, "cap_reached")


async def test_a_run_stopped_by_the_cap_reports_the_cap_code(t03_session: AsyncSession) -> None:
    _, course_id = await _plan_course_with_source(t03_session)
    run_id = await executor.new_run(t03_session, course_id=course_id, kind="generation", dag=[])
    await executor.stop_run(t03_session, run_id=run_id, marker=CAP_REACHED)
    assert (await executor.get_run(t03_session, run_id=run_id))["stop_code"] == "cap_reached"


async def test_a_writer_whose_replies_never_pass_the_format_check_is_held_and_the_run_goes_on(
    t03_session: AsyncSession,
) -> None:
    settings = _plan_settings()
    demo = await demo_t05.setup_demo(
        t03_session, settings=settings, email=demo_t05.fresh_email("hold")
    )
    course_id = demo["course_id"]
    await demo_t05.write_demo_tree(t03_session, course_id=course_id)
    sources = demo_t05.demo_sources(demo["passages"])
    base = {"course_id": str(course_id), "course_language": "en", "sources": sources, "plan": {}}
    dag = [
        {
            "step_key": "block_teach:b1",
            "handler": "generate_block",
            "depends_on": [],
            "block_type": "reading",
            "skeleton": {"plan_node_id": "b1", "role": "teach", "block_type": "reading"},
            **base,
        },
        {
            "step_key": "reader:t1",
            "handler": "read_topic",
            "depends_on": ["block_teach:b1"],
            "blocks": [{"plan_node_id": "b1"}],
            **base,
        },
    ]
    run_id = await executor.new_run(t03_session, course_id=course_id, kind="generation", dag=dag)
    await t03_session.commit()
    reader_handler = demo_t05.mock_demo_handler()
    writer_calls: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        system = str(json.loads(request.content.decode("utf-8")).get("instructions", ""))
        if system.startswith("# Reader"):
            return reader_handler(request)
        writer_calls.append("call")
        empty = {"block_type": "reading", "text": "", "citations": [], "answer": ""}
        return httpx2.Response(200, json=_plan_completed(json.dumps(empty)))

    provider, _ = _provider(t03_session, handler)
    final = await executor.drive_run(
        create_session_factory(_db_url()),
        run_id=run_id,
        provider_factory=lambda session: provider,
        handlers=executor.generation_handlers(),
    )
    assert final["status"] == "done", final["stop_marker"]
    assert {s["step_key"]: s["status"] for s in final["steps"]} == {
        "block_teach:b1": "done",
        "reader:t1": "done",
    }
    block = (
        await t03_session.execute(
            sa.text("SELECT status, hold_reason FROM blocks WHERE plan_node_id = 'b1'")
        )
    ).one()
    assert block[0] == "held" and block[1]
    assert len(writer_calls) == executor.CODE_REASKS + 1
    variants = (
        await t03_session.execute(
            sa.text("SELECT variant FROM llm_calls WHERE run_id = :r AND agent = 'block_teach'"),
            {"r": str(run_id)},
        )
    ).scalars()
    assert sorted(variants) == [0, 1, 2]
    output = (
        await t03_session.execute(
            sa.text(
                "SELECT output FROM run_steps WHERE run_id = :r AND step_key = 'block_teach:b1'"
            ),
            {"r": str(run_id)},
        )
    ).scalar_one()
    assert output["held"] is True
    assert output["hold_reason"] == "The writer returned no usable result after 3 tries."


PLAN_AGENTS = ("# Researcher", "# Outline", "# Architect")
BUILD_CODES = frozenset({"course.plan", "course.generate", "course.edit", "run.start"})


def _build_handler(*, crash_on: str | None = None, question: bool = True) -> Any:
    """The model side: plan fixtures for the plan prompts, demo replies for writers and readers."""
    plan = _plan_mock_handler()
    writers = demo_t05.mock_demo_handler()
    state: dict[str, Any] = {"seen": [], "crashed": False}

    def handler(request: httpx2.Request) -> httpx2.Response:
        system = str(json.loads(request.content.decode("utf-8")).get("instructions", ""))
        state["seen"].append(system[:12])
        if crash_on and not state["crashed"] and system.startswith(crash_on):
            state["crashed"] = True
            raise asyncio.CancelledError
        if system.startswith(PLAN_AGENTS):
            reply = plan(request)
            if question and system.startswith("# Outline"):
                body = reply.json()
                text = body["output"][0]["content"][0]["text"]
                outline = json.loads(text)
                outline["questions"] = [{"key": "depth", "question": "How deep should it go?"}]
                body["output"][0]["content"][0]["text"] = json.dumps(outline)
                return httpx2.Response(200, json=body)
            return reply
        return writers(request)

    handler.state = state  # type: ignore[attr-defined]
    return handler


async def _queue_build(session: AsyncSession, user_id: UUID, course_id: UUID, **over: Any) -> UUID:
    args: dict[str, Any] = {"queries": ["literacy duty"], "cap_usd": Decimal("15")}
    args.update(over)
    operation = await build.submit_build(
        session,
        actor=_plan_actor(user_id, BUILD_CODES),
        course_id=course_id,
        defer=None,
        **args,
    )
    await session.commit()
    return operation


async def _run_build(session: AsyncSession, operation: UUID, handler: Any) -> dict[str, Any]:
    settings = _plan_settings()
    url = _db_url()
    ledger = create_session_factory(url)
    client = guarded_client(settings, inner=httpx2.MockTransport(handler))
    return await build.run_build_operation(
        session,
        operation,
        provider_factory=lambda s: MetaProvider(s, settings, client, ledger),
        session_factory=create_session_factory(url),
    )


async def _runs(session: AsyncSession, course_id: UUID) -> list[tuple[str, str]]:
    rows = await session.execute(
        sa.text("SELECT kind, status FROM runs WHERE course_id = :c ORDER BY created_at, id"),
        {"c": str(course_id)},
    )
    return [(str(r[0]), str(r[1])) for r in rows.all()]


async def test_one_build_plans_assumes_the_open_question_generates_and_ends_ready(
    t03_session: AsyncSession,
) -> None:
    user_id, course_id = await _plan_course_with_source(t03_session)
    operation = await _queue_build(t03_session, user_id, course_id)
    detail = await _run_build(t03_session, operation, _build_handler())
    assert detail["state"] == "ready", detail
    kinds = [kind for kind, _ in await _runs(t03_session, course_id)]
    assert kinds.count("plan") == 1 and kinds.count("generation") == 1
    assert kinds.count("rewrite") == 0
    assert all(status == "done" for _, status in await _runs(t03_session, course_id))
    answer = (
        await t03_session.execute(
            sa.text("SELECT answer FROM profile_constraints WHERE key = 'depth'")
        )
    ).scalar_one()
    assert answer == {"value": build.ASSUMED_SENTENCE, "assumed": True}
    assert await courses.blocking_questions(t03_session, course_id=course_id) == []
    status = (await build.build_status(t03_session, course_id=course_id))["build"]
    assert [a["key"] for a in status["assumed"]] == ["depth"]
    drafts = (
        await t03_session.execute(sa.text("SELECT count(*) FROM blocks WHERE status = 'draft'"))
    ).scalar_one()
    assert drafts == 0


async def test_a_tiny_cap_stops_the_build_with_the_cap_sentence_and_continue_reuses_the_run(
    t03_session: AsyncSession,
) -> None:
    from app.services.operations import _set_status

    user_id, course_id = await _plan_course_with_source(t03_session)
    operation = await _queue_build(t03_session, user_id, course_id, cap_usd=Decimal("0.0001"))
    with pytest.raises(build.BuildStopped) as stopped:
        await _run_build(t03_session, operation, _build_handler())
    assert stopped.value.code == "cap" and CAP_REACHED in str(stopped.value)
    plan_runs = [r for r in await _runs(t03_session, course_id) if r[0] == "plan"]
    assert plan_runs == [("plan", "stopped")]
    run_id = (
        await t03_session.execute(sa.text("SELECT id FROM runs WHERE kind = 'plan'"))
    ).scalar_one()
    assert (await executor.get_run(t03_session, run_id=run_id))["stop_code"] == "cap_reached"
    paid = (
        await t03_session.execute(
            sa.text("SELECT count(*) FROM llm_calls WHERE run_id = :r AND cost_usd > 0"),
            {"r": str(run_id)},
        )
    ).scalar_one()
    await _set_status(
        t03_session, operation, status="failed", progress=1.0, error=str(stopped.value)
    )
    await t03_session.commit()
    kept = (await build.build_status(t03_session, course_id=course_id))["build"]
    assert kept["state"] == "stopped" and kept["stop_code"] == "cap" and kept["events"]
    resumed = await _queue_build(
        t03_session, user_id, course_id, cap_usd=Decimal("15"), continue_build=True, queries=[]
    )
    detail = await _run_build(t03_session, resumed, _build_handler())
    assert detail["state"] == "ready"
    assert [r for r in await _runs(t03_session, course_id) if r[0] == "plan"] == [("plan", "done")]
    assert (
        await t03_session.execute(
            sa.text(
                "SELECT started_at FROM (SELECT params->>'started_at' AS started_at"
                " FROM operations WHERE kind = 'build_course') s GROUP BY started_at"
            )
        )
    ).all().__len__() == 1
    assert paid >= 1


async def test_a_build_whose_worker_died_is_revived_and_pays_no_done_step_twice(
    t03_session: AsyncSession,
) -> None:
    user_id, course_id = await _plan_course_with_source(t03_session)
    operation = await _queue_build(t03_session, user_id, course_id)
    with pytest.raises(asyncio.CancelledError):
        await _run_build(t03_session, operation, _build_handler(crash_on="# Outline"))
    await t03_session.execute(
        sa.text(
            "UPDATE operations SET status = 'running',"
            " updated_at = now() - interval '10 minutes' WHERE id = :o"
        ),
        {"o": str(operation)},
    )
    await t03_session.commit()
    run_id = (
        await t03_session.execute(sa.text("SELECT id FROM runs WHERE kind = 'plan'"))
    ).scalar_one()
    researcher_paid = (
        await t03_session.execute(
            sa.text(
                "SELECT count(*) FROM llm_calls WHERE run_id = :r AND agent = 'researcher'"
                " AND cost_usd > 0"
            ),
            {"r": str(run_id)},
        )
    ).scalar_one()
    assert researcher_paid >= 1
    queued: list[UUID] = []

    async def defer(op: UUID) -> None:
        queued.append(op)

    assert await build.revive_stranded_builds(t03_session, defer=defer) == [operation]
    assert queued == [operation]
    detail = await _run_build(t03_session, operation, _build_handler())
    assert detail["state"] == "ready"
    assert [r for r in await _runs(t03_session, course_id) if r[0] == "plan"] == [("plan", "done")]
    assert (
        await t03_session.execute(
            sa.text(
                "SELECT count(*) FROM llm_calls WHERE run_id = :r AND agent = 'researcher'"
                " AND cost_usd > 0"
            ),
            {"r": str(run_id)},
        )
    ).scalar_one() == researcher_paid


async def test_revive_needs_a_running_build_gone_quiet_and_a_free_lock(
    t03_session: AsyncSession,
) -> None:
    user_id, course_id = await _plan_course_with_source(t03_session)
    operation = await _queue_build(t03_session, user_id, course_id)
    queued: list[UUID] = []

    async def defer(op: UUID) -> None:
        queued.append(op)

    async def age(status: str, minutes: int) -> None:
        await t03_session.execute(
            sa.text(
                "UPDATE operations SET status = :s,"
                " updated_at = now() - make_interval(mins => :m) WHERE id = :o"
            ),
            {"s": status, "m": minutes, "o": str(operation)},
        )
        await t03_session.commit()

    await age("running", 1)
    assert await build.revive_stranded_builds(t03_session, defer=defer) == []
    await age("done", 10)
    assert await build.revive_stranded_builds(t03_session, defer=defer) == []
    await age("running", 10)
    engine = create_session_factory(_db_url()).kw["bind"]
    async with engine.connect() as holder:
        await holder.execute(
            sa.text("SELECT pg_advisory_lock(hashtext(:k))"), {"k": f"build:{course_id}"}
        )
        assert await build.revive_stranded_builds(t03_session, defer=defer) == []
        await holder.execute(
            sa.text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": f"build:{course_id}"}
        )
    assert queued == []
    assert await build.revive_stranded_builds(t03_session, defer=defer) == [operation]
    assert await build.revive_stranded_builds(t03_session, defer=defer) == []


async def test_while_a_build_is_live_every_other_submit_on_the_course_is_refused(
    t03_session: AsyncSession,
) -> None:
    user_id, course_id = await _plan_course_with_source(t03_session)
    actor = _plan_actor(
        user_id, frozenset({*BUILD_CODES, "operation.run", "course.edit", "run.retry"})
    )
    first = await _queue_build(t03_session, user_id, course_id)
    assert (await _run_build(t03_session, first, _build_handler()))["state"] == "ready"
    await t03_session.execute(
        sa.text("UPDATE operations SET status = 'done' WHERE id = :o"), {"o": str(first)}
    )
    await t03_session.commit()
    assert not await planruns.live_work_on(t03_session, course_id=course_id)
    await _queue_build(t03_session, user_id, course_id, cap_usd=Decimal("1"))
    assert await planruns.live_work_on(t03_session, course_id=course_id)
    refused: list[Any] = [
        build.submit_build(
            t03_session, actor=actor, course_id=course_id, queries=["x"], cap_usd=Decimal("1")
        ),
        planruns.submit_plan(t03_session, actor=actor, course_id=course_id, queries=["x"]),
        planruns.submit_generate(t03_session, actor=actor, course_id=course_id),
        planruns.submit_translate(t03_session, actor=actor, course_id=course_id, languages=["nb"]),
        courses.submit_rewrite_operation(
            t03_session,
            actor=actor,
            kind="rewrite_held_batch",
            params={"course_id": str(course_id), "block_ids": []},
        ),
    ]
    for call in refused:
        with pytest.raises(ConflictError):
            await call


async def test_the_build_routes_are_owner_scoped_and_a_live_build_blocks_a_block_rewrite(
    t03_session: AsyncSession,
) -> None:
    from t06_helpers import make_user

    from app.rbac.catalogue import sync_catalogue
    from app.rbac.roles import assign_role, create_role

    user_id, course_id = await _plan_course_with_source(t03_session)
    first = await _queue_build(t03_session, user_id, course_id)
    assert (await _run_build(t03_session, first, _build_handler()))["state"] == "ready"
    await t03_session.execute(
        sa.text("UPDATE operations SET status = 'done' WHERE id = :o"), {"o": str(first)}
    )
    await sync_catalogue(t03_session)
    await create_role(
        t03_session,
        name=f"builder-{first.hex[:8]}",
        permission_codes=["course.edit", "run.start", "course.plan", "course.generate"],
        actor=None,
    )
    stranger = await make_user(t03_session, "stranger")
    for account in (user_id, stranger):
        await assign_role(
            t03_session, account, f"builder-{first.hex[:8]}", scope_tags=[], actor=None
        )
    nobody = await make_user(t03_session, "nobody")
    await t03_session.commit()
    owner = await cookie_for(t03_session, user_id)
    url = f"/api/v1/courses/{course_id}/build"
    async with http(owner[0]) as client:
        headers = {"X-CSRF-Token": owner[1]}
        none = await client.get(url)
        assert none.status_code == 200
        assert none.json()["build"]["state"] == "ready"
        started = await client.post(url, json={"cap_usd": "5"}, headers=headers)
        assert started.status_code == 202, started.text
        seen = (await client.get(url)).json()["build"]
        assert seen["state"] == "building" and seen["cap_usd"] == "5"
        assert (await client.post(url, json={"cap_usd": "5"}, headers=headers)).status_code == 409
        block = (
            await t03_session.execute(
                sa.text(
                    "SELECT b.id FROM blocks b JOIN topics t ON t.id = b.topic_id"
                    " JOIN modules m ON m.id = t.module_id WHERE m.course_id = :c LIMIT 1"
                ),
                {"c": str(course_id)},
            )
        ).scalar_one()
        rewrite = await client.post(f"/api/v1/blocks/{block}/rewrite", headers=headers)
        assert rewrite.status_code == 409, rewrite.text
        assert (await client.post(url, json={"cap_usd": "0"}, headers=headers)).status_code == 422
        stopped = await client.post(f"{url}/stop", headers=headers)
        assert stopped.status_code == 200 and stopped.json() == {"ok": True}, stopped.text
        ended = (await client.get(url)).json()["build"]
        assert ended["state"] == "stopped" and ended["error"] == build.STOPPED_BY_AUTHOR
        assert (await client.post(f"{url}/stop", headers=headers)).status_code == 409
    other = await cookie_for(t03_session, stranger)
    async with http(other[0]) as client:
        assert (await client.get(url)).status_code == 404
        assert (
            await client.post(f"{url}/stop", headers={"X-CSRF-Token": other[1]})
        ).status_code == 404
    outsider = await cookie_for(t03_session, nobody)
    async with http(outsider[0]) as client:
        assert (await client.get(url)).status_code == 403


async def _insert_op(
    session: AsyncSession,
    course_id: UUID,
    user_id: UUID,
    *,
    kind: str,
    status: str,
    minutes: int,
    result: dict[str, Any] | None = None,
) -> UUID:
    params = {"course_id": str(course_id), "cap_usd": "15", "started_at": "2026-11-01T00:00:00Z"}
    row = await session.execute(
        sa.text(
            "INSERT INTO operations (kind, status, params, result, progress, actor_id, updated_at)"
            " VALUES (:k, :s, CAST(:p AS jsonb), CAST(:r AS jsonb), 0, :a,"
            " now() - make_interval(mins => :m)) RETURNING id"
        ),
        {
            "k": kind,
            "s": status,
            "p": json.dumps(params),
            "r": json.dumps(result) if result is not None else None,
            "a": str(user_id),
            "m": minutes,
        },
    )
    await session.commit()
    return UUID(str(row.scalar_one()))


async def test_an_operation_nothing_drives_no_longer_locks_the_course(
    t03_session: AsyncSession,
) -> None:
    user_id, course_id = await _plan_course_with_source(t03_session)
    actor = _plan_actor(user_id, frozenset({*BUILD_CODES, "operation.run"}))
    stale_queued = await _insert_op(
        t03_session, course_id, user_id, kind="build_course", status="queued", minutes=600
    )
    stale_running = await _insert_op(
        t03_session, course_id, user_id, kind="submit_generate", status="running", minutes=600
    )
    assert not await planruns.live_work_on(t03_session, course_id=course_id)
    await planruns.submit_plan(t03_session, actor=actor, course_id=course_id, queries=["x"])
    for op in (stale_queued, stale_running):
        await t03_session.execute(
            sa.text("UPDATE operations SET updated_at = now() WHERE id = :o"), {"o": str(op)}
        )
    await t03_session.commit()
    assert await planruns.live_work_on(t03_session, course_id=course_id)


async def test_a_quiet_build_whose_driver_still_holds_the_lock_keeps_the_course_busy(
    t03_session: AsyncSession,
) -> None:
    user_id, course_id = await _plan_course_with_source(t03_session)
    await _insert_op(
        t03_session, course_id, user_id, kind="build_course", status="running", minutes=10
    )
    assert not await planruns.live_work_on(t03_session, course_id=course_id)
    engine = create_session_factory(_db_url()).kw["bind"]
    async with engine.connect() as holder:
        await holder.execute(
            sa.text("SELECT pg_advisory_lock(hashtext(:k))"), {"k": f"build:{course_id}"}
        )
        assert await planruns.live_work_on(t03_session, course_id=course_id)
        await holder.execute(
            sa.text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": f"build:{course_id}"}
        )


async def test_a_stranded_running_operation_whose_run_is_live_still_counts(
    t03_session: AsyncSession,
) -> None:
    user_id, course_id = await _plan_course_with_source(t03_session)
    run_id = await executor.new_run(t03_session, course_id=course_id, kind="generation", dag=[])
    await t03_session.commit()
    await _insert_op(
        t03_session,
        course_id,
        user_id,
        kind="submit_generate",
        status="running",
        minutes=600,
        result={"run_id": str(run_id)},
    )
    await executor.stop_run(t03_session, run_id=run_id, marker="x")
    assert not await planruns.live_work_on(t03_session, course_id=course_id)
    await t03_session.execute(
        sa.text("UPDATE runs SET status = 'running' WHERE id = :r"), {"r": str(run_id)}
    )
    await t03_session.commit()
    assert await planruns.live_work_on(t03_session, course_id=course_id)


async def test_revive_re_queues_a_build_that_never_started_and_stop_ends_one_nothing_drives(
    t03_session: AsyncSession,
) -> None:
    user_id, course_id = await _plan_course_with_source(t03_session)
    operation = await _insert_op(
        t03_session, course_id, user_id, kind="build_course", status="queued", minutes=10
    )
    queued: list[UUID] = []

    async def defer(op: UUID) -> None:
        queued.append(op)

    assert await build.revive_stranded_builds(t03_session, defer=defer) == [operation]
    shown = (await build.build_status(t03_session, course_id=course_id))["build"]
    assert shown["state"] == "building" and shown["can_stop"] is True
    actor = _plan_actor(user_id, BUILD_CODES)
    await build.stop_build(t03_session, actor=actor, course_id=course_id)
    stopped = (await build.build_status(t03_session, course_id=course_id))["build"]
    assert stopped["state"] == "stopped" and stopped["error"] == build.STOPPED_BY_AUTHOR
    assert not await planruns.live_work_on(t03_session, course_id=course_id)


async def test_two_submits_at_once_make_one_build(t03_session: AsyncSession) -> None:
    user_id, course_id = await _plan_course_with_source(t03_session)
    actor = _plan_actor(user_id, BUILD_CODES)

    async def submit() -> str:
        async with create_session_factory(_db_url())() as session:
            try:
                await build.submit_build(
                    session, actor=actor, course_id=course_id, queries=["q"], cap_usd=Decimal("1")
                )
            except ConflictError:
                return "refused"
            return "queued"

    outcomes = sorted(await asyncio.gather(submit(), submit(), submit()))
    assert outcomes == ["queued", "refused", "refused"]


async def test_a_second_build_job_for_a_course_that_is_being_built_fails_instead_of_idling(
    t03_session: AsyncSession,
) -> None:
    user_id, course_id = await _plan_course_with_source(t03_session)
    await _insert_op(
        t03_session, course_id, user_id, kind="build_course", status="running", minutes=0
    )
    duplicate = await _insert_op(
        t03_session, course_id, user_id, kind="build_course", status="queued", minutes=0
    )
    engine = create_session_factory(_db_url()).kw["bind"]
    async with engine.connect() as holder:
        await holder.execute(
            sa.text("SELECT pg_advisory_lock(hashtext(:k))"), {"k": f"build:{course_id}"}
        )
        with pytest.raises(ConflictError, match="another build"):
            await _run_build(t03_session, duplicate, _build_handler())
        await holder.execute(
            sa.text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": f"build:{course_id}"}
        )


async def test_search_and_background_calls_are_refused_at_the_ceiling_like_a_json_call(
    t03_session: AsyncSession,
) -> None:
    from app.agents import generate_block

    _, course_id = await _plan_course_with_source(t03_session)
    run_id = await executor.new_run(t03_session, course_id=course_id, kind="plan", dag=[])
    await t03_session.execute(
        sa.text("UPDATE runs SET spend_ceiling_usd = 0 WHERE id = :r"), {"r": str(run_id)}
    )
    await t03_session.commit()
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append("call")
        return httpx2.Response(500)

    provider, _ = _provider(t03_session, handler)
    with pytest.raises(SpendCapReached):
        await provider.search_web(agent="researcher", query="literacy", run_id=run_id)
    with pytest.raises(SpendCapReached):
        await provider.start_background(
            agent="block_teach",
            prompt="p",
            schema=generate_block.schema_for("reading"),
            run_id=run_id,
        )
    assert seen == []


async def _drive_one_writer(
    session: AsyncSession, *, rewrite: bool, reply: dict[str, Any]
) -> dict[str, Any]:
    settings = _plan_settings()
    demo = await demo_t05.setup_demo(
        session, settings=settings, email=demo_t05.fresh_email("rw" if rewrite else "gen")
    )
    course_id = demo["course_id"]
    await demo_t05.write_demo_tree(session, course_id=course_id)
    base = {
        "course_id": str(course_id),
        "course_language": "en",
        "sources": demo_t05.demo_sources(demo["passages"]),
        "plan": {},
    }
    entry = {
        "step_key": "block_teach:b1",
        "handler": "generate_block",
        "depends_on": [],
        "block_type": "reading",
        "skeleton": {"plan_node_id": "b1", "role": "teach", "block_type": "reading"},
        **base,
    }
    if rewrite:
        entry["rewrite"] = True
        entry["variant_offset"] = 4
    run_id = await executor.new_run(session, course_id=course_id, kind="rewrite", dag=[entry])
    await session.commit()

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=_plan_completed(json.dumps(reply)))

    provider, _ = _provider(session, handler)
    return await executor.drive_run(
        create_session_factory(_db_url()),
        run_id=run_id,
        provider_factory=lambda s: provider,
        handlers=executor.generation_handlers(),
    )


async def test_a_rewrite_whose_replies_are_unusable_stops_the_run_instead_of_holding_quietly(
    t03_session: AsyncSession,
) -> None:
    empty = {"block_type": "reading", "text": "", "citations": [], "answer": ""}
    final = await _drive_one_writer(t03_session, rewrite=True, reply=empty)
    assert final["status"] == "stopped"
    assert {s["step_key"]: s["status"] for s in final["steps"]} == {"block_teach:b1": "failed"}
    variants = (
        await t03_session.execute(
            sa.text("SELECT variant FROM llm_calls WHERE run_id = :r"), {"r": str(final["id"])}
        )
    ).scalars()
    assert sorted(variants) == [4, 5, 6]


async def test_a_second_job_for_the_build_already_being_driven_answers_duplicate(
    t03_session: AsyncSession,
) -> None:
    user_id, course_id = await _plan_course_with_source(t03_session)
    operation = await _insert_op(
        t03_session, course_id, user_id, kind="build_course", status="running", minutes=0
    )
    engine = create_session_factory(_db_url()).kw["bind"]
    async with engine.connect() as holder:
        await holder.execute(
            sa.text("SELECT pg_advisory_lock(hashtext(:k))"), {"k": f"build:{course_id}"}
        )
        detail = await _run_build(t03_session, operation, _build_handler())
        await holder.execute(
            sa.text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": f"build:{course_id}"}
        )
    assert detail == {"state": "duplicate"}


async def test_a_step_that_fails_twice_in_a_row_stops_the_build_with_that_step(
    t03_session: AsyncSession,
) -> None:
    user_id, course_id = await _plan_course_with_source(t03_session)
    operation = await _queue_build(t03_session, user_id, course_id)
    inner = _build_handler()
    outline_calls: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        system = str(json.loads(request.content.decode("utf-8")).get("instructions", ""))
        if system.startswith("# Outline"):
            outline_calls.append("call")
            return httpx2.Response(200, json=_plan_completed("{}"))
        return inner(request)

    with pytest.raises(build.BuildStopped) as stopped:
        await asyncio.wait_for(_run_build(t03_session, operation, handler), timeout=60)
    assert stopped.value.code == "step_failed"
    row = (
        await t03_session.execute(
            sa.text("SELECT result FROM operations WHERE id = :o"), {"o": str(operation)}
        )
    ).scalar_one()
    assert row["resumes"] == 1 and row["stop_code"] == "step_failed"
    plan_run = (
        await t03_session.execute(sa.text("SELECT id FROM runs WHERE kind = 'plan'"))
    ).scalar_one()
    final = await executor.get_run(t03_session, run_id=plan_run)
    failed_keys = [s["step_key"] for s in final["steps"] if s["status"] == "failed"]
    assert row["last_failed_step"] == failed_keys[0] and failed_keys[0].startswith("architect")
    assert final["stop_marker"] and str(stopped.value) == final["stop_marker"]
    assert len(outline_calls) >= 2


async def test_the_authors_stop_during_a_run_ends_the_build_at_once(
    t03_session: AsyncSession,
) -> None:
    user_id, course_id = await _plan_course_with_source(t03_session)
    operation = await _queue_build(t03_session, user_id, course_id)
    inner = _build_handler()
    asked: list[str] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        system = str(json.loads(request.content.decode("utf-8")).get("instructions", ""))
        if system.startswith("# Researcher") and not asked:
            asked.append("stop")
            async with create_session_factory(_db_url())() as other:
                await other.execute(
                    sa.text("UPDATE runs SET stop_marker = 'cancel requested' WHERE kind = 'plan'")
                )
                await other.commit()
        return inner(request)

    with pytest.raises(build.BuildStopped) as stopped:
        await _run_build(t03_session, operation, handler)
    assert stopped.value.code == "cancelled" and str(stopped.value) == build.STOPPED_BY_AUTHOR
    assert [r for r in await _runs(t03_session, course_id) if r[0] == "plan"] == [
        ("plan", "stopped")
    ]


async def test_a_cancelled_run_stays_cancelled_when_the_build_is_revived_and_continue_lifts_it(
    t03_session: AsyncSession,
) -> None:
    user_id, course_id = await _plan_course_with_source(t03_session)
    operation = await _queue_build(t03_session, user_id, course_id)
    run_id = await planruns.start_plan_run(t03_session, course_id=course_id, queries=["x"])
    await executor.stop_run(t03_session, run_id=run_id, marker="cancel requested")
    handler = _build_handler()
    with pytest.raises(build.BuildStopped) as stopped:
        await _run_build(t03_session, operation, handler)
    assert stopped.value.code == "cancelled" and handler.state["seen"] == []
    from app.services.operations import _set_status

    await _set_status(t03_session, operation, status="failed", progress=1.0, error="stopped")
    await t03_session.commit()
    resumed = await _queue_build(t03_session, user_id, course_id, continue_build=True)
    assert (await _run_build(t03_session, resumed, _build_handler()))["state"] == "ready"


async def test_a_plan_run_left_by_an_earlier_attempt_is_not_resumed_with_this_forms_queries(
    t03_session: AsyncSession,
) -> None:
    user_id, course_id = await _plan_course_with_source(t03_session)
    old = await planruns.start_plan_run(t03_session, course_id=course_id, queries=["old topic"])
    await executor.stop_run(t03_session, run_id=old, marker="x")
    operation = await _queue_build(t03_session, user_id, course_id)
    assert (await _run_build(t03_session, operation, _build_handler()))["state"] == "ready"
    assert [r for r in await _runs(t03_session, course_id) if r[0] == "plan"] == [
        ("plan", "stopped"),
        ("plan", "done"),
    ]


async def test_the_model_staying_down_stops_the_build_after_three_waits(
    t03_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id, course_id = await _plan_course_with_source(t03_session)
    operation = await _queue_build(t03_session, user_id, course_id)
    real_sleep = asyncio.sleep
    waits: list[float] = []

    async def quick(delay: float, *args: Any) -> None:
        waits.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", quick)

    def down(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(503, json={"error": {"message": "overloaded"}})

    with pytest.raises(build.BuildStopped) as stopped:
        await asyncio.wait_for(_run_build(t03_session, operation, down), timeout=60)
    assert stopped.value.code == "model_down"
    assert [w for w in waits if w >= 60] == [60, 180, 540]


async def test_a_build_stops_the_moment_spend_equals_the_cap(t03_session: AsyncSession) -> None:
    from app.llm.calls import log_call

    user_id, course_id = await _plan_course_with_source(t03_session)
    run_id = await executor.new_run(t03_session, course_id=course_id, kind="plan", dag=[])
    await log_call(
        t03_session,
        agent="researcher",
        model="muse-spark-1.3",
        prompt_version="v1",
        input_tokens=1000,
        cached_tokens=0,
        output_tokens=500,
        duration_ms=10,
        request={"input": "q"},
        response={"ranked": []},
        run_id=run_id,
    )
    await t03_session.commit()
    spent = await build.build_spent(
        t03_session, course_id=course_id, started_at="2000-01-01T00:00:00Z"
    )
    assert spent > 0

    def check(cap: Decimal) -> build._Build:
        factory = create_session_factory(_db_url())
        return build._Build(
            operation_id=UUID(int=0),
            course_id=course_id,
            queries=[],
            cap=cap,
            started_at="2000-01-01T00:00:00Z",
            provider_factory=None,
            session_factory=factory,
        )

    operation = await _insert_op(
        t03_session, course_id, user_id, kind="build_course", status="running", minutes=0
    )
    at_cap = check(spent)
    at_cap.op = operation
    with pytest.raises(build.BuildStopped) as stopped:
        await at_cap.check_cap()
    assert stopped.value.code == "cap"
    above = check(spent + Decimal("0.000001"))
    above.op = operation
    assert await above.check_cap() == Decimal("0.000001")


async def test_the_service_refuses_a_zero_cap_and_a_continue_with_nothing_stopped(
    t03_session: AsyncSession,
) -> None:
    from app.errors import UnprocessableError, ValidationError

    user_id, course_id = await _plan_course_with_source(t03_session)
    actor = _plan_actor(user_id, BUILD_CODES)
    with pytest.raises(ValidationError):
        await build.submit_build(
            t03_session, actor=actor, course_id=course_id, queries=["q"], cap_usd=Decimal("0")
        )
    with pytest.raises(UnprocessableError):
        await build.submit_build(
            t03_session,
            actor=actor,
            course_id=course_id,
            queries=["q"],
            cap_usd=Decimal("1"),
            continue_build=True,
        )
    await _insert_op(t03_session, course_id, user_id, kind="build_course", status="done", minutes=0)
    with pytest.raises(UnprocessableError):
        await build.submit_build(
            t03_session,
            actor=actor,
            course_id=course_id,
            queries=["q"],
            cap_usd=Decimal("1"),
            continue_build=True,
        )


async def test_a_held_block_after_a_pass_that_released_another_gets_exactly_one_rewrite_run(
    t03_session: AsyncSession,
) -> None:
    user_id, course_id = await _plan_course_with_source(t03_session)
    operation = await _queue_build(t03_session, user_id, course_id)
    inner = _build_handler()
    order: list[str] = []
    asks: dict[str, int] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content.decode("utf-8"))
        system = str(body.get("instructions", ""))
        if system.startswith(
            ("# Researcher", "# Outline", "# Architect", "# Saboteur", "# Reader")
        ):
            return inner(request)
        skeleton = demo_t05._section(str(body.get("input", "")), "Block skeleton")
        node = str(skeleton.get("plan_node_id", ""))
        if node not in order:
            order.append(node)
        asks[node] = asks.get(node, 0) + 1
        always_bad = node == order[0]
        bad_at_first = (
            len(order) > 1 and node == order[1] and asks[node] <= executor.CODE_REASKS + 1
        )
        if always_bad or bad_at_first:
            reply = inner(request).json()
            written = json.loads(reply["output"][0]["content"][0]["text"])
            for citation in written.get("citations", []):
                citation["quote"] = "a sentence that no approved passage contains"
            reply["output"][0]["content"][0]["text"] = json.dumps(written)
            return httpx2.Response(200, json=reply)
        return inner(request)

    detail = await _run_build(t03_session, operation, handler)
    kinds = [kind for kind, _ in await _runs(t03_session, course_id)]
    assert kinds.count("generation") == 1
    assert kinds.count("rewrite") == 1, kinds
    assert detail["state"] == "ready" and detail["held_count"] >= 1


async def test_the_same_idempotency_key_returns_the_first_build(t03_session: AsyncSession) -> None:
    user_id, course_id = await _plan_course_with_source(t03_session)
    first = await _queue_build(t03_session, user_id, course_id, idempotency_key="press-once")
    again = await _queue_build(t03_session, user_id, course_id, idempotency_key="press-once")
    assert again == first
    count = (
        await t03_session.execute(
            sa.text("SELECT count(*) FROM operations WHERE kind = 'build_course'")
        )
    ).scalar_one()
    assert count == 1


async def test_stop_asks_the_live_run_to_cancel_and_leaves_the_build_to_end_itself(
    t03_session: AsyncSession,
) -> None:
    user_id, course_id = await _plan_course_with_source(t03_session)
    run_id = await planruns.start_plan_run(t03_session, course_id=course_id, queries=["x"])
    await t03_session.execute(
        sa.text("UPDATE runs SET status = 'running' WHERE id = :r"), {"r": str(run_id)}
    )
    operation = await _insert_op(
        t03_session,
        course_id,
        user_id,
        kind="build_course",
        status="running",
        minutes=0,
        result={"current_run_id": str(run_id)},
    )
    await build.stop_build(
        t03_session, actor=_plan_actor(user_id, BUILD_CODES), course_id=course_id
    )
    marker = (
        await t03_session.execute(
            sa.text("SELECT stop_marker FROM runs WHERE id = :r"), {"r": str(run_id)}
        )
    ).scalar_one()
    status = (
        await t03_session.execute(
            sa.text("SELECT status FROM operations WHERE id = :o"), {"o": str(operation)}
        )
    ).scalar_one()
    assert marker == "cancel requested" and status == "running"


async def test_three_finished_generation_runs_that_left_drafts_stop_the_build(
    t03_session: AsyncSession,
) -> None:
    from app.services.operations import _set_status

    user_id, course_id = await _plan_course_with_source(t03_session)
    first = await _queue_build(t03_session, user_id, course_id)
    assert (await _run_build(t03_session, first, _build_handler()))["state"] == "ready"
    for _ in range(2):
        await executor.new_run(t03_session, course_id=course_id, kind="generation", dag=[])
    await t03_session.execute(
        sa.text("UPDATE runs SET status = 'done' WHERE course_id = :c"), {"c": str(course_id)}
    )
    await t03_session.execute(
        sa.text(
            "UPDATE blocks SET status = 'draft' WHERE id = (SELECT b.id FROM blocks b"
            " JOIN topics t ON t.id = b.topic_id JOIN modules m ON m.id = t.module_id"
            " WHERE m.course_id = :c LIMIT 1)"
        ),
        {"c": str(course_id)},
    )
    await _set_status(t03_session, first, status="failed", progress=1.0, error="stopped")
    await t03_session.commit()
    again = await _queue_build(t03_session, user_id, course_id, continue_build=True)
    with pytest.raises(build.BuildStopped) as stopped:
        await _run_build(t03_session, again, _build_handler())
    assert stopped.value.code == "step_failed" and "could not be written" in str(stopped.value)
    kinds = [kind for kind, _ in await _runs(t03_session, course_id)]
    assert kinds.count("generation") == 3


async def test_a_build_the_author_stopped_while_queued_makes_no_call_when_its_job_starts(
    t03_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.operations import execute_operation

    user_id, course_id = await _plan_course_with_source(t03_session)
    await t03_session.execute(
        sa.text("UPDATE users SET is_superadmin = true WHERE id = :u"), {"u": str(user_id)}
    )
    operation = await _queue_build(t03_session, user_id, course_id)
    await build.stop_build(
        t03_session, actor=_plan_actor(user_id, BUILD_CODES), course_id=course_id
    )
    await t03_session.commit()
    handler = _build_handler()
    settings = _plan_settings()
    client = guarded_client(settings, inner=httpx2.MockTransport(handler))
    ledger = create_session_factory(_db_url())
    monkeypatch.setattr(
        planruns,
        "real_provider_factory",
        lambda _settings: lambda s: MetaProvider(s, settings, client, ledger),
    )
    await execute_operation(operation, database_url=_db_url())
    assert handler.state["seen"] == []
    row = (
        await t03_session.execute(
            sa.text("SELECT status, error FROM operations WHERE id = :op"), {"op": str(operation)}
        )
    ).one()
    assert tuple(row) == ("failed", build.STOPPED_BY_AUTHOR)


async def test_a_build_row_the_author_ended_between_stages_stops_the_loop_before_its_next_paid_step(
    t03_session: AsyncSession,
) -> None:
    user_id, course_id = await _plan_course_with_source(t03_session)
    operation = await _queue_build(t03_session, user_id, course_id)
    inner = _build_handler()

    async def handler(request: httpx2.Request) -> httpx2.Response:
        if not inner.state["seen"]:
            async with create_session_factory(_db_url())() as other:
                await other.execute(
                    sa.text("UPDATE operations SET status = 'failed' WHERE id = :op"),
                    {"op": str(operation)},
                )
                await other.commit()
        return inner(request)

    with pytest.raises(build.BuildStopped) as stopped:
        await _run_build(t03_session, operation, handler)
    assert stopped.value.code == "cancelled"
    assert 1 <= len(inner.state["seen"]) <= 3
