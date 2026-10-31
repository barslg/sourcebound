"""Procrastinate application used by the compose worker service.

Day-1 defines the app object only; job wrappers arrive with the first
operation that takes time. The DSN is derived from DATABASE_URL by
dropping the async dialect prefix; nothing connects at import time.
"""

from __future__ import annotations

from procrastinate import App, PsycopgConnector

from app.config import get_settings


def psycopg_dsn(url: str) -> str:
    """Convert an async SQLAlchemy URL to a plain psycopg DSN."""
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg://"):
        if url.startswith(prefix):
            return "postgresql://" + url[len(prefix) :]
    return url


def create_worker_app() -> App:
    """Build the Procrastinate app from settings without connecting.

    import_paths pulls in app.tasks.jobs, so the worker process that
    loads this app always registers the operation tasks (no TaskNotFound).
    """
    settings = get_settings()
    return App(
        connector=PsycopgConnector(conninfo=psycopg_dsn(settings.database_url)),
        import_paths=["app.tasks.jobs"],
    )


app = create_worker_app()
