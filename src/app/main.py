"""FastAPI application factory with startup validation.

The lifespan validates every registered LLM schema against Meta's strict
subset before serving traffic, syncs the RBAC permission catalogue,
and bridges Postgres NOTIFY into the SSE bus, so a bad schema or a
missing permission stops the deploy instead of failing a request.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import Settings, get_settings, validate_runtime_settings
from app.db import get_session_factory
from app.errors import register_exception_handlers
from app.llm import prompts
from app.llm.registry import validate_step_registry
from app.logging import configure_logging
from app.redact import settings_secrets
from app.routes import auth as auth_routes
from app.routes import ops as ops_routes
from app.routes import t02 as t02_routes


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Validate schemas, sync RBAC, and forward NOTIFY events to SSE."""
    from app.services.events import listen_forever
    from app.startup import startup_sync

    settings: Settings = app.state.settings
    validate_runtime_settings(settings)
    validate_step_registry()
    prompts.validate_registered_schemas()
    prompts.validate_prompt_files()
    listener: asyncio.Task[None] | None = None
    if app.state.sync_on_startup:
        session_factory = get_session_factory()
        async with session_factory() as session:
            await startup_sync(session)
            await session.commit()
        listener = asyncio.ensure_future(listen_forever(settings.database_url))
    try:
        yield
    finally:
        if listener is not None:
            listener.cancel()


def create_app(settings: Settings | None = None, *, sync_on_startup: bool = True) -> FastAPI:
    """Build the FastAPI application; routes only parse and validate."""
    resolved = settings or get_settings()
    configure_logging(resolved.log_level, secrets=tuple(settings_secrets(resolved)))
    app = FastAPI(title="Grounded Course Studio", lifespan=lifespan)
    app.state.settings = resolved
    app.state.sync_on_startup = sync_on_startup
    register_exception_handlers(app)

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(auth_routes.router, prefix="/api/v1")
    app.include_router(t02_routes.users_router, prefix="/api/v1")
    app.include_router(t02_routes.roles_router, prefix="/api/v1")
    app.include_router(t02_routes.invites_router, prefix="/api/v1")
    app.include_router(ops_routes.invites_accept_router, prefix="/api/v1")
    app.include_router(ops_routes.operations_router, prefix="/api/v1")
    app.include_router(ops_routes.events_router, prefix="/api/v1")
    return app


app = create_app()
