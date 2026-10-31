"""Structured logging with secret redaction baked into the pipeline."""

from __future__ import annotations

from typing import Any

import structlog
from structlog.types import EventDict, WrappedLogger

from app.redact import redact

_EXTRA_SECRETS: tuple[str, ...] = ()


def _redact_value(value: object) -> object:
    if isinstance(value, str):
        return redact(value, secrets=_EXTRA_SECRETS)
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(item) for item in value]
    return value


def _redact_processor(logger: WrappedLogger, method: str, event_dict: EventDict) -> EventDict:
    del logger, method
    for key, value in event_dict.items():
        event_dict[key] = _redact_value(value)
    return event_dict


def configure_logging(level: str = "INFO", secrets: tuple[str, ...] = ()) -> None:
    """Configure structlog once at startup; safe to call twice."""
    import logging

    global _EXTRA_SECRETS
    _EXTRA_SECRETS = tuple(secrets)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> Any:
    """Return a bound logger for the calling module."""
    return structlog.get_logger(name)
