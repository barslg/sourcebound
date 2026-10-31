"""One exception hierarchy plus the single exception-to-HTTP table.

Errors are wrapped at the layer that catches them; routes render them
through the table below so httpx and driver details never leak outward.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Base of every error this service raises on purpose."""

    status_code: int = 500
    error_code: str = "internal"


class ValidationError(AppError):
    status_code = 400
    error_code = "bad_request"


class AuthError(AppError):
    status_code = 401
    error_code = "unauthenticated"


class ForbiddenError(AppError):
    status_code = 403
    error_code = "forbidden"


class NotFoundError(AppError):
    status_code = 404
    error_code = "not_found"


class ConflictError(AppError):
    status_code = 409
    error_code = "conflict"


class UnsupportedMediaError(AppError):
    """The upload media has no ingestion path (images/audio need theirs)."""

    status_code = 415
    error_code = "unsupported_media"


class LLMError(AppError):
    """The model call failed after the retry policy was exhausted."""

    status_code = 502
    error_code = "llm_unavailable"


class ConfigurationError(AppError):
    """The service is misconfigured (a missing key, a bad setting)."""

    status_code = 500
    error_code = "misconfigured"


EXCEPTION_STATUS_TABLE: dict[type[Exception], int] = {
    ValidationError: 400,
    AuthError: 401,
    ForbiddenError: 403,
    NotFoundError: 404,
    ConflictError: 409,
    UnsupportedMediaError: 415,
    LLMError: 502,
    ConfigurationError: 500,
    AppError: 500,
}


def http_status_for(exc: Exception) -> int:
    """Look up the HTTP status for any exception; unknown maps to 500."""
    for cls in type(exc).__mro__:
        if cls in EXCEPTION_STATUS_TABLE:
            return EXCEPTION_STATUS_TABLE[cls]
    return 500


def _error_code(exc: Exception) -> str:
    if isinstance(exc, AppError):
        return exc.error_code
    return "internal"


async def _render(request: Request, exc: Exception) -> JSONResponse:
    del request
    message = str(exc) if isinstance(exc, AppError) else "internal error"
    return JSONResponse(
        status_code=http_status_for(exc),
        content={"error": _error_code(exc), "message": message},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the single rendering table to a FastAPI application."""

    async def _app_error(request: Request, exc: Any) -> JSONResponse:
        return await _render(request, exc)

    app.add_exception_handler(AppError, _app_error)
    app.add_exception_handler(Exception, _app_error)
