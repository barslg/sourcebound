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


_EN_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "of",
        "to",
        "in",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "with",
        "for",
        "on",
        "that",
        "this",
        "it",
        "as",
        "by",
        "an",
        "a",
        "or",
        "not",
        "must",
        "shall",
        "will",
        "have",
        "has",
        "from",
        "their",
        "they",
        "you",
        "we",
        "if",
        "when",
        "who",
        "what",
        "which",
    }
)
_NB_STOPWORDS = frozenset(
    {
        "og",
        "i",
        "er",
        "det",
        "at",
        "av",
        "til",
        "på",
        "med",
        "for",
        "en",
        "et",
        "som",
        "ikke",
        "skal",
        "må",
        "kan",
        "vil",
        "har",
        "være",
        "eller",
        "men",
        "om",
        "når",
        "hvem",
        "hva",
        "hvilke",
        "denne",
        "dette",
        "også",
        "derfor",
        "fordi",
        "mellom",
        "gjennom",
        "gjelder",
        "blir",
        "våre",
        "deres",
        "samtidig",
        "etter",
        "før",
        "hos",
    }
)
_UK_STOPWORDS = frozenset(
    {
        "та",
        "і",
        "в",
        "у",
        "є",
        "що",
        "на",
        "з",
        "із",
        "до",
        "для",
        "не",
        "це",
        "як",
        "або",
        "але",
        "який",
        "яка",
        "які",
        "також",
        "тому",
        "коли",
        "хто",
        "повинні",
        "можуть",
        "цього",
        "при",
        "про",
        "від",
    }
)


_NB_ONLY = frozenset({"av", "hva", "blir", "etter", "mellom", "gjennom", "gjelder", "våre"})
_DA_ONLY = frozenset(
    {
        "af",
        "hvad",
        "bliver",
        "blive",
        "efter",
        "mellem",
        "gennem",
        "nogle",
        "noget",
        "meget",
        "jeg",
        "sig",
        "mig",
        "dig",
        "ved",
        "hvilket",
        "hvorfor",
        "udsat",
    }
)
# Function words Norwegian and Danish share: they are evidence for both,
# so only the exclusive words and spellings can separate the two.
_DA_STOPWORDS = (_NB_STOPWORDS - _NB_ONLY) | _DA_ONLY
_SV_STOPWORDS = frozenset(
    {"och", "att", "är", "inte", "ska", "för", "från", "också", "när", "vad", "ett", "alla", "vi"}
)
_RU_STOPWORDS = frozenset(
    {"и", "что", "это", "как", "или", "от", "из", "также", "должны", "должен", "быть", "является"}
)
_NB_STEMS = ("arbeid", "veil", "opplær", "sikkerhet", "kunnskap")
_DA_STEMS = ("arbejd", "medarbejd", "vejl", "oplær", "sikkerhed", "kundskab", "uddan")
_UK_ONLY_LETTERS = re.compile("[іїєґ]")
_RU_ONLY_LETTERS = re.compile("[ыэъё]")


def _words_with(words: list[str], pattern: str) -> int:
    return sum(1 for w in words if re.search(pattern, w))


def language_scores(text: str) -> dict[str, int]:
    """Evidence per language; script is weak, never sole, evidence.

    One æøå/Cyrillic word (a place name like Tromsø inside an English
    sentence) scores nothing: script counts only from two or more
    script-bearing words. Ukrainian needs a Ukrainian-only letter and
    no Russian-only letter; ä/ö rule Norwegian and Danish out;
    Norwegian and Danish share function words, so their exclusive
    words and spellings decide.
    """
    lowered = text.lower()
    words = re.findall(r"[^\W\d_]+", lowered)
    word_set = set(words)
    cyrillic = _words_with(words, "[Ѐ-џ]")
    uk_letters = _UK_ONLY_LETTERS.search(lowered) is not None
    ru_letters = _RU_ONLY_LETTERS.search(lowered) is not None
    scores = {
        "en": len(word_set & _EN_STOPWORDS),
        "nb": len(word_set & _NB_STOPWORDS) + sum(1 for w in words if w.startswith(_NB_STEMS)),
        "da": len(word_set & _DA_STOPWORDS) + sum(1 for w in words if w.startswith(_DA_STEMS)),
        "sv": len(word_set & _SV_STOPWORDS),
        "uk": 0,
        "ru": len(word_set & _RU_STOPWORDS) if not uk_letters else 0,
    }
    if uk_letters and not ru_letters:
        scores["uk"] = len(word_set & _UK_STOPWORDS) + (3 if cyrillic >= 2 else 0)
    if ru_letters and not uk_letters and cyrillic >= 2:
        scores["ru"] += 3
    if _words_with(words, "[æøå]") >= 2:
        scores["nb"] += 2
        scores["da"] += 2
    if _words_with(words, "[äö]") >= 2:
        scores["sv"] += 3
        scores["nb"] = 0
        scores["da"] = 0
    return scores


