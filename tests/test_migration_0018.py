"""Migration 0018 round-trip: courses.status gains `generated`; downgrade maps it to draft."""

from __future__ import annotations

import psycopg
from alembic import command
from conftest import require_postgres, test_database_url
from test_migration_0013 import _cfg


def _sync(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def test_0018_allows_generated_and_downgrade_turns_it_into_draft() -> None:
    url = test_database_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    with psycopg.connect(_sync(url), autocommit=True) as conn:
        owner = conn.execute(
            "INSERT INTO users (email, display_name) VALUES ('m18-owner@example.com', 'O')"
            " ON CONFLICT (email) DO UPDATE SET display_name = 'O' RETURNING id"
        ).fetchone()[0]  # type: ignore[index]
        conn.execute(
            "INSERT INTO courses (owner_id, title, status) VALUES (%s, 'm18', 'generated')",
            (owner,),
        )
    command.downgrade(_cfg(url), "0017")
    with psycopg.connect(_sync(url), autocommit=True) as conn:
        status = conn.execute("SELECT status FROM courses WHERE title = 'm18'").fetchone()[0]  # type: ignore[index]
        assert status == "draft"
        try:
            conn.execute("UPDATE courses SET status = 'generated' WHERE title = 'm18'")
        except psycopg.errors.CheckViolation:
            pass
        else:
            raise AssertionError("downgrade must restore the narrower status check")
    command.upgrade(_cfg(url), "head")
    with psycopg.connect(_sync(url), autocommit=True) as conn:
        conn.execute("DELETE FROM courses WHERE title = 'm18'")
        conn.execute("DELETE FROM users WHERE email = 'm18-owner@example.com'")
