"""Migration 0015 round-trip on the isolated test database.

0015 adds passages.is_content (NOT NULL, default true): reuse notices
and source lines are stored but never offered to the spot-the-error
picker. Downgrade must drop exactly that column and keep every passage.
"""

from __future__ import annotations

import psycopg
from alembic import command
from conftest import require_postgres, test_database_url
from test_migration_0001 import _harness_tables
from test_migration_0013 import _cfg


def _column(url: str) -> tuple[str, str] | None:
    sync = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    with psycopg.connect(sync) as conn:
        row = conn.execute(
            "SELECT is_nullable, column_default FROM information_schema.columns"
            " WHERE table_name = 'passages' AND column_name = 'is_content'"
        ).fetchone()
    return (str(row[0]), str(row[1])) if row else None


def _passage_count(url: str) -> int:
    sync = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    with psycopg.connect(sync) as conn:
        return int(conn.execute("SELECT count(*) FROM passages").fetchone()[0])  # type: ignore[index]


def test_0015_adds_is_content_not_null_default_true() -> None:
    url = test_database_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    assert _column(url) == ("NO", "true")


def test_0015_downgrade_drops_only_the_column_and_upgrade_restores_it() -> None:
    url = test_database_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    kept = _passage_count(url)
    before = set(_harness_tables(url)["passages"])
    command.downgrade(_cfg(url), "0014")
    assert _column(url) is None
    assert set(_harness_tables(url)["passages"]) == before - {"is_content"}
    assert _passage_count(url) == kept, "downgrade keeps every passage row"
    command.upgrade(_cfg(url), "head")
    assert _column(url) == ("NO", "true")
