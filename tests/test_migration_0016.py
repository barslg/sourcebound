"""Migration 0016 round-trip: translation_blocks.source_digest and variant."""

from __future__ import annotations

from alembic import command
from alembic.config import Config
from conftest import require_postgres, test_database_url
from test_migration_0001 import _harness_tables


def _cfg(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def test_0016_adds_digest_and_variant_and_downgrade_removes_only_them() -> None:
    url = test_database_url()
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
