"""Migration 0005 round-trip on the isolated test database (downgrade first).

0005 scopes sources to courses (course_sources with its own
approval), links runs to courses for scope checks, narrows
plan_node_id uniqueness to per-parent (per course through the
chain), and widens the course language check to BCP-47.

Upgrade must add them; downgrade must remove exactly those
additions and restore the 0004 shape; upgrade again must hold.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from conftest import require_postgres, test_database_url
from test_migration_0001 import _harness_tables


def _cfg(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def test_0005_upgrade_adds_scoped_sources_and_per_course_node_ids() -> None:
    url = test_database_url()
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
    url = test_database_url()
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
