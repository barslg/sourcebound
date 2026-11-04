"""Structured logging with secret redaction baked into the pipeline."""

from __future__ import annotations

import logging
from collections.abc import Callable

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


QUIET_JOBS = ("builds.revive",)
QUIET_LOGGERS = ("procrastinate.periodic", "procrastinate.worker", "procrastinate.worker.worker")


def _not_a_quiet_job(record: logging.LogRecord) -> bool:
    """False for the worker's lines about the once-a-minute housekeeping job."""
    message = record.getMessage()
    return not any(job in message for job in QUIET_JOBS)


def _pretty_renderer() -> Callable[[WrappedLogger, str, EventDict], str]:
    """Model calls as one readable line each, every other event as a colored key=value line."""
    from app.llm.call_line import render_call_line

    console = structlog.dev.ConsoleRenderer(colors=True)

    def render(logger: WrappedLogger, method: str, event_dict: EventDict) -> str:
        if event_dict.get("event") == "model_call":
            return render_call_line(dict(event_dict), color=True)
        return str(console(logger, method, event_dict))

    return render


def configure_logging(
    level: str = "INFO", secrets: tuple[str, ...] = (), *, log_format: str = "pretty"
) -> None:
    """Configure structlog once at startup (pretty console lines or JSON); safe to call twice."""
    global _EXTRA_SECRETS
    _EXTRA_SECRETS = tuple(secrets)
    for name in QUIET_LOGGERS:
        quiet = logging.getLogger(name)
        if _not_a_quiet_job not in quiet.filters:
            quiet.addFilter(_not_a_quiet_job)
    pretty = log_format == "pretty"
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(
                fmt="%Y-%m-%d %H:%M:%S" if pretty else "iso", utc=not pretty
            ),
            _redact_processor,
            _pretty_renderer() if pretty else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=False,
    )
