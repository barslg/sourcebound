"""Library: immutable sources, code chunking, FTS retrieval, URL intake.

Uploads land on disk under the configured path with a sha256 name;
sources are immutable by content_sha256 (a re-crawl supersedes).
Chunking is code: paragraphs with page/paragraph, heading-path, or
timestamp addresses. URL fetch never follows redirects and honours
timeouts from settings; robots-disallowed pages go to the human queue.
Web-search candidates wait for human approval before ingestion.
Audio is transcribed with muse-voice-transcribe-1.0 into timestamped
passages. No embeddings anywhere.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import urllib.robotparser
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.course.tree import TS_CONFIGS
from app.errors import ConfigurationError, NotFoundError, ValidationError

UPLOAD_MEDIA = ("pdf", "text", "markdown", "image", "audio")
ADDRESS_KIND_BY_MEDIA = {
    "pdf": "pdf",
    "text": "pdf",
    "markdown": "md",
    "image": "figure",
    "audio": "av",
}

TS_CONFIG_BY_LANGUAGE = TS_CONFIGS


def content_sha256(content: bytes) -> str:
    """Immutable identity of one source blob."""
    return hashlib.sha256(content).hexdigest()


def store_path(store_root: str | Path, digest: str, suffix: str) -> Path:
    """Disk path for one blob: <root>/<digest><suffix>."""
    return Path(store_root) / f"{digest}{suffix}"


async def store_upload(
    session: AsyncSession,
    *,
    settings: Settings,
    created_by: UUID | None,
    filename: str,
    media: str,
    content: bytes,
    licence: str = "",
) -> UUID:
    """Persist an upload: disk blob plus immutable source row."""
    if media not in UPLOAD_MEDIA:
        raise ValidationError(f"unsupported media: {media}")
    if not content:
        raise ValidationError("upload content is empty")
    digest = content_sha256(content)
    existing = (
        await session.execute(
            sa.text("SELECT id FROM sources WHERE content_sha256 = :digest"),
            {"digest": digest},
        )
    ).first()
    if existing is not None:
        return UUID(str(existing[0]))
    suffix = Path(filename).suffix.lower() or ".bin"
    root = Path(settings.source_store_path)
    await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(store_path(root, digest, suffix).write_bytes, content)
    row = (
        await session.execute(
            sa.text(
                "INSERT INTO sources"
                " (content_sha256, kind, media, title, licence, approval, storage_path,"
                " created_by)"
                " VALUES (:digest, 'upload', :media, :title, :licence, 'pending',"
                " :path, :actor) RETURNING id"
            ),
            {
                "digest": digest,
                "media": media,
                "title": filename,
                "licence": licence,
                "path": str(store_path(root, digest, suffix)),
                "actor": str(created_by) if created_by else None,
            },
        )
    ).one()
    return UUID(str(row[0]))


def chunk_text(text: str, *, kind: str = "pdf", page_of: int = 0) -> list[dict[str, Any]]:
    """Split plain text into addressed passages (page/paragraph).

    Form-feed characters start a new page; blank lines split
    paragraphs. Address keys are stable: `p<page>/para<paragraph>`.
    """
    passages: list[dict[str, Any]] = []
    page = page_of + 1
    paragraph = 0
    for block in re.split(r"\f", text):
        paragraph = 0
        for para in re.split(r"\n\s*\n", block):
            cleaned = " ".join(para.split())
            if not cleaned:
                continue
            paragraph += 1
            passages.append(
                {
                    "text": cleaned,
                    "address": {"kind": kind, "page": page, "para": paragraph},
                    "address_key": f"p{page}/para{paragraph}",
                }
            )
        page += 1
    return passages


def chunk_markdown(text: str) -> list[dict[str, Any]]:
    """Split markdown into heading-pathed passages (`h:A>B/para<n>`)."""
    passages: list[dict[str, Any]] = []
    headings: list[str] = []
    paragraph = 0
    for block in re.split(r"\n\s*\n", text):
        cleaned = block.strip()
        if not cleaned:
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", cleaned, re.DOTALL)
        if heading:
            level = len(heading.group(1))
            headings = headings[: level - 1] + [" ".join(heading.group(2).split())]
            paragraph = 0
            continue
        paragraph += 1
        path = ">".join(headings) if headings else "body"
        passages.append(
            {
                "text": " ".join(cleaned.split()),
                "address": {"kind": "md", "heading_path": path, "para": paragraph},
                "address_key": f"h:{path}/para{paragraph}",
            }
        )
    return passages


def chunk_transcript(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Timestamped passages from transcription segments (`t<start>-<end>`)."""
    passages: list[dict[str, Any]] = []
    for index, segment in enumerate(segments, start=1):
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        start = str(segment.get("start", f"00:00:{index:02d}"))
        end = str(segment.get("end", start))
        passages.append(
            {
                "text": " ".join(text.split()),
                "address": {"kind": "av", "t_start": start, "t_end": end},
                "address_key": f"t{start}-{end}",
            }
        )
    return passages


