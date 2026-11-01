"""Secret redaction for logs and errors.

Only the client module ever holds the live key; everything else passes
opaque values through here before they reach logs, errors, or responses.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

REDACTED = "[redacted]"

# Key-shaped tokens (Meta API keys, bearer tokens, OpenAI-style keys).
_PATTERNS = (
    re.compile(r"LLM_[A-Za-z0-9_\-]{8,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"GOCSPX-[A-Za-z0-9_\-]{8,}"),
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


def settings_secrets(settings: object) -> list[str]:
    """Every SecretStr value from config, for redaction pipelines.

    Reads the settings object generically so a newly added SecretStr
    setting is masked without touching this module.
    """
    from pydantic import SecretStr

    found: list[str] = []
    fields = getattr(type(settings), "model_fields", None)
    names = list(fields) if fields else [name for name in dir(settings) if not name.startswith("_")]
    for name in names:
        try:
            value = getattr(settings, name)
        except Exception:
            continue
        if isinstance(value, SecretStr):
            secret = value.get_secret_value()
            if secret:
                found.append(secret)
    return found
