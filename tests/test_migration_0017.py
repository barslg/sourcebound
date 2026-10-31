"""Migration 0017 round-trip: learner_answers with cascade on user delete.

Caller of every helper here: these tests. The table is written by the
learner answer route (POST /learn/blocks/{id}/answer) and read by the
mastery and teacher-report queries; deleting a user must take their
answers with them (PRINCIPLES: personal data, answers, progress cascade).
"""

from __future__ import annotations

import json

import psycopg
from alembic import command
from conftest import require_postgres, test_database_url
from test_migration_0001 import _harness_tables
from test_migration_0013 import _cfg


def _sync(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def test_0017_creates_learner_answers_with_the_brief_columns() -> None:
    url = test_database_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    columns = _harness_tables(url)["learner_answers"]
    assert {
        "id",
        "user_id",
        "block_id",
        "block_version_id",
        "language",
        "payload",
        "score",
        "attempt_no",
        "result",
        "created_at",
    } <= set(columns)


def test_0017_user_delete_cascades_answers_and_downgrade_drops_only_the_table() -> None:
    url = test_database_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    with psycopg.connect(_sync(url), autocommit=True) as conn:
        owner = conn.execute(
            "INSERT INTO users (email, display_name) VALUES ('m17-owner@example.com', 'O')"
            " ON CONFLICT (email) DO UPDATE SET display_name = 'O' RETURNING id"
        ).fetchone()[0]  # type: ignore[index]
        learner = conn.execute(
            "INSERT INTO users (email, display_name) VALUES ('m17-learner@example.com', 'L')"
            " ON CONFLICT (email) DO UPDATE SET display_name = 'L' RETURNING id"
        ).fetchone()[0]  # type: ignore[index]
        course = conn.execute(
            "INSERT INTO courses (owner_id, title) VALUES (%s, 'm17') RETURNING id", (owner,)
        ).fetchone()[0]  # type: ignore[index]
        module = conn.execute(
            "INSERT INTO modules (course_id, ordinal, title) VALUES (%s, 0, 'm') RETURNING id",
            (course,),
        ).fetchone()[0]  # type: ignore[index]
        topic = conn.execute(
            "INSERT INTO topics (module_id, ordinal, kind, title, plan_node_id)"
            " VALUES (%s, 0, 'lesson', 't', gen_random_uuid()::text) RETURNING id",
            (module,),
        ).fetchone()[0]  # type: ignore[index]
        block = conn.execute(
            "INSERT INTO blocks (topic_id, plan_node_id, ordinal, role, block_type, brief)"
            " VALUES (%s, gen_random_uuid()::text, 0, 'check', 'choice', 'b') RETURNING id",
            (topic,),
        ).fetchone()[0]  # type: ignore[index]
        version = conn.execute(
            "INSERT INTO block_versions (block_id, version_no, content) VALUES (%s, 1, '{}')"
            " RETURNING id",
            (block,),
        ).fetchone()[0]  # type: ignore[index]
        conn.execute(
            "INSERT INTO learner_answers (user_id, block_id, block_version_id, language,"
            " payload, score, attempt_no, result) VALUES (%s, %s, %s, 'en', %s, 1.0, 1, '{}')",
            (learner, block, version, json.dumps({"picked": 0})),
        )
        conn.execute("DELETE FROM users WHERE id = %s", (learner,))
        left = conn.execute("SELECT count(*) FROM learner_answers").fetchone()[0]  # type: ignore[index]
        assert left == 0, "deleting the learner deletes their answers"
        assert (
            conn.execute("SELECT count(*) FROM blocks WHERE id = %s", (block,)).fetchone()[0] == 1
        )  # type: ignore[index]
    before = set(_harness_tables(url))
    command.downgrade(_cfg(url), "0016")
    after = set(_harness_tables(url))
    assert before - after == {"learner_answers"}
    command.upgrade(_cfg(url), "head")
    assert "learner_answers" in _harness_tables(url)
    with psycopg.connect(_sync(url), autocommit=True) as conn:
        conn.execute("DELETE FROM courses WHERE title = 'm17'")
        conn.execute("DELETE FROM users WHERE email IN ('m17-owner@example.com')")