async def ingest_passages(
    session: AsyncSession,
    *,
    source_id: UUID,
    passages: list[dict[str, Any]],
    ts_config: str,
) -> int:
    """Write addressed passages; address_key is unique per source."""
    count = 0
    for passage in passages:
        await session.execute(
            sa.text(
                "INSERT INTO passages (source_id, text, address, address_key, ts_config)"
                " VALUES (:source, :text, CAST(:address AS jsonb), :key, :cfg)"
                " ON CONFLICT (source_id, address_key) DO NOTHING"
            ),
            {
                "source": str(source_id),
                "text": passage["text"],
                "address": json.dumps(passage["address"]),
                "key": passage["address_key"],
                "cfg": ts_config,
            },
        )
        count += 1
    return count


def ts_config_for_language(language: str) -> str:
    """FTS configuration per course language (uk uses simple + pg_trgm)."""
    try:
        return TS_CONFIG_BY_LANGUAGE[language]
    except KeyError:
        raise ValidationError(f"unsupported language: {language}") from None


async def approve_source(
    session: AsyncSession, *, source_id: UUID, approved_by: UUID | None
) -> None:
    """Human approval moves a source from pending to approved."""
    row = (
        await session.execute(
            sa.text(
                "UPDATE sources SET approval = 'approved', approved_by = :actor"
                " WHERE id = :source AND approval = 'pending' RETURNING id"
            ),
            {"actor": str(approved_by) if approved_by else None, "source": str(source_id)},
        )
    ).first()
    if row is None:
        raise NotFoundError(f"source {source_id} is not pending approval")


@dataclass(frozen=True)
class FetchedPage:
    url: str
    status: int
    content_type: str
    text: str


async def fetch_url(url: str, *, settings: Settings, client: Any | None = None) -> FetchedPage:
    """Fetch one URL: no redirects, settings timeouts, size cap.

    The caller injects the HTTP client (tests use a mock transport);
    only http(s) URLs are fetched.
    """
    import httpx

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValidationError(f"only http(s) URLs are fetched: {url!r}")
    owned = client is not None
    active = client or httpx.AsyncClient(follow_redirects=False)
    try:
        response = await active.get(
            url, timeout=settings.source_fetch_timeout_s, follow_redirects=False
        )
    finally:
        if not owned:
            await active.aclose()
    if response.status_code in (301, 302, 303, 307, 308):
        raise ValidationError(f"redirects are never followed: {url!r}")
    body = response.content[: settings.source_fetch_max_bytes]
    return FetchedPage(
        url=url,
        status=response.status_code,
        content_type=response.headers.get("content-type", ""),
        text=body.decode("utf-8", errors="replace"),
    )


async def robots_allowed(url: str, *, settings: Settings, client: Any | None = None) -> bool:
    """True unless the site's robots.txt disallows this path."""
    import httpx

    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    owned = client is not None
    active = client or httpx.AsyncClient(follow_redirects=False)
    try:
        try:
            response = await active.get(robots_url, timeout=settings.source_fetch_timeout_s)
        except Exception:
            return True
        if response.status_code != 200:
            return True
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(response.text.splitlines())
        return parser.can_fetch("*", url)
    finally:
        if not owned:
            await active.aclose()


async def intake_url(
    session: AsyncSession,
    *,
    settings: Settings,
    url: str,
    course_id: UUID | None,
    client: Any | None = None,
) -> dict[str, Any]:
    """URL intake: robots-disallowed pages go to the human queue.

    Returns {"source_id": ...} for fetched pages or {"queued": ...}
    for robots-disallowed ones. Raises when the fetch is refused.
    """
    if not await robots_allowed(url, settings=settings, client=client):
        row = (
            await session.execute(
                sa.text(
                    "INSERT INTO human_queue (kind, url, reason, payload)"
                    " VALUES ('robots_disallowed', :url, :reason,"
                    " CAST(:payload AS jsonb)) RETURNING id"
                ),
                {
                    "url": url,
                    "reason": "robots.txt disallows automated fetching",
                    "payload": json.dumps({"course_id": str(course_id) if course_id else None}),
                },
            )
        ).one()
        return {"queued": str(row[0])}
    page = await fetch_url(url, settings=settings, client=client)
    if page.status != 200:
        raise ValidationError(f"URL fetch failed with status {page.status}: {url!r}")
    digest = content_sha256(page.text.encode("utf-8"))
    existing = (
        await session.execute(
            sa.text("SELECT id FROM sources WHERE content_sha256 = :digest"),
            {"digest": digest},
        )
    ).first()
    if existing is not None:
        return {"source_id": str(existing[0])}
    row = (
        await session.execute(
            sa.text(
                "INSERT INTO sources"
                " (content_sha256, kind, media, title, licence, approval, storage_path)"
                " VALUES (:digest, 'url', 'text', :title, '', 'pending', '') RETURNING id"
            ),
            {"digest": digest, "title": url},
        )
    ).one()
    return {"source_id": str(row[0])}


