"""Migration 0002 round-trip on the isolated test database (downgrade first).

0002 owns every column/table T02 needs beyond the day-1 harness:
bans and legacy holders on users, identities, backend sessions,
one-time OIDC states, audit log, operations, notifications, invite
uses, scope tags on user_roles, CRUD matrix plus scope tags on roles,
and revocation plus course tags on invites.

Upgrade must add them; downgrade must remove exactly those additions
(Alembic keeps its own alembic_version row; the Procrastinate schema
is owned by the worker, not by this migration).
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


def test_0002_upgrade_adds_t02_columns_and_tables() -> None:
    url = test_database_url()
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
    url = test_database_url()
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
