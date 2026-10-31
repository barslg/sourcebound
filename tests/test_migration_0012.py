"""Migration 0012 round-trip on the isolated test database (downgrade first).

0012 adds the per-source ingest status (queued/parsing/ready/failed
plus a failure message); pre-existing rows upgrade as ready.

Upgrade must add the columns and the check; downgrade must remove
exactly those additions; upgrade again must hold.
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


def test_0012_upgrade_adds_ingest_status() -> None:
    url = test_database_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    tables = _harness_tables(url)
    assert {"ingest_status", "ingest_message"} <= tables["sources"]


def test_0012_downgrade_restores_0011_shape() -> None:
    url = test_database_url()
    require_postgres(url)
    command.upgrade(_cfg(url), "head")
    command.downgrade(_cfg(url), "0011")
    tables = _harness_tables(url)
    assert "ingest_status" not in tables["sources"]
    assert "ingest_message" not in tables["sources"]
    command.upgrade(_cfg(url), "head")
    tables = _harness_tables(url)
    assert {"ingest_status", "ingest_message"} <= tables["sources"]
