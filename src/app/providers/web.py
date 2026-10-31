"""Web provider: fetch, robots, and transcription over clients/web.py.

Services call this provider, never the HTTP client: the provider owns
timeouts (from settings), the no-redirect policy, and the SSRF guard,
while clients/web.py owns the bytes on the wire.
"""

from __future__ import annotations

import asyncio
import urllib.robotparser
from typing import Any
from urllib.parse import urlparse

from app.clients.web import (
    FetchedPage,
    build_client,
    fetch_page,
    fetch_robots_text,
    post_transcription,
)
from app.clients.web import (
    ensure_public_url as _ensure_public_url,
)
from app.config import Settings
from app.errors import ConfigurationError

__all__ = [
    "FetchedPage",
    "ensure_public_url",
    "fetch_url",
    "robots_allowed",
    "request_transcription",
]


async def ensure_public_url(url: str) -> str:
    """Raise unless the URL resolves to a public IP (single check, no fetch)."""
    return await asyncio.to_thread(_ensure_public_url, url)


async def fetch_url(url: str, *, settings: Settings, client: Any | None = None) -> FetchedPage:
    """Fetch one URL: guarded, no redirects, settings timeouts, size cap.

    The caller may inject the HTTP client (tests use a mock transport);
    otherwise a default client is built from settings and closed after.
    """
    owned = client is not None
    active: Any = client if owned else build_client(settings)
    try:
        return await fetch_page(
            active,
            url,
            timeout_s=settings.source_fetch_timeout_s,
            max_bytes=settings.source_fetch_max_bytes,
        )
    finally:
        if not owned:
            await active.aclose()


async def robots_allowed(url: str, *, settings: Settings, client: Any | None = None) -> bool:
    """True only when the site's robots.txt allows this path.

    Fails closed: an unreadable robots.txt queues the page for a
    human instead of fetching blind.
    """
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    owned = client is not None
    active: Any = client if owned else build_client(settings)
    try:
        from app.errors import ValidationError as _ValidationError

        try:
            text = await fetch_robots_text(
                active, robots_url, timeout_s=settings.source_fetch_timeout_s
            )
        except _ValidationError:
            return False
    finally:
        if not owned:
            await active.aclose()
    if text is None:
        return False
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(text.splitlines())
    return parser.can_fetch("*", url)


async def request_transcription(
    audio: bytes, *, settings: Settings, client: Any | None = None
) -> Any:
    """Transcription payload; raises without MODEL_API_KEY (fail closed)."""
    secret = settings.model_api_key
    if secret is None or not secret.get_secret_value():
        raise ConfigurationError("MODEL_API_KEY is not set")
    owned = client is not None
    active: Any = client if owned else build_client(settings)
    try:
        return await post_transcription(
            active,
            base_url=settings.model_api_base_url,
            api_key=secret.get_secret_value(),
            audio=audio,
            timeout_s=settings.source_fetch_timeout_s,
        )
    finally:
        if not owned:
            await active.aclose()
