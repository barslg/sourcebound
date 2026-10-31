"""Library: immutable uploads, code chunking, FTS, URL intake, candidates.

Uploads are stored on disk under a sha256 name; chunking assigns
stable page/paragraph, heading-path, and timestamp addresses; Postgres
full-text search finds passages with no embedding model anywhere; URL
fetches never follow redirects and robots-disallowed pages land in the
human queue; web candidates wait for human approval.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from uuid import UUID

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.services import library


def _settings(**overrides: Any) -> Settings:
    return Settings(
        _env_file=None,
        source_store_path="/tmp/muse-t03-sources",
        **overrides,
    )


async def _user(session: AsyncSession) -> UUID:
    row = (
        await session.execute(
            sa.text("INSERT INTO users (email, display_name) VALUES (:e, 'N') RETURNING id"),
            {"e": f"t03-{uuid.uuid4().hex[:12]}@example.com"},
        )
    ).one()
    await session.commit()
    return UUID(str(row[0]))


async def test_upload_is_stored_by_sha256_and_deduplicated(t03_session: AsyncSession) -> None:
    settings = _settings()
    user_id = await _user(t03_session)
    content = b"Article 4 requires AI literacy.\n\nProviders must train staff."
    first = await library.store_upload(
        t03_session,
        settings=settings,
        created_by=user_id,
        filename="act.txt",
        media="text",
        content=content,
    )
    await t03_session.commit()
    second = await library.store_upload(
        t03_session,
        settings=settings,
        created_by=user_id,
        filename="copy.txt",
        media="text",
        content=content,
    )
    assert first == second
    digest = library.content_sha256(content)
    blob = library.store_path(settings.source_store_path, digest, ".txt")
    assert await asyncio.to_thread(blob.exists)


async def test_text_chunking_assigns_page_paragraph_addresses() -> None:
    passages = library.chunk_text("First para.\n\nSecond para.\fThird page para.")
    assert [item["address_key"] for item in passages] == [
        "p1/para1",
        "p1/para2",
        "p2/para1",
    ]
    assert all(item["address"]["kind"] == "pdf" for item in passages)


async def test_markdown_chunking_uses_heading_paths() -> None:
    passages = library.chunk_markdown("# Intro\n\nHello.\n\n## Setup\n\nDo this.")
    assert passages[0]["address_key"] == "h:Intro/para1"
    assert passages[1]["address_key"] == "h:Intro>Setup/para1"


async def test_ingested_passages_are_found_by_fts(t03_session: AsyncSession) -> None:
    from app.providers.retrieval import FtsRetriever

    settings = _settings()
    user_id = await _user(t03_session)
    source_id = await library.store_upload(
        t03_session,
        settings=settings,
        created_by=user_id,
        filename="act.txt",
        media="text",
        content=b"AI literacy is an employer duty under Article 4.",
    )
    await library.ingest_passages(
        t03_session,
        source_id=source_id,
        passages=library.chunk_text("AI literacy is an employer duty under Article 4."),
        ts_config="english",
    )
    await library.approve_source(t03_session, source_id=source_id, approved_by=user_id)
    await t03_session.commit()
    found = await FtsRetriever().search(
        t03_session, query="employer duty literacy", ts_config="english", limit=5
    )
    assert len(found) == 1
    assert found[0].address_key == "p1/para1"
    assert found[0].rank == 1


async def test_fts_falls_back_to_or_when_no_passage_holds_every_word(
    t03_session: AsyncSession,
) -> None:
    from app.providers.retrieval import FtsRetriever

    settings = _settings()
    user_id = await _user(t03_session)
    source_id = await library.store_upload(
        t03_session,
        settings=settings,
        created_by=user_id,
        filename="two.txt",
        media="text",
        content=b"Literacy first.\n\nOversight second.",
    )
    await library.ingest_passages(
        t03_session,
        source_id=source_id,
        passages=library.chunk_text("Literacy first.\n\nOversight second."),
        ts_config="english",
    )
    await t03_session.commit()
    found = await FtsRetriever().search(
        t03_session, query="literacy oversight", ts_config="english", limit=5
    )
    assert {item.address_key for item in found} == {"p1/para1", "p1/para2"}


def test_no_embedding_model_is_used() -> None:
    import pathlib
    import re

    repo = pathlib.Path("src/app")
    offenders: list[str] = []
    for path in repo.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in (
            r"sentence_transformers",
            r"pgvector",
            r"[^_a-z]embed\s*\(",
            r"Embeddings",
            r"<\s*->|<\s*#>",
        ):
            if re.search(pattern, text):
                offenders.append(f"{path}: {pattern}")
    assert offenders == []


async def test_fetch_never_follows_redirects() -> None:
    settings = _settings()

    def redirect(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://example.com/other"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(redirect))
    with pytest.raises(Exception, match="redirects are never followed"):
        await library.fetch_url("https://example.com/page", settings=settings, client=client)


async def test_robots_disallowed_pages_go_to_human_queue(t03_session: AsyncSession) -> None:
    settings = _settings()

    def robots(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /page")
        return httpx.Response(200, text="secret content")

    client = httpx.AsyncClient(transport=httpx.MockTransport(robots))
    result = await library.intake_url(
        t03_session,
        settings=settings,
        url="https://example.com/page",
        course_id=None,
        client=client,
    )
    assert "queued" in result
    await t03_session.commit()
    count = (await t03_session.execute(sa.text("SELECT count(*) FROM human_queue"))).scalar()
    assert count == 1


async def test_web_candidates_need_human_approval(t03_session: AsyncSession) -> None:
    user_id = await _user(t03_session)
    candidate_id = await library.add_web_candidate(
        t03_session,
        course_id=None,
        query="AI Act literacy",
        url="https://example.com/act",
        title="AI Act",
    )
    await t03_session.commit()
    source_id = await library.approve_candidate(
        t03_session, candidate_id=candidate_id, approved_by=user_id
    )
    await t03_session.commit()
    status = (
        await t03_session.execute(
            sa.text("SELECT approval FROM sources WHERE id = :source"),
            {"source": str(source_id)},
        )
    ).scalar()
    assert status == "approved"


async def test_transcription_price_and_key_guard() -> None:
    assert library.transcription_cost_usd(2.0) == "0.360000"
    settings = _settings(model_api_key=None)
    from app.errors import ConfigurationError

    with pytest.raises(ConfigurationError):
        await library.transcribe_audio(b"fake", settings=settings)
