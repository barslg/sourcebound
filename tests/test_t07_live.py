"""T07b live acceptance: DEMO course en -> nb and en -> uk via the product.

POST /api/v1/courses/{id}/translate {languages:[nb,uk]} -> 202 operation
-> worker drives a translation run -> status done; course_translations
rows per language; every block ok|held with reason; a learner GET with
?lang=nb receives nb content (held blocks: source + localised notice);
the planted sentence survives re-derived by kind; a correct answer on a
translated exercise scores the same as on the English one; a second run
pays zero; cost printed.

The demo course is the one T05b builds: setup_demo + plan tree +
submit_generate through the product path on the real API. T07c: the
acceptance line is printed OUTSIDE capture and appended to
tmp/t07_acceptance.txt, the ledger is copied to
tests/fixtures/t07_live_ledger.json before any wipe, grading parity
uses submissions built from the model's raw replies (never from the
stored key), and the tree carries a cloze block. A 402 from the Model
API at the first call stops the test with a billing message.
"""

from __future__ import annotations

import json
import os
import pathlib
from decimal import Decimal
from typing import Any
from uuid import UUID

import demo_t05
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.live

LEDGER_PATH = pathlib.Path("tests/fixtures/t07_live_ledger.json")


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
                            block("b5", "practice", "cloze", "Complete the literacy duty"),
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


def _write_ledger(rows: list[Any]) -> None:
    """Copy the live ledger to the tracked fixture; the previous copy stays beside it."""
    previous = LEDGER_PATH.with_suffix(".prev.json")
    payload = json.dumps(
        [
            {
                "agent": r[0],
                "model": r[1],
                "cost_usd": str(r[2]),
                "tokens_cached": int(r[3] or 0),
                "request": r[4],
                "response": r[5],
                "status": r[6],
            }
            for r in rows
        ],
        ensure_ascii=False,
        default=str,
    )
    if LEDGER_PATH.exists() and LEDGER_PATH.read_text(encoding="utf-8") != payload:
        previous.write_bytes(LEDGER_PATH.read_bytes())  # never overwrite evidence silently
    LEDGER_PATH.write_text(payload, encoding="utf-8")


async def _export_ledger(session: AsyncSession, run_ids: list[UUID]) -> Decimal:
    """Copy this test's ledger rows out of the DB before any wipe; returns the cost."""
    rows = (
        await session.execute(
            sa.text(
                "SELECT agent, model, cost_usd, tokens_cached, request, response, status"
                " FROM llm_calls WHERE run_id::text = ANY(:ids) ORDER BY created_at"
            ),
            {"ids": [str(r) for r in run_ids]},
        )
    ).all()
    if rows:
        _write_ledger(list(rows))
    return sum((Decimal(str(r[2])) for r in rows), Decimal(0))


def _announce(capfd: pytest.CaptureFixture[str], line: str) -> None:
    """Print the acceptance line on the real stdout (capture cannot swallow it)."""
    with capfd.disabled():
        print(line, flush=True)
    scratch = pathlib.Path("tmp")
    scratch.mkdir(exist_ok=True)
    with (scratch / "t07_acceptance.txt").open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


async def _fail_on_billing(session: AsyncSession, run_id: UUID) -> None:
    """A 402 from the Model API is billing, not a bug: stop with that message."""
    errors = (
        (
            await session.execute(
                sa.text("SELECT error FROM run_steps WHERE run_id = :r AND error IS NOT NULL"),
                {"r": str(run_id)},
            )
        )
        .scalars()
        .all()
    )
    if any("402" in str(e) or "Payment Required" in str(e) for e in errors):
        await _export_ledger(session, [run_id])
        pytest.fail(
            f"BILLING: the Model API answered 402 at the first model call ({errors[0][:160]})"
        )


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
    await _fail_on_billing(t03_session, run_id)
    # transient model failures stop the run; the product resume path
    # continues from the last good step (bounded loop).
    for _attempt in range(8):
        row = (
            await t03_session.execute(
                sa.text("SELECT status FROM runs WHERE id = :r"), {"r": str(run_id)}
            )
        ).first()
        if row is None or row[0] != "stopped":
            break
        await planruns.resume_run(t03_session, actor=actor, run_id=run_id)
        resume_op = (
            await t03_session.execute(
                sa.text(
                    "SELECT id FROM operations WHERE kind = 'resume_run'"
                    " AND params->>'run_id' = :r ORDER BY created_at DESC LIMIT 1"
                ),
                {"r": str(run_id)},
            )
        ).scalar_one()
        await planruns.run_resume_operation(
            t03_session,
            UUID(str(resume_op)),
            provider_factory=_provider_factory(settings, factory),
            session_factory=factory,
        )
    row = (
        await t03_session.execute(
            sa.text("SELECT status FROM runs WHERE id = :r"), {"r": str(run_id)}
        )
    ).first()
    assert row is not None and row[0] == "done", f"generation run status {row}"
    version_id = await courses.snapshot_version(t03_session, course_id=course_id)
    await t03_session.commit()
    await _export_ledger(t03_session, [run_id])
    return course_id, version_id, demo["user_id"]


