"""Plan runs: operations, author editing, resumable executor, no-double-pay.

submit_plan and submit_generate persist operations with a per-actor
idempotency key; the author edits the plan tree and validate lists
blocking questions; generation refuses while a required question is
open. A planted 500 stops the run with a marker; resume continues
from the last good step and no done step is paid twice; a second
identical run through the guarded transport pays zero.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from uuid import UUID

import httpx2
import pytest
import sqlalchemy as sa
from conftest import test_database_url
from sqlalchemy.ext.asyncio import AsyncSession
from t02_helpers import make_session_factory
from transport_guard import guarded_client

from app.config import Settings
from app.providers.llm import MetaProvider
from app.services import courses as course_service
from app.services import library, planruns

BASE = "https://api.meta.ai/v1"


def _settings(**overrides: Any) -> Settings:
    return Settings(
        _env_file=None,
        model_api_key="synthetic-test-key",
        model_api_base_url=BASE,
        llm_spend_ceiling_usd=50.0,
        source_store_path="/tmp/muse-t03-sources",
        **overrides,
    )


def _completed(text: str) -> dict[str, Any]:
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


def _architect_output() -> dict[str, Any]:
    import pathlib

    return json.loads(pathlib.Path("tests/fixtures/architect_output.json").read_text())


def _mock_handler(fail_architect: bool = False) -> Any:
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content.decode("utf-8"))
        prompt = str(body.get("input", ""))
        seen.append(prompt[:40])
        if fail_architect and prompt.startswith("# Architect"):
            return httpx2.Response(500, json={"error": {"message": "planted 500"}})
        if prompt.startswith("# Researcher"):
            dynamic = json.loads(prompt.split("## Task input (typed, assembled by code)\n")[1])
            ranked = [
                {"handle": item["handle"], "rank": index, "score": 0.9 - index * 0.1}
                for index, item in enumerate(dynamic["passages"][:3], 1)
            ]
            reply = {
                "query": dynamic["query"],
                "rephrased": dynamic["rephrased"],
                "ranked": ranked,
                "web_candidates": [
                    {
                        "url": "https://example.com/act",
                        "title": "AI Act",
                        "why": "covers the gap",
                    }
                ],
            }
            return httpx2.Response(200, json=_completed(json.dumps(reply)))
        return httpx2.Response(200, json=_completed(json.dumps(_architect_output())))

    handler.seen = seen  # type: ignore[attr-defined]
    return handler


def _provider_factory(http: httpx2.AsyncClient, ledger: Any, settings: Settings) -> Any:
    def make(session: AsyncSession) -> MetaProvider:
        return MetaProvider(session, settings, http, ledger)

    return make


async def _course_with_source(session: AsyncSession) -> tuple[UUID, UUID]:
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
    settings = _settings()
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


def _actor(user_id: UUID, codes: frozenset[str]) -> Any:
    from app.rbac.principal import Principal

    return Principal(
        user_id=user_id,
        email="plan@example.com",
        roles=("author",),
        permissions=codes,
        scope_tags=(),
        is_superadmin=False,
    )


async def _drive(
    session: AsyncSession, run_id: UUID, handler: Any, settings: Settings
) -> dict[str, Any]:
    from app.db import create_session_factory
    from app.runs import executor

    url = test_database_url()
    ledger = create_session_factory(url)
    http = guarded_client(settings, inner=httpx2.MockTransport(handler))
    return await executor.drive_run(
        create_session_factory(url),
        run_id=run_id,
        provider_factory=_provider_factory(http, ledger, settings),
        handlers={
            "researcher": executor.plan_researcher_step,
            "architect": executor.plan_architect_step,
        },
    )


async def test_plan_submit_writes_run_and_drives_to_done(t03_session: AsyncSession) -> None:
    from app.runs import executor

    user_id, course_id = await _course_with_source(t03_session)
    actor = _actor(user_id, frozenset({"run.start", "operation.run", "course.edit", "course.plan"}))
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
    settings = _settings()
    final = await _drive(t03_session, run_id, _mock_handler(), settings)
    assert final["status"] == "done"
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
    user_id, course_id = await _course_with_source(t03_session)
    actor = _actor(user_id, frozenset({"run.start", "operation.run", "course.edit", "course.plan"}))
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
    settings = _settings()
    stopped = await _drive(t03_session, run_id, _mock_handler(fail_architect=True), settings)
    assert stopped["status"] == "stopped"
    assert stopped["stop_marker"] == "The model service did not answer. Try again in a minute."
    assert any(
        step["step_key"] == "architect:plan" and step["status"] == "failed"
        for step in stopped["steps"]
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
        actor=_actor(user_id, frozenset({"run.retry"})),
        run_id=run_id,
        defer=None,
    )
    assert resumed == run_id
    handler = _mock_handler()
    final = await _drive(t03_session, run_id, handler, settings)
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
    user_id, course_id = await _course_with_source(t03_session)
    actor = _actor(user_id, frozenset({"run.start", "operation.run", "course.edit", "course.plan"}))
    settings = _settings()
    run_ids = []
    for _ in range(2):
        operation_id = await planruns.submit_plan(
            t03_session,
            actor=actor,
            course_id=course_id,
            queries=["literacy duty"],
            defer=None,
        )
        await t03_session.commit()
        detail = await planruns.run_plan_operation(t03_session, operation_id)
        run_ids.append(UUID(str(detail["run_id"])))
    first_handler = _mock_handler()
    first = await _drive(t03_session, run_ids[0], first_handler, settings)
    assert first["status"] == "done"
    assert first_handler.seen != []
    second_handler = _mock_handler()
    second = await _drive(t03_session, run_ids[1], second_handler, settings)
    assert second["status"] == "done"
    assert second_handler.seen == []


async def test_generate_refuses_while_questions_open(t03_session: AsyncSession) -> None:
    user_id, course_id = await _course_with_source(t03_session)
    await course_service.set_profile(
        t03_session,
        course_id=course_id,
        methodology_codes=["direct_instruction"],
        bloom_level="understand",
        audience="staff",
        constraints=[
            {
                "kind": "scope",
                "key": "scope_act",
                "value": {},
                "required": True,
                "question": "Which act must the course cover?",
            }
        ],
    )
    await t03_session.commit()
    actor = _actor(user_id, frozenset({"run.start", "operation.run", "course.generate"}))
    questions = await course_service.blocking_questions(t03_session, course_id=course_id)
    assert len(questions) == 1
    with pytest.raises(Exception, match="answer required questions first"):
        await planruns.submit_generate(t03_session, actor=actor, course_id=course_id)
    await course_service.edit_plan(
        t03_session,
        course_id=course_id,
        operations=[{"op": "set_constraint", "key": "scope_act", "answer": {"act": "AI Act"}}],
    )
    await t03_session.commit()
    from app.errors import UnprocessableError

    with pytest.raises(UnprocessableError, match="plan is empty"):
        await planruns.submit_generate(t03_session, actor=actor, course_id=course_id, defer=None)
    await t03_session.execute(
        sa.text("INSERT INTO modules (course_id, ordinal, title) VALUES (:c, 0, 'Module')"),
        {"c": str(course_id)},
    )
    await t03_session.commit()
    operation_id = await planruns.submit_generate(
        t03_session, actor=actor, course_id=course_id, defer=None
    )
    assert operation_id is not None


async def test_plan_edit_and_idempotent_submit(t03_session: AsyncSession) -> None:
    user_id, course_id = await _course_with_source(t03_session)
    actor = _actor(user_id, frozenset({"run.start", "operation.run", "course.edit", "course.plan"}))
    first = await planruns.submit_plan(
        t03_session,
        actor=actor,
        course_id=course_id,
        queries=["duty"],
        idempotency_key="plan-1",
        defer=None,
    )
    await t03_session.commit()
    second = await planruns.submit_plan(
        t03_session,
        actor=actor,
        course_id=course_id,
        queries=["duty"],
        idempotency_key="plan-1",
        defer=None,
    )
    await t03_session.commit()
    assert first == second
    settings = _settings()
    detail = await planruns.run_plan_operation(t03_session, first)
    run_id = UUID(str(detail["run_id"]))
    await _drive(t03_session, run_id, _mock_handler(), settings)
    tree = await course_service.read_tree(t03_session, course_id=course_id)
    module_id = tree["modules"][0]["id"]
    topic_id = tree["modules"][0]["topics"][0]["id"]
    edited = await course_service.edit_plan(
        t03_session,
        course_id=course_id,
        operations=[
            {"op": "rename_module", "id": module_id, "title": "Renamed"},
            {"op": "move_topic", "id": topic_id, "ordinal": 0},
        ],
    )
    await t03_session.commit()
    assert edited["modules"][0]["title"] == "Renamed"
    version_id = await course_service.snapshot_version(t03_session, course_id=course_id)
    await t03_session.commit()
    assert isinstance(version_id, UUID)


async def test_citation_quote_must_be_verbatim() -> None:
    from app.runs.executor import verify_citations

    passages = {"p1": "Article 4 requires AI literacy among staff."}
    ok = [
        {
            "passage_id": "p1",
            "claim_path": "blocks.b1",
            "quote": "AI literacy",
            "quote_start": 19,
            "quote_end": 30,
        }
    ]
    assert verify_citations(ok, passages) == []
    invented = [
        {
            "passage_id": "p1",
            "claim_path": "blocks.b1",
            "quote": "AI obscurity",
            "quote_start": 0,
            "quote_end": 12,
        }
    ]
    holds = verify_citations(invented, passages)
    assert len(holds) == 1 and "verbatim" in holds[0]


async def test_step_claim_reuses_same_digest(t03_session: AsyncSession) -> None:
    from app.runs.executor import claim_step, finish_step, new_run

    user_id, course_id = await _course_with_source(t03_session)
    run_id = await new_run(
        t03_session,
        course_id=course_id,
        kind="plan",
        dag=[{"step_key": "researcher:0", "handler": "researcher"}],
    )
    await t03_session.commit()
    first = await claim_step(
        t03_session, run_id=run_id, step_key="researcher:0", input_digest="abc"
    )
    assert first["reused_output"] is None
    await finish_step(t03_session, step_id=first["step_id"], output={"ranked": []})
    await t03_session.commit()
    second = await claim_step(
        t03_session, run_id=run_id, step_key="researcher:0", input_digest="abc"
    )
    assert second["step_id"] == first["step_id"]
    assert second["reused_output"] == {"ranked": []}
    third = await claim_step(
        t03_session, run_id=run_id, step_key="researcher:0", input_digest="changed"
    )
    assert third["reused_output"] is None
