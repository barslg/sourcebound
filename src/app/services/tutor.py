"""Tutor: quotes-only answers from this course's passages, or "not in the materials".

Code decides, in this order: retrieval over the course's approved
passages (FTS, planted exercise sentences are block content and never
passages); a passage reaches the model only when its score clears the
threshold AND it holds at least `min_covered` distinct content lexemes
of the question (a rank alone overlaps on off-topic questions); no such
passage means `not_in_materials` without any model call; otherwise the
model chooses fragments and code keeps only those that are verbatim in
their passage. Caller:
controllers/learn.py ask (the learner's tutor panel).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import NotFoundError
from app.rbac.principal import Principal
from app.services import learner

NOT_IN_MATERIALS = "not_in_materials"
QUOTES = "quotes"
TUTOR_DAILY_LIMIT = 20
TUTOR_SITE_DAILY_LIMIT = 300
NOT_IN_MATERIALS_HINT = (
    "The course materials do not answer this question. Reread the topic or ask your teacher."
)


def _not_in_materials() -> dict[str, Any]:
    """The decided answer when nothing in the materials supports a reply: no passage, no guess."""
    return {"mode": NOT_IN_MATERIALS, "quotes": [], "hint": NOT_IN_MATERIALS_HINT}


async def ask(
    session: AsyncSession,
    principal: Principal,
    course_id: UUID,
    question: str,
    *,
    language: str | None,
    make_provider_factory: Any,
    min_score: float,
    min_covered: int,
    max_passages: int,
    max_chars: int,
) -> dict[str, Any]:
    """Answer one question from the course's own passages."""
    from app.agents import tutor
    from app.errors import ValidationError
    from app.providers.retrieval import get_retriever
    from app.runs.executor import tutor_call

    course = await learner.enrolled_course(session, principal, course_id, code="learn.ask")
    await _within_daily_limit(session, principal.user_id)
    text = " ".join(question.split())
    if not text or len(text) > max_chars:
        raise ValidationError(f"the question must be 1 to {max_chars} characters")
    found = await get_retriever("fts").search_course(
        session, query=text, course_id=course_id, limit=max_passages * 2
    )
    usable = [
        item
        for item in found
        if item.score is not None
        and item.score >= min_score
        and await _is_content(session, item.passage_id)
        and await _covers_question(session, text, item, min_covered=min_covered)
    ][:max_passages]
    if not usable:
        return _not_in_materials()
    handles = {f"P{i}": item for i, item in enumerate(usable, start=1)}
    prompt = tutor.build_prompt(
        question=text,
        passages=[{"handle": h, "text": item.text} for h, item in handles.items()],
        course_language=course["language"],
    )
    provider_factory = make_provider_factory()
    provider = provider_factory(session)
    try:
        reply = await tutor_call(
            provider,
            prompt=prompt,
            schema=tutor.schema(),
            subject_user_id=principal.user_id,
            check=tutor.check_output,
        )
    finally:
        closer = getattr(provider_factory, "aclose", None)
        if closer is not None:
            await closer()
    locale = learner.locale_of(language or course["language"])
    kept: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in reply.get("quotes", []):
        if not isinstance(item, dict):
            continue
        handle, quote = str(item.get("handle", "")), str(item.get("quote", "")).strip()
        passage = handles.get(handle)
        if (
            passage is None
            or not tutor.accept_quote(quote, passage.text)
            or (handle, quote) in seen
        ):
            continue
        seen.add((handle, quote))
        row = await _chip_row(session, passage.passage_id)
        kept.append({"quote": quote, "chip": learner.chip(handle, row, locale)})
    if not kept:
        return _not_in_materials()
    return {"mode": QUOTES, "quotes": kept}


