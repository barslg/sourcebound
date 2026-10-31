"""Prepare the isolated e2e database: create, migrate, reset, seed users and sessions.

Writes the session tokens to tmp/e2e-sessions.json at the repo root (git-ignored).
Never touches the dev database: the target is always the muse_e2e database.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import psycopg
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

ROOT = Path(__file__).resolve().parents[2]
BASE = os.environ.get("E2E_BASE_URL", "postgresql+asyncpg://muse:muse@localhost:5434/postgres")
DB_NAME = "muse_e2e"
URL = re.sub(r"/[^/?]*(\?.*)?$", f"/{DB_NAME}", BASE)

USERS = {
    "admin": ("admin@example.com", "Ada Admin", True),
    "waiting": ("waiting@example.com", "Walt Waiting", False),
    "student": ("student@example.com", "Sam Student", False),
    "other": ("other@example.com", "Olga Other", False),
    "assignee": ("assignee@example.com", "Anna Assignee", False),
    "banme": ("banme@example.com", "Bo Banme", False),
    "inviteme": ("inviteme@example.com", "Ines Inviteme", False),
    "deleteme": ("deleteme@example.com", "Dag Deleteme", False),
}


def ensure_database() -> None:
    admin_dsn = re.sub(r"/[^/?]*(\?.*)?$", "/postgres", BASE).replace(
        "postgresql+asyncpg://", "postgresql://", 1
    )
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,)).fetchone()
        if not exists:
            conn.execute(f'CREATE DATABASE "{DB_NAME}"')


def migrate() -> None:
    env = {**os.environ, "DATABASE_URL": URL}
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True)
    subprocess.run(["uv", "run", "python", "-m", "app.tasks.schema"], cwd=ROOT, env=env, check=True)


def seed_tree(prefix: str) -> Any:
    sys.path.insert(0, str(ROOT / "src"))
    from app.course.tree import CourseTree

    def block(node: str, role: str, brief: str) -> dict[str, object]:
        return {
            "plan_node_id": f"{prefix}-{node}",
            "role": role,
            "block_type": "text",
            "brief": brief,
            "source_refs": [{"handle": "P1"}],
        }

    def topic(node: str, title: str) -> dict[str, object]:
        return {
            "plan_node_id": f"{prefix}-{node}",
            "kind": "lesson",
            "title": title,
            "objectives": [f"Explain {title.lower()}"],
            "through_line_note": "Builds on the previous topic.",
            "blocks": [
                block(f"{prefix}-{node}-a", "teach", f"What is {title.lower()}?"),
                block(f"{prefix}-{node}-b", "check", f"Why does {title.lower()} matter?"),
            ],
        }

    def module(node: str, title: str, topics: list[dict[str, object]]) -> dict[str, object]:
        return {
            "plan_node_id": f"{prefix}-{node}",
            "title": title,
            "purpose": f"Teach {title}",
            "entry_knowledge": "none",
            "exit_knowledge": title,
            "topics": topics,
        }

    return CourseTree.model_validate(
        {
            "modules": [
                module("m1", "Foundations", [topic("t1", "Basics"), topic("t2", "Terms")]),
                module("m2", "Practice", [topic("t3", "Drills")]),
            ]
        }
    )


async def seed_planned_course(session: AsyncSession, owner: str, title: str) -> Any:
    from app.services.courses import create_course, set_profile, write_tree

    cid = await create_course(
        session, owner_id=owner, title=title, language="en", scope_tags=["e2e-course"]
    )
    await set_profile(
        session,
        course_id=cid,
        methodology_codes=["direct_instruction"],
        bloom_level="understand",
        audience="new nurses",
        constraints=[
            {
                "kind": "audience",
                "key": "audience_level",
                "required": True,
                "question": "Who is the learner at the start?",
            }
        ],
    )
    await write_tree(session, course_id=cid, tree=seed_tree(str(cid)[:8]), plan_digest="e2e")
    return cid


async def add_source(
    session: AsyncSession, course: Any, title: str, text: str, approval: str, licence: str
) -> str:
    import hashlib

    digest = hashlib.sha256(f"{course}:{title}".encode()).hexdigest()
    sid = (
        await session.execute(
            sa.text(
                "INSERT INTO sources (content_sha256, kind, media, title, licence, approval, as_of)"
                " VALUES (:d, 'upload', 'text', :t, :l, :a, DATE '2026-10-01') RETURNING id"
            ),
            {"d": digest, "t": title, "l": licence, "a": approval},
        )
    ).scalar_one()
    for n, para in enumerate(text.split("\n\n"), start=1):
        await session.execute(
            sa.text(
                "INSERT INTO passages (source_id, text, address, address_key)"
                " VALUES (:s, :t, CAST(:a AS jsonb), :k)"
            ),
            {
                "s": str(sid),
                "t": para,
                "a": json.dumps({"kind": "md", "heading_path": [], "para": n}),
                "k": f"para{n}",
            },
        )
    await session.execute(
        sa.text("INSERT INTO course_sources (course_id, source_id, approval) VALUES (:c, :s, :a)"),
        {"c": str(course), "s": str(sid), "a": approval},
    )
    return str(sid)


async def hold_blocks(
    session: AsyncSession, course: Any, source_id: str, reasons: list[str]
) -> None:
    """Mark the first check block of each topic held, with a real version and a citation to a passage."""
    passage = (
        await session.execute(
            sa.text("SELECT id FROM passages WHERE source_id = :s ORDER BY address_key LIMIT 1"),
            {"s": source_id},
        )
    ).scalar_one()
    rows = (
        await session.execute(
            sa.text(
                "SELECT b.id FROM blocks b JOIN topics t ON t.id = b.topic_id JOIN modules m ON m.id = t.module_id"
                " WHERE m.course_id = :c AND b.role = 'check' ORDER BY m.ordinal, t.ordinal"
            ),
            {"c": str(course)},
        )
    ).all()
    for (block_id,), reason in zip(rows, reasons, strict=False):
        version = (
            await session.execute(
                sa.text(
                    "INSERT INTO block_versions (block_id, version_no, content)"
                    " SELECT :b, COALESCE(MAX(version_no), 0) + 1, CAST(:c AS jsonb) FROM block_versions WHERE block_id = :b RETURNING id"
                ),
                {"b": str(block_id), "c": json.dumps({"text": "Draft that the checker held."})},
            )
        ).scalar_one()
        await session.execute(
            sa.text(
                "INSERT INTO citations (block_version_id, passage_id, claim_path, quote) VALUES (:v, :p, 'text', 'quote')"
            ),
            {"v": str(version), "p": str(passage)},
        )
        await session.execute(
            sa.text(
                "UPDATE blocks SET status = 'held', hold_reason = :r, current_version_id = :v WHERE id = :b"
            ),
            {"r": reason, "v": str(version), "b": str(block_id)},
        )


async def seed_labs(session: AsyncSession, owner: str) -> dict[str, str]:
    from app.runs.executor import new_run

    labs: dict[str, str] = {}
    sources = await seed_planned_course(session, owner, "Sources lab")
    await add_source(
        session,
        sources,
        "WHO hand hygiene guide",
        "Clean hands before touching a patient.\n\nClean hands after touching a patient.",
        "pending",
        "CC BY 4.0",
    )
    await add_source(
        session, sources, "Nursing handbook", "Gloves do not replace hand hygiene.", "pending", ""
    )
    await add_source(
        session,
        sources,
        "Clinic protocol",
        "Alcohol rub takes twenty seconds.\n\nSoap and water takes forty seconds.",
        "approved",
        "Internal",
    )
    labs["sources"] = str(sources)
    fix = await seed_planned_course(session, owner, "Fix lab")
    source = await add_source(
        session,
        fix,
        "Fix lab protocol",
        "Alcohol rub takes twenty seconds.\n\nSoap and water takes forty seconds.",
        "approved",
        "Internal",
    )
    await hold_blocks(
        session,
        fix,
        source,
        [
            "The claim about twenty seconds is not in the cited passage.",
            "The quote does not match the source.",
            "The answer is visible in the question.",
        ],
    )
    labs["fix"] = str(fix)
    spent = await new_run(
        session,
        course_id=fix,
        kind="generate",
        dag=[{"step_key": "block:fix-a", "handler": "none", "depends_on": []}],
        estimate={
            "blocks": 6,
            "calls": 12,
            "tokens": 1000,
            "cost_usd": "1.00",
            "seconds": 30.0,
            "note": "Forecast, not a limit.",
        },
    )
    await session.execute(
        sa.text(
            "INSERT INTO llm_calls (agent, model, prompt_version, request, run_id, cost_usd) VALUES ('author', 'muse-spark-1.3', 'v1', '{}'::jsonb, :r, 0.1234)"
        ),
        {"r": str(spent)},
    )
    labs["live_run"] = str(spent)
    labs["conflict"] = str(await seed_planned_course(session, owner, "Conflict lab"))
    labs["dnd"] = str(await seed_planned_course(session, owner, "Drag lab"))
    labs["publish"] = str(await seed_planned_course(session, owner, "Publish lab"))
    return labs


async def seed_notification(session: AsyncSession, user: str) -> None:
    await session.execute(
        sa.text(
            "INSERT INTO notifications (user_id, kind, payload) VALUES (:u, 'operation.done', CAST(:p AS jsonb))"
        ),
        {
            "u": user,
            "p": json.dumps(
                {"operation_id": "00000000-0000-0000-0000-00000000e2e1", "status": "done"}
            ),
        },
    )


async def seed_course(session: AsyncSession, owner: str) -> dict[str, str]:
    from app.runs.executor import new_run, stop_run
    from app.services.courses import create_course

    cid = await seed_planned_course(session, owner, "Hand hygiene")
    editing = await seed_planned_course(session, owner, "Plan editing")
    empty = await create_course(
        session, owner_id=owner, title="Empty course", language="en", scope_tags=[]
    )
    run = await new_run(
        session,
        course_id=cid,
        kind="generate",
        dag=[{"step_key": "block:t1-a", "handler": "none", "depends_on": []}],
        estimate={
            "blocks": 6,
            "calls": 12,
            "tokens": 1000,
            "cost_usd": "0.42",
            "seconds": 30.0,
            "note": "Forecast, not a limit.",
        },
    )
    await session.execute(
        sa.text(
            "INSERT INTO run_steps (run_id, name, status, step_key, depends_on, input_digest, attempt, error)"
            " VALUES (:r, 'block:t1-a', 'failed', 'block:t1-a', '[]'::jsonb, 'x', 2, 'model quota exhausted')"
        ),
        {"r": str(run)},
    )
    await stop_run(session, run_id=run, marker="retry policy exhausted at block:t1-a")
    extra = await seed_labs(session, owner)
    return {
        "course": str(cid),
        "editing": str(editing),
        "empty": str(empty),
        "run": str(run),
        **extra,
    }


async def seed() -> dict[str, dict[str, str]]:
    sys.path.insert(0, str(ROOT / "src"))
    from app.auth.session import create_session

    engine = create_async_engine(URL)
    out: dict[str, dict[str, str]] = {}
    async with engine.begin() as conn:
        await conn.execute(sa.text("UPDATE blocks SET current_version_id = NULL"))
        for table in (
            "citations",
            "llm_calls",
            "run_steps",
            "runs",
            "course_versions",
            "block_versions",
            "blocks",
            "topics",
            "modules",
            "course_sources",
            "passages",
            "sources",
            "profile_constraints",
            "course_profiles",
            "courses",
            "invite_uses",
            "notifications",
            "operations",
            "audit_log",
            "auth_states",
            "sessions",
            "identities",
            "invites",
            "user_roles",
            "role_permissions",
            "users",
            "roles",
        ):
            try:
                async with conn.begin_nested():
                    await conn.execute(sa.text(f'DELETE FROM "{table}"'))
            except Exception:
                pass
    async with AsyncSession(engine, expire_on_commit=False) as session:
        for key, (email, name, admin) in USERS.items():
            row = (
                await session.execute(
                    sa.text(
                        "INSERT INTO users (email, display_name, avatar_url, is_superadmin)"
                        " VALUES (:e, :n, '', :a) RETURNING id"
                    ),
                    {"e": email, "n": name, "a": admin},
                )
            ).one()
            token, csrf = await create_session(session, row[0])
            out[key] = {
                "id": str(row[0]),
                "email": email,
                "name": name,
                "token": token,
                "csrf": csrf,
            }
        await session.commit()
        out["ids"] = await seed_course(session, out["admin"]["id"])
        await seed_notification(session, out["student"]["id"])
        await session.commit()
    await engine.dispose()
    return out


def main() -> None:
    ensure_database()
    migrate()
    sessions = asyncio.run(seed())
    target = ROOT / "tmp"
    target.mkdir(exist_ok=True)
    (target / "e2e-sessions.json").write_text(json.dumps(sessions, indent=2))
    print(f"seeded {len(sessions)} users into {DB_NAME}")


if __name__ == "__main__":
    main()
