"""Web content client: the only web-fetch I/O (services never import httpx).

Fetches never follow redirects, use the source-fetch timeout and size
cap from settings, and refuse non-public targets after DNS resolution
(SSRF guard): loopback, private, link-local, multicast, reserved, and
unresolvable hosts never receive a request. The hostname is resolved
exactly once and the request is sent to the checked IP with the
original Host header (plus SNI for https), so a rebinding resolver
that answers a private address second never sees the request.
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


def resolve_public_ips(hostname: str) -> list[str]:
    """Public IPs for one hostname, resolved exactly once.

    Every address the resolver returns is checked explicitly
    (loopback, private, link-local, multicast, reserved, unspecified,
    or not globally routable are all refused — `is_global` alone
    still admits multicast such as 224.0.0.1). The first public
    address wins; when none is public the host fails closed. Callers
    must connect to a returned IP, never resolve the hostname again.
    """
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise ValidationError(f"cannot resolve host: {hostname!r}") from exc
    addresses: set[str] = {str(info[4][0]) for info in infos}
    if not addresses:
        raise ValidationError(f"cannot resolve host: {hostname!r}")
    public: list[str] = []
    for raw in sorted(addresses):
        try:
            address = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise ValidationError(f"cannot resolve host: {hostname!r}") from exc
        if (
            address.is_loopback
            or address.is_private
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
            or not address.is_global
        ):
            continue
        public.append(raw)
    if not public:
        raise ValidationError(f"refusing non-public address for {hostname!r}")
    return public


def ensure_public_url(url: str) -> str:
    """Raise unless the URL resolves to at least one public IP address.

    The scheme must be http(s); loopback, private, link-local,
    multicast, reserved, unspecified, and unresolvable hosts fail
    closed. Fetch paths resolve once more and pin the checked IP.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValidationError(f"only http(s) URLs are fetched: {url!r}")
    resolve_public_ips(parsed.hostname)
    return url


def pinned_target(url: str, ip: str) -> tuple[str, dict[str, str], dict[str, Any]]:
    """Rewrite one URL to connect to a checked IP.

    Returns the pinned URL, the Host header preserving the original
    authority, and the request extensions (SNI keeps https certificate
    verification on the real hostname). IPv6 literals gain brackets.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    bracketed = f"[{ip}]" if ":" in ip and not ip.startswith("[") else ip
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path or "/"
    if parsed.query:
        path += f"?{parsed.query}"
    return (
        f"{parsed.scheme}://{bracketed}{port}{path}",
        {"host": parsed.netloc},
        {"sni_hostname": hostname} if parsed.scheme == "https" else {},
    )


async def _resolve_once(url: str) -> tuple[str, str]:
    """Validate one URL and resolve its hostname exactly once."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValidationError(f"only http(s) URLs are fetched: {url!r}")
    ips = await asyncio.to_thread(resolve_public_ips, parsed.hostname)
    return url, ips[0]


async def fetch_page(client: Any, url: str, *, timeout_s: float, max_bytes: int) -> FetchedPage:
    """GET one URL from its checked IP: guarded, no redirects, body capped."""
    _, ip = await _resolve_once(url)
    target, headers, extensions = pinned_target(url, ip)
    response = await client.get(
        target, headers=headers, extensions=extensions, timeout=timeout_s, follow_redirects=False
    )
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
    """robots.txt body from its checked IP, or None when unreadable."""
    try:
        _, ip = await _resolve_once(robots_url)
    except ValidationError:
        return None
    target, headers, extensions = pinned_target(robots_url, ip)
    try:
        response = await client.get(
            target,
            headers=headers,
            extensions=extensions,
            timeout=timeout_s,
            follow_redirects=False,
        )
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
