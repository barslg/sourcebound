"""Async SQLAlchemy engine and the one-session-per-request dependency.

Requests commit on success and roll back on any exception. Jobs take a
fresh session per task instead of reusing a request session.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings


def create_engine(url: str | None = None) -> AsyncEngine:
    """Pooled async engine with pre-ping; URL comes from settings."""
    return create_async_engine(
        url or get_settings().database_url,
        pool_pre_ping=True,
    )


def create_session_factory(url: str) -> async_sessionmaker[AsyncSession]:
    """Standalone session factory for one URL (ledger writes, tests)."""
    return async_sessionmaker(
        create_async_engine(url, pool_pre_ping=True),
        class_=AsyncSession,
        expire_on_commit=False,
    )


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Process-wide engine, created on first use."""
    global _engine
    if _engine is None:
        _engine = create_engine()
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Session factory with expire_on_commit=False for detached reads."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one AsyncSession per request."""
    session = get_session_factory()()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
