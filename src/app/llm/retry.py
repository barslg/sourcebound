"""Retry policy by HTTP status for Meta Model API calls.

429/500/503 retry with backoff plus jitter from 500 ms, honouring
Retry-After in delta-seconds or HTTP-date form; a dropped connection or
read timeout behaves like a 504 (the call outgrew the window and must
continue in the background, never blindly re-POSTed); 504 means the call
outgrew the window and must be re-issued with background=true;
400/401/402/403/404/413 are never retried.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

RETRYABLE = frozenset({429, 500, 503})
NEVER_RETRY = frozenset({400, 401, 402, 403, 404, 413})
MAX_ATTEMPTS = 4
BASE_BACKOFF_MS = 500


def should_retry(status: int, attempt: int) -> bool:
    """True while a retryable status still has attempts left (1-based)."""
    return status in RETRYABLE and 1 <= attempt < MAX_ATTEMPTS


def backoff_ms(attempt: int, retry_after_ms: int | None) -> float:
    """Exponential backoff from 500 ms with full jitter, never below
    the server's Retry-After hint."""
    shift = attempt - 1
    if shift < 0:
        shift = 0
    base = float(BASE_BACKOFF_MS * (2**shift))
    wait: float = base + random.uniform(0.0, base)
    if retry_after_ms is not None and float(retry_after_ms) > wait:
        wait = float(retry_after_ms)
    return wait


def retry_after_ms(error: Any) -> int | None:
    """Retry-After hint in ms: delta-seconds or an HTTP-date, else None."""
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    hint = headers.get("retry-after")
    if hint is None:
        return None
    try:
        return int(float(hint.strip()) * 1000)
    except ValueError:
        pass
    try:
        moment = parsedate_to_datetime(hint.strip())
    except (TypeError, ValueError):
        return None
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    delay_ms = int((moment - datetime.now(UTC)).total_seconds() * 1000)
    return max(delay_ms, 0)


def use_background(status: int) -> bool:
    """A gateway timeout means re-issuing the call with background=true."""
    return status == 504
