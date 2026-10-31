"""Web content client: the only web-fetch I/O (services never import httpx).

Fetches never follow redirects, use the source-fetch timeout and size
cap from settings, and refuse non-public targets after DNS resolution
(SSRF guard): loopback, private, link-local, multicast, reserved, and
unresolvable hosts never receive a request.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import Settings
from app.errors import ValidationError

REDIRECT_STATUSES = (301, 302, 303, 307, 308)


@dataclass(frozen=True)
class FetchedPage:
    """One fetched URL: status, content type, and capped decoded text."""

    url: str
    status: int
    content_type: str
    text: str


def build_client(settings: Settings) -> httpx.AsyncClient:
    """Default fetch client: no redirects, timeouts from settings."""
    return httpx.AsyncClient(follow_redirects=False, timeout=settings.source_fetch_timeout_s)


def ensure_public_url(url: str) -> str:
    """Raise unless the URL resolves to public IP addresses only.

    The scheme must be http(s) and every address the hostname resolves
    to must be globally routable; anything else (loopback, private,
    link-local, multicast, reserved, unresolvable) fails closed.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValidationError(f"only http(s) URLs are fetched: {url!r}")
    hostname = parsed.hostname
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise ValidationError(f"cannot resolve host: {hostname!r}") from exc
    addresses = {info[4][0] for info in infos}
    if not addresses:
        raise ValidationError(f"cannot resolve host: {hostname!r}")
    for raw in sorted(addresses):
        try:
            address = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise ValidationError(f"cannot resolve host: {hostname!r}") from exc
        if not address.is_global:
            raise ValidationError(f"refusing non-public address for {hostname!r}")
    return url


async def fetch_page(client: Any, url: str, *, timeout_s: float, max_bytes: int) -> FetchedPage:
    """GET one URL: guarded, no redirects, body capped at max_bytes."""
    await asyncio.to_thread(ensure_public_url, url)
    response = await client.get(url, timeout=timeout_s, follow_redirects=False)
    if response.status_code in REDIRECT_STATUSES:
        raise ValidationError(f"redirects are never followed: {url!r}")
    body = response.content[:max_bytes]
    return FetchedPage(
        url=url,
        status=response.status_code,
        content_type=response.headers.get("content-type", ""),
        text=body.decode("utf-8", errors="replace"),
    )


async def fetch_robots_text(client: Any, robots_url: str, *, timeout_s: float) -> str | None:
    """robots.txt body, or None when it cannot be read (fail open to fetch)."""
    await asyncio.to_thread(ensure_public_url, robots_url)
    try:
        response = await client.get(robots_url, timeout=timeout_s, follow_redirects=False)
    except Exception:
        return None
    if response.status_code != 200:
        return None
    return str(response.text)


async def post_transcription(
    client: Any, *, base_url: str, api_key: str, audio: bytes, timeout_s: float
) -> Any:
    """POST one transcription; non-200 answers are validation errors."""
    response = await client.post(
        f"{base_url}/audio/transcriptions",
        headers={"Authorization": f"Bearer {api_key}"},
        files={"file": ("audio.bin", audio)},
        data={"model": "muse-voice-transcribe-1.0", "response_format": "verbose_json"},
        timeout=timeout_s,
    )
    if response.status_code != 200:
        raise ValidationError(f"transcription failed with status {response.status_code}")
    return response.json()
