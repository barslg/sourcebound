"""T07b live acceptance: DEMO course en -> nb and en -> uk via the product.

POST /api/v1/courses/{id}/translate {languages:[nb,uk]} -> 202 operation
-> worker drives a translation run -> status done; course_translations
rows per language; every block ok|held with reason; a learner GET with
?lang=nb receives nb content (held blocks: source + localised notice);
the planted sentence survives re-derived by kind; a correct answer on a
translated exercise scores the same as on the English one; a second run
pays zero; cost printed.

The demo course is the one T05b builds: setup_demo + plan tree +
submit_generate through the product path on the real API.
"""

from __future__ import annotations

import json
import os
from typing import Any
from uuid import UUID

import demo_t05
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.live


def _live_settings(key: str) -> Any:
    from app.config import Settings

    return Settings(
        _env_file=None,
        model_api_key=key,
        source_store_path="/tmp/muse-t07-live-sources",
        llm_spend_ceiling_usd=25.0,
    )


def _provider_factory(settings: Any, ledger: Any) -> Any:
    from transport_guard import guarded_client

    from app.providers.llm import MetaProvider

    http = guarded_client(settings)

    def make(session: AsyncSession) -> MetaProvider:
        return MetaProvider(session, settings, http, ledger)

    return make


async def _write_demo_tree(session: AsyncSession, *, course_id: UUID) -> None:
    from app.course.tree import BlockSkeleton, CourseTree, ModuleNode, SourceRef, TopicNode
    from app.services.courses import write_tree

    def block(node: str, role: str, btype: str, brief: str) -> BlockSkeleton:
        return BlockSkeleton(
            plan_node_id=node,
            role=role,
            block_type=btype,
            brief=brief,
            source_refs=[SourceRef(handle="P1")],
        )

    tree = CourseTree(
        modules=[
            ModuleNode(
                plan_node_id="m1",
                title="Duties and risk tiers",
                purpose="Teach duties",
                entry_knowledge="none",
                exit_knowledge="duties",
                topics=[
                    TopicNode(
                        plan_node_id="t1",
                        kind="lesson",
                        title="Who must ensure AI literacy",
                        objectives=["name the duty"],
                        through_line_note="case",
                        blocks=[
                            block("b1", "teach", "reading", "AI literacy duty"),
                            block("b2", "check", "choice", "Who must ensure AI literacy?"),
                        ],
                    ),
                    TopicNode(
                        plan_node_id="t2",
                        kind="lesson",
                        title="Prohibitions and fines",
                        objectives=["name prohibitions"],
                        through_line_note="case",
                        blocks=[
                            block("b3", "teach", "reading", "Prohibited practices"),
                            block("b4", "practice", "spot_the_error", "Find the error?"),
                        ],
                    ),
                ],
            )
        ]
    )
    await write_tree(session, course_id=course_id, tree=tree, plan_digest="live-t07")
    await session.commit()


async def _build_demo_course(t03_session: AsyncSession, settings: Any) -> tuple[UUID, UUID, UUID]:
    """T05b product path: submit_generate -> worker drive -> run done."""
    from conftest import test_database_url as _db_url

    from app.db import create_session_factory
    from app.rbac.principal import Principal
    from app.services import courses, planruns

    demo = await demo_t05.setup_demo(
        t03_session, settings=settings, email=demo_t05.fresh_email("t07live")
    )
    course_id: UUID = demo["course_id"]
    await _write_demo_tree(t03_session, course_id=course_id)
    actor = Principal(
        user_id=demo["user_id"],
        email="live@example.com",
        roles=("author",),
        permissions=frozenset({"course.generate", "course.plan", "run.retry"}),
        scope_tags=(),
        is_superadmin=False,
    )
    op_id = await planruns.submit_generate(t03_session, actor=actor, course_id=course_id)
    factory = create_session_factory(_db_url())
    result = await planruns.run_generate_operation(
        t03_session,
        op_id,
        provider_factory=_provider_factory(settings, factory),
        session_factory=factory,
    )
    run_id = UUID(result["run_id"])
    row = (
        await t03_session.execute(
            sa.text("SELECT status FROM runs WHERE id = :r"), {"r": str(run_id)}
        )
    ).first()
    assert row is not None and row[0] == "done", f"generation run status {row}"
    version_id = await courses.snapshot_version(t03_session, course_id=course_id)
    await t03_session.commit()
    return course_id, version_id, demo["user_id"]