async def _within_daily_limit(session: AsyncSession, user_id: UUID) -> None:
    """429 once this user's, or the whole site's, paid tutor calls of the last day reach a limit.

    The per-user advisory lock lives until the request's transaction ends, so
    one user's parallel questions never overlap: the second is told to try
    again at once (a waiter would hold a pooled connection for the whole paid
    call of the first). The site-wide lock, taken after it in the same order
    for everyone, does the same across accounts: while one ask is paid and
    answered, another gets the "answering someone else" 429 and retries. Both
    are try-locks, so the count below is always read with no paid call in
    flight and cannot be passed by parallel asks.

    Counts llm_calls rows of agent "tutor" attributed to the user: only
    questions that reached the model cost the course owner money, so an
    off-topic question answered "not in the materials" never counts.
    """
    from app.errors import RateLimitedError

    got_user = (
        await session.execute(
            sa.text("SELECT pg_try_advisory_xact_lock(hashtext('tutor:' || :user))"),
            {"user": str(user_id)},
        )
    ).scalar_one()
    if not got_user:
        raise RateLimitedError("Your last question is still being answered; try again in a moment.")
    got_site = (
        await session.execute(sa.text("SELECT pg_try_advisory_xact_lock(hashtext('tutor:site'))"))
    ).scalar_one()
    if not got_site:
        raise RateLimitedError("The tutor is answering someone else; try again in a moment.")
    site = (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM llm_calls WHERE agent = 'tutor'"
                " AND created_at > now() - interval '1 day'"
            )
        )
    ).scalar_one()
    if int(site) >= TUTOR_SITE_DAILY_LIMIT:
        raise RateLimitedError(
            "The tutor has answered its daily number of questions on this site;"
            " it opens again tomorrow."
        )
    used = (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM llm_calls WHERE agent = 'tutor'"
                " AND subject_user_id = :user AND created_at > now() - interval '1 day'"
            ),
            {"user": str(user_id)},
        )
    ).scalar_one()
    if int(used) >= TUTOR_DAILY_LIMIT:
        raise RateLimitedError(
            f"You have asked the tutor {TUTOR_DAILY_LIMIT} questions in the last day;"
            " the tutor opens again tomorrow."
        )


async def _covers_question(
    session: AsyncSession, question: str, item: Any, *, min_covered: int
) -> bool:
    """True when the passage holds `min_covered` distinct content lexemes of the question.

    Lexemes are the question's tsvector terms in the passage's own text
    search config (stop words are gone); a `simple` config keeps them,
    so only terms of 4+ characters count there. A question with fewer
    content lexemes than `min_covered` needs all of them. Caller: ask.
    """
    row = (
        await session.execute(
            sa.text(
                "WITH q AS (SELECT unnest(tsvector_to_array("
                "to_tsvector(CAST(:cfg AS regconfig), :q))) AS lex),"
                " p AS (SELECT tsvector_to_array(to_tsvector(CAST(:cfg AS regconfig), :t))"
                " AS lexes) SELECT count(*), count(*) FILTER (WHERE lex = ANY(p.lexes))"
                " FROM q CROSS JOIN p WHERE (NOT :simple OR length(lex) >= 4)"
            ),
            {
                "cfg": item.ts_config,
                "q": question,
                "t": item.text,
                "simple": item.ts_config == "simple",
            },
        )
    ).one()
    total, matched = int(row[0]), int(row[1])
    return total > 0 and matched >= min(min_covered, total)


async def _is_content(session: AsyncSession, passage_id: UUID) -> bool:
    row = (
        await session.execute(
            sa.text("SELECT is_content FROM passages WHERE id = :p"), {"p": str(passage_id)}
        )
    ).first()
    return bool(row and row[0])


async def _chip_row(session: AsyncSession, passage_id: UUID) -> dict[str, Any]:
    row = (
        await session.execute(
            sa.text(
                "SELECT p.id, p.address, p.address_key, s.title FROM passages p"
                " JOIN sources s ON s.id = p.source_id WHERE p.id = :p"
            ),
            {"p": str(passage_id)},
        )
    ).first()
    if row is None:
        raise NotFoundError("not found")
    return {
        "passage_id": str(row[0]),
        "address": dict(row[1] or {}),
        "address_key": str(row[2]),
        "title": str(row[3]),
    }
