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

import psycopg
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parents[2]
BASE = os.environ.get("E2E_BASE_URL", "postgresql+asyncpg://muse:muse@localhost:5434/postgres")
DB_NAME = "muse_e2e"
URL = re.sub(r"/[^/?]*(\?.*)?$", f"/{DB_NAME}", BASE)

USERS = {
    "admin": ("admin@example.test", "Ada Admin", True),
    "waiting": ("waiting@example.test", "Walt Waiting", False),
    "student": ("student@example.test", "Sam Student", False),
    "other": ("other@example.test", "Olga Other", False),
    "assignee": ("assignee@example.test", "Anna Assignee", False),
    "banme": ("banme@example.test", "Bo Banme", False),
    "inviteme": ("inviteme@example.test", "Ines Inviteme", False),
    "deleteme": ("deleteme@example.test", "Dag Deleteme", False),
}


def ensure_database() -> None:
    admin_dsn = re.sub(r"/[^/?]*(\?.*)?$", "/postgres", BASE).replace("postgresql+asyncpg://", "postgresql://", 1)
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,)).fetchone()
        if not exists:
            conn.execute(f'CREATE DATABASE "{DB_NAME}"')


def migrate() -> None:
    env = {**os.environ, "DATABASE_URL": URL}
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True)
    subprocess.run(["uv", "run", "python", "-m", "app.tasks.schema"], cwd=ROOT, env=env, check=True)


async def seed() -> dict[str, dict[str, str]]:
    sys.path.insert(0, str(ROOT / "src"))
    from app.auth.session import create_session

    engine = create_async_engine(URL)
    out: dict[str, dict[str, str]] = {}
    async with engine.begin() as conn:
        for table in ("invite_uses", "notifications", "operations", "audit_log", "auth_states", "sessions",
                      "identities", "invites", "user_roles", "role_permissions", "users", "roles"):
            try:
                async with conn.begin_nested():
                    await conn.execute(sa.text(f'DELETE FROM "{table}"'))
            except Exception:
                pass
    from sqlalchemy.ext.asyncio import AsyncSession

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
            out[key] = {"id": str(row[0]), "email": email, "name": name, "token": token, "csrf": csrf}
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
