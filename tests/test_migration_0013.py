"""Migration 0013/0014 round-trip on the isolated test database.

0013 creates course_translations, translation_blocks, translation_memory.
0014 adds translation_blocks.translated_answer_key, runs.kind, the
course_translations.run_id foreign key, and translation_memory.course_id.
Downgrade must remove exactly those additions; upgrade again must hold.
"""

from __future__ import annotations

from alembic import command
from alembic.config import Config
from conftest import require_postgres, test_database_url
from test_migration_0001 import _harness_tables


def _cfg(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _fks(url: str) -> set[tuple[str, str]]:
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
    url = test_database_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    tables = _harness_tables(url)
    assert "course_translations" in tables
    assert "translation_blocks" in tables
    assert "translation_memory" in tables


def test_0014_adds_key_kind_fk_and_tm_course() -> None:
    url = test_database_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    tables = _harness_tables(url)
    assert "translated_answer_key" in tables["translation_blocks"]
    assert "kind" in tables["runs"]
    assert "course_id" in tables["translation_memory"]
    assert ("course_translations", "runs") in _fks(url)


def test_0014_downgrade_restores_0013_shape() -> None:
    url = test_database_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    command.downgrade(_cfg(url), "0013")
    tables = _harness_tables(url)
    assert "translated_answer_key" not in tables["translation_blocks"]
    assert "kind" not in tables["runs"]
    assert "course_id" not in tables["translation_memory"]
    assert ("course_translations", "runs") not in _fks(url)
    command.downgrade(_cfg(url), "0012")
    tables = _harness_tables(url)
    assert "course_translations" not in tables
    command.upgrade(_cfg(url), "head")
    tables = _harness_tables(url)
    assert "translated_answer_key" in tables["translation_blocks"]
