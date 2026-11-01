"""Build the demo course end to end from a recorded ledger: no model key, no spend.

Runs the product path (course, approved source, plan run, generation run,
publish) with MODEL_REPLAY_LEDGER pointing at the recorded live runs, so
the plan, every block and the reader verdicts are the recorded model
replies, validated and stored by the same code a live run uses. A request
the ledger never recorded stops the run loudly (ReplayMissError).

Usage: python scripts/demo_replay.py   (DATABASE_URL points at a migrated database)
Prints one `demo_replay: ...` line with the measured counts. Callers:
scripts/demo_replay.sh and tests/test_jobs_smoke.py.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

import sqlalchemy as sa

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

EXCERPT_PATH = REPO / "tests" / "fixtures" / "eurlex_excerpt.md"
DEFAULT_LEDGERS = ("tests/fixtures/t05_live_ledger.json", "tests/fixtures/t07_live_ledger.json")
COURSE_TITLE = "AI literacy at work: the EU AI Act"
LICENCE = "EUR-Lex reuse with acknowledgement (Decision 2011/833/EU)"
DEMO_OWNER = "demo-author@example.org"
QUERIES = (
    "Who must ensure AI literacy of staff under the AI Act?",
    "Which AI practices are prohibited and what fines apply?",
)
MAX_RESUMES = 3


def default_ledger_paths(root: Path = REPO) -> str:
    """Comma-separated recorded ledgers that exist under `root`."""
    return ",".join(str(root / name) for name in DEFAULT_LEDGERS if (root / name).is_file())


async def _status(session: Any, run_id: UUID) -> str:
    return str(
        (
            await session.execute(
                sa.text("SELECT status FROM runs WHERE id = :r"), {"r": str(run_id)}
            )
        ).scalar_one()
    )


async def _create_owner(session: Any) -> UUID:
    row = (
        await session.execute(
            sa.text(
                "INSERT INTO users (email, display_name, is_superadmin)"
                " VALUES (:e, 'Demo author', true) ON CONFLICT (email)"
                " DO UPDATE SET display_name = EXCLUDED.display_name RETURNING id"
            ),
            {"e": DEMO_OWNER},
        )
    ).one()
    return UUID(str(row[0]))


async def build_demo(settings: Any, excerpt: bytes | None = None) -> dict[str, Any]:
    """Create and generate the demo course; return its id and measured counts."""
    from app.db import create_session_factory
    from app.rbac.principal import load_principal
    from app.services import courses as course_service
    from app.services import library, planruns
    from app.startup import startup_sync

    text = excerpt if excerpt is not None else EXCERPT_PATH.read_bytes()
    factory = create_session_factory(settings.database_url)
    async with factory() as session:
        await startup_sync(session)
        owner = await _create_owner(session)
        await session.commit()
        actor = await load_principal(session, owner)
        course_id = await course_service.create_course(
            session, owner_id=owner, title=COURSE_TITLE, language="en"
        )
        await course_service.set_profile(
            session,
            course_id=course_id,
            methodology_codes=["merrill_first_principles"],
            bloom_level="apply",
            audience="non-lawyer municipal staff",
        )
        source_id = await library.store_upload(
            session,
            settings=settings,
            created_by=owner,
            filename="eurlex_excerpt.md",
            media="text",
            content=text,
            licence=LICENCE,
            course_id=course_id,
        )
        await library.ingest_passages(
            session,
            source_id=source_id,
            passages=library.chunk_markdown(text.decode("utf-8")),
            ts_config="english",
        )
        await library.approve_source(session, source_id=source_id, approved_by=owner)
        await session.commit()

        plan_op = await planruns.submit_plan(
            session, actor=actor, course_id=course_id, queries=list(QUERIES)
        )
        plan = await planruns.run_plan_operation(
            session,
            plan_op,
            provider_factory=planruns.real_provider_factory(settings),
            session_factory=factory,
        )
        plan_run = UUID(plan["run_id"])
        if await _status(session, plan_run) != "done":
            raise SystemExit(f"demo_replay: the plan run did not finish ({plan_run})")

        gen_op = await planruns.submit_generate(session, actor=actor, course_id=course_id)
        gen = await planruns.run_generate_operation(
            session,
            gen_op,
            provider_factory=planruns.real_provider_factory(settings),
            session_factory=factory,
        )
        gen_run = UUID(gen["run_id"])
        for _ in range(MAX_RESUMES):
            if await _status(session, gen_run) != "stopped":
                break
            await planruns.resume_run(session, actor=actor, run_id=gen_run)
            resume_op = (
                await session.execute(
                    sa.text(
                        "SELECT id FROM operations WHERE kind = 'resume_run'"
                        " AND params->>'run_id' = :r ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"r": str(gen_run)},
                )
            ).scalar_one()
            await planruns.run_resume_operation(
                session,
                UUID(str(resume_op)),
                provider_factory=planruns.real_provider_factory(settings),
                session_factory=factory,
            )
        status = await _status(session, gen_run)
        counts = await _counts(session, course_id)
        published = False
        if status == "done" and counts["held"] == 0:
            await course_service.publish_course(session, course_id=course_id)
            await session.commit()
            published = True
        paid = (
            await session.execute(
                sa.text(
                    "SELECT coalesce(sum(cost_usd), 0), count(*) FROM llm_calls"
                    " WHERE run_id = ANY(:ids)"
                ),
                {"ids": [str(plan_run), str(gen_run)]},
            )
        ).one()
    return {
        "course_id": str(course_id),
        "run_status": status,
        "published": published,
        "cost_usd": float(paid[0]),
        "model_calls": int(paid[1]),
        **counts,
    }


async def _counts(session: Any, course_id: UUID) -> dict[str, int]:
    row = (
        await session.execute(
            sa.text(
                "SELECT count(*), count(b.current_version_id),"
                " count(*) FILTER (WHERE b.status = 'held'),"
                " (SELECT count(*) FROM citations c JOIN block_versions bv"
                "  ON bv.id = c.block_version_id JOIN blocks b2 ON b2.id = bv.block_id"
                "  JOIN topics t2 ON t2.id = b2.topic_id JOIN modules m2 ON m2.id = t2.module_id"
                "  WHERE m2.course_id = :c AND c.verified_by = 'code')"
                " FROM blocks b JOIN topics t ON t.id = b.topic_id"
                " JOIN modules m ON m.id = t.module_id WHERE m.course_id = :c"
            ),
            {"c": str(course_id)},
        )
    ).one()
    return {
        "blocks": int(row[0]),
        "blocks_written": int(row[1]),
        "held": int(row[2]),
        "citations_verified": int(row[3]),
    }


def main() -> int:
    """CLI entrypoint."""
    from app.config import get_settings

    settings = get_settings()
    if not settings.model_replay_ledger:
        settings = settings.model_copy(update={"model_replay_ledger": default_ledger_paths()})
    settings = settings.model_copy(update={"model_replay_loose": True})
    if not settings.model_replay_ledger:
        print("demo_replay: no ledger found; set MODEL_REPLAY_LEDGER", file=sys.stderr)
        return 1
    summary = asyncio.run(build_demo(settings))
    print(
        "demo_replay: " + " ".join(f"{key}={value}" for key, value in summary.items()),
        flush=True,
    )
    if summary["run_status"] != "done":
        print(
            "demo_replay: the run stopped where the ledger has no recorded reply (the rework of"
            " the block the reader held); every block is written, the author accepts or rewrites"
            " the held one on the Fix tab",
            flush=True,
        )
    return 0 if summary["blocks_written"] == summary["blocks"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
