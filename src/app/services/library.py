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
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.course.tree import TS_CONFIGS
from app.errors import NotFoundError, UnsupportedMediaError, ValidationError
from app.providers.web import FetchedPage as FetchedPage

UPLOAD_MEDIA = ("pdf", "text", "markdown", "image", "audio")
# Norwegian-only content words (no English overlap): æøå plus these decide.
_NB_MARKERS = frozenset(
    {
        "ikke",
        "også",
        "derfor",
        "fordi",
        "mellom",
        "gjennom",
        "gjelder",
        "ansatte",
        "arbeids",
        "blitt",
        "hvilke",
        "deres",
        "våre",
        "samtidig",
        "henhold",
    }
)
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


def detect_language(text: str) -> str:
    """Code heuristics for en/nb/uk (BRIEF §9): Cyrillic is uk, æøå or
    Norwegian-only words are nb, everything else is en. A stored
    override wins over this guess wherever callers accept one."""
    if re.search(r"[Ѐ-џ]", text):
        return "uk"
    if re.search(r"[æøåÆØÅ]", text):
        return "nb"
    words = set(re.findall(r"[a-zæøå]+", text.lower()))
    if words & _NB_MARKERS:
        return "nb"
    return "en"


def ts_config_for_passage(text: str, override: str | None = None) -> str:
    """FTS config for one passage: explicit override or detected."""
    if override is not None:
        if override not in TS_CONFIG_BY_LANGUAGE.values():
            raise ValidationError(f"unsupported ts_config: {override}")
        return override
    return TS_CONFIG_BY_LANGUAGE[detect_language(text)]


async def bind_source_to_course(session: AsyncSession, *, course_id: UUID, source_id: UUID) -> None:
    """Bind one source to one course (pending approval, idempotent)."""
    await session.execute(
        sa.text(
            "INSERT INTO course_sources (course_id, source_id, approval)"
            " VALUES (:course, :source, 'pending')"
            " ON CONFLICT (course_id, source_id) DO NOTHING"
        ),
        {"course": str(course_id), "source": str(source_id)},
    )


async def source_course_ids(session: AsyncSession, *, source_id: UUID) -> list[UUID]:
    """Courses one source is bound to (scope checks for approval)."""
    rows = (
        await session.execute(
            sa.text("SELECT course_id FROM course_sources WHERE source_id = :source"),
            {"source": str(source_id)},
        )
    ).all()
    return [UUID(str(row[0])) for row in rows]


async def store_upload(
    session: AsyncSession,
    *,
    settings: Settings,
    created_by: UUID | None,
    filename: str,
    media: str,
    content: bytes,
    licence: str = "",
    course_id: UUID | None = None,
) -> UUID:
    """Persist an upload: disk blob plus immutable source row.

    When `course_id` is given the source is bound to that course
    (pending); unbound sources stay invisible to every run.
    """
    if media not in UPLOAD_MEDIA:
        raise UnsupportedMediaError(f"unsupported media: {media}")
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
        if course_id is not None:
            await bind_source_to_course(
                session, course_id=course_id, source_id=UUID(str(existing[0]))
            )
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
    source_id = UUID(str(row[0]))
    if course_id is not None:
        await bind_source_to_course(session, course_id=course_id, source_id=source_id)
    return source_id


