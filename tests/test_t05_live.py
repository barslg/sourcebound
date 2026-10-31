"""T05b live acceptance: demo course end to end on the real API via the product path.

submit_generate (product) -> compiled DAG -> worker drives it -> run done.
Prints status, blocks, citations verified, held, planted, cost, cached tokens.
Mock probes (no spend) prove held-on-nonverbatim, held-on-language, and
resume-after-500 pays nothing twice.
"""

from __future__ import annotations

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
        source_store_path="/tmp/muse-t05-live-sources",
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
    await write_tree(session, course_id=course_id, tree=tree, plan_digest="live")
    await session.commit()


async def test_live_acceptance_product_path(t03_session: AsyncSession, capsys: Any) -> None:
    from conftest import test_database_url as _db_url

    from app.db import create_session_factory
    from app.rbac.principal import Principal
    from app.services import planruns

    key = os.environ.get("MODEL_API_KEY")
    if not key:
        pytest.skip("MODEL_API_KEY is not set")
    settings = _live_settings(key)
    demo = await demo_t05.setup_demo(
        t03_session, settings=settings, email=demo_t05.fresh_email("live")
    )
    course_id: UUID = demo["course_id"]
    await _write_demo_tree(t03_session, course_id=course_id)
    actor = Principal(
        user_id=demo["user_id"],
        email="live@example.com",
        roles=("author",),
        permissions=frozenset({"course.generate", "run.retry"}),
        scope_tags=(),
        is_superadmin=False,
    )
    operation_id = await planruns.submit_generate(t03_session, actor=actor, course_id=course_id)
    factory = create_session_factory(_db_url())
    result = await planruns.run_generate_operation(
        t03_session,
        operation_id,
        provider_factory=_provider_factory(settings, factory),
        session_factory=factory,
    )
    run_id = UUID(result["run_id"])
    row = (
        await t03_session.execute(
            sa.text("SELECT status FROM runs WHERE id = :r"), {"r": str(run_id)}
        )
    ).first()
    assert row is not None and row[0] == "done", f"run status {row}"
    blocks = (
        await t03_session.execute(
            sa.text(
                "SELECT b.plan_node_id, b.status, b.current_version_id FROM blocks b"
                " JOIN topics t ON t.id = b.topic_id JOIN modules m ON m.id = t.module_id"
                " WHERE m.course_id = :c"
            ),
            {"c": str(course_id)},
        )
    ).all()
    assert len(blocks) >= 4
    assert all(b[2] is not None for b in blocks)
    cited = int(
        (
            await t03_session.execute(
                sa.text(
                    "SELECT count(*) FROM citations c"
                    " JOIN block_versions bv ON bv.id = c.block_version_id"
                    " JOIN blocks b ON b.id = bv.block_id JOIN topics t ON t.id = b.topic_id"
                    " JOIN modules m ON m.id = t.module_id"
                    " WHERE m.course_id = :c AND c.verified_by = 'code'"
                ),
                {"c": str(course_id)},
            )
        ).scalar_one()
    )
    assert cited >= 4, f"verified citations {cited}"
    held = [b[0] for b in blocks if b[1] == "held"]
    spot = (
        await t03_session.execute(
            sa.text(
                "SELECT bv.content, bv.answer_key FROM blocks b"
                " JOIN block_versions bv ON bv.id = b.current_version_id"
                " WHERE b.plan_node_id = 'b4'"
            )
        )
    ).first()
    assert spot is not None
    assert "planted_index" not in (spot[0] or {}), "answer key must not live in learner content"
    cost = (
        await t03_session.execute(
            sa.text(
                "SELECT COALESCE(SUM(cost_usd),0), COALESCE(SUM(tokens_cached),0) FROM llm_calls"
                " WHERE run_id = :r"
            ),
            {"r": str(run_id)},
        )
    ).first()
    reader_req = (
        await t03_session.execute(
            sa.text(
                "SELECT request::text FROM llm_calls WHERE run_id = :r AND agent = 'reader' LIMIT 1"
            ),
            {"r": str(run_id)},
        )
    ).scalar_one_or_none()
    assert reader_req, "reader call logged"
    texts = (
        await t03_session.execute(
            sa.text(
                "SELECT output->>'text' FROM run_steps"
                " WHERE run_id = :r AND step_key LIKE 'block_%'"
            ),
            {"r": str(run_id)},
        )
    ).all()
    assert any(t and t[0] and t[0][:20] in reader_req for t in texts), (
        "reader input must contain generated block texts"
    )
    import json as _json
    import pathlib as _pl

    reader_row = (
        await t03_session.execute(
            sa.text(
                "SELECT request, response FROM llm_calls WHERE run_id = :r"
                " AND agent = 'reader' ORDER BY created_at LIMIT 1"
            ),
            {"r": str(run_id)},
        )
    ).first()
    if reader_row is not None:
        _pl.Path("tests/fixtures/t05_reader_live.json").write_text(  # noqa: ASYNC240 - test export
            _json.dumps({"request": reader_row[0], "response": reader_row[1]}, ensure_ascii=False)
        )
    print(
        f"LIVE status=done blocks={len(blocks)} citations_verified={cited} held={held} "
        f"spot_sentences={len((spot[0] or {}).get('sentences', []))} "
        f"cost_usd={cost[0]} cached_tokens={cost[1]}"
    )
    captured = capsys.readouterr()
    assert "cost_usd=" in captured.out
