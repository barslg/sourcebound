"""Secret redaction for logs and errors.

Only the client module ever holds the live key; everything else passes
opaque values through here before they reach logs, errors, or responses.
"""

from __future__ import annotations

import hmac
import re
from collections.abc import Iterable

REDACTED = "[redacted]"

# Key-shaped tokens (Meta API keys, bearer tokens, OpenAI-style keys).
_PATTERNS = (
    re.compile(r"LLM_[A-Za-z0-9_\-]{8,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9_\-.~+/=]{8,}"),
)


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    """Mask known secret values and key-shaped patterns in free text."""
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, REDACTED)
    for pattern in _PATTERNS:
        redacted = pattern.sub(
            lambda match: match.group(1) + REDACTED if match.lastindex else REDACTED,
            redacted,
        )
    return redacted


def constant_time_equal(left: str, right: str) -> bool:
    """Compare secrets without leaking length/prefix timing."""
    return hmac.compare_digest(left.encode(), right.encode())
