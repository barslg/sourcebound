"""Worker-job smoke: one enqueue, run, row per operation kind on the mock provider.

Kinds: echo, plan, generation (with resume and the no-double-pay money
guard, the reader's unknown/unread cases and the key validation), translation,
resume_run, transcribe_audio. rewrite_* kinds run run_rewrite_operation, which
drives the same generation handlers.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any
from uuid import UUID, uuid4

import demo_t05
import httpx
import httpx2
import pytest
import sqlalchemy as sa
from conftest import test_database_url as _db_url
from sqlalchemy.ext.asyncio import AsyncSession
from t02_helpers import make_session_factory, truncate_all, unique_email
from transport_guard import guarded_client

from app.config import Settings
from app.providers.llm import MetaProvider
from app.services import courses as course_service
from app.services import library, planruns


@pytest.fixture()
async def session() -> AsyncSession:
    factory = make_session_factory()
    async with factory() as sess:
        await truncate_all(sess)
        yield sess
        await sess.rollback()


async def _ops_user(session: AsyncSession) -> uuid.UUID:
    row = (
        await session.execute(
            sa.text("INSERT INTO users (email, display_name) VALUES (:e, 'N') RETURNING id"),
            {"e": unique_email()},
        )
    ).one()
    await session.commit()
    return uuid.UUID(str(row[0]))


def _ops_actor(user_id: uuid.UUID, codes: frozenset[str] = frozenset({"operation.run"})) -> object:
    from app.rbac.principal import Principal

    return Principal(
        user_id=user_id,
        email="worker@example.com",
        roles=("operator",),
        permissions=codes,
        scope_tags=(),
        is_superadmin=False,
    )


async def test_worker_completes_operation_and_notifies(session: AsyncSession) -> None:
    from app.rbac.catalogue import sync_catalogue
    from app.rbac.principal import load_principal
    from app.rbac.roles import assign_role, create_role
    from app.services.operations import execute_operation, submit_echo

    await sync_catalogue(session)
    await create_role(session, name="operator", permission_codes=["operation.run"], actor=None)
    user_id = await _ops_user(session)
    await assign_role(session, user_id, "operator", scope_tags=[], actor=None)
    await session.commit()
    actor = await load_principal(session, user_id)
    operation_id = await submit_echo(session, actor=actor, payload={"message": "hi"}, defer=None)
    await session.commit()
    from app.services.events import subscribe

    received: list[dict[str, object]] = []

    async def _listen() -> None:
        async for event in subscribe(user_id):
            received.append(event)
            break

    listener = asyncio.ensure_future(_listen())
    await asyncio.sleep(0.05)
    await execute_operation(operation_id, database_url=_db_url())
    await asyncio.wait_for(listener, timeout=10)
    async with make_session_factory()() as check:
        status, progress, result = (
            await check.execute(
                sa.text("SELECT status, progress, result FROM operations WHERE id = :o"),
                {"o": str(operation_id)},
            )
        ).one()
        assert status == "done"
        assert float(progress) == 1.0
        assert result["echo"] == "hi"
        audits = (
            await check.execute(
                sa.text("SELECT count(*) FROM audit_log WHERE entity_id = :o"),
                {"o": str(operation_id)},
            )
        ).scalar_one()
        assert audits >= 1
        notes = (
            await check.execute(
                sa.text("SELECT count(*) FROM notifications WHERE user_id = :u"),
                {"u": str(user_id)},
            )
        ).scalar_one()
        assert notes == 1
    assert received and received[0].get("operation_id") == str(operation_id)


async def test_worker_reauthorises_and_fails_without_rights(
    session: AsyncSession,
) -> None:
    from app.services.operations import execute_operation, submit_echo

    user_id = await _ops_user(session)
    operation_id = await submit_echo(
        session, actor=_ops_actor(user_id), payload={"message": "hi"}, defer=None
    )
    await session.commit()
    await session.execute(sa.text("DELETE FROM user_roles WHERE user_id = :u"), {"u": str(user_id)})
    await session.commit()

    await execute_operation(
        operation_id,
        database_url=_db_url(),
        required_code="operation.secret",
    )
    async with make_session_factory()() as check:
        status = (
            await check.execute(
                sa.text("SELECT status FROM operations WHERE id = :o"),
                {"o": str(operation_id)},
            )
        ).scalar_one()
        assert status == "failed"
        failed_note = (
            await check.execute(
                sa.text(
                    "SELECT kind FROM notifications WHERE user_id = :u"
                    " AND payload ->> 'operation_id' = :o"
                ),
                {"u": str(user_id), "o": str(operation_id)},
            )
        ).scalar_one()
        assert failed_note == "operation.failed"


async def test_bell_lists_the_newest_ten_with_the_operation_outcome(session: AsyncSession) -> None:
    """Eleven rows -> the ten newest; kind and status come from the operation, else the payload."""
    from app.services.notifications import list_notifications
    from app.services.operations import submit_echo

    user_id = await _ops_user(session)
    operation_id = await submit_echo(
        session, actor=_ops_actor(user_id), payload={"message": "hi"}, defer=None
    )
    await session.execute(
        sa.text("UPDATE operations SET status = 'done', kind = 'echo' WHERE id = :o"),
        {"o": str(operation_id)},
    )
    for n in range(9):
        await session.execute(
            sa.text(
                "INSERT INTO notifications (user_id, kind, payload, created_at)"
                " VALUES (:u, 'operation.done', '{\"status\": \"done\"}',"
                " now() - make_interval(mins => :m))"
            ),
            {"u": str(user_id), "m": n + 10},
        )
    await session.execute(
        sa.text(
            "INSERT INTO notifications (user_id, kind, payload, created_at)"
            " VALUES (:u, 'operation.done', '{}', now() - make_interval(mins => 2))"
        ),
        {"u": str(user_id)},
    )
    await session.execute(
        sa.text(
            "INSERT INTO notifications (user_id, kind, payload, created_at)"
            " VALUES (:u, 'operation.done', CAST(:p AS jsonb), now())"
        ),
        {"u": str(user_id), "p": json.dumps({"operation_id": str(operation_id)})},
    )
    await session.commit()
    rows = await list_notifications(session, actor=_ops_actor(user_id))  # type: ignore[arg-type]
    assert len(rows) == 10
    assert (rows[0]["operation_kind"], rows[0]["status"]) == ("echo", "done")
    assert (rows[1]["operation_kind"], rows[1]["status"]) == ("", "")
    assert (rows[2]["operation_kind"], rows[2]["status"]) == ("", "done")


PLAN_BASE = "https://api.meta.ai/v1"


def _plan_settings(**overrides: Any) -> Settings:
    return Settings(
        _env_file=None,
        model_api_key="synthetic-test-key",
        model_api_base_url=PLAN_BASE,
        llm_spend_ceiling_usd=50.0,
        source_store_path="/tmp/muse-t03-sources",
        **overrides,
    )


def _plan_completed(text: str) -> dict[str, Any]:
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 1761878400,
        "status": "completed",
        "model": "muse-spark-1.3",
        "output": [
            {
                "type": "message",
                "id": "msg_1",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
        "usage": {
            "input_tokens": 100,
            "output_tokens": 10,
            "input_tokens_details": [{"cached_tokens": 0}],
            "output_tokens_details": [{"reasoning_tokens": 0}],
            "total_tokens": 110,
        },
    }


def _plan_architect_output() -> dict[str, Any]:
    import pathlib

    return json.loads(pathlib.Path("tests/fixtures/architect_output.json").read_text())


def _plan_outline_output(section_ids: list[str]) -> dict[str, Any]:
    """The scenario the mock outline writes: one module, one lesson topic over every section."""
    return {
        "learner": "A municipal case worker who has never configured an AI tool.",
        "case": "A triage tool adopted by the team with its oversight owner named.",
        "modules": [
            {
                "plan_node_id": "m1",
                "title": "The literacy duty",
                "purpose": "Ground the AI literacy duty in Article 4.",
                "entry_knowledge": "None: the course opens the case.",
                "exit_knowledge": "Staff can name the AI literacy duty and its owner.",
                "sections": section_ids,
                "topics": [
                    {
                        "plan_node_id": "t1",
                        "title": "Article 4 basics",
                        "annotation": "Names the Article 4 duty and who carries it.",
                        "introduces_terms": ["AI literacy"],
                        "prepares_for": "The module test applies the oversight rule.",
                        "requires": [],
                        "sections": section_ids,
                    }
                ],
            }
        ],
        "uncovered": [],
        "questions": [],
    }


def _plan_mock_handler(fail_architect: bool = False) -> Any:
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content.decode("utf-8"))
        prompt = str(body.get("input", ""))
        system = str(body.get("instructions", ""))
        seen.append(system[:40])
        if fail_architect and system.startswith("# Architect"):
            return httpx2.Response(500, json={"error": {"message": "planted 500"}})
        if system.startswith("# Researcher"):
            dynamic = json.loads(prompt.split("## Task input (typed, assembled by code)\n")[1])
            ranked = [
                {"handle": item["handle"], "rank": index, "score": 0.9 - index * 0.1}
                for index, item in enumerate(dynamic["passages"][:3], 1)
            ]
            reply = {
                "query": dynamic["query"],
                "rephrased": dynamic["rephrased"],
                "ranked": ranked,
                "gaps": [],
            }
            return httpx2.Response(200, json=_plan_completed(json.dumps(reply)))
        if system.startswith("# Outline"):
            dynamic = json.loads(prompt.split("## Task input (typed, assembled by code)\n")[1])
            return httpx2.Response(
                200,
                json=_plan_completed(
                    json.dumps(_plan_outline_output([s["id"] for s in dynamic["sections"]]))
                ),
            )
        return httpx2.Response(200, json=_plan_completed(json.dumps(_plan_architect_output())))

    handler.seen = seen  # type: ignore[attr-defined]
    return handler


def _plan_provider_factory(http: httpx2.AsyncClient, ledger: Any, settings: Settings) -> Any:
    def make(session: AsyncSession) -> MetaProvider:
        return MetaProvider(session, settings, http, ledger)

    return make


async def _plan_course_with_source(session: AsyncSession) -> tuple[UUID, UUID]:
    user_id = UUID(
        str(
            (
                await session.execute(
                    sa.text(
                        "INSERT INTO users (email, display_name) VALUES (:e, 'N') RETURNING id"
                    ),
                    {"e": f"plan-{uuid.uuid4().hex[:12]}@example.com"},
                )
            ).one()[0]
        )
    )
    course_id = await course_service.create_course(
        session, owner_id=user_id, title="AI literacy", language="en"
    )
    await course_service.set_profile(
        session,
        course_id=course_id,
        methodology_codes=["direct_instruction"],
        bloom_level="understand",
        audience="municipal staff",
    )
    settings = _plan_settings()
    source_id = await library.store_upload(
        session,
        settings=settings,
        created_by=user_id,
        filename="act.txt",
        media="text",
        content=(
            b"Article 4 requires AI literacy among staff.\n\n"
            b"Providers and deployers must ensure training.\n\n"
            b"Human oversight applies to high-risk systems."
        ),
        course_id=course_id,
    )
    await library.ingest_passages(
        session,
        source_id=source_id,
        passages=library.chunk_text(
            "Article 4 requires AI literacy among staff.\n\n"
            "Providers and deployers must ensure training.\n\n"
            "Human oversight applies to high-risk systems."
        ),
        ts_config="english",
    )
    await library.approve_source(session, source_id=source_id, approved_by=user_id)
    await session.commit()
    return user_id, course_id


def _plan_actor(user_id: UUID, codes: frozenset[str]) -> Any:
    from app.rbac.principal import Principal

    return Principal(
        user_id=user_id,
        email="plan@example.com",
        roles=("author",),
        permissions=codes,
        scope_tags=(),
        is_superadmin=False,
    )


async def _plan_drive(
    session: AsyncSession, run_id: UUID, handler: Any, settings: Settings
) -> dict[str, Any]:
    from app.db import create_session_factory
    from app.runs import executor

    url = _db_url()
    ledger = create_session_factory(url)
    http = guarded_client(settings, inner=httpx2.MockTransport(handler))
    return await executor.drive_run(
        create_session_factory(url),
        run_id=run_id,
        provider_factory=_plan_provider_factory(http, ledger, settings),
        handlers={
            "researcher": executor.plan_researcher_step,
            "architect": executor.plan_architect_step,
        },
    )


async def test_plan_submit_writes_run_and_drives_to_done(t03_session: AsyncSession) -> None:
    from app.runs import executor

    user_id, course_id = await _plan_course_with_source(t03_session)
    actor = _plan_actor(
        user_id, frozenset({"run.start", "operation.run", "course.edit", "course.plan"})
    )
    operation_id = await planruns.submit_plan(
        t03_session,
        actor=actor,
        course_id=course_id,
        queries=["literacy duty"],
        defer=None,
    )
    await t03_session.commit()
    detail = await planruns.run_plan_operation(t03_session, operation_id)
    run_id = UUID(str(detail["run_id"]))
    settings = _plan_settings()
    final = await _plan_drive(t03_session, run_id, _plan_mock_handler(), settings)
    assert final["status"] == "done", final["stop_marker"]
    assert all(step["status"] == "done" for step in final["steps"])
    tree = await course_service.read_tree(t03_session, course_id=course_id)
    assert len(tree["modules"]) == 1
    assert tree["modules"][0]["topics"][0]["blocks"][0]["brief"]
    pairs = (await t03_session.execute(sa.text("SELECT count(*) FROM retrieval_pairs"))).scalar()
    assert pairs and pairs >= 1
    async with make_session_factory()() as fresh:
        await planruns.refresh_plan_estimate(fresh, run_id=run_id)
        estimate = (
            await fresh.execute(
                sa.text("SELECT estimate FROM runs WHERE id = :run"), {"run": str(run_id)}
            )
        ).scalar_one()
    assert estimate["blocks"] == 4
    assert estimate["note"] == "Forecast, not a limit."
    _ = executor.PLAN_HANDLERS


async def test_planted_500_stops_run_and_resume_pays_nothing_twice(
    t03_session: AsyncSession,
) -> None:
    user_id, course_id = await _plan_course_with_source(t03_session)
    actor = _plan_actor(
        user_id, frozenset({"run.start", "operation.run", "course.edit", "course.plan"})
    )
    operation_id = await planruns.submit_plan(
        t03_session,
        actor=actor,
        course_id=course_id,
        queries=["oversight duty"],
        defer=None,
    )
    await t03_session.commit()
    detail = await planruns.run_plan_operation(t03_session, operation_id)
    run_id = UUID(str(detail["run_id"]))
    settings = _plan_settings()
    stopped = await _plan_drive(
        t03_session, run_id, _plan_mock_handler(fail_architect=True), settings
    )
    assert stopped["status"] == "stopped"
    assert stopped["stop_code"] == "model_down"
    assert any(
        s["step_key"] == "architect:plan" and s["status"] != "done" for s in stopped["steps"]
    )

    async def paid_cost(agent: str) -> Any:
        from decimal import Decimal

        total = (
            await t03_session.execute(
                sa.text(
                    "SELECT coalesce(sum(cost_usd), 0) FROM llm_calls"
                    " WHERE run_id = :run AND agent = :agent"
                ),
                {"run": str(run_id), "agent": agent},
            )
        ).scalar_one()
        return total if isinstance(total, Decimal) else Decimal(str(total))

    researcher_cost = await paid_cost("researcher")
    assert researcher_cost > 0
    resumed = await planruns.resume_run(
        t03_session,
        actor=_plan_actor(user_id, frozenset({"run.retry"})),
        run_id=run_id,
        defer=None,
    )
    assert resumed == run_id
    handler = _plan_mock_handler()
    final = await _plan_drive(t03_session, run_id, handler, settings)
    assert final["status"] == "done"
    # The done researcher step is reused, never repaid; the architect pays once.
    assert await paid_cost("researcher") == researcher_cost
    architect_rows = (
        await t03_session.execute(
            sa.text(
                "SELECT count(*) FROM llm_calls"
                " WHERE run_id = :run AND agent = 'architect' AND cost_usd > 0"
            ),
            {"run": str(run_id)},
        )
    ).scalar_one()
    assert architect_rows == 1
    researcher_posts = [seen for seen in handler.seen if seen.startswith("# Researcher")]
    assert researcher_posts == []


async def test_second_identical_run_pays_zero(t03_session: AsyncSession) -> None:
    user_id, course_id = await _plan_course_with_source(t03_session)
    actor = _plan_actor(
        user_id, frozenset({"run.start", "operation.run", "course.edit", "course.plan"})
    )
    settings = _plan_settings()
    operation_id = await planruns.submit_plan(
        t03_session,
        actor=actor,
        course_id=course_id,
        queries=["literacy duty"],
        defer=None,
    )
    await t03_session.commit()
    detail = await planruns.run_plan_operation(t03_session, operation_id)
    first_id = UUID(str(detail["run_id"]))
    first_handler = _plan_mock_handler()
    first = await _plan_drive(t03_session, first_id, first_handler, settings)
    assert first["status"] == "done"
    assert first_handler.seen != []
    # A second submit is a new run only once the first finished: the
    # single-active-run guard refuses while it is going, so the operation
    # is marked done here the way the worker leaves it in production.
    await t03_session.execute(
        sa.text("UPDATE operations SET status = 'done' WHERE id = :o"),
        {"o": str(operation_id)},
    )
    await t03_session.commit()
    second_op = await planruns.submit_plan(
        t03_session,
        actor=actor,
        course_id=course_id,
        queries=["literacy duty"],
        defer=None,
    )
    await t03_session.commit()
    second_detail = await planruns.run_plan_operation(t03_session, second_op)
    second_id = UUID(str(second_detail["run_id"]))
    second_handler = _plan_mock_handler()
    second = await _plan_drive(t03_session, second_id, second_handler, settings)
    assert second["status"] == "done"
    assert second_handler.seen == []


GEN_BASE = "https://api.meta.ai/v1"


def _gen_settings(**overrides: Any) -> Settings:
    return Settings(
        _env_file=None,
        model_api_key="synthetic-test-key",
        model_api_base_url=GEN_BASE,
        llm_spend_ceiling_usd=50.0,
        source_store_path="/tmp/muse-t05-sources",
        **overrides,
    )


def _gen_completed(text: str) -> dict[str, Any]:
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 1761878400,
        "status": "completed",
        "model": "muse-spark-1.3",
        "output": [
            {
                "type": "message",
                "id": "msg_1",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
        "usage": {
            "input_tokens": 100,
            "output_tokens": 10,
            "input_tokens_details": [{"cached_tokens": 0}],
            "output_tokens_details": [{"reasoning_tokens": 0}],
            "total_tokens": 110,
        },
    }


def _gen_block_reply() -> dict[str, Any]:
    return {
        "block_type": "reading",
        "text": "Deployers must train staff.",
        "citations": [{"passage_id": "P1", "quote": "train staff"}],
        "answer": "key",
    }


def _gen_reader_reply() -> dict[str, Any]:
    from app.agents.reader import RUBRIC_ITEMS

    return {
        "verdicts": [
            {
                "plan_node_id": "b1",
                "verdict": "pass",
                "items": {
                    item: {
                        "verdict": "pass",
                        "evidence": (
                            "not applicable"
                            if item == "planted_error"
                            else "Deployers must train staff."
                        ),
                    }
                    for item in RUBRIC_ITEMS
                },
            }
        ]
    }


def _gen_mock_handler(fail_reader: bool = False) -> Any:
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content.decode("utf-8"))
        system = str(body.get("instructions", ""))
        seen.append(system[:20])
        if fail_reader and system.startswith("# Reader"):
            return httpx2.Response(500, json={"error": {"message": "planted 500"}})
        if system.startswith("# Reader"):
            return httpx2.Response(200, json=_gen_completed(json.dumps(_gen_reader_reply())))
        return httpx2.Response(200, json=_gen_completed(json.dumps(_gen_block_reply())))

    handler.seen = seen  # type: ignore[attr-defined]
    return handler


def _gen_provider_factory(http: httpx2.AsyncClient, ledger: Any, settings: Settings) -> Any:
    def make(session: AsyncSession) -> MetaProvider:
        return MetaProvider(session, settings, http, ledger)

    return make


def _gen_dag(course_id: UUID) -> list[dict[str, Any]]:
    return [
        {
            "step_key": "block_teach:b1",
            "handler": "generate_block",
            "depends_on": [],
            "block_type": "reading",
            "course_id": str(course_id),
            "course_language": "en",
            "sources": [],
            "plan": {},
            "dossier": {},
            "skeleton": {"plan_node_id": "b1"},
        },
        {
            "step_key": "reader:t1",
            "handler": "read_topic",
            "depends_on": ["block_teach:b1"],
            "course_id": str(course_id),
            "course_language": "en",
            "sources": [],
            "plan": {},
            "dossier": {},
            "blocks": [{"plan_node_id": "b1"}],
        },
    ]


async def _gen_drive(
    session: AsyncSession, run_id: UUID, handler: Any, settings: Settings
) -> dict[str, Any]:
    from app.db import create_session_factory
    from app.runs import executor

    url = _db_url()
    ledger = create_session_factory(url)
    http = guarded_client(settings, inner=httpx2.MockTransport(handler))
    return await executor.drive_run(
        create_session_factory(url),
        run_id=run_id,
        provider_factory=_gen_provider_factory(http, ledger, settings),
        handlers={
            "generate_block": executor.generate_block_step,
            "read_topic": executor.read_topic_step,
        },
    )


async def _gen_paid_calls(session: AsyncSession, run_id: UUID) -> int:
    return int(
        (
            await session.execute(
                sa.text("SELECT count(*) FROM llm_calls WHERE run_id = :run AND cost_usd > 0"),
                {"run": str(run_id)},
            )
        ).scalar_one()
    )


async def test_generation_resume_pays_nothing_twice(t03_session: AsyncSession) -> None:
    from app.runs import executor

    user_id = UUID(
        str(
            (
                await t03_session.execute(
                    sa.text(
                        "INSERT INTO users (email, display_name) VALUES (:e, 'N') RETURNING id"
                    ),
                    {"e": f"t05-{uuid.uuid4().hex[:12]}@example.com"},
                )
            ).one()[0]
        )
    )
    course_id = await course_service.create_course(
        t03_session, owner_id=user_id, title="T05 resume", language="en"
    )
    await t03_session.commit()
    settings = _gen_settings()
    dag = _gen_dag(course_id)
    run_id = await executor.new_run(t03_session, course_id=course_id, kind="generation", dag=dag)
    await t03_session.commit()

    stopped = await _gen_drive(t03_session, run_id, _gen_mock_handler(fail_reader=True), settings)
    assert stopped["status"] == "stopped"
    paid_before = await _gen_paid_calls(t03_session, run_id)
    assert paid_before >= 1
    block_paid_before = await _gen_block_paid(t03_session, run_id)

    await t03_session.execute(
        sa.text("UPDATE runs SET status = 'running', stop_marker = NULL WHERE id = :run"),
        {"run": str(run_id)},
    )
    await t03_session.commit()
    resumed = await _gen_drive(t03_session, run_id, _gen_mock_handler(), settings)
    assert resumed["status"] == "done"
    paid_after = await _gen_paid_calls(t03_session, run_id)
    assert paid_after == paid_before + 1, "only the failed step pays on resume"
    block_paid = await _gen_block_paid(t03_session, run_id)
    assert block_paid == block_paid_before, "done block steps are never repaid"


async def _gen_block_paid(session: AsyncSession, run_id: UUID) -> int:
    return int(
        (
            await session.execute(
                sa.text(
                    "SELECT count(*) FROM llm_calls"
                    " WHERE run_id = :run AND agent = 'block_teach' AND cost_usd > 0"
                ),
                {"run": str(run_id)},
            )
        ).scalar_one()
    )


async def test_second_identical_generation_run_pays_zero(t03_session: AsyncSession) -> None:
    from app.runs import executor

    user_id = UUID(
        str(
            (
                await t03_session.execute(
                    sa.text(
                        "INSERT INTO users (email, display_name) VALUES (:e, 'N') RETURNING id"
                    ),
                    {"e": f"t05-{uuid.uuid4().hex[:12]}@example.com"},
                )
            ).one()[0]
        )
    )
    course_id = await course_service.create_course(
        t03_session, owner_id=user_id, title="T05 identical", language="en"
    )
    await t03_session.commit()
    settings = _gen_settings()
    run_ids = []
    for _ in range(2):
        dag = _gen_dag(course_id)
        run_ids.append(
            await executor.new_run(t03_session, course_id=course_id, kind="generation", dag=dag)
        )
        await t03_session.commit()
    first_handler = _gen_mock_handler()
    first = await _gen_drive(t03_session, run_ids[0], first_handler, settings)
    assert first["status"] == "done"
    assert first_handler.seen != []
    second_handler = _gen_mock_handler()
    second = await _gen_drive(t03_session, run_ids[1], second_handler, settings)
    assert second["status"] == "done"
    assert second_handler.seen == []
    assert await _gen_paid_calls(t03_session, run_ids[1]) == 0


TR_NB_PREFIX = "Dette er og skal være slik at det ikke bare gjelder: "


def _tr_settings(**overrides: Any) -> Any:
    from app.config import Settings

    return Settings(
        _env_file=None,
        model_api_key="synthetic-test-key",
        model_api_base_url="https://api.meta.ai/v1",
        llm_spend_ceiling_usd=50.0,
        source_store_path="/tmp/muse-t07b-demo",
        **overrides,
    )


def tr_nb_handler(*, bad_node: str = "", echo_first: bool = False) -> Any:
    """Mock translator: real nb-looking output; optionally bad/echo once."""
    state = {"echoed": False}

    def handler(request: httpx2.Request) -> httpx2.Response:
        from demo_t05 import _completed

        body = json.loads(request.content.decode("utf-8"))
        prompt = str(body.get("input", ""))
        if "## Terms\n" in prompt:
            terms = json.loads(prompt.split("## Terms\n", 1)[1].split("\n\n## ")[0])
            reply = {"segments": [{"id": t, "text": f"{t}·nb"} for t in terms]}
            return httpx2.Response(200, json=_completed(json.dumps(reply)))
        segments = json.loads(prompt.split("## Segments\n", 1)[1])
        if bad_node and bad_node in prompt and "## Glossary" in prompt:
            pass
        out = []
        for seg in segments:
            text = str(seg["text"])
            if echo_first and not state["echoed"]:
                out.append({"id": seg["id"], "text": text})
                continue
            out.append({"id": seg["id"], "text": f"{TR_NB_PREFIX}{text} [nb]"})
        if echo_first:
            state["echoed"] = True
        # bad block: return wrong ids for the block whose stem marker is set
        if bad_node and any(bad_node in str(s["text"]) for s in segments):
            out = [{"id": "wrong-id", "text": "Feil og dette er galt."}]
        return httpx2.Response(200, json=_completed(json.dumps({"segments": out})))

    handler.state = state  # type: ignore[attr-defined]
    return handler


async def _tr_drive(run_id: UUID, handler: Any, settings: Any) -> Any:
    from transport_guard import guarded_client

    from app.db import create_session_factory
    from app.providers.llm import MetaProvider
    from app.runs import executor
    from app.services.planruns import plan_handlers

    url = _db_url()
    ledger = create_session_factory(url)
    http = guarded_client(settings, inner=httpx2.MockTransport(handler))

    def make(session: AsyncSession) -> MetaProvider:
        return MetaProvider(session, settings, http, ledger)

    return await executor.drive_run(
        create_session_factory(url),
        run_id=run_id,
        provider_factory=make,
        handlers=plan_handlers(),
    )


async def _tr_setup(t03_session: AsyncSession, settings: Any, prefix: str) -> Any:
    from app.services import courses

    demo = await demo_t05.setup_demo(
        t03_session, settings=settings, email=demo_t05.fresh_email(prefix)
    )
    version_id = await courses.snapshot_version(t03_session, course_id=demo["course_id"])
    await t03_session.commit()
    return demo, version_id


def _tr_blocks() -> list[dict[str, Any]]:
    return [
        {
            "plan_node_id": "b1",
            "block_type": "reading",
            "content": {
                "body": "Providers and deployers must ensure staff training and safety at work.",
                "citations": [{"passage_id": "P1", "quote": "prohibited"}],
            },
            "answer_key": {},
        },
        {
            "plan_node_id": "b2",
            "block_type": "choice",
            "content": {
                "stem": "BADBLOCK Who must ensure staff training at work and in the office?",
                "options": ["Providers and deployers who are here", "Nobody who is here"],
            },
            "answer_key": {"correct": 0},
        },
    ]


async def _tr_statuses(session: AsyncSession, version_id: Any, lang: str = "nb") -> Any:
    rows = (
        await session.execute(
            sa.text(
                "SELECT plan_node_id, tb.status, reason FROM translation_blocks tb"
                " JOIN course_translations ct ON ct.id = tb.translation_id"
                " WHERE ct.course_version_id = :v AND ct.language = :l ORDER BY plan_node_id"
            ),
            {"v": str(version_id), "l": lang},
        )
    ).all()
    return {str(r[0]): (str(r[1]), str(r[2] or "")) for r in rows}


async def test_translation_run_resumes_through_worker(t03_session: AsyncSession) -> None:
    """A stopped translation run resumes: handlers registered, rows stored."""

    from app.db import create_session_factory
    from app.rbac.principal import Principal
    from app.runs import executor
    from app.services import planruns
    from app.translation.pipeline import compile_translation_dag

    settings = _tr_settings()
    demo, version_id = await _tr_setup(t03_session, settings, "t07resume")
    dag = compile_translation_dag(
        blocks=_tr_blocks()[:1],
        source_language="en",
        target_languages=["nb"],
        glossary={},
        course_id=str(demo["course_id"]),
        version_id=str(version_id),
    )
    run_id = await executor.new_run(
        t03_session, course_id=demo["course_id"], kind="translation", dag=dag
    )
    await t03_session.execute(
        sa.text(
            "UPDATE runs SET status = 'stopped', stop_marker = 'stopped at x: E' WHERE id = :r"
        ),
        {"r": str(run_id)},
    )
    await t03_session.commit()
    actor = Principal(
        user_id=demo["user_id"],
        email="r@example.com",
        roles=("author",),
        permissions=frozenset({"run.retry"}),
        scope_tags=(),
        is_superadmin=False,
    )
    await planruns.resume_run(t03_session, actor=actor, run_id=run_id)
    op = (
        await t03_session.execute(
            sa.text(
                "SELECT id FROM operations WHERE kind = 'resume_run'"
                " ORDER BY created_at DESC LIMIT 1"
            )
        )
    ).first()
    assert op is not None
    factory = create_session_factory(_db_url())
    from transport_guard import guarded_client

    from app.providers.llm import MetaProvider

    http = guarded_client(settings, inner=httpx2.MockTransport(tr_nb_handler()))
    ledger = create_session_factory(_db_url())

    def make(session: AsyncSession) -> MetaProvider:
        return MetaProvider(session, settings, http, ledger)

    await planruns.run_resume_operation(
        t03_session, UUID(str(op[0])), provider_factory=make, session_factory=factory
    )
    run = (
        await t03_session.execute(
            sa.text("SELECT status FROM runs WHERE id = :r"), {"r": str(run_id)}
        )
    ).scalar()
    assert run == "done", run
    statuses = await _tr_statuses(t03_session, version_id)
    assert statuses["b1"][0] == "ok", statuses


async def test_submit_translate_operation_product_path(t03_session: AsyncSession) -> None:
    """submit_translate -> worker body -> rows + learner view (mock model)."""

    from app.db import create_session_factory
    from app.rbac.principal import Principal
    from app.services import courses, planruns

    settings = _tr_settings()
    demo = await demo_t05.setup_demo(
        t03_session, settings=settings, email=demo_t05.fresh_email("t07op")
    )
    course_id: UUID = demo["course_id"]
    # write two generated blocks directly as current versions
    from app.course.tree import BlockSkeleton, CourseTree, ModuleNode, SourceRef, TopicNode
    from app.services.courses import write_tree

    def blk(node: str, role: str, btype: str) -> BlockSkeleton:
        return BlockSkeleton(
            plan_node_id=node,
            role=role,
            block_type=btype,
            brief="b",
            source_refs=[SourceRef(handle="P1")],
        )

    tree = CourseTree(
        modules=[
            ModuleNode(
                plan_node_id="m1",
                title="M",
                purpose="p",
                entry_knowledge="e",
                exit_knowledge="x",
                topics=[
                    TopicNode(
                        plan_node_id="t1",
                        kind="lesson",
                        title="T",
                        objectives=["o"],
                        through_line_note="c",
                        blocks=[blk("b1", "teach", "reading"), blk("b2", "check", "choice")],
                    )
                ],
            )
        ]
    )
    await write_tree(t03_session, course_id=course_id, tree=tree, plan_digest="t07b")
    # persist current versions for b1/b2
    for node, content, key in [
        ("b1", _tr_blocks()[0]["content"], {}),
        (
            "b2",
            {
                "stem": "Who must ensure staff training at work and in the office?",
                "options": ["Providers and deployers who are here", "Nobody who is here"],
            },
            {"correct": 0},
        ),
    ]:
        brow = (
            await t03_session.execute(
                sa.text("SELECT id, current_version_id FROM blocks WHERE plan_node_id = :n"),
                {"n": node},
            )
        ).first()
        await t03_session.execute(
            sa.text(
                "UPDATE block_versions SET content = CAST(:c AS jsonb),"
                " answer_key = CAST(:k AS jsonb) WHERE id = :v"
            ),
            {"v": str(brow[1]), "c": json.dumps(content), "k": json.dumps(key)},
        )
    await courses.snapshot_version(t03_session, course_id=course_id)
    await t03_session.commit()
    actor = Principal(
        user_id=demo["user_id"],
        email="op@example.com",
        roles=("author",),
        permissions=frozenset({"course.generate"}),
        scope_tags=(),
        is_superadmin=False,
    )
    op_id = await planruns.submit_translate(
        t03_session, actor=actor, course_id=course_id, languages=["nb"]
    )
    factory = create_session_factory(_db_url())
    from transport_guard import guarded_client

    from app.providers.llm import MetaProvider

    http = guarded_client(settings, inner=httpx2.MockTransport(tr_nb_handler()))
    ledger = create_session_factory(_db_url())

    def make(session: AsyncSession) -> MetaProvider:
        return MetaProvider(session, settings, http, ledger)

    result = await planruns.run_translate_operation(
        t03_session, op_id, provider_factory=make, session_factory=factory
    )
    run = (
        await t03_session.execute(
            sa.text("SELECT status, kind FROM runs WHERE id = :r"), {"r": result["run_id"]}
        )
    ).first()
    assert run is not None and run[0] == "done" and run[1] == "translation", run
    version_id = await planruns._latest_version_id(t03_session, course_id=course_id)
    statuses = await _tr_statuses(t03_session, version_id)
    assert statuses and all(v[0] in ("ok", "held") for v in statuses.values()), statuses
    outline, blocks = await _preview_blocks(t03_session, course_id, "nb")
    assert blocks, outline
    assert outline["available_languages"], outline


async def _preview_blocks(
    session: AsyncSession, course_id: UUID, language: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The author's preview of a draft: its outline and every topic's blocks, flattened."""
    from app.rbac.principal import load_principal
    from app.services.learner import preview_outline, preview_topic

    owner = (
        await session.execute(
            sa.text("SELECT owner_id FROM courses WHERE id = :c"), {"c": str(course_id)}
        )
    ).scalar_one()
    actor = await load_principal(session, UUID(str(owner)))
    outline = await preview_outline(session, actor, course_id, language=language, threshold=0.8)
    blocks: list[dict[str, Any]] = []
    for module in outline["modules"]:
        for topic in module["topics"]:
            page = await preview_topic(
                session, actor, course_id, UUID(topic["id"]), language=language, threshold=0.8
            )
            blocks.extend(page["blocks"])
    return outline, blocks


