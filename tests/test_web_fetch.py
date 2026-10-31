"""Web fetch: SSRF guard, no redirects, settings timeouts.

The HTTP fetch lives in clients/web.py and is called through
providers/web.py; services/library.py never imports httpx. Private and
loopback targets are refused after DNS resolution, before any request.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.config import Settings
from app.errors import ValidationError
from app.services import library


def _settings(**overrides: Any) -> Settings:
    return Settings(
        _env_file=None,
        source_store_path="/tmp/muse-t03-sources",
        **overrides,
    )


async def test_loopback_is_refused_before_any_request() -> None:
    """127.0.0.1 never gets a request, even with no client injected."""
    with pytest.raises(ValidationError, match="non-public"):
        await library.fetch_url("http://127.0.0.1/secret", settings=_settings())


async def test_private_range_is_refused_before_any_request() -> None:
    """RFC 1918 targets never get a request."""
    with pytest.raises(ValidationError, match="non-public"):
        await library.fetch_url("http://10.1.2.3/internal", settings=_settings())


async def test_non_http_scheme_is_refused() -> None:
    """Only http(s) URLs are fetched."""
    with pytest.raises(ValidationError, match="only http"):
        await library.fetch_url("ftp://example.com/file", settings=_settings())


async def test_unresolvable_host_is_refused() -> None:
    """.invalid never resolves, so no request leaves the process."""
    with pytest.raises(ValidationError, match="resolve"):
        await library.fetch_url("http://nonexistent.invalid/page", settings=_settings())


async def test_provider_fetch_returns_page_on_200() -> None:
    """The provider path returns status, content type, and capped text."""
    from app.providers import web as web_provider

    def ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<p>hi</p>")

    client = httpx.AsyncClient(transport=httpx.MockTransport(ok))
    page = await web_provider.fetch_url(
        "https://example.com/page", settings=_settings(), client=client
    )
    assert page.status == 200
    assert page.content_type == "text/html"
    assert page.text == "<p>hi</p>"


async def test_provider_fetch_still_refuses_redirects() -> None:
    """Redirects are never followed on the provider path either."""

    from app.providers import web as web_provider

    def redirect(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://example.com/other"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(redirect))
    with pytest.raises(ValidationError, match="redirects are never followed"):
        await web_provider.fetch_url(
            "https://example.com/page", settings=_settings(), client=client
        )
