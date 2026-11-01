"""T05c live acceptance: demo course end to end on the real API, DB-measured.

In one database: courses A and C already exist with the same plan ids
(b1..b4); course B is built ONLY through the product path —
submit_plan (researcher + architect) then submit_generate (compiled
DAG, worker drive) — and ends `done`. Every assertion below reads the
database, never a printout: A and C untouched (row counts, statuses,
current versions), every B block validated against its type schema,
the reader's logged requests carry full learner content + answer keys,
the spot key's true_sentence is the planted sentence's source, a
second identical submit_generate pays 0 HTTP posts, and no UUID
appears in any prompt or learner render. The live ledger is copied to
tests/fixtures/t05_live_ledger.json inside the test, before any
conftest wipe can take it. Cost is printed from the ledger.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import acceptance_t05 as acceptance
import demo_t05
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.live

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
LEDGER_PATH = pathlib.Path("tests/fixtures/t05_live_ledger.json")


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


def _actor(user_id: UUID) -> Any:
    from app.rbac.principal import Principal

    return Principal(
        user_id=user_id,
        email="live@example.com",
        roles=("author",),
        permissions=frozenset({"course.plan", "course.generate", "run.retry"}),
        scope_tags=(),
        is_superadmin=False,
    )


async def _snapshot(session: AsyncSession, course_id: UUID) -> dict[str, Any]:
    blocks = (
        await session.execute(
            sa.text(
                "SELECT b.plan_node_id, b.status, b.hold_reason, b.current_version_id,"
                " b.created_at FROM blocks b JOIN topics t ON t.id = b.topic_id"
                " JOIN modules m ON m.id = t.module_id WHERE m.course_id = :c"
                " ORDER BY b.plan_node_id"
            ),
            {"c": str(course_id)},
        )
    ).all()
    counts = {}
    for table, join in (
        (
            "citations",
            "c JOIN block_versions bv ON bv.id = c.block_version_id"
            " JOIN blocks b ON b.id = bv.block_id",
        ),
        ("block_versions", "bv JOIN blocks b ON b.id = bv.block_id"),
    ):
        counts[table] = int(
            (
                await session.execute(
                    sa.text(
                        f"SELECT count(*) FROM {table} {join}"
                        " JOIN topics t ON t.id = b.topic_id"
                        " JOIN modules m ON m.id = t.module_id WHERE m.course_id = :c"
                    ),
                    {"c": str(course_id)},
                )
            ).scalar_one()
        )
    course = (
        await session.execute(
            sa.text(
                "SELECT status, row_version, updated_at, published_version_id"
                " FROM courses WHERE id = :c"
            ),
            {"c": str(course_id)},
        )
    ).one()
    return {
        "blocks": [(str(r[0]), str(r[1]), r[2], str(r[3]), str(r[4])) for r in blocks],
        "course": (str(course[0]), int(course[1]), str(course[2]), str(course[3])),
        **counts,
    }


async def _drive_plan(
    session: AsyncSession, settings: Any, *, course_id: UUID, user_id: UUID
) -> UUID:
    from conftest import test_database_url as _db_url

    from app.db import create_session_factory
    from app.services import planruns

    factory = create_session_factory(_db_url())
    operation_id = await planruns.submit_plan(
        session,
        actor=_actor(user_id),
        course_id=course_id,
        queries=[
            "Who must ensure AI literacy of staff under the AI Act?",
            "Which AI practices are prohibited and what fines apply?",
        ],
    )
    result = await planruns.run_plan_operation(
        session,
        operation_id,
        provider_factory=_provider_factory(settings, factory),
        session_factory=factory,
    )
    return UUID(result["run_id"])


async def _drive_generate(
    session: AsyncSession, settings: Any, *, course_id: UUID, user_id: UUID
) -> UUID:
    from conftest import test_database_url as _db_url

    from app.db import create_session_factory
    from app.services import planruns

    factory = create_session_factory(_db_url())
    operation_id = await planruns.submit_generate(
        session, actor=_actor(user_id), course_id=course_id
    )
    result = await planruns.run_generate_operation(
        session,
        operation_id,
        provider_factory=_provider_factory(settings, factory),
        session_factory=factory,
    )
    run_id = UUID(result["run_id"])
    # Transient model failures stop the run with a marker; the product
    # resume path continues from the last good step and pays nothing
    # twice. Loop it (bounded) until the run ends done.
    for _attempt in range(8):
        status = (
            await session.execute(
                sa.text("SELECT status FROM runs WHERE id = :r"), {"r": str(run_id)}
            )
        ).scalar_one()
        if status != "stopped":
            break
        await planruns.resume_run(session, actor=_actor(user_id), run_id=run_id)
        resume_op = (
            await session.execute(
                sa.text(
                    "SELECT id FROM operations WHERE kind = 'resume_run'"
                    " AND params->>'run_id' = :r ORDER BY created_at DESC LIMIT 1"
                ),
                {"r": str(run_id)},
            )
        ).scalar_one()
        await planruns.run_resume_operation(
            session,
            UUID(str(resume_op)),
            provider_factory=_provider_factory(settings, factory),
            session_factory=factory,
        )
    return run_id


async def _logged_inputs(session: AsyncSession, run_id: UUID, agent: str) -> list[str]:
    """The prompts of one agent's logged requests in a run, oldest first (replays included)."""
    rows = (
        await session.execute(
            sa.text(
                "SELECT request FROM llm_calls WHERE run_id = :r AND agent = :a"
                " ORDER BY created_at, id"
            ),
            {"r": str(run_id), "a": agent},
        )
    ).all()
    return [str((row[0] or {}).get("input", "")) for row in rows]