async def test_live_translation_acceptance(t03_session: AsyncSession, capsys: Any) -> None:
    from conftest import test_database_url as _db_url

    from app.db import create_session_factory
    from app.services import planruns
    from app.translation.checks import verify_spot_error

    key = os.environ.get("MODEL_API_KEY")
    if not key:
        pytest.skip("MODEL_API_KEY is not set")
    settings = _live_settings(key)
    course_id, version_id, _user_id = await _build_demo_course(t03_session, settings)
    factory = create_session_factory(_db_url())
    make = _provider_factory(settings, factory)

    from app.rbac.principal import Principal

    # Product submit path (the POST /courses/{id}/translate route is a
    # thin wrapper over submit_translate; see test_t07b_db for the
    # operation body and routes inventory for the route itself).
    actor_row = (
        await t03_session.execute(
            sa.text("SELECT owner_id FROM courses WHERE id = :c"), {"c": str(course_id)}
        )
    ).first()
    actor = Principal(
        user_id=UUID(str(actor_row[0])),
        email="live@example.com",
        roles=("author",),
        permissions=frozenset({"course.generate"}),
        scope_tags=(),
        is_superadmin=False,
    )
    op_id = await planruns.submit_translate(
        t03_session, actor=actor, course_id=course_id, languages=["nb", "uk"]
    )
    result = await planruns.run_translate_operation(
        t03_session, op_id, provider_factory=make, session_factory=factory
    )
    run_id = UUID(result["run_id"])
    run_row = (
        await t03_session.execute(
            sa.text("SELECT status, kind FROM runs WHERE id = :r"), {"r": str(run_id)}
        )
    ).first()
    assert run_row is not None and run_row[0] == "done" and run_row[1] == "translation", run_row

    report: dict[str, Any] = {}
    for language in ("nb", "uk"):
        rows = (
            await t03_session.execute(
                sa.text(
                    "SELECT tb.plan_node_id, tb.status, tb.reason, tb.content,"
                    " tb.translated_answer_key"
                    " FROM translation_blocks tb"
                    " JOIN course_translations ct ON ct.id = tb.translation_id"
                    " WHERE ct.course_version_id = :v AND ct.language = :l"
                    " ORDER BY tb.plan_node_id"
                ),
                {"v": str(version_id), "l": language},
            )
        ).all()
        assert rows, f"no translation blocks for {language}"
        for _node, status, _reason, _content, _tkey in rows:
            assert status in ("ok", "held"), (language, _node, status, _reason)
        report[language] = [
            {"node": str(r[0]), "status": str(r[1]), "reason": str(r[2] or "")} for r in rows
        ]
        # learner view through the product read path
        view = await planruns.learner_course_view(
            t03_session, course_id=course_id, language=language
        )
        assert view["blocks"], language
        for block in view["blocks"]:
            if block["language"] == language:
                assert block["fallback_notice"] is None
            else:
                assert block["fallback_notice"], (language, block)
        # planted sentence survives, re-derived by kind
        spot = next((r for r in rows if str(r[0]) == "b4"), None)
        assert spot is not None, f"no spot block in {language}"
        src_rows = (
            await t03_session.execute(
                sa.text(
                    "SELECT bv.content, bv.answer_key FROM blocks b"
                    " JOIN block_versions bv ON bv.id = b.current_version_id"
                    " WHERE b.plan_node_id = 'b4'"
                )
            )
        ).first()
        assert src_rows is not None
        src_content, src_key = dict(src_rows[0] or {}), dict(src_rows[1] or {})
        tkey = dict(spot[4] or {})
        planted_survived = (
            spot[1] == "ok"
            and verify_spot_error(
                {
                    **src_content,
                    "planted_index": src_key.get("planted_index"),
                    "kind": src_key.get("kind"),
                },
                {
                    **dict(spot[3] or {}),
                    "planted_index": src_key.get("planted_index"),
                    "kind": src_key.get("kind"),
                },
                {**src_key, "translated_true_sentence": tkey.get("true_sentence", "")},
            )
            == []
        )
        report[language + "_planted_survived"] = planted_survived
        assert planted_survived, (language, spot[1], spot[2])
        # output language measured, not assumed
        from app.services.library import detect_language

        reading = next((r for r in rows if str(r[0]) == "b1"), None)
        if reading is not None and reading[1] == "ok":
            body = str(dict(reading[3] or {}).get("body", ""))
            assert detect_language(body) == language, (language, body[:80])

    # grading parity on the translated choice + spot exercises (nb)
    parity: dict[str, Any] = {}
    for block_type, node in (("choice", "b2"), ("spot_the_error", "b4")):
        from importlib import import_module

        grade = import_module(f"app.blocks.{block_type}.check").grade
        src = (
            await t03_session.execute(
                sa.text(
                    "SELECT bv.content, bv.answer_key FROM blocks b"
                    " JOIN block_versions bv ON bv.id = b.current_version_id"
                    " WHERE b.plan_node_id = :n"
                ),
                {"n": node},
            )
        ).first()
        tr = (
            await t03_session.execute(
                sa.text(
                    "SELECT tb.content, tb.translated_answer_key FROM translation_blocks tb"
                    " JOIN course_translations ct ON ct.id = tb.translation_id"
                    " WHERE ct.language = 'nb' AND tb.plan_node_id = :n"
                ),
                {"n": node},
            )
        ).first()
        assert src is not None and tr is not None
        src_content, src_key = dict(src[0] or {}), dict(src[1] or {})
        tr_content, tr_key = dict(tr[0] or {}), dict(tr[1] or {})
        if block_type == "choice":
            picked = src_key.get("correct")
            en = grade(src_content, src_key, {"picked": picked})
            nb = grade(tr_content, tr_key, {"picked": tr_key.get("correct", picked)})
        else:
            en = grade(
                src_content,
                src_key,
                {
                    "sentence": str(src_key.get("planted_sentence", "")),
                    "kind": str(src_key.get("kind", "")),
                    "passage": str(src_key.get("passage", "")),
                    "correct_value": str(src_key.get("true_sentence", "")),
                },
            )
            nb = grade(
                tr_content,
                tr_key,
                {
                    "sentence": str(tr_key.get("planted_sentence", "")),
                    "kind": str(tr_key.get("kind", "")),
                    "passage": str(tr_key.get("passage", "")),
                    "correct_value": str(tr_key.get("true_sentence", "")),
                },
            )
        parity[node] = {"en": en["score"], "nb": nb["score"]}
        assert nb["score"] == en["score"] == 1.0, (node, parity[node])

    cost = (
        await t03_session.execute(
            sa.text(
                "SELECT COALESCE(SUM(cost_usd),0), COALESCE(SUM(tokens_cached),0),"
                " count(*) FILTER (WHERE cost_usd > 0) FROM llm_calls WHERE run_id = :r"
            ),
            {"r": str(run_id)},
        )
    ).first()
    assert cost is not None and float(cost[0]) > 0, "first translation run must pay"

    # second identical run pays zero
    op2 = await planruns.submit_translate(
        t03_session, actor=actor, course_id=course_id, languages=["nb", "uk"]
    )
    result2 = await planruns.run_translate_operation(
        t03_session, op2, provider_factory=make, session_factory=factory
    )
    paid2 = (
        await t03_session.execute(
            sa.text("SELECT count(*) FROM llm_calls WHERE run_id = :r AND cost_usd > 0"),
            {"r": result2["run_id"]},
        )
    ).scalar()
    assert int(paid2 or 0) == 0, f"second run paid {paid2}"

    print(
        "LIVE-T07 "
        + json.dumps(
            {
                "run_status": "done",
                "per_language": report,
                "grading_parity": parity,
                "cost_usd": str(cost[0]),
                "cached_tokens": int(cost[1]),
                "paid_calls": int(cost[2]),
                "second_run_paid": int(paid2 or 0),
            },
            ensure_ascii=False,
        )
    )
    captured = capsys.readouterr()
    assert "LIVE-T07" in captured.out and "cost_usd" in captured.out