def extract_pdf_pages(content: bytes) -> list[dict[str, Any]]:
    """PDF text extraction (pypdf) with page addresses: `p<page>/para<n>`.

    Binary uploads must never be chunked as UTF-8 text: a PDF decoded
    as text produced hundreds of garbage passages. Raises
    ValidationError when the bytes are not a readable PDF.
    """
    from io import BytesIO

    from pypdf import PdfReader

    try:
        reader = PdfReader(BytesIO(content))
        pages = list(reader.pages)
    except Exception as exc:
        raise ValidationError(f"unreadable PDF upload: {exc}") from exc
    passages: list[dict[str, Any]] = []
    for page_no, page in enumerate(pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            raise ValidationError(f"unreadable PDF page {page_no}: {exc}") from exc
        paragraph = 0
        for para in re.split(r"\n\s*\n", text):
            cleaned = " ".join(para.split())
            if not cleaned:
                continue
            paragraph += 1
            passages.append(
                {
                    "text": cleaned,
                    "address": {"kind": "pdf", "page": page_no, "para": paragraph},
                    "address_key": f"p{page_no}/para{paragraph}",
                }
            )
    return passages


def chunk_upload(content: bytes, *, media: str, filename: str) -> list[dict[str, Any]]:
    """Code chunking per upload media; image/audio have their own paths.

    PDF pages are extracted with page addresses; text and markdown are
    chunked as before; images are stored with no passages (until the
    SAM/figure path lands) and audio waits for its transcription job,
    so both return no passages here instead of garbage text.
    """
    if media == "pdf" or content[:4] == b"%PDF":
        return extract_pdf_pages(content)
    if media == "markdown":
        return chunk_markdown(content.decode("utf-8", errors="replace"))
    if media in ("text",):
        return chunk_text(content.decode("utf-8", errors="replace"), kind="pdf")
    if media in ("image", "audio"):
        return []
    raise UnsupportedMediaError(f"unsupported media: {media}")


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
    ts_config: str | None = None,
) -> int:
    """Write addressed passages; address_key is unique per source.

    `ts_config` is a stored per-passage override; without one the
    language is detected per passage by code heuristics (en/nb/uk).
    """
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
                "cfg": ts_config_for_passage(passage["text"], ts_config),
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
    session: AsyncSession,
    *,
    source_id: UUID,
    approved_by: UUID | None,
    course_id: UUID | None = None,
) -> None:
    """Human approval moves a source binding from pending to approved.

    With `course_id` only that course's binding is approved (404 when
    the source is not bound to the course); without one every binding
    is approved. The source row follows: runs only see passages whose
    binding AND source are both approved for their own course.
    """
    if course_id is not None:
        row = (
            await session.execute(
                sa.text(
                    "UPDATE course_sources SET approval = 'approved',"
                    " approved_by = :actor, approved_at = now()"
                    " WHERE course_id = :course AND source_id = :source"
                    " AND approval = 'pending' RETURNING id"
                ),
                {
                    "actor": str(approved_by) if approved_by else None,
                    "course": str(course_id),
                    "source": str(source_id),
                },
            )
        ).first()
        if row is None:
            bound = (
                await session.execute(
                    sa.text(
                        "SELECT id FROM course_sources"
                        " WHERE course_id = :course AND source_id = :source"
                    ),
                    {"course": str(course_id), "source": str(source_id)},
                )
            ).first()
            if bound is None:
                raise NotFoundError(f"source {source_id} is not pending approval")
    else:
        await session.execute(
            sa.text(
                "UPDATE course_sources SET approval = 'approved',"
                " approved_by = :actor, approved_at = now()"
                " WHERE source_id = :source AND approval = 'pending'"
            ),
            {"actor": str(approved_by) if approved_by else None, "source": str(source_id)},
        )
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
        exists = (
            await session.execute(
                sa.text("SELECT id FROM sources WHERE id = :source"),
                {"source": str(source_id)},
            )
        ).first()
        if exists is None:
            raise NotFoundError(f"source {source_id} not found")


async def fetch_url(url: str, *, settings: Settings, client: Any | None = None) -> FetchedPage:
    """Fetch one URL via the web provider (guarded, no redirects).

    The caller may inject the HTTP client (tests use a mock transport);
    only http(s) URLs on public addresses are fetched.
    """
    from app.providers import web as web_provider

    return await web_provider.fetch_url(url, settings=settings, client=client)


async def robots_allowed(url: str, *, settings: Settings, client: Any | None = None) -> bool:
    """True unless the site's robots.txt disallows this path."""
    from app.providers import web as web_provider

    return await web_provider.robots_allowed(url, settings=settings, client=client)


