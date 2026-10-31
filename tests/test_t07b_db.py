"""T07b DB probes: wiring, TM scope, bad-reply isolation, held recovery."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import demo_t05
import httpx2
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

NB_PREFIX = "Dette er og skal være slik at det ikke bare gjelder: "
UK_PREFIX = "Це є та що повинно бути так коли хто працює і не тільки: "


def _settings(**overrides: Any) -> Any:
    from app.config import Settings

    return Settings(
        _env_file=None,
        model_api_key="synthetic-test-key",
        model_api_base_url="https://api.meta.ai/v1",
        llm_spend_ceiling_usd=50.0,
        source_store_path="/tmp/muse-t07b-demo",
        **overrides,
    )


def nb_handler(*, bad_node: str = "", echo_first: bool = False) -> Any:
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
            out.append({"id": seg["id"], "text": f"{NB_PREFIX}{text} [nb]"})
        if echo_first:
            state["echoed"] = True
        # bad block: return wrong ids for the block whose stem marker is set
        if bad_node and any(bad_node in str(s["text"]) for s in segments):
            out = [{"id": "wrong-id", "text": "Feil og dette er galt."}]
        return httpx2.Response(200, json=_completed(json.dumps({"segments": out})))

    handler.state = state  # type: ignore[attr-defined]
    return handler


async def _drive(run_id: UUID, handler: Any, settings: Any) -> Any:
    from conftest import test_database_url as _db_url
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


async def _setup(t03_session: AsyncSession, settings: Any, prefix: str) -> Any:
    from app.services import courses

    demo = await demo_t05.setup_demo(
        t03_session, settings=settings, email=demo_t05.fresh_email(prefix)
    )
    version_id = await courses.snapshot_version(t03_session, course_id=demo["course_id"])
    await t03_session.commit()
    return demo, version_id


def _blocks() -> list[dict[str, Any]]:
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


async def _statuses(session: AsyncSession, version_id: Any, lang: str = "nb") -> Any:
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


async def test_tm_scoped_per_course_and_refreshable(t03_session: AsyncSession) -> None:
    from app.translation.memory import tm_lookup, tm_store

    await tm_store(
        t03_session,
        source_text="Hello staff.",
        translated_text="Hei ansatte.",
        source_language="en",
        target_language="nb",
        course_id="11111111-1111-1111-1111-111111111111",
    )
    assert (
        await tm_lookup(
            t03_session,
            source_text="Hello staff.",
            source_language="en",
            target_language="nb",
            course_id="11111111-1111-1111-1111-111111111111",
        )
        == "Hei ansatte."
    )
    assert (
        await tm_lookup(
            t03_session,
            source_text="Hello staff.",
            source_language="en",
            target_language="nb",
            course_id="22222222-2222-2222-2222-222222222222",
        )
        is None
    )
    await tm_store(
        t03_session,
        source_text="Hello staff.",
        translated_text="Hei alle ansatte.",
        source_language="en",
        target_language="nb",
        course_id="11111111-1111-1111-1111-111111111111",
    )
    assert (
        await tm_lookup(
            t03_session,
            source_text="Hello staff.",
            source_language="en",
            target_language="nb",
            course_id="11111111-1111-1111-1111-111111111111",
        )
        == "Hei alle ansatte."
    )
    await tm_store(
        t03_session,
        source_text="Planted.",
        translated_text="Plantet.",
        source_language="en",
        target_language="nb",
        planted=True,
    )
    assert (
        await tm_lookup(
            t03_session, source_text="Planted.", source_language="en", target_language="nb"
        )
        is None
    )


async def test_new_run_stores_kind(t03_session: AsyncSession) -> None:
    from app.runs import executor

    demo, _v = await _setup(t03_session, _settings(), "t07kind")
    run_id = await executor.new_run(
        t03_session, course_id=demo["course_id"], kind="translation", dag=[]
    )
    await t03_session.commit()
    kind = (
        await t03_session.execute(
            sa.text("SELECT kind FROM runs WHERE id = :r"), {"r": str(run_id)}
        )
    ).scalar()
    assert kind == "translation"
    assert set(executor.translation_handlers()) == {"translate_block", "translate_glossary"}
    from app.services.planruns import plan_handlers

    handlers = plan_handlers()
    assert handlers["translate_block"] is executor.translate_block_step
    assert handlers["translate_glossary"] is executor.translate_glossary_step


async def test_echo_held_no_tm_and_localised_fallback(t03_session: AsyncSession) -> None:
    from app.translation.pipeline import translate_course
    from app.translation.variants import learner_block

    settings = _settings()
    demo, version_id = await _setup(t03_session, settings, "t07echo")
    blocks = _blocks()[:1]
    handler = nb_handler(echo_first=True)
    await translate_course(
        t03_session,
        settings,
        course_id=demo["course_id"],
        version_id=version_id,
        source_language="en",
        target_languages=["nb"],
        blocks=blocks,
        glossary={},
        driver=lambda rid: _drive(rid, handler, settings),
    )
    statuses = await _statuses(t03_session, version_id)
    assert statuses["b1"][0] == "held", statuses
    assert "reply not in target language" in statuses["b1"][1]
    tm = (await t03_session.execute(sa.text("SELECT count(*) FROM translation_memory"))).scalar()
    assert int(tm or 0) == 0
    resolved = await learner_block(
        t03_session,
        course_id=demo["course_id"],
        language="nb",
        block=blocks[0],
        source_language="en",
    )
    assert resolved["language"] == "en"
    assert resolved["content"] == blocks[0]["content"]
    assert resolved["fallback_notice"] and "oversatt" in resolved["fallback_notice"]
    same = await learner_block(
        t03_session,
        course_id=demo["course_id"],
        language="en",
        block=blocks[0],
        source_language="en",
    )
    assert same["fallback_notice"] is None


async def test_bad_reply_holds_one_block_run_done(t03_session: AsyncSession) -> None:
    from app.translation.pipeline import translate_course

    settings = _settings()
    demo, version_id = await _setup(t03_session, settings, "t07bad")
    handler = nb_handler(bad_node="BADBLOCK")
    run_id = await translate_course(
        t03_session,
        settings,
        course_id=demo["course_id"],
        version_id=version_id,
        source_language="en",
        target_languages=["nb"],
        blocks=_blocks(),
        glossary={},
        driver=lambda rid: _drive(rid, handler, settings),
    )
    run = (
        await t03_session.execute(
            sa.text("SELECT status FROM runs WHERE id = :r"), {"r": str(run_id)}
        )
    ).scalar()
    assert run == "done", run
    statuses = await _statuses(t03_session, version_id)
    assert statuses["b1"][0] == "ok", statuses
    assert statuses["b2"][0] == "held", statuses


async def test_held_block_recovers_on_second_run(t03_session: AsyncSession) -> None:
    from app.translation.pipeline import translate_course

    settings = _settings()
    demo, version_id = await _setup(t03_session, settings, "t07rec")
    blocks = _blocks()[:1]
    handler = nb_handler(echo_first=True)
    await translate_course(
        t03_session,
        settings,
        course_id=demo["course_id"],
        version_id=version_id,
        source_language="en",
        target_languages=["nb"],
        blocks=blocks,
        glossary={},
        driver=lambda rid: _drive(rid, handler, settings),
    )
    assert (await _statuses(t03_session, version_id))["b1"][0] == "held"
    handler2 = nb_handler()
    await translate_course(
        t03_session,
        settings,
        course_id=demo["course_id"],
        version_id=version_id,
        source_language="en",
        target_languages=["nb"],
        blocks=blocks,
        glossary={},
        driver=lambda rid: _drive(rid, handler2, settings),
    )
    assert (await _statuses(t03_session, version_id))["b1"][0] == "ok"


async def test_translation_run_resumes_through_worker(t03_session: AsyncSession) -> None:
    """A stopped translation run resumes: handlers registered, rows stored."""
    from conftest import test_database_url as _db_url

    from app.db import create_session_factory
    from app.rbac.principal import Principal
    from app.runs import executor
    from app.services import planruns
    from app.translation.pipeline import compile_translation_dag

    settings = _settings()
    demo, version_id = await _setup(t03_session, settings, "t07resume")
    dag = compile_translation_dag(
        blocks=_blocks()[:1],
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

    http = guarded_client(settings, inner=httpx2.MockTransport(nb_handler()))
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
    statuses = await _statuses(t03_session, version_id)
    assert statuses["b1"][0] == "ok", statuses


async def test_submit_translate_operation_product_path(t03_session: AsyncSession) -> None:
    """submit_translate -> worker body -> rows + learner view (mock model)."""
    from conftest import test_database_url as _db_url

    from app.db import create_session_factory
    from app.rbac.principal import Principal
    from app.services import courses, planruns

    settings = _settings()
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
        ("b1", _blocks()[0]["content"], {}),
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

    http = guarded_client(settings, inner=httpx2.MockTransport(nb_handler()))
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
    statuses = await _statuses(t03_session, version_id)
    assert statuses and all(v[0] in ("ok", "held") for v in statuses.values()), statuses
    view = await planruns.learner_course_view(t03_session, course_id=course_id, language="nb")
    assert view["blocks"], view
    assert view["available_languages"], view