async def _why_stopped(session: AsyncSession, run_id: UUID) -> str:
    """Step errors of a run (kept in run_steps only; stop markers are plain sentences)."""
    rows = (
        await session.execute(
            sa.text(
                "SELECT step_key, left(error, 220) FROM run_steps"
                " WHERE run_id = :r AND error IS NOT NULL"
            ),
            {"r": str(run_id)},
        )
    ).all()
    return "; ".join(f"{key}: {error}" for key, error in rows)


def _write_ledger(rows: list[Any]) -> str:
    """Copy the live ledger to the tracked fixture; keep the previous one beside it, timestamped."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    previous = LEDGER_PATH.with_name(f"{LEDGER_PATH.stem}.{stamp}.prev.json")
    payload = json.dumps(
        [
            {
                "agent": r[0],
                "model": r[1],
                "cost_usd": str(r[2]),
                "tokens_cached": int(r[3] or 0),
                "request": r[4],
                "response": r[5],
            }
            for r in rows
        ],
        ensure_ascii=False,
        default=str,
    )
    if LEDGER_PATH.exists() and LEDGER_PATH.read_text(encoding="utf-8") != payload:
        previous.write_bytes(LEDGER_PATH.read_bytes())  # never overwrite evidence silently
    LEDGER_PATH.write_text(payload, encoding="utf-8")
    return str(LEDGER_PATH)


async def _export_ledger(session: AsyncSession, run_ids: list[UUID]) -> Decimal:
    """Copy this test's ledger rows out of the DB before any wipe; returns the cost."""
    rows = (
        await session.execute(
            sa.text(
                "SELECT agent, model, cost_usd, tokens_cached, request, response"
                " FROM llm_calls WHERE run_id::text = ANY(:ids) ORDER BY created_at"
            ),
            {"ids": [str(r) for r in run_ids]},
        )
    ).all()
    if any(Decimal(str(r[2])) > 0 for r in rows):  # a run that paid nothing never replaces evidence
        _write_ledger(list(rows))
    return sum((Decimal(str(r[2])) for r in rows), Decimal(0))


def _announce(capfd: pytest.CaptureFixture[str], line: str) -> None:
    """Print the acceptance line on the real stdout (not swallowed by capture)."""
    with capfd.disabled():
        print(line, flush=True)
    scratch = pathlib.Path("tmp")
    scratch.mkdir(exist_ok=True)
    with (scratch / "t05c_acceptance.txt").open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


