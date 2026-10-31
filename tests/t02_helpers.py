"""T02 test helpers: isolated async sessions on muse_test, truncation, app client.

DB-backed tests run against the dedicated test database created and
migrated by tests/conftest.py. Every helper here uses a short-lived
async session per call; tests never share a session across awaits that
need isolation (concurrency tests rely on this).
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from conftest import test_database_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def make_session_factory() -> async_sessionmaker[AsyncSession]:
    """Fresh session factory pointed at the isolated test database."""
    engine = create_async_engine(test_database_url(), pool_pre_ping=True)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def truncate_all(session: AsyncSession) -> None:
    """Remove every row from every app table; keep alembic/procrastinate state.

    Each table clears in its own savepoint: a table blocked by an
    out-of-scope foreign key (course owners are RESTRICT) skips without
    rolling back the tables already cleared.
    """
    tables = [
        "invite_uses",
        "notifications",
        "operations",
        "audit_log",
        "auth_states",
        "sessions",
        "identities",
        "superadmin_identities",
        "bans",
        "invites",
        "user_roles",
        "role_permissions",
        "permissions",
        "users",
        "roles",
        "run_steps",
        "runs",
        "llm_calls",
        "llm_replies",
    ]
    for table in tables:
        nested = await session.begin_nested()
        try:
            await session.execute(sa.text(f'DELETE FROM "{table}"'))
        except Exception:
            await nested.rollback()
        else:
            await nested.commit()
    await session.commit()


def unique_email(prefix: str = "user") -> str:
    """Fresh email address so tests never collide on the unique column."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}@example.com"
