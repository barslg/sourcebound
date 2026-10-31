"""Shared fixtures: isolated test database, migration, and HTTP guard.

DB-backed tests run against a dedicated test database (never the dev
database), created and migrated once per session. They skip with a reason
when Postgres is unreachable. A session-wide transport guard forbids any
HTTP outside the Model API allowlist for every test, even without an
explicit guarded client.
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Iterator
from urllib.parse import urlparse

import pytest

from app.config import get_settings


def derive_test_url(url: str) -> str:
    """Swap the database name for `muse_test`, keeping host, port, and args."""
    return re.sub(r"/[^/?]*(\?.*)?$", r"/muse_test\1", url)


def test_database_url() -> str:
    """Dedicated test database URL: TEST_DATABASE_URL or derived `muse_test`."""
    override = (get_settings().test_database_url or "").strip()
    if override:
        return override
    return derive_test_url(get_settings().database_url)


def test_db_name() -> str:
    """Database name parsed from the test URL (quoted at CREATE time)."""
    sync = test_database_url().replace("postgresql+asyncpg://", "postgresql://", 1)
    return urlparse(sync).path.lstrip("/") or "muse_test"


def _maintenance_url() -> str:
    sync = test_database_url().replace("postgresql+asyncpg://", "postgresql://", 1)
    return re.sub(r"/[^/?]*(\?.*)?$", r"/postgres\1", sync)


def postgres_reachable(url: str) -> bool:
    """True when a Postgres answers at the given SQLAlchemy URL."""
    import psycopg

    dsn = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    try:
        conn = psycopg.connect(dsn, connect_timeout=3)
    except Exception:
        return False
    conn.close()
    return True


def require_postgres(url: str) -> None:
    """Skip the calling test when Postgres is unreachable."""
    if not postgres_reachable(url):
        pytest.skip(f"no Postgres reachable for {url!r}; run `docker compose up -d db`")


@pytest.fixture(scope="session", autouse=True)
def _test_database() -> Iterator[str]:
    """Create the test database once per session and migrate it to head.

    Yields the test URL without touching anything when Postgres is down;
    DB-backed tests then skip via require_postgres. This fixture is sync
    so Alembic's asyncio.run() never meets a running event loop.
    """
    url = test_database_url()
    if not postgres_reachable(_maintenance_url()):
        warnings.warn(
            f"Postgres unreachable; DB-backed tests will skip (wanted {url!r})",
            stacklevel=2,
        )
        yield url
        return
    import psycopg
    from psycopg import errors as pg_errors

    name = test_db_name()
    try:
        with psycopg.connect(_maintenance_url(), connect_timeout=5, autocommit=True) as conn:
            try:
                conn.execute(f'CREATE DATABASE "{name}"')
            except pg_errors.DuplicateDatabase:
                pass
    except Exception as exc:
        warnings.warn(
            f"cannot create test database {name!r}: {exc}; DB tests will skip",
            stacklevel=2,
        )
        yield url
        return
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    yield url


T03_TABLES = [
    "retrieval_pairs",
    "citations",
    "passages",
    "source_candidates",
    "human_queue",
    "sources",
    "profile_constraints",
    "course_profiles",
    "block_versions",
    "blocks",
    "topics",
    "modules",
    "course_versions",
    "courses",
    "course_groups",
    "methodologies",
    "llm_calls",
    "llm_replies",
    "run_steps",
    "runs",
    "operations",
    "notifications",
]


@pytest.fixture()
async def t03_session() -> Iterator[object]:
    """Isolated session with course/library/run tables truncated."""
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import AsyncSession
    from t02_helpers import make_session_factory

    factory = make_session_factory()
    async with factory() as session:
        session_: AsyncSession = session
        for table in T03_TABLES:
            try:
                await session_.execute(sa.text(f'DELETE FROM "{table}"'))
            except Exception:
                await session_.rollback()
        await session_.commit()
        yield session_
        await session_.rollback()


@pytest.fixture(scope="session", autouse=True)
def _session_http_guard() -> Iterator[None]:
    """Forbid off-allowlist HTTP for every test in this session."""
    from transport_guard import install_session_guard

    uninstall = install_session_guard()
    try:
        yield
    finally:
        uninstall()
