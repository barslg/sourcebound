"""Learner routes: enrolled courses, topics, passages, answers. Parse and validate only."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header
from fastapi.responses import FileResponse

from app.auth.ctx import RequestContext, get_ctx
from app.controllers import learn as controller
from app.routes.ids import block_id_param, course_id_param, passage_id_param, topic_id_param
from app.schemas import learn as dto

router = APIRouter(prefix="/learn", tags=["learn"])


def _lang(lang: str | None, accept_language: str | None) -> str | None:
    return controller.resolve_language(lang, accept_language)


@router.get("/courses")
async def list_courses(
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> list[dict[str, Any]]:
    """Published courses the caller is enrolled in, with progress (courses screen)."""
    return await controller.list_courses(ctx)


@router.get("/courses/{course_id}")
async def get_course(
    course_id: Annotated[str, Depends(course_id_param)],
    lang: str | None = None,
    accept_language: Annotated[str | None, Header()] = None,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, Any]:
    """The published outline with per-topic progress (course outline screen)."""
    return await controller.get_course(
        ctx, course_id=course_id, language=_lang(lang, accept_language)
    )


@router.get("/courses/{course_id}/sources")
async def get_sources(
    course_id: Annotated[str, Depends(course_id_param)],
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, Any]:
    """The course's sources under their citation numbers (sources page)."""
    return await controller.get_sources(ctx, course_id=course_id)


@router.get("/courses/{course_id}/glossary")
async def get_glossary(
    course_id: Annotated[str, Depends(course_id_param)],
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, Any]:
    """The course's terms with their introducing sentence (glossary page)."""
    return await controller.get_glossary(ctx, course_id=course_id)


@router.get("/topics/{topic_id}")
async def get_topic(
    topic_id: Annotated[str, Depends(topic_id_param)],
    lang: str | None = None,
    accept_language: Annotated[str | None, Header()] = None,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, Any]:
    """A topic's blocks through the learner serializers (reading column, module test page)."""
    return await controller.get_topic(ctx, topic_id=topic_id, language=_lang(lang, accept_language))


@router.get("/passages/{passage_id}")
async def get_passage(
    passage_id: Annotated[str, Depends(passage_id_param)],
    lang: str | None = None,
    accept_language: Annotated[str | None, Header()] = None,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, Any]:
    """Passage text for the companion panel (citation chip click)."""
    return await controller.get_passage(
        ctx, passage_id=passage_id, language=_lang(lang, accept_language)
    )


@router.get("/topics/{topic_id}/illustration")
async def get_illustration(
    topic_id: Annotated[str, Depends(topic_id_param)],
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> FileResponse:
    """The topic's picture (one simple image per topic drawn at build time)."""
    path = await controller.get_illustration(ctx, topic_id=topic_id)
    return FileResponse(path, media_type="image/webp")


@router.post("/blocks/{block_id}/answer")
async def answer_block(
    block_id: Annotated[str, Depends(block_id_param)],
    payload: dict[str, Any],
    lang: str | None = None,
    accept_language: Annotated[str | None, Header()] = None,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, Any]:
    """Synchronous grading by code; attempts never lock (reading column, find-the-error)."""
    return await controller.answer_block(
        ctx, block_id=block_id, payload=payload, language=_lang(lang, accept_language)
    )


@router.post("/topics/{topic_id}/submit")
async def submit_module_test(
    topic_id: Annotated[str, Depends(topic_id_param)],
    body: dto.ModuleTestSubmit,
    lang: str | None = None,
    accept_language: Annotated[str | None, Header()] = None,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, Any]:
    """Submit the whole module-test page; retakes allowed (module test screen)."""
    return await controller.submit_module_test(
        ctx, topic_id=topic_id, answers=body.answers, language=_lang(lang, accept_language)
    )


@router.post("/courses/{course_id}/ask")
async def ask(
    course_id: Annotated[str, Depends(course_id_param)],
    body: dto.AskRequest,
    lang: str | None = None,
    accept_language: Annotated[str | None, Header()] = None,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, Any]:
    """Quotes-only tutor answer from this course's passages (tutor panel)."""
    return await controller.ask(
        ctx, course_id=course_id, question=body.question, language=_lang(lang, accept_language)
    )
