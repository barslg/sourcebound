"""Migration 0001 round-trip on the isolated test database.

Upgrade builds the harness tables, downgrade removes them (Alembic keeps
its own alembic_version row), and a second upgrade works again. Runs
against muse_test, never the dev database (see conftest).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

# Table -> required column subset (types checked loosely via names only).
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
    from conftest import require_postgres, test_database_url

    url = test_database_url()
    require_postgres(url)
    cfg = _cfg(url)
    command.downgrade(cfg, "base")
    assert _harness_tables(url) == {}

    command.upgrade(cfg, "head")
    tables = _harness_tables(url)
    assert set(tables) == set(EXPECTED_COLUMNS)
    for table, columns in EXPECTED_COLUMNS.items():
        assert columns <= tables[table], f"{table} missing {columns - tables[table]}"

    command.downgrade(cfg, "base")
    assert _harness_tables(url) == {}

    command.upgrade(cfg, "head")
    assert set(_harness_tables(url)) == set(EXPECTED_COLUMNS)
