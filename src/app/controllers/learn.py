"""Learner controllers: authenticate, authorise by catalogue code, delegate."""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from app.auth.ctx import RequestContext, require_auth
from app.errors import ValidationError
from app.rbac.require import require

_LANG = re.compile(r"^[A-Za-z]{2,3}$")


def resolve_language(lang: str | None, accept_language: str | None) -> str | None:
    """?lang= wins, then the first Accept-Language tag's primary subtag."""
    candidate = lang
    if candidate is None and accept_language:
        candidate = accept_language.split(",")[0].strip().split("-")[0].split(";")[0]
    if candidate is None or candidate == "":
        return None
    if not _LANG.match(candidate):
        raise ValidationError("lang must be a language code such as en, nb, uk")
    return candidate.lower()


async def list_courses(ctx: RequestContext) -> list[dict[str, Any]]:
    from app.services.learner import list_enrolled

    actor = require_auth(ctx)
    require(actor, "learn.read")
    return await list_enrolled(ctx.db, actor, threshold=ctx.settings.mastery_threshold)


async def get_course(
    ctx: RequestContext, *, course_id: str, language: str | None
) -> dict[str, Any]:
    from app.services.learner import course_outline

    actor = require_auth(ctx)
    require(actor, "learn.read")
    return await course_outline(
        ctx.db,
        actor,
        UUID(course_id),
        language=language,
        threshold=ctx.settings.mastery_threshold,
    )


async def get_topic(ctx: RequestContext, *, topic_id: str, language: str | None) -> dict[str, Any]:
    from app.services.learner import topic_view

    actor = require_auth(ctx)
    require(actor, "learn.read")
    return await topic_view(
        ctx.db,
        actor,
        UUID(topic_id),
        language=language,
        threshold=ctx.settings.mastery_threshold,
    )


async def get_passage(
    ctx: RequestContext, *, passage_id: str, language: str | None
) -> dict[str, Any]:
    from app.services.learner import passage_view

    actor = require_auth(ctx)
    require(actor, "learn.read")
    return await passage_view(ctx.db, actor, UUID(passage_id), language=language or "en")


async def answer_block(
    ctx: RequestContext, *, block_id: str, payload: dict[str, Any], language: str | None
) -> dict[str, Any]:
    from app.services.grading import answer_block as answer_use_case

    actor = require_auth(ctx)
    require(actor, "learn.answer")
    reply = await answer_use_case(
        ctx.db,
        actor,
        UUID(block_id),
        payload,
        language=language,
        max_bytes=ctx.settings.answer_max_bytes,
        threshold=ctx.settings.mastery_threshold,
    )
    await ctx.db.commit()
    return reply


async def submit_module_test(
    ctx: RequestContext, *, topic_id: str, answers: dict[str, dict[str, Any]], language: str | None
) -> dict[str, Any]:
    from app.services.grading import submit_module_test as submit_use_case

    actor = require_auth(ctx)
    require(actor, "learn.answer")
    reply = await submit_use_case(
        ctx.db,
        actor,
        UUID(topic_id),
        answers,
        language=language,
        max_bytes=ctx.settings.answer_max_bytes,
        threshold=ctx.settings.mastery_threshold,
    )
    await ctx.db.commit()
    return reply


async def ask(
    ctx: RequestContext, *, course_id: str, question: str, language: str | None
) -> dict[str, Any]:
    from app.services import planruns
    from app.services.tutor import ask as ask_use_case

    actor = require_auth(ctx)
    require(actor, "learn.ask")
    settings = ctx.settings
    reply = await ask_use_case(
        ctx.db,
        actor,
        UUID(course_id),
        question,
        language=language,
        make_provider_factory=lambda: planruns.real_provider_factory(settings),
        min_score=settings.tutor_min_score,
        max_passages=settings.tutor_max_passages,
        max_chars=settings.tutor_question_max_chars,
    )
    await ctx.db.commit()
    return reply
