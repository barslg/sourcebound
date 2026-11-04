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
    plain_message: bool = False


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


class UnprocessableError(AppError):
    """The request is valid but the resource state refuses it (held blocks)."""

    status_code = 422
    error_code = "unprocessable"
    plain_message = True


class UnsupportedMediaError(AppError):
    """The upload media has no ingestion path (images/audio need theirs)."""

    status_code = 415
    error_code = "unsupported_media"


class RateLimitedError(AppError):
    """A per-user allowance is used up; the message says when it opens again."""

    status_code = 429
    error_code = "rate_limited"
    plain_message = True


class LLMError(AppError):
    """The model call failed after the retry policy was exhausted."""

    status_code = 502
    error_code = "llm_unavailable"


class ReplayMissError(LLMError):
    """Replay mode was asked for a request its ledger never recorded."""


class UnparseableReplyError(LLMError):
    """A paid reply that is not a strict JSON object: kept for the ledger.

    The call completed and was billed, so the usage and the raw text
    travel with the error and the provider logs them before re-raising.
    """

    def __init__(
        self,
        message: str,
        *,
        raw_text: str = "",
        input_tokens: int = 0,
        cached_tokens: int = 0,
        output_tokens: int = 0,
        reasoning_tokens: int = 0,
    ) -> None:
        super().__init__(message)
        self.raw_text = raw_text
        self.input_tokens = input_tokens
        self.cached_tokens = cached_tokens
        self.output_tokens = output_tokens
        self.reasoning_tokens = reasoning_tokens


class SpendCapReached(AppError):
    """A run reached its spend ceiling: no further paid call is made.

    Deliberately not an LLMError: the rework loop turns an LLMError into a
    held block, while a cap must stop the run and the build.
    """

    status_code = 402
    error_code = "cap_reached"
    plain_message = True


class RunCancelled(AppError):
    """The run carries a stop marker (the author's Stop): no further paid call is made."""

    status_code = 409
    error_code = "cancelled"
    plain_message = True


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
    UnprocessableError: 422,
    UnsupportedMediaError: 415,
    RateLimitedError: 429,
    LLMError: 502,
    SpendCapReached: 402,
    RunCancelled: 409,
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


def _request_locale(request: Request) -> str | None:
    """Explicit request language: ?lang= wins, then Accept-Language."""
    try:
        lang = request.query_params.get("lang")
    except Exception:
        lang = None
    if lang:
        return str(lang).split("-")[0].lower()
    header = ""
    try:
        header = str(request.headers.get("accept-language", ""))
    except Exception:
        header = ""
    first = header.split(",")[0].strip().split(";")[0].strip()
    if not first or first == "*":
        return None
    return first.split("-")[0].lower()


async def _render(request: Request, exc: Exception) -> JSONResponse:
    from app.i18n.messages import error_message

    code = _error_code(exc)
    locale = _request_locale(request)
    specific = str(exc) if isinstance(exc, AppError) and str(exc) else error_message(code, "en")
    if locale is not None and not (isinstance(exc, AppError) and exc.plain_message):
        # An explicit language gets the localised catalogue message; a guard sentence
        # written for the person at the screen (plain_message) is shown as written.
        message = error_message(code, locale)
    else:
        message = specific
    content: dict[str, Any] = {"error": code, "message": message}
    if locale is not None:
        # The specific message rides beside the localised one.
        content["localized_message"] = error_message(code, locale)
    return JSONResponse(status_code=http_status_for(exc), content=content)


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the single rendering table to a FastAPI application."""

    async def _app_error(request: Request, exc: Any) -> JSONResponse:
        return await _render(request, exc)

    app.add_exception_handler(AppError, _app_error)
    app.add_exception_handler(Exception, _app_error)
