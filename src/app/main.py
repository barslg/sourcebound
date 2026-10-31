"""FastAPI application factory with startup validation.

The lifespan validates every registered LLM schema against Meta's strict
subset before serving traffic, so a bad schema stops the deploy instead
of failing a generation run halfway through.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import Settings, get_settings
from app.errors import register_exception_handlers
from app.llm import prompts
from app.logging import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Validate every LLM schema on startup; fail fast when invalid."""
    del app
    prompts.validate_registered_schemas()
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application; routes only parse and validate."""
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)
    app = FastAPI(title="Grounded Course Studio", lifespan=lifespan)
    register_exception_handlers(app)

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
