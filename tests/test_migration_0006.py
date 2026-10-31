"""Migration 0006 round-trip: courses.through_line JSONB.

Upgrade must add the column; downgrade must drop it; upgrade again
must hold.
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


def test_0006_upgrade_adds_through_line() -> None:
    url = test_database_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    tables = _harness_tables(url)
    assert "through_line" in tables["courses"]


def test_0006_downgrade_drops_through_line() -> None:
    url = test_database_url()
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
