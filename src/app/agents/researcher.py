"""Researcher: FTS first, the model only rephrases and ranks.

The researcher is mostly code: full-text search returns addressed
passages, the model reorders them against the query and proposes web
candidates for gaps. Every (query, passage) pair is saved to
retrieval_pairs — the training set for the T05 reranker.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import AGENT_SCHEMAS, assemble, system_prompt
from app.course.tree import SourceRef


def rephrase_queries(query: str) -> list[str]:
    """Code-first query variants: keyword folds, no model call.

    Lower-cased token folds plus the raw query; the model may add more
    rephrasings, but retrieval never waits for it.
    """
    cleaned = " ".join(query.split())
    if not cleaned:
        return []
    tokens = [token.strip(".,;:!?()[]\"'").lower() for token in cleaned.split()]
    folds = [cleaned]
    content = " ".join(token for token in tokens if len(token) > 2)
    if content and content != cleaned.lower():
        folds.append(content)
    return folds


def build_dynamic(
    query: str,
    rephrased: list[str],
    handles: list[dict[str, Any]],
    course_language: str = "en",
    source_languages: list[str] | None = None,
) -> str:
    """Typed dynamic part: language, query, rephrasings, passage handles."""
    return json.dumps(
        {
            "course_language": course_language,
            "source_languages": sorted(set(source_languages or [])),
            "query": query,
            "rephrased": rephrased,
            "passages": handles,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def build_prompt(
    query: str,
    rephrased: list[str],
    handles: list[dict[str, Any]],
    course_language: str = "en",
    source_languages: list[str] | None = None,
) -> str:
    """Full researcher prompt: stable system prefix plus typed input."""
    return assemble(
        system_prompt("researcher"),
        build_dynamic(query, rephrased, handles, course_language, source_languages),
    )


def check_output(payload: dict[str, Any], handles: set[str]) -> list[str]:
    """Validate a researcher reply in code; return error strings."""
    errors: list[str] = []
    ranked = payload.get("ranked", [])
    if not isinstance(ranked, list):
        return ["ranked must be a list"]
    seen_ranks: set[int] = set()
    for position, item in enumerate(ranked, start=1):
        if not isinstance(item, dict):
            errors.append(f"ranked[{position}]: must be an object")
            continue
        handle = item.get("handle")
        if handle not in handles:
            errors.append(f"ranked[{position}]: unknown handle {handle!r}")
        rank = item.get("rank")
        if rank != position:
            errors.append(f"ranked[{position}]: rank must be {position}, got {rank!r}")
        seen_ranks.add(rank) if isinstance(rank, int) else None
        score = item.get("score")
        if not isinstance(score, (int, float)) or not 0 <= float(score) <= 1:
            errors.append(f"ranked[{position}]: score must be 0..1")
    for candidate in payload.get("web_candidates", []):
        url = candidate.get("url", "") if isinstance(candidate, dict) else ""
        if not (isinstance(url, str) and url.startswith(("http://", "https://"))):
            errors.append(f"web candidate {url!r}: must be an http(s) URL")
    return errors


async def record_pairs(
    session: AsyncSession,
    *,
    query: str,
    query_lang: str,
    ranked: list[dict[str, Any]],
    handle_to_passage: dict[str, UUID],
    ranker: str,
    fts_order: list[UUID] | None = None,
    hard_negative_k: int = 3,
) -> None:
    """Persist every (query, passage) pair for reranker training.

    The model's ranking is stored as ranker='model' with model
    scores; the real FTS order is stored as ranker='fts' from the
    first run (BRIEF §3), and the top-k FTS hits the model did not
    cite are labelled hard negatives. The `ranker` argument names the
    model side; direct callers may pass their own model-side name.
    """
    import sqlalchemy as sa

    cited = {str(handle_to_passage.get(str(item.get("handle")))) for item in ranked}
    for item in ranked:
        passage_id = handle_to_passage.get(str(item.get("handle")))
        if passage_id is None:
            continue
        await session.execute(
            sa.text(
                "INSERT INTO retrieval_pairs"
                " (query, query_lang, passage_id, ranker, rank, score,"
                " label, label_source)"
                " VALUES (:query, :lang, :passage, :ranker, :rank, :score,"
                " 'retrieved', 'pipeline')"
            ),
            {
                "query": query,
                "lang": query_lang,
                "passage": str(passage_id),
                "ranker": ranker,
                "rank": int(item.get("rank", 0)),
                "score": item.get("score"),
            },
        )
    for position, passage_id in enumerate(fts_order or [], start=1):
        key = str(passage_id)
        if ranker == "fts" and (key in cited or position > hard_negative_k):
            continue
        label = "hard_negative" if key not in cited and position <= hard_negative_k else "retrieved"
        await session.execute(
            sa.text(
                "INSERT INTO retrieval_pairs"
                " (query, query_lang, passage_id, ranker, rank, score,"
                " label, label_source)"
                " VALUES (:query, :lang, :passage, 'fts', :rank, NULL,"
                " :label, 'pipeline')"
            ),
            {
                "query": query,
                "lang": query_lang,
                "passage": key,
                "rank": position,
                "label": label,
            },
        )


def schema() -> dict[str, Any]:
    """The researcher response schema (strict subset, startup-checked)."""
    return dict(AGENT_SCHEMAS["researcher"])


def handles_for_refs(refs: list[SourceRef]) -> list[str]:
    """Passage handles cited by block skeletons (kept beside the agent)."""
    return [ref.handle for ref in refs]
