"""Probe: a crawled page keeps its own title, and a re-crawl repairs a URL-only title."""

from __future__ import annotations

from typing import Any

import httpx2
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.services import library

_URL = "https://docs.example.test/chat"
_HTML = (
    "<html><head><title>Chat completions: your first call</title></head>"
    "<body><article><h1>Chat completions</h1><p>Each message has a role and content, "
    "and the array travels whole on every turn of the conversation.</p>"
    "<p>Messages accumulate tokens, which increases latency, cost and pressure on the "
    "context window of the model you call.</p></article></body></html>"
)


def _handler(request: httpx2.Request) -> httpx2.Response:
    if request.url.path == "/robots.txt":
        return httpx2.Response(200, text="User-agent: *\nAllow: /\n")
    return httpx2.Response(200, text=_HTML, headers={"content-type": "text/html"})


async def test_recrawl_repairs_a_url_only_title(
    t03_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.clients import web as web_client

    monkeypatch.setattr(web_client, "resolve_public_ips", lambda host: ["93.184.216.34"])
    settings = Settings(_env_file=None, model_api_key="k", source_store_path="/tmp/muse-url-title")
    http: Any = httpx2.AsyncClient(transport=httpx2.MockTransport(_handler))
    first = await library.intake_url(
        t03_session, settings=settings, url=_URL, course_id=None, client=http
    )
    source_id = first["source_id"]
    title = await t03_session.scalar(
        sa.text("SELECT title FROM sources WHERE id = :id"), {"id": source_id}
    )
    assert title == "Chat completions: your first call"
    # the 2026-11 rows: title was the URL itself
    await t03_session.execute(
        sa.text("UPDATE sources SET title = :url WHERE id = :id"), {"url": _URL, "id": source_id}
    )
    second = await library.intake_url(
        t03_session, settings=settings, url=_URL, course_id=None, client=http
    )
    assert second["source_id"] == source_id
    repaired = await t03_session.scalar(
        sa.text("SELECT title FROM sources WHERE id = :id"), {"id": source_id}
    )
    assert repaired == "Chat completions: your first call"


async def test_a_url_source_keeps_its_address(
    t03_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.clients import web as web_client

    monkeypatch.setattr(web_client, "resolve_public_ips", lambda host: ["93.184.216.34"])
    settings = Settings(_env_file=None, model_api_key="k", source_store_path="/tmp/muse-url-title")
    http: Any = httpx2.AsyncClient(transport=httpx2.MockTransport(_handler))
    url = _URL + "?v=address"
    first = await library.intake_url(
        t03_session, settings=settings, url=url, course_id=None, client=http
    )
    stored = await t03_session.scalar(
        sa.text("SELECT storage_path FROM sources WHERE id = :id"), {"id": first["source_id"]}
    )
    assert stored == url
    # the 2026-11 rows: no address at all; a re-crawl of the same page writes it
    await t03_session.execute(
        sa.text("UPDATE sources SET storage_path = '' WHERE id = :id"), {"id": first["source_id"]}
    )
    await library.intake_url(t03_session, settings=settings, url=url, course_id=None, client=http)
    repaired = await t03_session.scalar(
        sa.text("SELECT storage_path FROM sources WHERE id = :id"), {"id": first["source_id"]}
    )
    assert repaired == url