AUD_BASE = "https://api.meta.ai/v1"


def _aud_settings(**overrides: Any) -> Settings:
    return Settings(
        _env_file=None,
        model_api_key="synthetic-test-key",
        model_api_base_url=AUD_BASE,
        llm_spend_ceiling_usd=50.0,
        source_store_path="/tmp/muse-t03d-sources",
        **overrides,
    )


async def _aud_user(session: AsyncSession, tag: str) -> UUID:
    row = (
        await session.execute(
            sa.text("INSERT INTO users (email, display_name) VALUES (:e, 'N') RETURNING id"),
            {"e": f"t03d-{tag}-{uuid.uuid4().hex[:8]}@example.com"},
        )
    ).one()
    await session.commit()
    return UUID(str(row[0]))


async def _aud_course(
    session: AsyncSession, user_id: UUID, language: str = "en", title: str = "Course"
) -> UUID:
    course_id = await course_service.create_course(
        session, owner_id=user_id, title=title, language=language
    )
    await course_service.set_profile(
        session,
        course_id=course_id,
        methodology_codes=["direct_instruction"],
        bloom_level="understand",
        audience="municipal staff",
    )
    await session.commit()
    return course_id


async def test_audio_upload_is_queued_then_ready(t03_session: AsyncSession) -> None:
    """Audio sources report queued until transcription finishes."""
    from app.services.library import list_course_sources

    settings = _aud_settings()
    user = await _aud_user(t03_session, "ingest")
    course = await _aud_course(t03_session, user, title="Ingest")
    source_id = await library.store_upload(
        t03_session,
        settings=settings,
        created_by=user,
        filename="memo.wav",
        media="audio",
        content=b"RIFF" + b"\x00" * 128,
        course_id=course,
    )
    await library.ingest_passages(t03_session, source_id=source_id, passages=[])
    await library.set_ingest_status(
        t03_session, source_id=source_id, status="queued", message="transcription queued"
    )
    await t03_session.commit()
    sources = await list_course_sources(t03_session, course_id=course)
    assert sources[0]["ingest_status"] == "queued"

    def transcribe(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "segments": [
                    {"start": "00:00:00", "end": "00:00:30", "text": "Welcome staff."},
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(transcribe))
    count = await library.transcribe_source(
        t03_session, source_id=source_id, settings=settings, client=client
    )
    await t03_session.commit()
    assert count == 1
    sources = await list_course_sources(t03_session, course_id=course)
    assert sources[0]["ingest_status"] == "ready"


async def _smoke_course_and_dag(
    session: AsyncSession, prefix: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """A planned 4-block course (ids b1..b4) and its compiled generation DAG."""
    demo = await demo_t05.setup_demo(
        session, settings=_gen_settings(), email=demo_t05.fresh_email(prefix)
    )
    await demo_t05.write_demo_tree(session, course_id=demo["course_id"])
    dag = demo_t05.demo_dag(
        course_id=demo["course_id"],
        tree=demo_t05.demo_tree(),
        sources=demo_t05.demo_sources(demo["passages"]),
        dossier=demo_t05.demo_dossier(),
        planted=demo_t05.plant_spot_error(demo["passages"]),
    )
    return demo, dag


def _edited(inner: Any, edit: Any) -> Any:
    """Wrap a mock handler: `edit(prompt, reply)` may rewrite the model's JSON reply."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        response = inner(request)
        prompt = str(json.loads(request.content.decode("utf-8")).get("input", ""))
        reply = json.loads(response.json()["output"][0]["content"][0]["text"])
        return httpx2.Response(200, json=demo_t05._completed(json.dumps(edit(prompt, reply))))

    return handler


async def _smoke_run(
    session: AsyncSession, demo: dict[str, Any], dag: list[dict[str, Any]], handler: Any
) -> dict[str, Any]:
    from app.runs import executor

    run_id = await executor.new_run(
        session, course_id=demo["course_id"], kind="generation", dag=dag
    )
    await session.commit()
    return await _smoke_drive(run_id, handler)


async def _smoke_drive(run_id: UUID, handler: Any) -> dict[str, Any]:
    """Drive (or resume) one generation run against the mock model."""
    from app.db import create_session_factory
    from app.runs import executor

    settings = _gen_settings()
    ledger = create_session_factory(_db_url())
    http = guarded_client(settings, inner=httpx2.MockTransport(handler))
    return await executor.drive_run(
        create_session_factory(_db_url()),
        run_id=run_id,
        provider_factory=_gen_provider_factory(http, ledger, settings),
        handlers=demo_t05.generation_handlers(),
    )


async def _smoke_blocks(session: AsyncSession, course_id: UUID) -> dict[str, tuple[str, str]]:
    rows = (
        await session.execute(
            sa.text(
                "SELECT b.plan_node_id, b.status, coalesce(b.hold_reason, '') FROM blocks b"
                " JOIN topics t ON t.id = b.topic_id JOIN modules m ON m.id = t.module_id"
                " WHERE m.course_id = :c"
            ),
            {"c": str(course_id)},
        )
    ).all()
    return {str(r[0]): (str(r[1]), str(r[2])) for r in rows}


async def test_mock_product_path_runs_the_live_acceptance_checks(
    t03_session: AsyncSession,
) -> None:
    """The live acceptance's database checks (and the three acceptance_t05 checks) run on mock data.

    Without this the live-only code would execute once per paid run; a crash
    in it (a row unpacked with the wrong column count) showed up after the
    money was spent.
    """
    from test_live_generate import check_generated_course

    demo, dag = await _smoke_course_and_dag(t03_session, "smoke-acceptance")
    final = await _smoke_run(t03_session, demo, dag, demo_t05.mock_demo_handler())
    assert final["status"] == "done", final["stop_marker"]
    rows, cited, readers = await check_generated_course(
        t03_session, demo["course_id"], UUID(str(final["id"]))
    )
    assert len(rows) == 4 and cited >= 4 and readers


async def _rewrite_block(
    session: AsyncSession, demo: dict[str, Any], node: str, handler: Any
) -> dict[str, Any]:
    """Run the product rewrite operation for one block with a mock transport."""
    from app.db import create_session_factory

    settings = _gen_settings()
    block_id = (
        await session.execute(
            sa.text(
                "SELECT b.id FROM blocks b JOIN topics t ON t.id = b.topic_id"
                " JOIN modules m ON m.id = t.module_id"
                " WHERE m.course_id = :c AND b.plan_node_id = :n"
            ),
            {"c": str(demo["course_id"]), "n": node},
        )
    ).scalar_one()
    operation_id = (
        await session.execute(
            sa.text(
                "INSERT INTO operations (kind, status, params, progress, actor_id)"
                " VALUES ('rewrite_block', 'queued', CAST(:p AS jsonb), 0, :a) RETURNING id"
            ),
            {
                "p": json.dumps({"course_id": str(demo["course_id"]), "block_id": str(block_id)}),
                "a": str(demo["user_id"]),
            },
        )
    ).scalar_one()
    await session.commit()
    ledger = create_session_factory(_db_url())
    http = guarded_client(settings, inner=httpx2.MockTransport(handler))
    return await planruns.run_rewrite_operation(
        session,
        UUID(str(operation_id)),
        provider_factory=_gen_provider_factory(http, ledger, settings),
        session_factory=create_session_factory(_db_url()),
    )


async def _paid_block_calls(session: AsyncSession, course_id: UUID) -> int:
    return int(
        (
            await session.execute(
                sa.text(
                    "SELECT count(*) FROM llm_calls l JOIN runs r ON r.id = l.run_id"
                    " WHERE r.course_id = :c AND l.agent LIKE 'block\\_%' AND l.cost_usd > 0"
                ),
                {"c": str(course_id)},
            )
        ).scalar_one()
    )


async def _version_total(session: AsyncSession, course_id: UUID, node: str) -> int:
    return int(
        (
            await session.execute(
                sa.text(
                    "SELECT count(*) FROM block_versions bv JOIN blocks b ON b.id = bv.block_id"
                    " JOIN topics t ON t.id = b.topic_id JOIN modules m ON m.id = t.module_id"
                    " WHERE m.course_id = :c AND b.plan_node_id = :n"
                ),
                {"c": str(course_id), "n": node},
            )
        ).scalar_one()
    )


async def test_a_rewrite_that_returns_the_same_content_still_posts_and_releases_the_hold(
    t03_session: AsyncSession,
) -> None:
    """Guards the author's way out: a held block's rewrite is never a silent no-op.

    The model answers with the content the block already holds, twice. Each
    rewrite must reach the model (the variant grows), leave a new version,
    and the block leaves `held` when the reader passes it; the reader may
    see the topic's other block but cannot change its status.
    """
    demo, dag = await _smoke_course_and_dag(t03_session, "smoke-rewrite")
    final = await _smoke_run(t03_session, demo, dag, demo_t05.mock_demo_handler())
    assert final["status"] == "done", final["stop_marker"]
    before = await _smoke_blocks(t03_session, demo["course_id"])
    assert before["b1"][0] == "accepted" and before["b2"][0] == "accepted", before
    base = await _version_total(t03_session, demo["course_id"], "b1")

    def hold_sibling(prompt: str, reply: dict[str, Any]) -> dict[str, Any]:
        for verdict in reply.get("verdicts", []):
            if verdict["plan_node_id"] == "b2":
                verdict["verdict"] = "hold"
                verdict["hold_reason"] = "sibling judged outside the rewrite"
        return reply

    paid = await _paid_block_calls(t03_session, demo["course_id"])
    for round_no in (1, 2):
        await t03_session.execute(
            sa.text(
                "UPDATE blocks SET status = 'held', hold_reason = 'needs a rewrite' WHERE id IN"
                " (SELECT b.id FROM blocks b JOIN topics t ON t.id = b.topic_id"
                " JOIN modules m ON m.id = t.module_id"
                " WHERE m.course_id = :c AND b.plan_node_id = 'b1')"
            ),
            {"c": str(demo["course_id"])},
        )
        await t03_session.commit()
        handler = _edited(demo_t05.mock_demo_handler(), hold_sibling)
        await _rewrite_block(t03_session, demo, "b1", handler)
        blocks = await _smoke_blocks(t03_session, demo["course_id"])
        assert blocks["b1"][0] == "accepted", (round_no, blocks)
        assert blocks["b2"][0] == "accepted", (round_no, "sibling re-judged", blocks)
        now = await _paid_block_calls(t03_session, demo["course_id"])
        assert now > paid, (round_no, "the rewrite was served from the reply store", now, paid)
        paid = now
    assert await _version_total(t03_session, demo["course_id"], "b1") == base + 2


async def test_generation_reader_unknown_holds_the_block_and_the_author_has_a_way_out(
    t03_session: AsyncSession,
) -> None:
    """The reader cannot decide b2: it is held (never draft), the run ends done, accept works."""
    demo, dag = await _smoke_course_and_dag(t03_session, "smoke-unknown")

    def unknown_b2(prompt: str, reply: dict[str, Any]) -> dict[str, Any]:
        for verdict in reply.get("verdicts", []):
            if verdict["plan_node_id"] == "b2":
                verdict["verdict"] = "unknown"
                verdict["items"]["answer_key"]["verdict"] = "unknown"
        return reply

    handler = _edited(demo_t05.mock_demo_handler(), unknown_b2)
    final = await _smoke_run(t03_session, demo, dag, handler)
    assert final["status"] == "done", final["stop_marker"]
    blocks = await _smoke_blocks(t03_session, demo["course_id"])
    assert blocks["b2"][0] == "held" and "could not decide" in blocks["b2"][1], blocks
    assert all(status != "draft" for status, _ in blocks.values()), blocks
    block_id = (
        await t03_session.execute(
            sa.text(
                "SELECT b.id FROM blocks b JOIN topics t ON t.id = b.topic_id"
                " JOIN modules m ON m.id = t.module_id"
                " WHERE m.course_id = :c AND b.plan_node_id = 'b2'"
            ),
            {"c": str(demo["course_id"])},
        )
    ).scalar_one()
    await course_service.accept_block(t03_session, block_id=UUID(str(block_id)))
    await t03_session.commit()
    assert (await _smoke_blocks(t03_session, demo["course_id"]))["b2"][0] == "accepted"


async def test_generation_item_unknown_under_a_block_pass_is_held_not_accepted(
    t03_session: AsyncSession,
) -> None:
    demo, dag = await _smoke_course_and_dag(t03_session, "smoke-item")

    def item_unknown(prompt: str, reply: dict[str, Any]) -> dict[str, Any]:
        for verdict in reply.get("verdicts", []):
            if verdict["plan_node_id"] == "b1":
                verdict["items"]["grounding"]["verdict"] = "unknown"
        return reply

    final = await _smoke_run(
        t03_session, demo, dag, _edited(demo_t05.mock_demo_handler(), item_unknown)
    )
    assert final["status"] == "done"
    assert (await _smoke_blocks(t03_session, demo["course_id"]))["b1"][0] == "held"


async def test_generation_reader_reply_without_coverage_item_is_rejected(
    t03_session: AsyncSession,
) -> None:
    """T11 R9: the rubric has seven items; a six-item reader reply is a schema violation.

    Red on the six-item rubric (the run ends done), green when `coverage` is
    required: the reply is rejected, the reader step fails with the missing
    item named, and no block is accepted on that verdict.
    """
    demo, dag = await _smoke_course_and_dag(t03_session, "smoke-coverage")
    six = (
        "task_fit",
        "grounding",
        "continuity",
        "answer_key",
        "audience_language",
        "planted_error",
    )

    def six_items(prompt: str, reply: dict[str, Any]) -> dict[str, Any]:
        for verdict in reply.get("verdicts", []):
            verdict["items"] = {
                item: {"verdict": "pass", "evidence": f"quoted {item}"} for item in six
            }
        return reply

    final = await _smoke_run(
        t03_session, demo, dag, _edited(demo_t05.mock_demo_handler(), six_items)
    )
    assert final["status"] == "stopped", final
    raw_errors = [
        str(row[0])
        for row in (
            await t03_session.execute(
                sa.text(
                    "SELECT error FROM run_steps WHERE run_id = :run AND status = 'failed'"
                    " AND step_key LIKE 'reader:%'"
                ),
                {"run": str(final["id"])},
            )
        ).all()
    ]
    assert raw_errors and "missing rubric item coverage" in raw_errors[0], raw_errors
    assert all(
        status != "accepted"
        for status, _ in (await _smoke_blocks(t03_session, demo["course_id"])).values()
    )


async def test_generation_holds_an_unquoted_key_and_a_glued_teach_block_through_the_executor(
    t03_session: AsyncSession,
) -> None:
    """T11b wiring: the taught and adjacency checks run on every generated block."""
    demo, dag = await _smoke_course_and_dag(t03_session, "smoke-rules")
    sources = demo_t05.demo_sources(demo["passages"])
    second = sorted(sources, key=lambda s: s["address"])[1]

    def violate(prompt: str, reply: dict[str, Any]) -> dict[str, Any]:
        skeleton = prompt.rsplit("## Block skeleton", 1)[-1]
        if '"plan_node_id": "b2"' in skeleton and "options" in reply:
            reply["answer"] = "An invented rule nobody taught"
            reply["options"] = [{"text": "An invented rule nobody taught", "correct": True}]
        if '"plan_node_id": "b1"' in skeleton and "citations" in reply:
            first_quote = str(reply["citations"][0]["quote"])
            other = str(second["text"])[:40]
            reply["text"] = f"{first_quote} {other}"
            reply["citations"] = [reply["citations"][0], {"passage_id": "P2", "quote": other}]
        return reply

    final = await _smoke_run(t03_session, demo, dag, _edited(demo_t05.mock_demo_handler(), violate))
    assert final["status"] == "done", final["stop_marker"]
    blocks = await _smoke_blocks(t03_session, demo["course_id"])
    assert (
        blocks["b2"][0] == "held"
        and "not taught: the key claim 'An invented rule" in blocks["b2"][1]
    )
    assert blocks["b1"][0] == "held" and "false adjacency" in blocks["b1"][1], blocks["b1"]


async def test_generation_holds_a_narrating_reading_and_a_giveaway_choice_through_the_executor(
    t03_session: AsyncSession,
) -> None:
    """Wiring of the audit checks: the executor runs narration and giveaway on every draft."""
    demo, dag = await _smoke_course_and_dag(t03_session, "smoke-audit")

    def violate(prompt: str, reply: dict[str, Any]) -> dict[str, Any]:
        skeleton = prompt.rsplit("## Block skeleton", 1)[-1]
        if '"plan_node_id": "b1"' in skeleton and "citations" in reply:
            reply["text"] = str(reply["text"]) + " The value comes from the brief."
        if '"plan_node_id": "b2"' in skeleton and "options" in reply:
            copied = str(reply["citations"][0]["quote"]) + " " + str(reply["citations"][0]["quote"])
            reply["options"] = [
                {"text": copied, "correct": True},
                {"text": "A rule that nobody ever wrote down at work", "correct": False},
            ]
        return reply

    final = await _smoke_run(t03_session, demo, dag, _edited(demo_t05.mock_demo_handler(), violate))
    assert final["status"] == "done", final["stop_marker"]
    blocks = await _smoke_blocks(t03_session, demo["course_id"])
    assert blocks["b1"][0] == "held" and "narration: the text mentions" in blocks["b1"][1], blocks
    assert blocks["b2"][0] == "held" and "giveaway: only the correct options" in blocks["b2"][1], (
        blocks
    )


async def test_generation_run_never_ends_done_with_a_block_no_reader_covered(
    t03_session: AsyncSession,
) -> None:
    """Guards the author's way out: only a genuinely unread block stops the run."""
    demo, dag = await _smoke_course_and_dag(t03_session, "smoke-unread")
    dag = [step for step in dag if step["step_key"] != "reader:t2"]
    for step in dag:
        step["depends_on"] = [d for d in step.get("depends_on", []) if d != "reader:t2"]
    final = await _smoke_run(t03_session, demo, dag, demo_t05.mock_demo_handler())
    assert final["status"] == "stopped"
    assert "never read" in str(final["stop_marker"])
    blocks = await _smoke_blocks(t03_session, demo["course_id"])
    assert blocks["b3"][0] == "draft" and blocks["b1"][0] == "accepted", blocks


async def test_generation_choice_without_a_correct_option_is_held(
    t03_session: AsyncSession,
) -> None:
    """A key the grader would misread (nothing correct = 1.0 for picking nothing) holds."""
    demo, dag = await _smoke_course_and_dag(t03_session, "smoke-key")

    def no_correct(prompt: str, reply: dict[str, Any]) -> dict[str, Any]:
        if reply.get("block_type") == "choice":
            reply["options"] = [
                {"text": "One", "correct": False},
                {"text": "Two", "correct": False},
            ]
        return reply

    final = await _smoke_run(
        t03_session, demo, dag, _edited(demo_t05.mock_demo_handler(), no_correct)
    )
    assert final["status"] == "done"
    status, reason = (await _smoke_blocks(t03_session, demo["course_id"]))["b2"]
    assert status == "held" and "correct" in reason, (status, reason)


async def _dag_without_t2(demo: dict[str, Any], dag: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The DAG of a rewrite-sized run: topic t1 only, b3 and b4 stay draft outside it."""
    drop = {"reader:t2", "saboteur:b4"}
    kept = [
        e
        for e in dag
        if e["step_key"] not in drop
        and (e.get("skeleton") or {}).get("plan_node_id") not in ("b3", "b4")
    ]
    keys = {e["step_key"] for e in kept}
    return [e for e in kept if all(d in keys for d in e.get("depends_on", []))]


async def test_a_run_ends_done_when_its_own_blocks_are_read_though_others_stay_draft(
    t03_session: AsyncSession,
) -> None:
    """Guards the author's way out: the no-draft gate counts the run's own blocks only."""
    demo, dag = await _smoke_course_and_dag(t03_session, "smoke-own")
    final = await _smoke_run(
        t03_session, demo, await _dag_without_t2(demo, dag), demo_t05.mock_demo_handler()
    )
    assert final["status"] == "done", final["stop_marker"]
    blocks = await _smoke_blocks(t03_session, demo["course_id"])
    assert blocks["b3"][0] == "draft" and blocks["b1"][0] == "accepted", blocks


async def test_an_authors_accept_survives_a_fresh_run_replaying_the_reader_hold(
    t03_session: AsyncSession,
) -> None:
    """Guards the author's way out: the accept is pinned to the version it was given on."""
    demo, dag = await _smoke_course_and_dag(t03_session, "smoke-pin")

    def hold_b1(prompt: str, reply: dict[str, Any]) -> dict[str, Any]:
        for verdict in reply.get("verdicts", []):
            if verdict["plan_node_id"] == "b1":
                verdict["verdict"] = "hold"
                verdict["hold_reason"] = "held by the reader"
                verdict["items"]["grounding"]["verdict"] = "hold"
        return reply

    handler = _edited(demo_t05.mock_demo_handler(), hold_b1)
    assert (await _smoke_run(t03_session, demo, dag, handler))["status"] == "done"
    assert (await _smoke_blocks(t03_session, demo["course_id"]))["b1"][0] == "held"
    block_id = (
        await t03_session.execute(
            sa.text(
                "SELECT b.id FROM blocks b JOIN topics t ON t.id = b.topic_id"
                " JOIN modules m ON m.id = t.module_id"
                " WHERE m.course_id = :c AND b.plan_node_id = 'b1'"
            ),
            {"c": str(demo["course_id"])},
        )
    ).scalar_one()
    await course_service.accept_block(t03_session, block_id=UUID(str(block_id)))
    await t03_session.commit()
    assert (await _smoke_run(t03_session, demo, dag, handler))["status"] == "done"
    assert (await _smoke_blocks(t03_session, demo["course_id"]))["b1"][0] == "accepted"


TUTOR_REAL = (
    "Which measures must deployers take so their staff are AI literate?",
    "Does the literacy duty consider the context the AI system is used in?",
    "Is it allowed to use an AI system with subliminal techniques?",
    "Can an AI system manipulate people into decisions that harm them?",
    "How much is the fine for using a prohibited AI practice?",
    "What percentage of worldwide turnover can an undertaking be fined?",
    "What fine applies to notified bodies that do not comply?",
    "Are providers responsible for the AI literacy of persons acting on their behalf?",
    "What is the fine of EUR 15 000 000 for?",
    "Which deceptive techniques are prohibited?",
    "What does GOVERN do?",
    "Which function analyzes and monitors AI risk?",
    "List the characteristics of a trustworthy AI system.",
    "What should users know after the MAP function?",
    "Is GOVERN a cross-cutting function?",
    "How is risk measured?",
    "Are the categories broken into subcategories?",
    "Is privacy part of trustworthiness?",
    "Does risk treatment include responding to incidents?",
)
TUTOR_REFUSED_REAL = ("What is the purpose of MANAGE?",)
TUTOR_OFF_TOPIC = (
    "What is the capital of Norway?",
    "How do I reset my password?",
    "Which staff training is needed for forklift drivers?",
    "What is the penalty for late tax returns?",
    "How do I measure the temperature of an oven?",
    "Who is responsible for fire safety in an office?",
    "What are the risks of smoking?",
    "Explain the rules of chess.",
    "How do I manage my time better?",
    "What is the maximum speed on Norwegian roads?",
)


class _CountingProvider:
    calls = 0

    async def generate_json(self, **kwargs: Any) -> dict[str, Any]:
        type(self).calls += 1
        return {"quotes": []}


async def test_tutor_threshold_keeps_off_topic_questions_away_from_the_model(world: Any) -> None:
    """Money guard: held-out real questions reach the model, off-topic ones never do ($0).

    Two public sources (EUR-Lex AI Act excerpt, verbatim NIST AI RMF
    excerpt). The questions are the held-out set (the thresholds were
    calibrated on other questions): a passage needs the default score AND
    two distinct content lexemes of the question. One real question names
    a word no passage holds ("purpose") and is refused: the cost of the
    coverage rule, pinned so a change of the rule shows.
    """
    from pathlib import Path

    from app.rbac.principal import load_principal
    from app.services.tutor import NOT_IN_MATERIALS, ask

    s = world.s
    text = Path(__file__).parent.joinpath("fixtures/nist_ai_rmf_excerpt.md").read_text()
    source = await library.store_upload(
        s,
        settings=_gen_settings(),
        created_by=world.w["user_id"],
        filename="nist_ai_rmf_excerpt.md",
        media="text",
        content=text.encode(),
        licence="US Government work, public domain",
        course_id=world.w["course_id"],
    )
    await library.ingest_passages(
        s, source_id=source, passages=library.chunk_markdown(text), ts_config="english"
    )
    await library.approve_source(s, source_id=source, approved_by=world.w["user_id"])
    await s.commit()
    principal = await load_principal(s, world.student)
    defaults = Settings(_env_file=None)

    async def reaches_model(question: str) -> tuple[int, dict[str, Any]]:
        _CountingProvider.calls = 0
        reply = await ask(
            s,
            principal,
            UUID(world.course),
            question,
            language=None,
            make_provider_factory=lambda: lambda session: _CountingProvider(),
            min_score=defaults.tutor_min_score,
            min_covered=defaults.tutor_min_covered,
            max_passages=4,
            max_chars=500,
        )
        return _CountingProvider.calls, reply

    for question in TUTOR_REAL:
        calls, _ = await reaches_model(question)
        assert calls == 1, ("a real question was refused", question)
    for question in TUTOR_REFUSED_REAL:
        calls, reply = await reaches_model(question)
        assert calls == 0 and reply["mode"] == NOT_IN_MATERIALS, ("coverage rule changed", question)
    for question in TUTOR_OFF_TOPIC:
        calls, reply = await reaches_model(question)
        assert calls == 0 and reply["mode"] == NOT_IN_MATERIALS, ("paid for off-topic", question)
        assert reply["quotes"] == [] and "hint" in reply


async def _gen_course(session: AsyncSession, title: str) -> UUID:
    user_id = UUID(
        str(
            (
                await session.execute(
                    sa.text(
                        "INSERT INTO users (email, display_name) VALUES (:e, 'N') RETURNING id"
                    ),
                    {"e": f"replay-{uuid.uuid4().hex[:12]}@example.com"},
                )
            ).one()[0]
        )
    )
    course_id = await course_service.create_course(
        session, owner_id=user_id, title=title, language="en"
    )
    await session.commit()
    return course_id


async def _step_output(session: AsyncSession, run_id: UUID, step_key: str) -> Any:
    return (
        await session.execute(
            sa.text("SELECT output FROM run_steps WHERE run_id = :r AND step_key = :k"),
            {"r": str(run_id), "k": step_key},
        )
    ).scalar_one()


async def test_replay_ledger_serves_a_recorded_run_free_and_a_new_request_misses_loudly(
    t03_session: AsyncSession, tmp_path: Any
) -> None:
    """Record a run through the mock model, replay it on another course with no model reachable."""
    from app.runs import executor

    session = t03_session
    recorded_course = await _gen_course(session, "Replay recorded")
    recorded = await executor.new_run(
        session, course_id=recorded_course, kind="generation", dag=_gen_dag(recorded_course)
    )
    await session.commit()
    assert (await _gen_drive(session, recorded, _gen_mock_handler(), _gen_settings()))[
        "status"
    ] == "done"
    rows = (
        await session.execute(
            sa.text(
                "SELECT agent, model, request, response FROM llm_calls"
                " WHERE run_id = :r AND cost_usd > 0 ORDER BY created_at"
            ),
            {"r": str(recorded)},
        )
    ).all()
    assert {row[0] for row in rows} == {"block_teach", "reader"}
    ledger = tmp_path / "ledger.json"
    ledger.write_text(
        json.dumps(
            [{"agent": r[0], "model": r[1], "request": r[2], "response": r[3]} for r in rows]
        ),
        encoding="utf-8",
    )
    # A reply store holding the recorded replies would hide the ledger: start it empty.
    await session.execute(sa.text("DELETE FROM llm_replies"))
    await session.commit()

    settings = _gen_settings(model_replay_ledger=str(ledger))
    replay_course = await _gen_course(session, "Replay played")
    played = await executor.new_run(
        session, course_id=replay_course, kind="generation", dag=_gen_dag(replay_course)
    )
    await session.commit()
    unreachable = _gen_mock_handler()
    unreachable.seen.append("sentinel")
    assert (await _gen_drive(session, played, unreachable, settings))["status"] == "done"
    assert unreachable.seen == ["sentinel"], "replay mode never reaches the model"
    assert await _gen_paid_calls(session, played) == 0
    assert await _step_output(session, played, "block_teach:b1") == await _step_output(
        session, recorded, "block_teach:b1"
    )

    other_course = await _gen_course(session, "Replay missed")
    unrecorded_dag = _gen_dag(other_course)
    unrecorded_dag[0]["skeleton"] = {"plan_node_id": "never-recorded"}
    missed = await executor.new_run(
        session, course_id=other_course, kind="generation", dag=unrecorded_dag
    )
    await session.commit()
    stopped = await _gen_drive(session, missed, _gen_mock_handler(), settings)
    assert stopped["status"] == "stopped"
    error = (
        await session.execute(
            sa.text("SELECT error FROM run_steps WHERE run_id = :r AND status = 'failed'"),
            {"r": str(missed)},
        )
    ).scalar_one()
    assert "ReplayMissError" in error and "no recorded reply" in error


async def test_truncated_empty_reply_is_paid_logged_never_stored_and_re_asked_once(
    t03_session: AsyncSession,
) -> None:
    """Money guard: a reply that spent its whole budget on nothing is a rejected ledger row
    with its cost, never enters the reply store, and is asked once more with twice the budget."""
    user_id, course_id = await _plan_course_with_source(t03_session)
    actor = _plan_actor(
        user_id, frozenset({"run.start", "operation.run", "course.edit", "course.plan"})
    )
    operation_id = await planruns.submit_plan(
        t03_session, actor=actor, course_id=course_id, queries=["literacy duty"], defer=None
    )
    await t03_session.commit()
    detail = await planruns.run_plan_operation(t03_session, operation_id)
    run_id = UUID(str(detail["run_id"]))
    base = _plan_mock_handler()
    budgets: list[int] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content.decode("utf-8"))
        if str(body.get("instructions", "")).startswith("# Architect"):
            budgets.append(int(body["max_output_tokens"]))
            if len(budgets) == 1:
                truncated = _plan_completed("")
                truncated["usage"]["output_tokens"] = int(body["max_output_tokens"])
                return httpx2.Response(200, json=truncated)
        return base(request)

    final = await _plan_drive(t03_session, run_id, handler, _plan_settings())
    assert final["status"] == "done"
    assert len(budgets) == 2 and budgets[1] == 2 * budgets[0]
    rows = (
        await t03_session.execute(
            sa.text(
                "SELECT status, cost_usd FROM llm_calls WHERE agent = 'architect'"
                " ORDER BY created_at"
            )
        )
    ).all()
    assert [row[0] for row in rows] == ["rejected", "completed"]
    assert rows[0][1] > 0
    completed = (
        await t03_session.execute(
            sa.text("SELECT count(*) FROM llm_calls WHERE status = 'completed'")
        )
    ).scalar()
    stored = (await t03_session.execute(sa.text("SELECT count(*) FROM llm_replies"))).scalar()
    assert stored == completed


async def test_garbage_reply_is_rejected_once_and_never_re_asked(t03_session: AsyncSession) -> None:
    """Money guard: a non-empty reply that is not JSON is paid once and not asked again."""
    user_id, course_id = await _plan_course_with_source(t03_session)
    actor = _plan_actor(
        user_id, frozenset({"run.start", "operation.run", "course.edit", "course.plan"})
    )
    operation_id = await planruns.submit_plan(
        t03_session, actor=actor, course_id=course_id, queries=["literacy duty"], defer=None
    )
    await t03_session.commit()
    detail = await planruns.run_plan_operation(t03_session, operation_id)
    run_id = UUID(str(detail["run_id"]))
    base = _plan_mock_handler()
    architect_posts = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal architect_posts
        body = json.loads(request.content.decode("utf-8"))
        if str(body.get("instructions", "")).startswith("# Architect"):
            architect_posts += 1
            garbage = _plan_completed("this is not json")
            # garbage under the budget is garbage; at the budget it is a truncation (re-asked once)
            garbage["usage"]["output_tokens"] = int(body["max_output_tokens"]) - 1
            return httpx2.Response(200, json=garbage)
        return base(request)

    final = await _plan_drive(t03_session, run_id, handler, _plan_settings())
    assert final["status"] == "stopped"
    assert architect_posts == 1


def test_reader_hold_reason_carries_the_note_and_the_block_assignment() -> None:
    """The 2026-11-02 holds read "coverage: Solve harder problems…" — a span
    with no instruction; the writer must see what to change and which
    passages the plan assigned to the block."""
    from app.runs.executor import _assigned_handles, _hold_line

    assert _hold_line("coverage", {"verdict": "hold", "evidence": "a span", "note": ""}) == (
        "coverage: a span"
    )
    assert (
        _hold_line(
            "coverage",
            {"verdict": "hold", "evidence": "a span", "note": "carry P639  or declare it"},
        )
        == "coverage: carry P639 or declare it (evidence: a span)"
    )
    plan = {
        "modules": [
            {
                "topics": [
                    {
                        "blocks": [
                            {"plan_node_id": "m1-t1-b1", "source_handles": ["P1", "P2"]},
                            {"plan_node_id": "m1-t1-b2"},
                        ]
                    }
                ]
            }
        ]
    }
    assert _assigned_handles(plan) == {"m1-t1-b1": ["P1", "P2"], "m1-t1-b2": []}


def test_a_bigger_output_budget_does_not_rerun_a_finished_step(monkeypatch: Any) -> None:
    """2026-11-02 23:41: raising the reader budget mid-run re-read twenty
    finished topics on resume (≈$2.50). A finished reply is not less valid
    because the next call may be longer; model and effort still count."""
    from app.llm import registry
    from app.runs.executor import step_digest

    before = step_digest(agent="reader", prompt="p", upstream={"a": 1})
    model, effort, budget = registry.resolve_step("reader", None, None, None)
    monkeypatch.setattr(registry, "resolve_step", lambda *a, **k: (model, effort, budget * 2))
    monkeypatch.setattr(
        "app.runs.executor.resolve_step", lambda *a, **k: (model, effort, budget * 2)
    )
    assert step_digest(agent="reader", prompt="p", upstream={"a": 1}) == before
    monkeypatch.setattr("app.runs.executor.resolve_step", lambda *a, **k: (model, "low", budget))
    assert step_digest(agent="reader", prompt="p", upstream={"a": 1}) != before


async def test_recheck_releases_a_hold_the_current_checks_no_longer_find(
    t03_session: AsyncSession,
) -> None:
    """A hold from a check that was since fixed lifts without a paid rewrite.

    After a clean demo run: b1 is marked held with a code line the current
    adjacency check does not find, b2 with a reader line (not recomputable),
    b3 with the same code line but a citation quote that is no longer verbatim
    (a hold the current checks still find). Recheck releases b1 only, keeps b2's
    line and replaces b3's with what the check finds today. Then, with b3's
    topic read step gone, a clean b3 stays held (no verdict on this version),
    a clean b3 goes back to draft (no verdict on this version: the rework's
    reread picks it up), and b1 held on a non-verbatim quote stays held:
    stored content cannot prove that line either way.
    """
    from app.services.recheck import recheck_held

    demo, dag = await _smoke_course_and_dag(t03_session, "smoke-recheck")
    final = await _smoke_run(t03_session, demo, dag, demo_t05.mock_demo_handler())
    assert final["status"] == "done", final["stop_marker"]
    old = (
        "false adjacency: text from P1 runs into text from P9"
        " without a \u27e6P9\u27e7 marker between them"
    )
    reader_line = 'coverage: carry the second passage too (evidence: "tools into risk tiers")'
    course = str(demo["course_id"])
    block_sql = (
        " WHERE id IN (SELECT b.id FROM blocks b JOIN topics t ON t.id = b.topic_id"
        " JOIN modules m ON m.id = t.module_id WHERE m.course_id = :c AND b.plan_node_id = :n)"
    )

    async def hold(node: str, reason: str) -> None:
        await t03_session.execute(
            sa.text(
                "UPDATE blocks SET status = 'held', hold_reason = :r, accepted_version_id = NULL"
                + block_sql
            ),
            {"r": reason, "c": course, "n": node},
        )

    for node, reason in (("b1", old), ("b2", reader_line), ("b3", old)):
        await hold(node, reason)
    b3_version = (
        await t03_session.execute(
            sa.text("SELECT current_version_id FROM blocks" + block_sql), {"c": course, "n": "b3"}
        )
    ).scalar_one()
    verbatim = (
        (
            await t03_session.execute(
                sa.text(
                    "SELECT quote FROM citations WHERE block_version_id = :v ORDER BY quote_start"
                ),
                {"v": str(b3_version)},
            )
        )
        .scalars()
        .first()
    )
    await t03_session.execute(
        sa.text("UPDATE citations SET quote = 'words no passage says' WHERE block_version_id = :v"),
        {"v": str(b3_version)},
    )
    await t03_session.commit()
    result = await recheck_held(t03_session, course_id=demo["course_id"])
    await t03_session.commit()
    blocks = await _smoke_blocks(t03_session, demo["course_id"])
    assert blocks["b1"] == ("accepted", ""), blocks["b1"]
    assert blocks["b2"] == ("held", reader_line), blocks["b2"]
    assert blocks["b3"][0] == "held" and blocks["b3"][1].startswith(
        "quote is not verbatim in its passage"
    ), blocks["b3"]
    assert result == {"rechecked": 3, "released": 1, "unread": 0, "held": 2, "newly_held": 0}, (
        result
    )
    accepted = (
        await t03_session.execute(
            sa.text("SELECT accepted_version_id = current_version_id FROM blocks" + block_sql),
            {"c": course, "n": "b1"},
        )
    ).scalar_one()
    assert accepted is True
    await t03_session.execute(
        sa.text("UPDATE citations SET quote = :q WHERE block_version_id = :v"),
        {"q": verbatim, "v": str(b3_version)},
    )
    await hold("b3", old)
    dropped = "quote is not verbatim in its passage (P1)"
    await hold("b1", dropped)  # the executor drops such a quote from stored content
    await t03_session.execute(
        sa.text("DELETE FROM run_steps WHERE run_id = :run AND step_key = 'reader:t2'"),
        {"run": str(final["id"])},
    )
    await t03_session.commit()
    result = await recheck_held(t03_session, course_id=demo["course_id"])
    await t03_session.commit()
    blocks = await _smoke_blocks(t03_session, demo["course_id"])
    assert blocks["b3"] == ("draft", ""), blocks["b3"]
    assert blocks["b1"] == ("held", dropped), blocks["b1"]
    assert result == {"rechecked": 3, "released": 0, "unread": 1, "held": 2, "newly_held": 0}, (
        result
    )


async def test_generation_keeps_a_writer_leftover_with_the_block(
    t03_session: AsyncSession,
) -> None:
    """A passage the writer declares in `leftovers` is stored with the block, reason and all.

    The prompt promises the author sees what was left out; without storage
    the declaration died with the reply.
    """
    demo, dag = await _smoke_course_and_dag(t03_session, "smoke-leftovers")
    leftover = {"handle": "P9", "reason": "page metadata, not a claim"}

    def declare(prompt: str, reply: dict[str, Any]) -> dict[str, Any]:
        skeleton = prompt.rsplit("## Block skeleton", 1)[-1]
        if '"plan_node_id": "b1"' in skeleton and "citations" in reply:
            reply["leftovers"] = [leftover]
        return reply

    final = await _smoke_run(t03_session, demo, dag, _edited(demo_t05.mock_demo_handler(), declare))
    assert final["status"] == "done", final["stop_marker"]
    stored = (
        await t03_session.execute(
            sa.text(
                "SELECT bv.content -> 'leftovers' FROM blocks b"
                " JOIN block_versions bv ON bv.id = b.current_version_id"
                " JOIN topics t ON t.id = b.topic_id JOIN modules m ON m.id = t.module_id"
                " WHERE m.course_id = :c AND b.plan_node_id = 'b1'"
            ),
            {"c": str(demo["course_id"])},
        )
    ).scalar_one()
    assert stored == [leftover], stored


async def test_rework_resume_reads_a_draft_the_failed_pass_left_unread(
    t03_session: AsyncSession,
) -> None:
    """A rewrite whose reread never ran is read on resume, not left draft or re-paid.

    The 2026-11-03 Muse API course: the rework pass rewrote 23 blocks, then a
    reread timed out; on resume the rework recomputed its named list from the
    blocks held NOW, so the 23 drafts had no reader and the run could only
    stop again on the no-draft gate. A topic with a version newer than its
    last reading is read again, once: by the main pass on resume, or by the
    rework reread for the rewrites of its own pass.
    """
    demo, dag = await _smoke_course_and_dag(t03_session, "smoke-rework-resume")
    final = await _smoke_run(t03_session, demo, dag, demo_t05.mock_demo_handler())
    assert final["status"] == "done", final["stop_marker"]
    course = str(demo["course_id"])
    run_id = UUID(str(final["id"]))
    topic = (
        await t03_session.execute(
            sa.text(
                "SELECT t.plan_node_id FROM blocks b JOIN topics t ON t.id = b.topic_id"
                " JOIN modules m ON m.id = t.module_id"
                " WHERE m.course_id = :c AND b.plan_node_id = 'b1'"
            ),
            {"c": course},
        )
    ).scalar_one()
    # b1 gets a new version this run wrote after its topic was read: a draft nobody read
    await t03_session.execute(
        sa.text(
            "INSERT INTO block_versions"
            " (block_id, version_no, content, answer_key, schema_version, run_step_id)"
            " SELECT bv.block_id, bv.version_no + 1, bv.content, bv.answer_key,"
            " bv.schema_version, bv.run_step_id FROM blocks b"
            " JOIN block_versions bv ON bv.id = b.current_version_id"
            " JOIN topics t ON t.id = b.topic_id JOIN modules m ON m.id = t.module_id"
            " WHERE m.course_id = :c AND b.plan_node_id = 'b1'"
        ),
        {"c": course},
    )
    await t03_session.execute(
        sa.text(
            "UPDATE blocks SET status = 'draft', hold_reason = NULL, accepted_version_id = NULL,"
            " current_version_id = (SELECT id FROM block_versions"
            "  WHERE block_id = blocks.id ORDER BY version_no DESC LIMIT 1)"
            " WHERE id IN (SELECT b.id FROM blocks b JOIN topics t ON t.id = b.topic_id"
            " JOIN modules m ON m.id = t.module_id"
            " WHERE m.course_id = :c AND b.plan_node_id = 'b1')"
        ),
        {"c": course},
    )
    await t03_session.execute(
        sa.text(
            "UPDATE run_steps SET status = 'failed', error = 'LLMError: polling timed out'"
            " WHERE run_id = :run AND step_key = 'rework:named-blocks'"
        ),
        {"run": str(run_id)},
    )
    await t03_session.execute(
        sa.text("UPDATE runs SET status = 'stopped' WHERE id = :run"), {"run": str(run_id)}
    )
    await t03_session.commit()
    before = await demo_t05.paid_by_step(t03_session, run_id=run_id)
    held_before = sorted(
        node
        for node, (status, _) in (await _smoke_blocks(t03_session, demo["course_id"])).items()
        if status == "held"
    )
    resumed = await _smoke_drive(run_id, demo_t05.mock_demo_handler())
    assert resumed["status"] == "done", resumed["stop_marker"]
    after = await demo_t05.paid_by_step(t03_session, run_id=run_id)
    blocks = await _smoke_blocks(t03_session, demo["course_id"])
    assert blocks["b1"] == ("accepted", ""), blocks["b1"]
    # read exactly once more: by the main pass or the rework reread, never both
    reader_key = f"reader:{topic}"
    assert after[reader_key]["paid"] == before[reader_key]["paid"] + 1, (before, after)
    # the draft is read, not rewritten: only a still-held block may pay a writer again
    writers = sorted(
        k.partition(":")[2] for k in after if k.startswith("block_") and after[k] != before.get(k)
    )
    assert not set(writers) - set(held_before), (writers, held_before)
    rework = (
        await t03_session.execute(
            sa.text(
                "SELECT output FROM run_steps WHERE run_id = :run"
                " AND step_key = 'rework:named-blocks'"
            ),
            {"run": str(run_id)},
        )
    ).scalar_one()
    assert sorted(rework["reworked"]) == held_before, (rework, held_before)


async def test_resume_never_regenerates_a_block_this_run_already_wrote(
    t03_session: AsyncSession,
) -> None:
    """A finished block stays finished on resume whatever happened to its step digest.

    The 2026-11-03 resume of the Muse API course: the rework had rewritten
    blocks under their main step keys (new digests, new outputs) and a code
    change had moved the output budget out of the digest, so the main pass
    would have regenerated 132 of 193 finished steps. A block written by a
    step of this run is done; a topic read after its last version is read.
    """
    demo, dag = await _smoke_course_and_dag(t03_session, "smoke-resume-digests")
    final = await _smoke_run(t03_session, demo, dag, demo_t05.mock_demo_handler())
    assert final["status"] == "done", final["stop_marker"]
    run_id = UUID(str(final["id"]))
    await t03_session.execute(
        sa.text("UPDATE run_steps SET input_digest = 'stale' WHERE run_id = :run"),
        {"run": str(run_id)},
    )
    await t03_session.execute(
        sa.text(
            "UPDATE run_steps SET status = 'failed', error = 'LLMError: polling timed out'"
            " WHERE run_id = :run AND step_key = 'rework:named-blocks'"
        ),
        {"run": str(run_id)},
    )
    await t03_session.execute(
        sa.text("UPDATE runs SET status = 'stopped' WHERE id = :run"), {"run": str(run_id)}
    )
    await t03_session.commit()
    before = await demo_t05.paid_by_step(t03_session, run_id=run_id)
    blocks_before = await _smoke_blocks(t03_session, demo["course_id"])
    held_before = sorted(node for node, (status, _) in blocks_before.items() if status == "held")
    resumed = await _smoke_drive(run_id, demo_t05.mock_demo_handler())
    assert resumed["status"] == "done", resumed["stop_marker"]
    after = await demo_t05.paid_by_step(t03_session, run_id=run_id)
    paid_again = sorted(k for k in after if after[k]["paid"] != before.get(k, {}).get("paid"))
    # only the rework may pay: a rewrite of a held block and the reread of its topic
    held_topics = {
        str(row[0])
        for row in (
            await t03_session.execute(
                sa.text(
                    "SELECT t.plan_node_id FROM blocks b JOIN topics t ON t.id = b.topic_id"
                    " JOIN modules m ON m.id = t.module_id"
                    " WHERE m.course_id = :c AND b.plan_node_id = ANY(:nodes)"
                ),
                {"c": str(demo["course_id"]), "nodes": held_before},
            )
        ).all()
    }
    allowed = {f"reader:{t}" for t in held_topics} | {
        k for k in after if k.partition(":")[2] in held_before
    }
    assert not set(paid_again) - allowed, (paid_again, allowed)
    accepted_before = {n for n, (status, _) in blocks_before.items() if status == "accepted"}
    blocks = await _smoke_blocks(t03_session, demo["course_id"])
    assert {n for n in accepted_before if blocks[n][0] == "accepted"} == accepted_before, blocks


async def test_a_code_hold_is_fixed_inside_the_writer_step_before_the_reader_pays(
    t03_session: AsyncSession,
) -> None:
    """A writer whose draft fails a code check is asked again with the lines, in the same step.

    The Muse API course (2026-11-03) cycled hold → reader → rewrite → new
    hold three times ($6) over checks code can state at once (a quote not
    verbatim, a list item missing, two passages glued). The step now
    returns the lines to the writer with a Fix list and only stores what
    passes code; the reader judges meaning, never a line code already
    knew. Mock: b1's first reply misquotes one character, its second is
    verbatim.
    """
    demo, dag = await _smoke_course_and_dag(t03_session, "smoke-reask")
    inner = demo_t05.mock_demo_handler()
    prompts: list[str] = []

    def handler(request: Any) -> Any:
        body = json.loads(request.content.decode("utf-8"))
        system = str(body.get("instructions", ""))
        response = inner(request)
        if not system.startswith("# Teach writer"):
            return response
        prompt = str(body.get("input", ""))
        if '"plan_node_id": "b1"' not in prompt.split("## Block skeleton", 1)[-1]:
            return response
        prompts.append(prompt)
        if len(prompts) > 1:
            return response
        reply = json.loads(response.json()["output"][0]["content"][0]["text"])
        quote = str(reply["citations"][0]["quote"])
        reply["citations"][0]["quote"] = quote[:12] + "X" + quote[13:]
        return httpx2.Response(200, json=demo_t05._completed(json.dumps(reply)))

    final = await _smoke_run(t03_session, demo, dag, handler)
    assert final["status"] == "done", final["stop_marker"]
    blocks = await _smoke_blocks(t03_session, demo["course_id"])
    assert blocks["b1"][0] == "accepted", blocks["b1"]
    assert len(prompts) == 2, "the writer is asked once more, in the same step"
    assert "## Fix list" in prompts[1] and "quote is not verbatim" in prompts[1]
    assert "## Fix list" not in prompts[0]
    paid = await demo_t05.paid_by_step(t03_session, run_id=UUID(str(final["id"])))
    assert paid["block_teach:b1"]["paid"] == 2, paid["block_teach:b1"]
    untouched = await _version_total(t03_session, demo["course_id"], "b3")
    assert await _version_total(t03_session, demo["course_id"], "b1") == untouched, (
        "the held draft is never stored: b1 has as many versions as a block never held"
    )


async def test_a_re_ask_that_returns_nothing_usable_keeps_the_held_draft_and_the_run(
    t03_session: AsyncSession,
) -> None:
    """A re-ask may answer with nothing: the draft stays held and the run goes on.

    Rewrite 3 of the Muse API course (2026-11-03): the exercise writer's
    re-ask came back with no citations, the schema check rejected it and
    the whole rewrite run stopped. A re-ask can only improve a draft; the
    rework path still gets its turn on the held block.
    """
    demo, dag = await _smoke_course_and_dag(t03_session, "smoke-reask-empty")
    inner = demo_t05.mock_demo_handler()

    def handler(request: Any) -> Any:
        body = json.loads(request.content.decode("utf-8"))
        response = inner(request)
        if not str(body.get("instructions", "")).startswith("# Teach writer"):
            return response
        prompt = str(body.get("input", ""))
        if '"plan_node_id": "b1"' not in prompt.split("## Block skeleton", 1)[-1]:
            return response
        reply = json.loads(response.json()["output"][0]["content"][0]["text"])
        if "## Fix list" in prompt:
            reply["citations"] = []
        else:
            quote = str(reply["citations"][0]["quote"])
            reply["citations"][0]["quote"] = quote[:12] + "X" + quote[13:]
        return httpx2.Response(200, json=demo_t05._completed(json.dumps(reply)))

    final = await _smoke_run(t03_session, demo, dag, handler)
    assert final["status"] == "done", final["stop_marker"]
    step = (
        await t03_session.execute(
            sa.text(
                "SELECT status, output FROM run_steps"
                " WHERE run_id = :run AND step_key = 'block_teach:b1'"
            ),
            {"run": str(final["id"])},
        )
    ).one()
    assert step[0] == "done"
    assert dict(step[1]).get("held") is True
    assert "quote is not verbatim" in str(dict(step[1]).get("hold_reason", ""))


async def test_the_author_preview_is_the_learner_reading_of_the_draft(
    t03_session: AsyncSession,
) -> None:
    """A draft course previews as modules → topics → blocks with chips and held marks.

    The owner opened the Muse API course's preview (2026-11-03) and saw
    140 numbered blocks with no topic, module or title: the preview was a
    flat dump through its own serializer. The preview now runs the
    learner services over the draft; a learner's own route still answers
    404 until the course is published.
    """
    from app.errors import NotFoundError
    from app.rbac.principal import Principal, load_principal
    from app.services.learner import course_outline, preview_passage

    demo, dag = await _smoke_course_and_dag(t03_session, "smoke-preview")
    final = await _smoke_run(t03_session, demo, dag, demo_t05.mock_demo_handler())
    assert final["status"] == "done", final["stop_marker"]
    outline, blocks = await _preview_blocks(t03_session, demo["course_id"], "en")
    titles = [t["title"] for m in outline["modules"] for t in m["topics"]]
    assert outline["modules"] and titles and all(titles), outline
    held_in_db = await _smoke_blocks(t03_session, demo["course_id"])
    held_nodes = {node for node, (status, _) in held_in_db.items() if status == "held"}
    assert sum(t["held"] for m in outline["modules"] for t in m["topics"]) == len(held_nodes)
    assert len(blocks) == len(held_in_db), (len(blocks), len(held_in_db))
    assert {b["status"] for b in blocks} <= {"accepted", "held", "draft"}
    assert all(b["hold_reason"] for b in blocks if b["status"] == "held")
    with_chips = next(b for b in blocks if b["citations"])
    chip = with_chips["citations"][0]
    passage = await preview_passage(
        t03_session, demo["course_id"], UUID(chip["passage_id"]), language="en"
    )
    assert passage["text"] and passage["course_id"] == str(demo["course_id"])
    owner = await load_principal(t03_session, demo["user_id"])
    learner = Principal(
        user_id=owner.user_id,
        email=owner.email,
        roles=("student",),
        permissions=frozenset({"learn.read"}),
        scope_tags=(str(demo["course_id"]),),
        is_superadmin=False,
    )
    # the demo upload is content-addressed, so a second demo course shares the
    # passage; a course that never bound it is the honest outsider
    with pytest.raises(NotFoundError):
        await preview_passage(t03_session, uuid4(), UUID(chip["passage_id"]), language="en")
    with pytest.raises(NotFoundError):
        await course_outline(t03_session, learner, demo["course_id"], language="en", threshold=0.8)


async def test_recheck_on_an_unpublished_course_holds_an_accepted_block_a_current_check_rejects(
    t03_session: AsyncSession,
) -> None:
    """A check added after the run reaches the blocks the run accepted, only when asked."""
    from app.services.recheck import recheck_held

    demo, dag = await _smoke_course_and_dag(t03_session, "smoke-recheck-accepted")
    final = await _smoke_run(t03_session, demo, dag, demo_t05.mock_demo_handler())
    assert final["status"] == "done", final["stop_marker"]
    course = str(demo["course_id"])
    version = (
        await t03_session.execute(
            sa.text(
                "SELECT b.current_version_id FROM blocks b JOIN topics t ON t.id = b.topic_id"
                " JOIN modules m ON m.id = t.module_id"
                " WHERE m.course_id = :c AND b.plan_node_id = 'b1'"
            ),
            {"c": course},
        )
    ).scalar_one()
    await t03_session.execute(
        sa.text("UPDATE citations SET quote = 'words no passage says' WHERE block_version_id = :v"),
        {"v": str(version)},
    )
    await t03_session.commit()
    kept = await recheck_held(t03_session, course_id=demo["course_id"])
    await t03_session.commit()
    assert (await _smoke_blocks(t03_session, demo["course_id"]))["b1"][0] == "accepted"
    assert kept["newly_held"] == 0, kept
    result = await recheck_held(t03_session, course_id=demo["course_id"], hold_accepted=True)
    await t03_session.commit()
    status, reason = (await _smoke_blocks(t03_session, demo["course_id"]))["b1"]
    assert status == "held" and reason.startswith("quote is not verbatim"), (status, reason)
    assert result["newly_held"] == 1, result


async def test_recheck_releases_a_block_held_by_a_stored_narration_and_code_line(
    t03_session: AsyncSession,
) -> None:
    """The reason the executor stored for a draft that has since been fixed is dropped whole."""
    from app.course.text_checks import narration_holds, python_code_holds
    from app.services.recheck import recheck_held

    demo, dag = await _smoke_course_and_dag(t03_session, "smoke-recheck-lines")
    final = await _smoke_run(t03_session, demo, dag, demo_t05.mock_demo_handler())
    assert final["status"] == "done", final["stop_marker"]
    lines = [
        *narration_holds("The values are not given in the passages.", []),
        *python_code_holds("```python\nx = (1\n```"),
    ]
    assert len(lines) == 2
    await t03_session.execute(
        sa.text(
            "UPDATE blocks SET status = 'held', hold_reason = :r, accepted_version_id = NULL"
            " WHERE plan_node_id = 'b1' AND topic_id IN (SELECT t.id FROM topics t"
            " JOIN modules m ON m.id = t.module_id WHERE m.course_id = :c)"
        ),
        {"r": "; ".join(lines), "c": str(demo["course_id"])},
    )
    await t03_session.commit()
    result = await recheck_held(t03_session, course_id=demo["course_id"])
    await t03_session.commit()
    assert (await _smoke_blocks(t03_session, demo["course_id"]))["b1"] == ("accepted", "")
    assert result["released"] == 1, result


async def test_recheck_keeps_a_block_the_author_accepted_by_hand_when_a_check_would_hold_it(
    t03_session: AsyncSession,
) -> None:
    """accept_block pins the version; the sweep holds only what the run accepted on its own."""
    from app.services.courses import accept_block
    from app.services.recheck import recheck_held

    demo, dag = await _smoke_course_and_dag(t03_session, "smoke-recheck-hand")
    final = await _smoke_run(t03_session, demo, dag, demo_t05.mock_demo_handler())
    assert final["status"] == "done", final["stop_marker"]
    course = str(demo["course_id"])
    versions = {}
    for node in ("b1", "b3"):
        versions[node] = (
            await t03_session.execute(
                sa.text(
                    "SELECT b.id, b.current_version_id FROM blocks b JOIN topics t"
                    " ON t.id = b.topic_id JOIN modules m ON m.id = t.module_id"
                    " WHERE m.course_id = :c AND b.plan_node_id = :n"
                ),
                {"c": course, "n": node},
            )
        ).one()
        await t03_session.execute(
            sa.text(
                "UPDATE citations SET quote = 'words no passage says' WHERE block_version_id = :v"
            ),
            {"v": str(versions[node][1])},
        )
    await t03_session.execute(
        sa.text("UPDATE blocks SET status = 'held', hold_reason = 'x' WHERE id = :b"),
        {"b": str(versions["b1"][0])},
    )
    await accept_block(t03_session, block_id=versions["b1"][0])
    await t03_session.commit()
    result = await recheck_held(t03_session, course_id=demo["course_id"], hold_accepted=True)
    await t03_session.commit()
    blocks = await _smoke_blocks(t03_session, demo["course_id"])
    assert blocks["b1"] == ("accepted", ""), blocks["b1"]
    assert blocks["b3"][0] == "held", blocks["b3"]
    assert result["newly_held"] == 1, result
