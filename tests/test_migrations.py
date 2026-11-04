"""Migration round trips (permanent): upgrade, downgrade and upgrade again on the test database.

One section per migration; the data-bearing ones (0014 orphans, 0015 rows kept,
0017 cascade, 0018 status map, 0019 pins) carry rows through the downgrade.
"""

from __future__ import annotations

import json

import psycopg
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from conftest import require_postgres
from conftest import test_database_url as _db_url

EXPECTED_COLUMNS: dict[str, set[str]] = {
    "users": {"id", "email", "display_name", "is_superadmin", "created_at"},
    "roles": {"id", "name", "created_at"},
    "permissions": {"code", "category", "label", "description"},
    "role_permissions": {"role_id", "permission_code"},
    "user_roles": {"user_id", "role_id"},
    "invites": {"id", "token", "max_uses", "used_count", "created_by", "created_at"},
    "runs": {"id", "status", "current_step", "error_marker", "created_at", "updated_at"},
    "run_steps": {"id", "run_id", "name", "status", "error", "started_at", "finished_at"},
    "llm_calls": {
        "id",
        "agent",
        "model",
        "prompt_version",
        "tokens_input",
        "tokens_cached",
        "tokens_output",
        "cost_usd",
        "duration_ms",
        "request",
        "response",
        "run_id",
        "step_id",
        "created_at",
    },
    "llm_replies": {"request_hash", "model", "prompt_version", "response", "created_at"},
}