def detect_language(text: str) -> str:
    """Code heuristics for en/nb/uk plus da/sv/ru as rivals (BRIEF §9).

    Function words decide; script only reinforces. English is the
    scoreless default for Latin text; Cyrillic text without evidence is
    `unknown`, never English. A tie is `unknown` (held), never a guess.
    A stored override wins over this guess wherever callers accept one.
    """
    scores = language_scores(text)
    best = max(scores, key=lambda lang: scores[lang])
    if scores[best] == 0:
        return "unknown" if _words_with(re.findall(r"[^\W\d_]+", text.lower()), "[Ѐ-џ]") else "en"
    if list(scores.values()).count(scores[best]) > 1:
        return "unknown"
    return best


async def set_passage_language_override(
    session: AsyncSession, *, passage_id: str, language: str
) -> str:
    """Store a human language override on one passage; return its ts_config."""
    if language in TS_CONFIG_BY_LANGUAGE.values():
        config = language
    else:
        config = ts_config_for_language(language)
    await session.execute(
        sa.text("UPDATE passages SET ts_config = :cfg WHERE id = :pid"),
        {"cfg": config, "pid": passage_id},
    )
    return config


def ts_config_for_passage(text: str, override: str | None = None) -> str:
    """FTS config for one passage: explicit override or detected."""
    if override is not None:
        if override not in TS_CONFIG_BY_LANGUAGE.values():
            raise ValidationError(f"unsupported ts_config: {override}")
        return override
    detected = detect_language(text)
    return TS_CONFIG_BY_LANGUAGE.get(
        detected, "simple" if detected in ("da", "sv", "ru") else "english"
    )


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
                " created_by, ingest_status)"
                " VALUES (:digest, 'upload', :media, :title, :licence, 'pending',"
                " :path, :actor, :ingest) RETURNING id"
            ),
            {
                "digest": digest,
                "media": media,
                "title": filename,
                "licence": licence,
                "path": str(store_path(root, digest, suffix)),
                "actor": str(created_by) if created_by else None,
                "ingest": "queued" if media == "audio" else "ready",
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
            cleaned = _clean_block(para)
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
    return rejoin_list_passages(passages)


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


_BULLET_LINE_RE = re.compile(r"^\s*(?:[-•*]|\d+[.)])\s+\S")


def _clean_block(block: str) -> str:
    """Clean one block, keeping numbered/bulleted list lines on their own lines.

    Collapsing a list into one running sentence would split the claim
    (intro sentence ending with a colon plus its items) into fragments
    no generator can carry whole.
    """
    lines = [" ".join(line.split()) for line in str(block).splitlines()]
    lines = [line for line in lines if line]
    if not any(_BULLET_LINE_RE.match(line) for line in lines):
        return " ".join(lines)
    out: list[str] = []
    prose: list[str] = []
    for line in lines:
        if _BULLET_LINE_RE.match(line):
            if prose:
                out.append(" ".join(prose))
                prose = []
            out.append(line)
        else:
            prose.append(line)
    if prose:
        out.append(" ".join(prose))
    return "\n".join(out)


def rejoin_list_passages(passages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Re-join a colon-intro passage with the list passages that follow it.

    Ingest keeps "intro sentence + its list items" as one passage; this
    also repairs already-split passages at read time: a passage whose
    text ends with ':' absorbs following passages whose lines are all
    list items, keeping the first passage's address.
    """
    out: list[dict[str, Any]] = []
    for passage in passages:
        text = str(passage.get("text", ""))
        lines = [line for line in text.splitlines() if line.strip()]
        is_list = bool(lines) and all(_BULLET_LINE_RE.match(line) for line in lines)
        same_source = out and (
            "source_id" not in passage
            or "source_id" not in out[-1]
            or passage.get("source_id") == out[-1].get("source_id")
        )
        if same_source and is_list and str(out[-1].get("text", "")).rstrip().endswith(":"):
            merged = dict(out[-1])
            merged["text"] = str(merged["text"]).rstrip() + "\n" + text.strip()
            out[-1] = merged
            continue
        out.append(passage)
    return out


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
            cleaned = _clean_block(para)
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
    return rejoin_list_passages(passages)


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
                "text": _clean_block(cleaned),
                "address": {"kind": "md", "heading_path": path, "para": paragraph},
                "address_key": f"h:{path}/para{paragraph}",
            }
        )
    return rejoin_list_passages(passages)


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
    from app.course.errorspot import is_content_text

    passages = rejoin_list_passages(list(passages))
    count = 0
    for passage in passages:
        await session.execute(
            sa.text(
                "INSERT INTO passages (source_id, text, address, address_key, ts_config,"
                " is_content)"
                " VALUES (:source, :text, CAST(:address AS jsonb), :key, :cfg, :content)"
                " ON CONFLICT (source_id, address_key) DO NOTHING"
            ),
            {
                "source": str(source_id),
                "text": passage["text"],
                "address": json.dumps(passage["address"]),
                "key": passage["address_key"],
                "cfg": ts_config_for_passage(passage["text"], ts_config),
                "content": bool(passage.get("is_content", is_content_text(passage["text"]))),
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
    A thin source (a page that gave no readable text) can never be
    approved: the author must paste or upload its content instead.
    """
    ingest = (
        await session.execute(
            sa.text("SELECT ingest_status FROM sources WHERE id = :source"),
            {"source": str(source_id)},
        )
    ).first()
    if ingest is not None and ingest[0] == "thin":
        raise ValidationError(THIN_MESSAGE)
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


THIN_MESSAGE = "The page gave no readable text; paste or upload its content"
_MIN_PAGE_TOKENS = 40
_MAX_GLUE_PER_100_TOKENS = 3.0
# A lowercase letter run straight into a capital, or a sentence end with no
# space before the next capital: words glued across element boundaries.
_GLUE_RE = re.compile(r"[a-z][A-Z]|[a-z][.:!?][A-Z]")


def is_thin_page(text: str) -> bool:
    """True when a fetched page gave no readable text: too little, no sentence, or glued.

    Measured on muse_stand 2026-11-01: the two tag-stripped docs pages
    carried 6.3 and 8.4 glued joins per 100 tokens ("built for Muse
    SparkMuse Code and the Model APIWorkflowsSession messaging");
    every readable page and upload stayed under 1.2. Fewer than 40
    tokens, or no sentence punctuation at all, is a shell or a menu.
    """
    tokens = re.findall(r"[^\W\d_]+", str(text))
    if len(tokens) < _MIN_PAGE_TOKENS:
        return True
    if not re.search(r"[.!?]", str(text)):
        return True
    return 100 * len(_GLUE_RE.findall(str(text))) / len(tokens) >= _MAX_GLUE_PER_100_TOKENS


def _is_html(content_type: str, text: str) -> bool:
    head = text.lstrip()[:200].lower()
    return "html" in content_type.lower() or head.startswith(("<!doctype", "<html"))


def web_passages(
    page: FetchedPage, parser_spec: dict[str, Any] | None
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Addressed passages of one fetched page and the parser spec that produced them.

    HTML is read block by block through a declarative spec: the supplied
    or stored spec first, then the spec derived from the page structure,
    then the whole body. Raw markup is never chunked as text (the 2026-11
    defect: `<!DOCTYPE html>...` as a passage). Plain-text and markdown
    answers are chunked as text with web addresses. Passages under one
    heading path are numbered so every address key is unique.
    """
    if not _is_html(page.content_type, page.text):
        if "markdown" in page.content_type.lower():
            return chunk_markdown(page.text), None
        return chunk_text(page.text, kind="web"), None
    from app.agents import librarian

    candidates: list[dict[str, Any]] = [spec for spec in (parser_spec,) if spec]
    derived = librarian.derive_parser_spec(page.text)
    if derived is not None:
        candidates.append(derived)
    candidates.append(librarian.body_spec())
    for spec in candidates:
        try:
            records = librarian.execute_parser_spec(page.text, spec)
        except ValueError as exc:
            if spec is parser_spec:
                raise ValidationError(f"invalid parser spec: {exc}") from exc
            continue
        passages = _web_records_to_passages(records)
        if passages:
            return passages, spec
    return [], None


def _web_records_to_passages(records: list[dict[str, str]]) -> list[dict[str, Any]]:
    """One passage per block record, keyed `h:<heading path>/para<n>` like markdown."""
    counts: dict[str, int] = {}
    passages: list[dict[str, Any]] = []
    for record in records:
        text = _clean_block(record["body"])
        if not text:
            continue
        path = record["address"] or "body"
        counts[path] = counts.get(path, 0) + 1
        passages.append(
            {
                "text": text,
                "address": {"kind": "web", "heading_path": path, "para": counts[path]},
                "address_key": f"h:{path}/para{counts[path]}",
            }
        )
    return rejoin_list_passages(passages)


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


async def stored_parser_spec(session: AsyncSession, *, url: str) -> dict[str, Any] | None:
    """The newest stored parser spec for one URL, if any source has one."""
    row = (
        await session.execute(
            sa.text(
                "SELECT parser_spec FROM sources WHERE title = :url AND kind = 'url'"
                " AND parser_spec IS NOT NULL ORDER BY created_at DESC LIMIT 1"
            ),
            {"url": url},
        )
    ).first()
    if row is None or not row[0]:
        return None
    return dict(row[0])


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
    for robots-disallowed ones. Non-http(s) schemes are refused with
    422 and internal URLs are refused outright — neither ever reaches
    the human queue. The page is read through web_passages(): the
    supplied or stored declarative spec (never generated code), else
    the derived one, else the whole body; the spec used is stored on
    the source for re-crawls. A page that gives no readable text is
    stored as `thin` with no passages and can never be approved.
    """
    from urllib.parse import urlparse

    from app.errors import UnprocessableError
    from app.providers import web as web_provider

    if urlparse(url).scheme not in ("http", "https"):
        raise UnprocessableError(f"only http(s) URLs are fetched: {url!r}")
    await web_provider.ensure_public_url(url)
    if parser_spec is None:
        parser_spec = await stored_parser_spec(session, url=url)
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
                " (content_sha256, kind, media, title, licence, approval, storage_path,"
                " ingest_status)"
                " VALUES (:digest, 'url', 'text', :title, '', 'pending', '',"
                " 'ready') RETURNING id"
            ),
            {"digest": digest, "title": url},
        )
    ).one()
    source_id = UUID(str(row[0]))
    if course_id is not None:
        await bind_source_to_course(session, course_id=course_id, source_id=source_id)
    chunks, used_spec = web_passages(page, parser_spec)
    if used_spec is not None:
        await session.execute(
            sa.text("UPDATE sources SET parser_spec = CAST(:spec AS jsonb) WHERE id = :source"),
            {"spec": json.dumps(used_spec), "source": str(source_id)},
        )
    if is_thin_page("\n".join(str(chunk.get("text", "")) for chunk in chunks)):
        await set_ingest_status(session, source_id=source_id, status="thin", message=THIN_MESSAGE)
        await set_source_as_of(session, source_id=source_id, as_of=date.today())
        return {"source_id": str(source_id)}
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
    chunks, _spec = web_passages(page, None)
    if is_thin_page("\n".join(str(chunk.get("text", "")) for chunk in chunks)):
        raise ValidationError(THIN_MESSAGE)
    digest = content_sha256(page.text.encode("utf-8"))
    row = (
        await session.execute(
            sa.text(
                "INSERT INTO sources (content_sha256, kind, media, title, approval,"
                " approved_by, storage_path, ingest_status)"
                " VALUES (:digest, 'url', 'text', :title, 'approved', :actor, '',"
                " 'ready')"
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
        await ingest_passages(session, source_id=source_id, passages=chunks)
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
    await set_ingest_status(session, source_id=source_id, status="parsing")
    try:
        audio = await asyncio.to_thread(Path(row[0]).read_bytes)
        segments = await transcribe_audio(audio, settings=settings, client=client)
        passages = chunk_transcript(segments)
        count = await ingest_passages(session, source_id=source_id, passages=passages)
    except Exception as exc:
        await set_ingest_status(
            session, source_id=source_id, status="failed", message=str(exc)[:500]
        )
        raise
    await set_ingest_status(session, source_id=source_id, status="ready")
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


async def set_ingest_status(
    session: AsyncSession,
    *,
    source_id: UUID,
    status: str,
    message: str | None = None,
) -> None:
    """Record one source's ingest state (queued/parsing/ready/failed/thin)."""
    if status not in ("queued", "parsing", "ready", "failed", "thin"):
        raise ValidationError(f"unknown ingest status: {status!r}")
    row = (
        await session.execute(
            sa.text(
                "UPDATE sources SET ingest_status = :status, ingest_message = :message"
                " WHERE id = :source RETURNING id"
            ),
            {"status": status, "message": message, "source": str(source_id)},
        )
    ).first()
    if row is None:
        raise NotFoundError(f"source {source_id} not found")


async def list_course_sources(session: AsyncSession, *, course_id: UUID) -> list[dict[str, Any]]:
    """Sources bound to one course with binding approval and ingest progress.

    A pending binding is a candidate awaiting human approval; held
    bindings were rejected and stay out of retrieval. Progress is the
    ingested passage count; ingest_status reports the pipeline state
    (queued/parsing/ready/failed/thin) with a message on failure or
    when the page gave no readable text.
    """
    rows = (
        await session.execute(
            sa.text(
                "SELECT s.id, s.title, s.media, s.licence, s.as_of, cs.approval,"
                " count(p.id), s.ingest_status, s.ingest_message FROM course_sources cs"
                " JOIN sources s ON s.id = cs.source_id"
                " LEFT JOIN passages p ON p.source_id = s.id"
                " WHERE cs.course_id = :course"
                " GROUP BY s.id, s.title, s.media, s.licence, s.as_of,"
                " cs.approval, s.ingest_status, s.ingest_message,"
                " s.created_at ORDER BY s.created_at, s.id"
            ),
            {"course": str(course_id)},
        )
    ).all()
    return [
        {
            "id": str(row[0]),
            "title": row[1],
            "media": row[2],
            "licence": row[3],
            "as_of": row[4].isoformat() if row[4] else None,
            "approval": row[5],
            "progress": int(row[6]),
            "ingest_status": row[7],
            "ingest_message": row[8],
        }
        for row in rows
    ]


async def reject_source(session: AsyncSession, *, source_id: UUID) -> None:
    """Reject a source: pending bindings are held out of retrieval.

    Rejection never deletes the immutable source row; it flips the
    source and its pending bindings to held so no run can cite them.
    """
    row = (
        await session.execute(
            sa.text("SELECT id FROM sources WHERE id = :source"),
            {"source": str(source_id)},
        )
    ).first()
    if row is None:
        raise NotFoundError(f"source {source_id} not found")
    await session.execute(
        sa.text(
            "UPDATE course_sources SET approval = 'held'"
            " WHERE source_id = :source AND approval = 'pending'"
        ),
        {"source": str(source_id)},
    )
    await session.execute(
        sa.text("UPDATE sources SET approval = 'held' WHERE id = :source"),
        {"source": str(source_id)},
    )


async def list_human_queue(
    session: AsyncSession, *, course_ids: list[UUID] | None
) -> list[dict[str, Any]]:
    """Open human-queue items scoped to the caller's courses.

    `course_ids=None` is the superadmin view (every open item);
    otherwise only items whose payload names an owned course. Items
    queued without a course stay visible to superadmins only.
    """
    rows = (
        await session.execute(
            sa.text(
                "SELECT id, kind, url, reason, payload, created_at FROM human_queue"
                " WHERE status = 'open' ORDER BY created_at, id"
            )
        )
    ).all()
    owned = {str(course) for course in course_ids} if course_ids is not None else None
    items: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(row[4] or {})
        course = payload.get("course_id")
        if owned is not None and (course is None or str(course) not in owned):
            continue
        items.append(
            {
                "id": str(row[0]),
                "kind": row[1],
                "url": row[2],
                "reason": row[3],
                "payload": payload,
                "created_at": row[5].isoformat() if row[5] else None,
            }
        )
    return items


async def resolve_human_queue(
    session: AsyncSession,
    *,
    item_id: UUID,
    action: str,
    settings: Settings,
    approved_by: UUID | None,
    client: Any | None = None,
) -> dict[str, Any]:
    """Resolve one open queue item: approve ingests it, dismiss drops it.

    Approval is the human override: the URL is fetched (still
    SSRF-guarded, robots waived by the human decision), ingested as a
    pending source bound to the payload's course, and the item closes.
    Returns {"source_id"} on approve, {"ok": True} on dismiss.
    """
    if action not in ("approve", "dismiss"):
        raise ValidationError(f"unknown queue action: {action!r}")
    row = (
        await session.execute(
            sa.text(
                "SELECT id, kind, url, payload FROM human_queue"
                " WHERE id = :item AND status = 'open'"
            ),
            {"item": str(item_id)},
        )
    ).first()
    if row is None:
        raise NotFoundError(f"queue item {item_id} not found")
    if action == "dismiss":
        await session.execute(
            sa.text("UPDATE human_queue SET status = 'done' WHERE id = :item"),
            {"item": str(item_id)},
        )
        return {"ok": True}
    payload = dict(row[3] or {})
    course_id = UUID(str(payload["course_id"])) if payload.get("course_id") else None
    page = await fetch_url(str(row[2]), settings=settings, client=client)
    if page.status != 200:
        raise ValidationError(f"URL fetch failed with status {page.status}: {row[2]!r}")
    chunks, _spec = web_passages(page, None)
    if is_thin_page("\n".join(str(chunk.get("text", "")) for chunk in chunks)):
        raise ValidationError(THIN_MESSAGE)
    digest = content_sha256(page.text.encode("utf-8"))
    existing = (
        await session.execute(
            sa.text("SELECT id FROM sources WHERE content_sha256 = :digest"),
            {"digest": digest},
        )
    ).first()
    if existing is not None:
        source_id = UUID(str(existing[0]))
        if course_id is not None:
            await bind_source_to_course(session, course_id=course_id, source_id=source_id)
    else:
        created = (
            await session.execute(
                sa.text(
                    "INSERT INTO sources"
                    " (content_sha256, kind, media, title, licence, approval,"
                    " storage_path, ingest_status)"
                    " VALUES (:digest, 'url', 'text', :title, '', 'pending', '',"
                    " 'ready') RETURNING id"
                ),
                {"digest": digest, "title": str(row[2])},
            )
        ).one()
        source_id = UUID(str(created[0]))
        if course_id is not None:
            await bind_source_to_course(session, course_id=course_id, source_id=source_id)
        await ingest_passages(session, source_id=source_id, passages=chunks)
        await set_source_as_of(session, source_id=source_id, as_of=date.today())
    await session.execute(
        sa.text("UPDATE human_queue SET status = 'done' WHERE id = :item"),
        {"item": str(item_id)},
    )
    return {"source_id": str(source_id)}


async def submit_transcribe(
    session: AsyncSession,
    *,
    actor: Any,
    source_id: UUID,
    defer: Any = None,
) -> UUID:
    """Queue audio transcription as an operation (202); the worker runs it."""
    from app.rbac.require import require
    from app.services.audit import write_audit

    require(actor, "source.upload")
    row = (
        await session.execute(
            sa.text("SELECT id FROM sources WHERE id = :source"),
            {"source": str(source_id)},
        )
    ).first()
    if row is None:
        raise NotFoundError(f"source {source_id} not found")
    created = (
        await session.execute(
            sa.text(
                "INSERT INTO operations (kind, status, params, progress, actor_id)"
                " VALUES ('transcribe_audio', 'queued', CAST(:params AS jsonb), 0, :actor)"
                " RETURNING id"
            ),
            {
                "params": json.dumps({"source_id": str(source_id)}),
                "actor": str(actor.user_id),
            },
        )
    ).one()
    operation_id = UUID(str(created[0]))
    await write_audit(
        session,
        actor_id=actor.user_id,
        action="transcribe_audio.submit",
        entity="operation",
        entity_id=str(operation_id),
        meta={"source_id": str(source_id)},
    )
    await session.commit()
    if defer is not None:
        await defer(operation_id)
    return operation_id