async def add_web_candidate(
    session: AsyncSession,
    *,
    course_id: UUID | None,
    query: str,
    url: str,
    title: str = "",
    snippet: str = "",
) -> UUID:
    """Park a Spark web_search hit for human approval before ingestion."""
    row = (
        await session.execute(
            sa.text(
                "INSERT INTO source_candidates (course_id, query, url, title, snippet)"
                " VALUES (:course, :query, :url, :title, :snippet) RETURNING id"
            ),
            {
                "course": str(course_id) if course_id else None,
                "query": query,
                "url": url,
                "title": title,
                "snippet": snippet,
            },
        )
    ).one()
    return UUID(str(row[0]))


async def approve_candidate(
    session: AsyncSession, *, candidate_id: UUID, approved_by: UUID | None
) -> UUID:
    """Approve a web candidate: creates the pending URL source row."""
    candidate = (
        await session.execute(
            sa.text("SELECT url, title, status FROM source_candidates WHERE id = :candidate"),
            {"candidate": str(candidate_id)},
        )
    ).first()
    if candidate is None or candidate[2] != "pending":
        raise NotFoundError(f"candidate {candidate_id} is not pending approval")
    await session.execute(
        sa.text("UPDATE source_candidates SET status = 'approved' WHERE id = :candidate"),
        {"candidate": str(candidate_id)},
    )
    digest = content_sha256(f"candidate:{candidate[0]}".encode())
    row = (
        await session.execute(
            sa.text(
                "INSERT INTO sources (content_sha256, kind, media, title, approval,"
                " approved_by, storage_path)"
                " VALUES (:digest, 'url', 'text', :title, 'approved', :actor, '')"
                " ON CONFLICT (content_sha256) DO UPDATE SET approval = 'approved'"
                " RETURNING id"
            ),
            {
                "digest": digest,
                "title": candidate[1] or candidate[0],
                "actor": str(approved_by) if approved_by else None,
            },
        )
    ).one()
    return UUID(str(row[0]))


def transcription_cost_usd(audio_hours: float) -> str:
    """Cost of transcription at the registry's dated audio-hour price."""
    from decimal import Decimal

    from app.llm.registry import get_model

    spec = get_model("muse-voice-transcribe-1.0")
    assert spec.unit_price_usd is not None
    cost = (Decimal(str(audio_hours)) * spec.unit_price_usd).quantize(Decimal("0.000001"))
    return str(cost)


async def transcribe_audio(
    audio: bytes, *, settings: Settings, client: Any | None = None
) -> list[dict[str, Any]]:
    """Transcribe audio with muse-voice-transcribe-1.0 into segments.

    Needs MODEL_API_KEY; raises ConfigurationError without one so the
    live test skips instead of failing.
    """
    secret = settings.model_api_key
    if secret is None or not secret.get_secret_value():
        raise ConfigurationError("MODEL_API_KEY is not set")
    import httpx

    owned = client is not None
    active = client or httpx.AsyncClient(follow_redirects=False)
    try:
        response = await active.post(
            f"{settings.model_api_base_url}/audio/transcriptions",
            headers={"Authorization": f"Bearer {secret.get_secret_value()}"},
            files={"file": ("audio.bin", audio)},
            data={"model": "muse-voice-transcribe-1.0", "response_format": "verbose_json"},
            timeout=settings.source_fetch_timeout_s,
        )
    finally:
        if not owned:
            await active.aclose()
    if response.status_code != 200:
        raise ValidationError(f"transcription failed with status {response.status_code}")
    payload = response.json()
    segments = payload.get("segments", [])
    if not isinstance(segments, list):
        raise ValidationError("transcription returned no segments")
    return [
        {
            "start": str(item.get("start", "")),
            "end": str(item.get("end", "")),
            "text": str(item.get("text", "")),
        }
        for item in segments
        if isinstance(item, dict)
    ]


async def set_source_as_of(session: AsyncSession, *, source_id: UUID, as_of: date) -> None:
    """Stamp the visible 'as of' date shown on every citation."""
    row = (
        await session.execute(
            sa.text("UPDATE sources SET as_of = :day WHERE id = :source RETURNING id"),
            {"day": as_of.isoformat(), "source": str(source_id)},
        )
    ).first()
    if row is None:
        raise NotFoundError(f"source {source_id} not found")