def _cfg(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _sync_url(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _inspector_tables(url: str) -> dict[str, set[str]]:
    engine = sa.create_engine(_sync_url(url))
    try:
        inspector = sa.inspect(engine)
        return {
            table: {col["name"] for col in inspector.get_columns(table)}
            for table in inspector.get_table_names()
        }
    finally:
        engine.dispose()


def _harness_tables(url: str) -> dict[str, set[str]]:
    """Tables owned by migration 0001.

    Alembic keeps its own alembic_version row after `downgrade base`, and
    the Procrastinate schema (applied by a different tool, owned by the
    worker) may share this test database — neither belongs to 0001.
    """
    return {
        table: columns
        for table, columns in _inspector_tables(url).items()
        if table != "alembic_version" and not table.startswith("procrastinate_")
    }


def test_0001_upgrade_then_downgrade_then_upgrade() -> None:
    from conftest import require_postgres
    from conftest import test_database_url as _db_url

    url = _db_url()
    require_postgres(url)
    cfg = _cfg(url)
    command.downgrade(cfg, "base")
    assert _harness_tables(url) == {}

    command.upgrade(cfg, "0001")
    tables = _harness_tables(url)
    assert set(tables) == set(EXPECTED_COLUMNS)
    for table, columns in EXPECTED_COLUMNS.items():
        assert columns <= tables[table], f"{table} missing {columns - tables[table]}"

    command.downgrade(cfg, "base")
    assert _harness_tables(url) == {}

    command.upgrade(cfg, "head")
    tables = _harness_tables(url)
    for table, columns in EXPECTED_COLUMNS.items():
        if table == "invites":
            # At head the plaintext token is gone (T02b item 7: sha256 only).
            columns = {"id", "token_hash", "max_uses", "used_count", "created_by", "created_at"}
        assert columns <= tables[table], f"{table} missing {columns - tables[table]}"


def test_0002_upgrade_adds_t02_columns_and_tables() -> None:
    url = _db_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    tables = _harness_tables(url)
    assert {"banned_at", "banned_reason", "is_legacy", "avatar_url"} <= tables["users"]
    assert "identities" in tables
    assert {"provider", "subject", "user_id", "email"} <= tables["identities"]
    assert "sessions" in tables
    assert {"token_hash", "user_id", "csrf_token", "expires_at"} <= tables["sessions"]
    assert "auth_states" in tables
    assert "audit_log" in tables
    assert "operations" in tables
    assert {"status", "params", "progress", "result", "error", "actor_id"} <= tables["operations"]
    assert "notifications" in tables
    assert "invite_uses" in tables
    assert "scope_tags" in tables["user_roles"]
    assert {"crud", "scope_tags"} <= tables["roles"]
    assert {"revoked_at", "course_tags", "granted_role"} <= tables["invites"]


def test_0002_downgrade_removes_only_t02_additions() -> None:
    url = _db_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    command.downgrade(_cfg(url), "0001")
    tables = _harness_tables(url)
    assert not ({"banned_at", "banned_reason", "is_legacy", "avatar_url"} <= tables["users"])
    for removed in (
        "identities",
        "sessions",
        "auth_states",
        "audit_log",
        "operations",
        "notifications",
        "invite_uses",
    ):
        assert removed not in tables
    assert "scope_tags" not in tables["user_roles"]
    assert "crud" not in tables["roles"]
    assert "revoked_at" not in tables["invites"]
    command.upgrade(_cfg(url), "head")
    engine = sa.create_engine(url.replace("postgresql+asyncpg://", "postgresql://", 1))
    try:
        inspector = sa.inspect(engine)
        assert "audit_log" in inspector.get_table_names()
    finally:
        engine.dispose()


_m0004_ADDED_TABLES = frozenset(
    {
        "course_groups",
        "courses",
        "modules",
        "topics",
        "blocks",
        "block_versions",
        "course_versions",
        "methodologies",
        "course_profiles",
        "profile_constraints",
        "sources",
        "passages",
        "citations",
        "retrieval_pairs",
        "source_candidates",
        "human_queue",
    }
)


def test_0004_upgrade_adds_course_library_and_run_columns() -> None:
    url = _db_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    tables = _harness_tables(url)
    assert _m0004_ADDED_TABLES <= set(tables)
    assert {"owner_id", "title", "language", "status", "scope_tags", "row_version"} <= tables[
        "courses"
    ]
    assert {"course_id", "ordinal", "entry_knowledge", "exit_knowledge"} <= tables["modules"]
    assert {"kind", "plan_node_id", "through_line_note", "plan_digest"} <= tables["topics"]
    assert {"plan_node_id", "brief", "current_version_id", "status", "hold_reason"} <= tables[
        "blocks"
    ]
    assert {"content", "answer_key", "schema_version", "run_step_id"} <= tables["block_versions"]
    assert {"content_sha256", "licence", "approval", "parser_spec", "supersedes_id"} <= tables[
        "sources"
    ]
    assert {"address", "address_key", "ts_config", "tsv"} <= tables["passages"]
    assert {"passage_id", "claim_path", "quote_start", "quote_end", "verified_by"} <= tables[
        "citations"
    ]
    assert {"query_lang", "ranker", "rank", "score", "label", "label_source"} <= tables[
        "retrieval_pairs"
    ]
    assert {"compiled_dag", "dag_digest", "prompt_snapshot", "estimate", "stop_marker"} <= tables[
        "runs"
    ]
    assert {"step_key", "depends_on", "input_digest", "output", "attempt"} <= tables["run_steps"]
    assert "idempotency_key" in tables["operations"]


def test_0004_downgrade_removes_only_t03_additions() -> None:
    url = _db_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    command.downgrade(_cfg(url), "0003")
    tables = _harness_tables(url)
    for removed in _m0004_ADDED_TABLES:
        assert removed not in tables
    assert "compiled_dag" not in tables["runs"]
    assert "step_key" not in tables["run_steps"]
    assert "idempotency_key" not in tables["operations"]
    command.upgrade(_cfg(url), "head")
    engine = sa.create_engine(url.replace("postgresql+asyncpg://", "postgresql://", 1))
    try:
        inspector = sa.inspect(engine)
        assert "courses" in inspector.get_table_names()
    finally:
        engine.dispose()


def test_0005_upgrade_adds_scoped_sources_and_per_course_node_ids() -> None:
    url = _db_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    tables = _harness_tables(url)
    assert "course_sources" in tables
    assert {
        "course_id",
        "source_id",
        "approval",
        "approved_by",
        "approved_at",
    } <= tables["course_sources"]
    assert "course_id" in tables["runs"]


def test_0005_downgrade_restores_0004_shape() -> None:
    url = _db_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    command.downgrade(_cfg(url), "0004")
    tables = _harness_tables(url)
    assert "course_sources" not in tables
    assert "course_id" not in tables["runs"]
    command.upgrade(_cfg(url), "head")
    engine = sa.create_engine(url.replace("postgresql+asyncpg://", "postgresql://", 1))
    try:
        inspector = sa.inspect(engine)
        assert "course_sources" in inspector.get_table_names()
    finally:
        engine.dispose()


def test_0006_upgrade_adds_through_line() -> None:
    url = _db_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    tables = _harness_tables(url)
    assert "through_line" in tables["courses"]


def test_0006_downgrade_drops_through_line() -> None:
    url = _db_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    command.downgrade(_cfg(url), "0005")
    tables = _harness_tables(url)
    assert "through_line" not in tables["courses"]
    assert "course_sources" in tables
    command.upgrade(_cfg(url), "head")
    engine = sa.create_engine(url.replace("postgresql+asyncpg://", "postgresql://", 1))
    try:
        inspector = sa.inspect(engine)
        assert "course_sources" in inspector.get_table_names()
    finally:
        engine.dispose()


def test_0012_upgrade_adds_ingest_status() -> None:
    url = _db_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    tables = _harness_tables(url)
    assert {"ingest_status", "ingest_message"} <= tables["sources"]


def test_0012_downgrade_restores_0011_shape() -> None:
    url = _db_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    command.downgrade(_cfg(url), "0011")
    tables = _harness_tables(url)
    assert "ingest_status" not in tables["sources"]
    assert "ingest_message" not in tables["sources"]
    command.upgrade(_cfg(url), "head")
    tables = _harness_tables(url)
    assert {"ingest_status", "ingest_message"} <= tables["sources"]


def _m0013_fks(url: str) -> set[tuple[str, str]]:
    import psycopg

    sync = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    with psycopg.connect(sync) as conn:
        rows = conn.execute(
            "SELECT conrelid::regclass::text, confrelid::regclass::text"
            " FROM pg_constraint WHERE contype = 'f'"
            " AND conrelid::regclass::text IN"
            " ('course_translations', 'translation_blocks', 'translation_memory')"
        ).fetchall()
    return {(str(a), str(b)) for a, b in rows}


def test_0013_creates_translation_tables() -> None:
    url = _db_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    tables = _harness_tables(url)
    assert "course_translations" in tables
    assert "translation_blocks" in tables
    assert "translation_memory" in tables


def test_0014_adds_key_kind_fk_and_tm_course() -> None:
    url = _db_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    tables = _harness_tables(url)
    assert "translated_answer_key" in tables["translation_blocks"]
    assert "kind" in tables["runs"]
    assert "course_id" in tables["translation_memory"]
    assert ("course_translations", "runs") in _m0013_fks(url)


def test_0014_downgrade_restores_0013_shape() -> None:
    url = _db_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    command.downgrade(_cfg(url), "0013")
    tables = _harness_tables(url)
    assert "translated_answer_key" not in tables["translation_blocks"]
    assert "kind" not in tables["runs"]
    assert "course_id" not in tables["translation_memory"]
    assert ("course_translations", "runs") not in _m0013_fks(url)
    command.downgrade(_cfg(url), "0012")
    tables = _harness_tables(url)
    assert "course_translations" not in tables
    command.upgrade(_cfg(url), "head")
    tables = _harness_tables(url)
    assert "translated_answer_key" in tables["translation_blocks"]


def test_0014_clears_orphan_run_ids_before_the_foreign_key() -> None:
    """A translation row whose run vanished must not stop the upgrade."""
    import psycopg

    url = _db_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    command.downgrade(_cfg(url), "0013")
    sync = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    with psycopg.connect(sync, autocommit=True) as conn:
        user = conn.execute(
            "INSERT INTO users (email, display_name) VALUES"
            " ('orphan-0014@example.com', 'O') ON CONFLICT (email) DO UPDATE"
            " SET display_name = 'O' RETURNING id"
        ).fetchone()
        assert user is not None
        course = conn.execute(
            "INSERT INTO courses (owner_id, title) VALUES (%s, 'orphan') RETURNING id",
            (user[0],),
        ).fetchone()
        assert course is not None
        version = conn.execute(
            "INSERT INTO course_versions (course_id, snapshot) VALUES (%s, '{}') RETURNING id",
            (course[0],),
        ).fetchone()
        assert version is not None
        conn.execute(
            "INSERT INTO course_translations (id, course_version_id, language, run_id)"
            " VALUES (gen_random_uuid(), %s, 'nb', gen_random_uuid())",
            (version[0],),
        )
    command.upgrade(_cfg(url), "head")
    with psycopg.connect(sync, autocommit=True) as conn:
        row = conn.execute(
            "SELECT run_id FROM course_translations ct JOIN course_versions cv"
            " ON cv.id = ct.course_version_id WHERE cv.course_id = %s",
            (course[0],),
        ).fetchone()
        conn.execute("DELETE FROM courses WHERE id = %s", (course[0],))
    assert row is not None and row[0] is None


def _m0015_column(url: str) -> tuple[str, str] | None:
    sync = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    with psycopg.connect(sync) as conn:
        row = conn.execute(
            "SELECT is_nullable, column_default FROM information_schema.columns"
            " WHERE table_name = 'passages' AND column_name = 'is_content'"
        ).fetchone()
    return (str(row[0]), str(row[1])) if row else None


def _m0015_passage_count(url: str) -> int:
    sync = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    with psycopg.connect(sync) as conn:
        return int(conn.execute("SELECT count(*) FROM passages").fetchone()[0])  # type: ignore[index]


def test_0015_adds_is_content_not_null_default_true() -> None:
    url = _db_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    assert _m0015_column(url) == ("NO", "true")


def test_0015_downgrade_drops_only_the_column_and_upgrade_restores_it() -> None:
    url = _db_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    kept = _m0015_passage_count(url)
    before = set(_harness_tables(url)["passages"])
    command.downgrade(_cfg(url), "0014")
    assert _m0015_column(url) is None
    # 0023 (ordinal) is downgraded on the way to 0014 as well
    assert set(_harness_tables(url)["passages"]) == before - {"is_content", "ordinal"}
    assert _m0015_passage_count(url) == kept, "downgrade keeps every passage row"
    command.upgrade(_cfg(url), "head")
    assert _m0015_column(url) == ("NO", "true")


def test_0016_adds_digest_and_variant_and_downgrade_removes_only_them() -> None:
    url = _db_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    columns = _harness_tables(url)["translation_blocks"]
    assert {"source_digest", "variant"} <= set(columns)
    command.downgrade(_cfg(url), "0015")
    columns = _harness_tables(url)["translation_blocks"]
    assert "source_digest" not in columns and "variant" not in columns
    assert "translated_answer_key" in columns
    command.upgrade(_cfg(url), "head")
    assert "variant" in _harness_tables(url)["translation_blocks"]


def _m0017_sync(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def test_0017_creates_learner_answers_with_the_brief_columns() -> None:
    url = _db_url()
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
    url = _db_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    with psycopg.connect(_m0017_sync(url), autocommit=True) as conn:
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
    assert before - after == {"learner_answers", "learner_block_views", "user_model_keys"}
    command.upgrade(_cfg(url), "head")
    assert "learner_answers" in _harness_tables(url)
    with psycopg.connect(_m0017_sync(url), autocommit=True) as conn:
        conn.execute("DELETE FROM courses WHERE title = 'm17'")
        conn.execute("DELETE FROM users WHERE email IN ('m17-owner@example.com')")


def _m0018_sync(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def test_0018_allows_generated_and_downgrade_turns_it_into_draft() -> None:
    url = _db_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    with psycopg.connect(_m0018_sync(url), autocommit=True) as conn:
        owner = conn.execute(
            "INSERT INTO users (email, display_name) VALUES ('m18-owner@example.com', 'O')"
            " ON CONFLICT (email) DO UPDATE SET display_name = 'O' RETURNING id"
        ).fetchone()[0]  # type: ignore[index]
        conn.execute(
            "INSERT INTO courses (owner_id, title, status) VALUES (%s, 'm18', 'generated')",
            (owner,),
        )
    command.downgrade(_cfg(url), "0017")
    with psycopg.connect(_m0018_sync(url), autocommit=True) as conn:
        status = conn.execute("SELECT status FROM courses WHERE title = 'm18'").fetchone()[0]  # type: ignore[index]
        assert status == "draft"
        try:
            conn.execute("UPDATE courses SET status = 'generated' WHERE title = 'm18'")
        except psycopg.errors.CheckViolation:
            pass
        else:
            raise AssertionError("downgrade must restore the narrower status check")
    command.upgrade(_cfg(url), "head")
    with psycopg.connect(_m0018_sync(url), autocommit=True) as conn:
        conn.execute("DELETE FROM courses WHERE title = 'm18'")
        conn.execute("DELETE FROM users WHERE email = 'm18-owner@example.com'")


def test_0019_pins_cascade_and_downgrade_drops_only_the_two_additions() -> None:
    url = _db_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    sync = _sync_url(url)
    with psycopg.connect(sync, autocommit=True) as conn:
        user = conn.execute(
            "INSERT INTO users (email, display_name) VALUES ('m19@example.com', 'M')"
            " ON CONFLICT (email) DO UPDATE SET display_name = 'M' RETURNING id"
        ).fetchone()[0]  # type: ignore[index]
        course = conn.execute(
            "INSERT INTO courses (owner_id, title) VALUES (%s, 'm19') RETURNING id", (user,)
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
        conn.execute("UPDATE blocks SET accepted_version_id = %s WHERE id = %s", (version, block))
        conn.execute("DELETE FROM block_versions WHERE id = %s", (version,))
        pinned = conn.execute(
            "SELECT accepted_version_id FROM blocks WHERE id = %s", (block,)
        ).fetchone()[0]  # type: ignore[index]
        assert pinned is None, "a deleted version unpins the accept"
    command.downgrade(_cfg(url), "0018")
    with psycopg.connect(sync, autocommit=True) as conn:
        assert (
            conn.execute("SELECT count(*) FROM blocks WHERE id = %s", (block,)).fetchone()[0] == 1  # type: ignore[index]
        )
        columns = {
            row[0]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'blocks'"
            ).fetchall()
        }
        assert "accepted_version_id" not in columns
        assert "learner_block_views" not in {
            row[0]
            for row in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            ).fetchall()
        }
    command.upgrade(_cfg(url), "head")
    with psycopg.connect(sync, autocommit=True) as conn:
        learner = conn.execute(
            "INSERT INTO users (email, display_name) VALUES ('m19-learner@example.com', 'L')"
            " ON CONFLICT (email) DO UPDATE SET display_name = 'L' RETURNING id"
        ).fetchone()[0]  # type: ignore[index]
        conn.execute(
            "INSERT INTO learner_block_views (user_id, block_id, language) VALUES (%s, %s, 'nb')",
            (learner, block),
        )
        conn.execute("DELETE FROM users WHERE id = %s", (learner,))
        left = conn.execute("SELECT count(*) FROM learner_block_views").fetchone()[0]  # type: ignore[index]
        assert left == 0, "deleting the learner deletes the served-language rows"
        conn.execute("DELETE FROM courses WHERE title = 'm19'")
        conn.execute("DELETE FROM users WHERE email = 'm19@example.com'")


def test_0020_qualifies_plain_group_tags_pins_accepts_and_downgrade_keeps_the_data() -> None:
    url = _db_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    command.downgrade(_cfg(url), "0019")
    sync = _sync_url(url)
    course_id = "11111111-2222-3333-4444-555555555555"
    with psycopg.connect(sync, autocommit=True) as conn:
        conn.execute("DELETE FROM courses WHERE title = 'm20'")
        conn.execute("DELETE FROM invites WHERE token_hash = 'm20-hash'")
        owner = conn.execute(
            "INSERT INTO users (email, display_name) VALUES ('m20-owner@example.com', 'O')"
            " ON CONFLICT (email) DO UPDATE SET display_name = 'O' RETURNING id"
        ).fetchone()[0]  # type: ignore[index]
        learner = conn.execute(
            "INSERT INTO users (email, display_name) VALUES ('m20-learner@example.com', 'L')"
            " ON CONFLICT (email) DO UPDATE SET display_name = 'L' RETURNING id"
        ).fetchone()[0]  # type: ignore[index]
        conn.execute("INSERT INTO roles (name) VALUES ('student') ON CONFLICT (name) DO NOTHING")
        role = conn.execute("SELECT id FROM roles WHERE name = 'student'").fetchone()[0]  # type: ignore[index]
        invite = conn.execute(
            "INSERT INTO invites (scope, course_tags, created_by, granted_role, token_hash)"
            " VALUES ('oslo', %s, %s, 'student', 'm20-hash') RETURNING id",
            (["oslo", course_id], owner),
        ).fetchone()[0]  # type: ignore[index]
        conn.execute(
            "INSERT INTO invite_uses (invite_id, user_id) VALUES (%s, %s)", (invite, learner)
        )
        conn.execute(
            "INSERT INTO user_roles (user_id, role_id, scope_tags) VALUES (%s, %s, %s)"
            " ON CONFLICT (user_id, role_id) DO UPDATE SET scope_tags = EXCLUDED.scope_tags",
            (learner, role, ["oslo", "unexplained"]),
        )
        course = conn.execute(
            "INSERT INTO courses (owner_id, title) VALUES (%s, 'm20') RETURNING id", (owner,)
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
            "INSERT INTO blocks (topic_id, plan_node_id, ordinal, role, block_type, brief, status)"
            " VALUES (%s, gen_random_uuid()::text, 0, 'check', 'choice', 'b', 'accepted')"
            " RETURNING id",
            (topic,),
        ).fetchone()[0]  # type: ignore[index]
        version = conn.execute(
            "INSERT INTO block_versions (block_id, version_no, content) VALUES (%s, 1, '{}')"
            " RETURNING id",
            (block,),
        ).fetchone()[0]  # type: ignore[index]
        conn.execute("UPDATE blocks SET current_version_id = %s WHERE id = %s", (version, block))

    def state(conn: psycopg.Connection) -> tuple[object, object, object]:
        return (
            conn.execute("SELECT course_tags FROM invites WHERE id = %s", (invite,)).fetchone()[0],  # type: ignore[index]
            conn.execute(
                "SELECT scope_tags FROM user_roles WHERE user_id = %s AND role_id = %s",
                (learner, role),
            ).fetchone()[0],  # type: ignore[index]
            conn.execute(
                "SELECT accepted_version_id FROM blocks WHERE id = %s", (block,)
            ).fetchone()[0],  # type: ignore[index]
        )

    command.upgrade(_cfg(url), "head")
    with psycopg.connect(sync, autocommit=True) as conn:
        tags, grant, pin = state(conn)
        assert tags == sorted([course_id, f"{owner}:oslo"]), tags
        assert grant == sorted([f"{owner}:oslo", "unexplained"]), grant
        assert pin == version
    command.downgrade(_cfg(url), "0019")
    command.upgrade(_cfg(url), "head")
    with psycopg.connect(sync, autocommit=True) as conn:
        assert state(conn) == (tags, grant, version), "a second run changes nothing"
        conn.execute("DELETE FROM courses WHERE title = 'm20'")
        conn.execute("DELETE FROM invites WHERE token_hash = 'm20-hash'")
        conn.execute(
            "DELETE FROM users WHERE email IN ('m20-owner@example.com', 'm20-learner@example.com')"
        )


def test_0021_adds_uncovered_and_downgrade_drops_only_it() -> None:
    url = _db_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    assert "uncovered" in _harness_tables(url)["courses"]
    command.downgrade(_cfg(url), "0020")
    columns = _harness_tables(url)["courses"]
    assert "uncovered" not in columns and "through_line" in columns
    command.upgrade(_cfg(url), "head")
    assert "uncovered" in _harness_tables(url)["courses"]


def test_0022_allows_thin_and_downgrade_turns_it_into_failed_keeping_the_message() -> None:
    url = _db_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    sync = _sync_url(url)
    digest = "m22-" + "0" * 60
    with psycopg.connect(sync, autocommit=True) as conn:
        conn.execute("DELETE FROM sources WHERE content_sha256 = %s", (digest,))
        conn.execute(
            "INSERT INTO sources (content_sha256, kind, media, title, approval, storage_path,"
            " ingest_status, ingest_message) VALUES (%s, 'url', 'text', 'm22', 'pending', '',"
            " 'thin', 'no readable text')",
            (digest,),
        )
    command.downgrade(_cfg(url), "0021")
    with psycopg.connect(sync, autocommit=True) as conn:
        row = conn.execute(
            "SELECT ingest_status, ingest_message FROM sources WHERE content_sha256 = %s",
            (digest,),
        ).fetchone()
        assert row == ("failed", "no readable text"), row
        try:
            conn.execute(
                "UPDATE sources SET ingest_status = 'thin' WHERE content_sha256 = %s", (digest,)
            )
        except psycopg.errors.CheckViolation:
            pass
        else:
            raise AssertionError("downgrade must restore the narrower ingest_status check")
    command.upgrade(_cfg(url), "head")
    with psycopg.connect(sync, autocommit=True) as conn:
        conn.execute(
            "UPDATE sources SET ingest_status = 'thin' WHERE content_sha256 = %s", (digest,)
        )
        conn.execute("DELETE FROM sources WHERE content_sha256 = %s", (digest,))


def test_0023_adds_passage_ordinal_backfilled_in_address_order_and_downgrade_drops_it() -> None:
    url = _db_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    sync = _sync_url(url)
    with psycopg.connect(sync, autocommit=True) as conn:
        conn.execute("DELETE FROM sources WHERE content_sha256 = 'm23'")
        source = conn.execute(
            "INSERT INTO sources (content_sha256, kind, media, title, approval, storage_path)"
            " VALUES ('m23', 'upload', 'text', 'm23', 'pending', '') RETURNING id"
        ).fetchone()[0]  # type: ignore[index]
        for key in ("p1/para2", "p1/para1"):
            conn.execute(
                "INSERT INTO passages (source_id, text, address, address_key, ts_config)"
                " VALUES (%s, 'x.', '{}', %s, 'english')",
                (source, key),
            )
    command.downgrade(_cfg(url), "0022")
    assert "ordinal" not in _harness_tables(url)["passages"]
    command.upgrade(_cfg(url), "head")
    with psycopg.connect(sync, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT address_key, ordinal FROM passages WHERE source_id = %s ORDER BY ordinal",
            (source,),
        ).fetchall()
        assert rows == [("p1/para1", 0), ("p1/para2", 1)], rows
        conn.execute("DELETE FROM sources WHERE id = %s", (source,))


def test_0024_adds_topic_scenario_defaulting_to_empty_and_downgrade_drops_it() -> None:
    url = _db_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    sync = _sync_url(url)
    assert "scenario" in _harness_tables(url)["topics"]
    with psycopg.connect(sync, autocommit=True) as conn:
        default = conn.execute(
            "SELECT column_default, is_nullable FROM information_schema.columns"
            " WHERE table_name = 'topics' AND column_name = 'scenario'"
        ).fetchone()
    assert default is not None and default[0].startswith("'{}'") and default[1] == "NO"
    command.downgrade(_cfg(url), "0023")
    assert "scenario" not in _harness_tables(url)["topics"]
    command.upgrade(_cfg(url), "head")
    assert "scenario" in _harness_tables(url)["topics"]


def test_0026_adds_is_public_false_and_gives_roleless_accounts_the_tagless_student_grant() -> None:
    url = _db_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    sync = _sync_url(url)
    with psycopg.connect(sync, autocommit=True) as conn:
        default = conn.execute(
            "SELECT column_default, is_nullable FROM information_schema.columns"
            " WHERE table_name = 'courses' AND column_name = 'is_public'"
        ).fetchone()
        assert default == ("false", "NO"), default
        conn.execute("DELETE FROM users WHERE email = 'm26@example.com'")
        user = conn.execute(
            "INSERT INTO users (email, display_name) VALUES ('m26@example.com', 'm26') RETURNING id"
        ).fetchone()[0]  # type: ignore[index]
    command.downgrade(_cfg(url), "0025")
    assert "is_public" not in _harness_tables(url)["courses"]
    command.upgrade(_cfg(url), "head")
    with psycopg.connect(sync, autocommit=True) as conn:
        grants = conn.execute(
            "SELECT r.name, ur.scope_tags FROM user_roles ur JOIN roles r ON r.id = ur.role_id"
            " WHERE ur.user_id = %s",
            (user,),
        ).fetchall()
        assert grants == [("student", [])], grants
        conn.execute("DELETE FROM users WHERE id = %s", (user,))


def test_0027_adds_nullable_run_spend_ceiling_and_downgrade_drops_only_it() -> None:
    url = _db_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    sync = _sync_url(url)
    with psycopg.connect(sync, autocommit=True) as conn:
        column = conn.execute(
            "SELECT data_type, numeric_precision, numeric_scale, is_nullable"
            " FROM information_schema.columns"
            " WHERE table_name = 'runs' AND column_name = 'spend_ceiling_usd'"
        ).fetchone()
    assert column == ("numeric", 12, 6, "YES"), column
    command.downgrade(_cfg(url), "0026")
    assert "spend_ceiling_usd" not in _harness_tables(url)["runs"]
    assert "is_public" in _harness_tables(url)["courses"]
    command.upgrade(_cfg(url), "head")
    assert "spend_ceiling_usd" in _harness_tables(url)["runs"]