async def test_live_translation_acceptance(t03_session: AsyncSession, capfd: Any) -> None:
    from importlib import import_module

    from conftest import test_database_url as _db_url
    from t07_parity import english_submissions, raw_segments, translated_submissions

    from app.db import create_session_factory
    from app.rbac.principal import Principal
    from app.services import planruns
    from app.services.library import detect_language

    key = os.environ.get("MODEL_API_KEY")
    if not key:
        pytest.skip("MODEL_API_KEY is not set")
    settings = _live_settings(key)
    course_id, version_id, _user_id = await _build_demo_course(t03_session, settings)
    factory = create_session_factory(_db_url())
    make = _provider_factory(settings, factory)
    run_ids: list[UUID] = []
    actor_row = (
        await t03_session.execute(
            sa.text("SELECT owner_id FROM courses WHERE id = :c"), {"c": str(course_id)}
        )
    ).first()
    assert actor_row is not None
    actor = Principal(
        user_id=UUID(str(actor_row[0])),
        email="live@example.com",
        roles=("author",),
        permissions=frozenset({"course.generate"}),
        scope_tags=(),
        is_superadmin=False,
    )
    try:
        # Product submit path (the POST /courses/{id}/translate route is a thin
        # wrapper over submit_translate; test_t07c_product drives the route).
        op_id = await planruns.submit_translate(
            t03_session, actor=actor, course_id=course_id, languages=["nb", "uk"]
        )
        result = await planruns.run_translate_operation(
            t03_session, op_id, provider_factory=make, session_factory=factory
        )
        run_id = UUID(result["run_id"])
        run_ids.append(run_id)
        await _fail_on_billing(t03_session, run_id)
        run_row = (
            await t03_session.execute(
                sa.text("SELECT status, kind FROM runs WHERE id = :r"), {"r": str(run_id)}
            )
        ).first()
        assert run_row is not None and run_row[0] == "done" and run_row[1] == "translation", run_row
        cost = await _export_ledger(t03_session, run_ids)  # before the second run and any wipe

        sources = {
            str(r[0]): (str(r[1]), dict(r[2] or {}), dict(r[3] or {}))
            for r in (
                await t03_session.execute(
                    sa.text(
                        "SELECT b.plan_node_id, b.block_type, bv.content, bv.answer_key"
                        " FROM blocks b JOIN block_versions bv ON bv.id = b.current_version_id"
                        " JOIN topics t ON t.id = b.topic_id JOIN modules m ON m.id = t.module_id"
                        " WHERE m.course_id = :c"
                    ),
                    {"c": str(course_id)},
                )
            ).all()
        }
        assert any(t == "cloze" for t, _c, _k in sources.values()), "demo tree needs a cloze"
        report: dict[str, Any] = {}
        from app.translation.checks import verify_spot_error

        for language in ("nb", "uk"):
            rows = (
                await t03_session.execute(
                    sa.text(
                        "SELECT tb.plan_node_id, tb.status, tb.reason, tb.content,"
                        " tb.translated_answer_key FROM translation_blocks tb"
                        " JOIN course_translations ct ON ct.id = tb.translation_id"
                        " WHERE ct.course_version_id = :v AND ct.language = :l"
                        " ORDER BY tb.plan_node_id"
                    ),
                    {"v": str(version_id), "l": language},
                )
            ).all()
            assert rows, f"no translation blocks for {language}"
            per_block: list[dict[str, Any]] = []
            for node, status, reason, content, _tkey in rows:
                assert status in ("ok", "held"), (language, node, status, reason)
                detected = ""
                if status == "ok":
                    texts = [
                        str(v)
                        for k, v in dict(content or {}).items()
                        if k in ("body", "stem", "text", "scenario", "caption")
                    ]
                    if str(sources[str(node)][0]) == "spot_the_error":
                        texts = [str(x) for x in dict(content or {}).get("sentences", [])]
                    detected = detect_language(" ".join(texts))
                    assert detected == language, (language, node, detected)
                per_block.append(
                    {
                        "node": str(node),
                        "status": str(status),
                        "reason": str(reason or ""),
                        "detected": detected,
                    }
                )
            report[language] = per_block
            view = await planruns.learner_course_view(
                t03_session, course_id=course_id, language=language
            )
            assert view["blocks"], language
            for block in view["blocks"]:
                if block["language"] == language:
                    assert block["fallback_notice"] is None
                else:
                    assert block["fallback_notice"], (language, block)
            # the planted sentence survives: kind check positive on the SOURCE pair
            # AND on the translated pair (verify_spot_error runs both)
            spot = next(r for r in rows if str(r[0]) == "b4")
            _btype, src_content, src_key = sources["b4"]
            tkey = dict(spot[4] or {})
            problems = verify_spot_error(
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
            report[language + "_planted"] = {
                "status": str(spot[1]),
                "kind": src_key.get("kind"),
                "problems": problems,
            }
            assert spot[1] == "ok" and problems == [], (language, spot[1], spot[2], problems)

        # grading parity (nb): submissions from the model's raw replies, not from the key
        parity: dict[str, Any] = {}
        for node, (block_type, src_content, src_key) in sorted(sources.items()):
            if block_type not in ("choice", "cloze", "spot_the_error"):
                continue
            tr = (
                await t03_session.execute(
                    sa.text(
                        "SELECT tb.content, tb.translated_answer_key, tb.status"
                        " FROM translation_blocks tb JOIN course_translations ct"
                        " ON ct.id = tb.translation_id"
                        " WHERE ct.course_version_id = :v AND ct.language = 'nb'"
                        " AND tb.plan_node_id = :n"
                    ),
                    {"v": str(version_id), "n": node},
                )
            ).first()
            assert tr is not None and tr[2] == "ok", (node, tr and tr[2])
            grade = import_module(f"app.blocks.{block_type}.check").grade
            raw = await raw_segments(t03_session, run_id=run_id, node=node, language="nb")
            en_right, en_wrong = english_submissions(block_type, src_key, src_content)
            nb_right, nb_wrong = translated_submissions(block_type, src_key, dict(tr[0] or {}), raw)
            scores = {
                "en": grade(src_content, src_key, en_right)["score"],
                "nb": grade(dict(tr[0] or {}), dict(tr[1] or {}), nb_right)["score"],
                "nb_wrong": grade(dict(tr[0] or {}), dict(tr[1] or {}), nb_wrong)["score"],
            }
            parity[f"{node}:{block_type}"] = scores
            assert scores["nb"] == scores["en"] == 1.0 and scores["nb_wrong"] < 1.0, (node, scores)

        paid = (
            await t03_session.execute(
                sa.text(
                    "SELECT count(*) FILTER (WHERE cost_usd > 0), COALESCE(SUM(tokens_cached), 0)"
                    " FROM llm_calls WHERE run_id = :r"
                ),
                {"r": str(run_id)},
            )
        ).one()
        assert cost > 0 and int(paid[0]) > 0, "first translation run must pay"

        op2 = await planruns.submit_translate(
            t03_session, actor=actor, course_id=course_id, languages=["nb", "uk"]
        )
        result2 = await planruns.run_translate_operation(
            t03_session, op2, provider_factory=make, session_factory=factory
        )
        run_ids.append(UUID(result2["run_id"]))
        paid2 = (
            await t03_session.execute(
                sa.text("SELECT count(*) FROM llm_calls WHERE run_id = :r AND cost_usd > 0"),
                {"r": result2["run_id"]},
            )
        ).scalar()
        assert int(paid2 or 0) == 0, f"second run paid {paid2}"
        _announce(
            capfd,
            "LIVE-T07 "
            + json.dumps(
                {
                    "run_status": "done",
                    "per_language": report,
                    "grading_parity": parity,
                    "cost_usd": str(cost),
                    "cached_tokens": int(paid[1]),
                    "paid_calls": int(paid[0]),
                    "second_run_paid": int(paid2 or 0),
                    "ledger": str(LEDGER_PATH),
                },
                ensure_ascii=False,
            ),
        )
    finally:
        await _export_ledger(t03_session, run_ids)