async def test_live_acceptance_product_path(
    t03_session: AsyncSession, capfd: pytest.CaptureFixture[str]
) -> None:
    key = os.environ.get("MODEL_API_KEY")
    if not key:
        pytest.skip("MODEL_API_KEY is not set")
    settings = _live_settings(key)
    session = t03_session

    # Older course C exists before B is planned; its plan is cloned from B's LIVE plan below,
    # so it shares every id the architect really chose (a hand-written b1..b4 shares none).
    demo_c = await demo_t05.setup_demo(
        session, settings=settings, email=demo_t05.fresh_email("live-c")
    )

    # Course B: sources only; its plan comes from submit_plan (researcher + architect).
    demo_b = await demo_t05.setup_demo(
        session, settings=settings, email=demo_t05.fresh_email("live-b")
    )
    course_b: UUID = demo_b["course_id"]
    user_b: UUID = demo_b["user_id"]
    run_ids: list[UUID] = []
    cost = Decimal(0)
    try:
        plan_run = await _drive_plan(session, settings, course_id=course_b, user_id=user_b)
        run_ids.append(plan_run)
        plan_status = (
            await session.execute(
                sa.text("SELECT status FROM runs WHERE id = :r"), {"r": str(plan_run)}
            )
        ).scalar_one()
        assert plan_status == "done", (
            f"plan run {plan_status}: {await _why_stopped(session, plan_run)}"
        )

        # Courses A and C share B's plan ids exactly (the cross-course write trap).
        demo_a = await demo_t05.setup_demo(
            session, settings=settings, email=demo_t05.fresh_email("live-a")
        )
        await demo_t05.clone_plan(session, source=course_b, target=demo_a["course_id"])
        await demo_t05.clone_plan(session, source=course_b, target=demo_c["course_id"])
        before_a = await _snapshot(session, demo_a["course_id"])
        before_c = await _snapshot(session, demo_c["course_id"])

        gen_run = await _drive_generate(session, settings, course_id=course_b, user_id=user_b)
        run_ids.append(gen_run)
        gen_status = (
            await session.execute(
                sa.text("SELECT status FROM runs WHERE id = :r"), {"r": str(gen_run)}
            )
        ).scalar_one()
        assert gen_status == "done", (
            f"generation run {gen_status}: {await _why_stopped(session, gen_run)}"
        )

        assert await _snapshot(session, demo_a["course_id"]) == before_a, "A changed"
        assert await _snapshot(session, demo_c["course_id"]) == before_c, "C changed"

        rows = (
            await session.execute(
                sa.text(
                    "SELECT b.plan_node_id, b.block_type, b.status, bv.content, bv.answer_key,"
                    " t.plan_node_id FROM blocks b"
                    " JOIN block_versions bv ON bv.id = b.current_version_id"
                    " JOIN topics t ON t.id = b.topic_id"
                    " JOIN modules m ON m.id = t.module_id WHERE m.course_id = :c"
                    " ORDER BY b.plan_node_id"
                ),
                {"c": str(course_b)},
            )
        ).all()
        planned = (
            await session.execute(
                sa.text(
                    "SELECT count(*) FROM blocks b JOIN topics t ON t.id = b.topic_id"
                    " JOIN modules m ON m.id = t.module_id WHERE m.course_id = :c"
                ),
                {"c": str(course_b)},
            )
        ).scalar_one()
        assert len(rows) == planned >= 4, "every planned block has a current version"
        assert all(r[2] != "draft" for r in rows), [(r[0], r[2]) for r in rows]
        from importlib import import_module

        for node, btype, _status, content, _key in rows:
            errors = import_module(f"app.blocks.{btype}.schema").validate(dict(content or {}))
            assert not errors, (node, btype, errors, content)

        cited = int(
            (
                await session.execute(
                    sa.text(
                        "SELECT count(*) FROM citations c"
                        " JOIN block_versions bv ON bv.id = c.block_version_id"
                        " JOIN blocks b ON b.id = bv.block_id JOIN topics t ON t.id = b.topic_id"
                        " JOIN modules m ON m.id = t.module_id"
                        " WHERE m.course_id = :c AND c.verified_by = 'code'"
                    ),
                    {"c": str(course_b)},
                )
            ).scalar_one()
        )
        assert cited >= 4, f"verified citations {cited}"

        # The logged reader and saboteur requests, parsed (never substring-matched).
        reader_inputs = await _logged_inputs(session, gen_run, "reader")
        assert reader_inputs, "reader calls logged"
        readers = [acceptance.ReaderRequest(text) for text in reader_inputs]
        current = [
            acceptance.Block(
                node=str(r[0]),
                block_type=str(r[1]),
                status=str(r[2]),
                content=dict(r[3] or {}),
                key=dict(r[4] or {}),
                topic=str(r[5]),
            )
            for r in rows
        ]
        acceptance.check_reader_saw_every_block(current, readers)
        acceptance.check_held_blocks_were_read_again(current, readers)
        acceptance.check_spot_exercises(
            current, readers, await _logged_inputs(session, gen_run, "saboteur")
        )

        # No UUID in any prompt of the generation run, none in learner renders.
        all_inputs = [
            str((r[0] or {}).get("input", ""))
            for r in (
                await session.execute(
                    sa.text("SELECT request FROM llm_calls WHERE run_id = :r"),
                    {"r": str(gen_run)},
                )
            ).all()
        ]
        assert all_inputs and not any(UUID_RE.search(text) for text in all_inputs)
        from app.blocks.registry import learner_serializers

        render = learner_serializers()["card"]
        for node, btype, _status, content, akey in rows:
            rendered = render(str(btype), dict(content or {}), dict(akey or {}))
            assert not UUID_RE.search(str(rendered)), (node, btype, rendered)

        # Evidence survives: copy the live ledger BEFORE the second run and any wipe.
        cost = await _export_ledger(session, run_ids)

        # Second identical submit_generate: 0 HTTP posts (llm_calls rows of the new run).
        gen_run2 = await _drive_generate(session, settings, course_id=course_b, user_id=user_b)
        run_ids.append(gen_run2)
        status2 = (
            await session.execute(
                sa.text("SELECT status FROM runs WHERE id = :r"), {"r": str(gen_run2)}
            )
        ).scalar_one()
        assert status2 == "done", status2
        posts2 = int(
            (
                await session.execute(
                    sa.text(
                        "SELECT count(*) FROM llm_calls WHERE run_id = :r"
                        " AND (cost_usd > 0 OR tokens_input > 0)"
                    ),
                    {"r": str(gen_run2)},
                )
            ).scalar_one()
        )
        assert posts2 == 0, f"second run made {posts2} HTTP posts"
        _announce(
            capfd,
            f"LIVE-T05C status=done blocks={len(rows)} citations_verified={cited}"
            f" held={[r[0] for r in rows if r[2] == 'held']}"
            f" reader_request_chars={[len(t) for t in reader_inputs]}"
            f" second_run_posts={posts2} ledger={LEDGER_PATH} cost_usd={cost}",
        )
    finally:
        await _export_ledger(session, run_ids)