async def intake_url(
    session: AsyncSession,
    *,
    settings: Settings,
    url: str,
    course_id: UUID | None,
    client: Any | None = None,
    parser_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """URL intake: robots-disallowed pages go to the human queue.

    Returns {"source_id": ...} for fetched pages or {"queued": ...}
    for robots-disallowed ones. Raises when the fetch is refused.
    With `parser_spec` the declarative librarian spec is executed by
    code (never generated code) and stored on the source for re-crawls;
    otherwise the page text is chunked with web addresses.
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
        if course_id is not None:
            await bind_source_to_course(
                session, course_id=course_id, source_id=UUID(str(existing[0]))
            )
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
    source_id = UUID(str(row[0]))
    if course_id is not None:
        await bind_source_to_course(session, course_id=course_id, source_id=source_id)
    if parser_spec is not None:
        from app.agents import librarian

        try:
            records = librarian.execute_parser_spec(page.text, parser_spec)
        except ValueError as exc:
            raise ValidationError(f"invalid parser spec: {exc}") from exc
        chunks = [
            {
                "text": record["body"],
                "address": {"kind": "web", "anchor": record["address"] or record["title"]},
                "address_key": record["address"] or f"title:{record['title']}",
            }
            for record in records
        ]
        await session.execute(
            sa.text("UPDATE sources SET parser_spec = CAST(:spec AS jsonb) WHERE id = :source"),
            {"spec": json.dumps(parser_spec), "source": str(source_id)},
        )
    else:
        chunks = chunk_text(page.text, kind="web")
    await ingest_passages(session, source_id=source_id, passages=chunks)
    await set_source_as_of(session, source_id=source_id, as_of=date.today())
    return {"source_id": str(source_id)}


async def get_candidate(session: AsyncSession, *, candidate_id: UUID) -> dict[str, Any]:
    """One web candidate with its course link (scope checks for approval)."""
    row = (
        await session.execute(
            sa.text(
                "SELECT id, course_id, query, url, title, snippet, status"
                " FROM source_candidates WHERE id = :candidate"
            ),
            {"candidate": str(candidate_id)},
        )
    ).first()
    if row is None:
        raise NotFoundError(f"candidate {candidate_id} not found")
    return {
        "id": str(row[0]),
        "course_id": str(row[1]) if row[1] is not None else None,
        "query": row[2],
        "url": row[3],
        "title": row[4],
        "snippet": row[5],
        "status": row[6],
    }


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
    session: AsyncSession,
    *,
    candidate_id: UUID,
    approved_by: UUID | None,
    settings: Settings,
    client: Any | None = None,
) -> UUID:
    """Approve a web candidate: fetch + ingest first, then approve.

    Approving a candidate fetches the URL (SSRF-guarded) and ingests
    addressed passages, so the source never stands empty; the
    candidate's course gets an approved binding. When the fetched
    content already exists (ON CONFLICT) the existing row is returned
    untouched: a conflict never flips another course's approval.
    """
    candidate = (
        await session.execute(
            sa.text(
                "SELECT url, title, status, course_id FROM source_candidates WHERE id = :candidate"
            ),
            {"candidate": str(candidate_id)},
        )
    ).first()
    if candidate is None or candidate[2] != "pending":
        raise NotFoundError(f"candidate {candidate_id} is not pending approval")
    url, title, _, course_id = candidate
    page = await fetch_url(url, settings=settings, client=client)
    if page.status != 200:
        raise ValidationError(f"URL fetch failed with status {page.status}: {url!r}")
    digest = content_sha256(page.text.encode("utf-8"))
    row = (
        await session.execute(
            sa.text(
                "INSERT INTO sources (content_sha256, kind, media, title, approval,"
                " approved_by, storage_path)"
                " VALUES (:digest, 'url', 'text', :title, 'approved', :actor, '')"
                " ON CONFLICT (content_sha256) DO NOTHING RETURNING id"
            ),
            {
                "digest": digest,
                "title": title or url,
                "actor": str(approved_by) if approved_by else None,
            },
        )
    ).first()
    if row is None:
        existing = (
            await session.execute(
                sa.text("SELECT id FROM sources WHERE content_sha256 = :digest"),
                {"digest": digest},
            )
        ).one()
        source_id = UUID(str(existing[0]))
    else:
        source_id = UUID(str(row[0]))
        await ingest_passages(
            session,
            source_id=source_id,
            passages=chunk_text(page.text, kind="web"),
        )
        await set_source_as_of(session, source_id=source_id, as_of=date.today())
    await session.execute(
        sa.text("UPDATE source_candidates SET status = 'approved' WHERE id = :candidate"),
        {"candidate": str(candidate_id)},
    )
    if course_id is not None:
        await bind_source_to_course(session, course_id=UUID(str(course_id)), source_id=source_id)
        await approve_source(
            session,
            source_id=source_id,
            approved_by=approved_by,
            course_id=UUID(str(course_id)),
        )
    return source_id


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
    from app.providers import web as web_provider

    payload = await web_provider.request_transcription(audio, settings=settings, client=client)
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


def _stamp_seconds(stamp: str) -> float | None:
    """Parse an H:MM:SS / MM:SS transcription stamp to seconds."""
    try:
        parts = [float(part) for part in str(stamp).split(":")]
    except ValueError:
        return None
    total = 0.0
    for part in parts:
        total = total * 60.0 + part
    return total


async def queue_transcription(session: AsyncSession, *, source_id: UUID) -> UUID:
    """Queue an audio source for transcription (its own ingest path)."""
    row = (
        await session.execute(
            sa.text(
                "INSERT INTO human_queue (kind, url, reason, payload)"
                " VALUES ('transcribe_audio', '', 'audio transcription queued',"
                " CAST(:payload AS jsonb)) RETURNING id"
            ),
            {"payload": json.dumps({"source_id": str(source_id)})},
        )
    ).one()
    return UUID(str(row[0]))


async def transcribe_source(
    session: AsyncSession,
    *,
    source_id: UUID,
    settings: Settings,
    client: Any | None = None,
) -> int:
    """Transcribe an audio source: provider call, ledger row, passages.

    The model key is read by the provider from settings; services
    never touch it. The call is recorded in llm_calls (the ledger) at
    the registry's audio-hour price, then timestamped passages are
    ingested and the queued job is marked done.
    """
    row = (
        await session.execute(
            sa.text("SELECT storage_path, media FROM sources WHERE id = :source"),
            {"source": str(source_id)},
        )
    ).first()
    if row is None:
        raise NotFoundError(f"source {source_id} not found")
    if row[1] != "audio":
        raise ValidationError(f"source {source_id} is not audio")
    audio = await asyncio.to_thread(Path(row[0]).read_bytes)
    segments = await transcribe_audio(audio, settings=settings, client=client)
    passages = chunk_transcript(segments)
    count = await ingest_passages(session, source_id=source_id, passages=passages)
    ends = [_stamp_seconds(item.get("end", "")) for item in segments]
    hours = (max([end for end in ends if end is not None] or [0.0])) / 3600.0
    await session.execute(
        sa.text(
            "INSERT INTO llm_calls"
            " (agent, model, prompt_version, tokens_input, tokens_output,"
            " cost_usd, duration_ms, request)"
            " VALUES ('transcriber', 'muse-voice-transcribe-1.0', 'transcribe:1',"
            " 0, 0, :cost, 0, CAST(:request AS jsonb))"
        ),
        {
            "cost": transcription_cost_usd(hours),
            "request": json.dumps({"source_id": str(source_id), "segments": len(segments)}),
        },
    )
    await session.execute(
        sa.text(
            "UPDATE human_queue SET status = 'done'"
            " WHERE kind = 'transcribe_audio' AND status = 'open'"
            " AND payload->>'source_id' = :source"
        ),
        {"source": str(source_id)},
    )
    return count


async def set_source_as_of(session: AsyncSession, *, source_id: UUID, as_of: date) -> None:
    """Stamp the visible 'as of' date shown on every citation."""
    row = (
        await session.execute(
            sa.text("UPDATE sources SET as_of = :day WHERE id = :source RETURNING id"),
            {"day": as_of, "source": str(source_id)},
        )
    ).first()
    if row is None:
        raise NotFoundError(f"source {source_id} not found")
