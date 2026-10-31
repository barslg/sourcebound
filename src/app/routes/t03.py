"""T03 routes: courses, plan editing, sources, runs. Parse and validate only."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, UploadFile

from app.auth.ctx import RequestContext, get_ctx
from app.controllers import t03 as controller
from app.routes.ids import (
    block_id_param,
    candidate_id_param,
    course_id_param,
    item_id_param,
    run_id_param,
    source_id_param,
)
from app.schemas import t03 as dto

courses_router = APIRouter(prefix="/courses", tags=["courses"])
sources_router = APIRouter(prefix="/sources", tags=["sources"])
runs_router = APIRouter(prefix="/runs", tags=["runs"])
blocks_router = APIRouter(prefix="/blocks", tags=["blocks"])
methodologies_router = APIRouter(prefix="/methodologies", tags=["methodologies"])
human_queue_router = APIRouter(prefix="/human-queue", tags=["human-queue"])


@courses_router.post("", status_code=201)
async def create_course(
    body: dto.CourseCreateRequest,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, str]:
    """Create a draft course."""
    return await controller.create_course(ctx, body=body)


@courses_router.get("/{course_id}")
async def get_course(
    course_id: Annotated[str, Depends(course_id_param)],
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, Any]:
    """One course with its profile and plan validation state."""
    return await controller.get_course(ctx, course_id=course_id)


@courses_router.post("/{course_id}/profile")
async def set_profile(
    course_id: Annotated[str, Depends(course_id_param)],
    body: dto.ProfileRequest,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, bool]:
    """Replace the course profile with typed constraint rows."""
    return await controller.set_profile(ctx, course_id=course_id, body=body)


@courses_router.get("")
async def list_courses(
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> list[dict[str, Any]]:
    """The caller's course library with profile, plan, and question flags."""
    return await controller.list_courses(ctx)


@courses_router.patch("/{course_id}/plan")
async def edit_plan(
    course_id: Annotated[str, Depends(course_id_param)],
    body: dto.PlanEditRequest,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, Any]:
    """Author edits: rename/move/delete modules and topics, constraints."""
    return await controller.edit_plan(
        ctx, course_id=course_id, operations=body.operations, base_version=body.base_version
    )


@courses_router.get("/{course_id}/plan")
async def get_plan(
    course_id: Annotated[str, Depends(course_id_param)],
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, Any]:
    """The plan tree, same shape as the PATCH response, with source handles."""
    return await controller.get_plan(ctx, course_id=course_id)


@courses_router.get("/{course_id}/report", response_model=None)
async def get_report(
    course_id: Annotated[str, Depends(course_id_param)],
    format: str = "json",  # noqa: A002 - the public query name is `format`
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> Any:
    """Per learner and topic: completion, mastery, find-the-error counters; ?format=csv exports."""
    report = await controller.get_report(ctx, course_id=course_id, as_csv=format == "csv")
    if isinstance(report, str):
        from fastapi.responses import PlainTextResponse

        return PlainTextResponse(
            report,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="report-{course_id}.csv"'},
        )
    return report


@courses_router.get("/{course_id}/estimate")
async def get_estimate(
    course_id: Annotated[str, Depends(course_id_param)],
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, Any]:
    """Cost/time forecast for the course: a forecast, never a limit."""
    return await controller.get_estimate(ctx, course_id=course_id)


@courses_router.get("/{course_id}/sources")
async def list_course_sources(
    course_id: Annotated[str, Depends(course_id_param)],
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> list[dict[str, Any]]:
    """Sources bound to the course with approval and ingest progress."""
    return await controller.list_sources(ctx, course_id=course_id)


@courses_router.get("/{course_id}/held")
async def get_held(
    course_id: Annotated[str, Depends(course_id_param)],
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, Any]:
    """Held blocks with passages plus the batch rewrite quote."""
    return await controller.get_held(ctx, course_id=course_id)


@courses_router.post("/{course_id}/held/rewrite", status_code=202)
async def rewrite_held(
    course_id: Annotated[str, Depends(course_id_param)],
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, str]:
    """Rewrite every held block as one operation."""
    return await controller.rewrite_held(ctx, course_id=course_id)


@courses_router.post("/{course_id}/publish", status_code=201)
async def publish_course(
    course_id: Annotated[str, Depends(course_id_param)],
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, str]:
    """Snapshot an immutable published version; 422 while a block is held."""
    return await controller.publish_course(ctx, course_id=course_id)


@courses_router.get("/{course_id}/plan/validate")
async def validate_plan(
    course_id: Annotated[str, Depends(course_id_param)],
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, Any]:
    """List blocking questions; generation is refused while any is open."""
    return await controller.validate_plan(ctx, course_id=course_id)


@courses_router.post("/{course_id}/plan/submit", status_code=202)
async def submit_plan(
    course_id: Annotated[str, Depends(course_id_param)],
    body: dto.PlanSubmitRequest,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, str]:
    """Submit plan generation as an operation (researcher + architect)."""
    return await controller.submit_plan(
        ctx,
        course_id=course_id,
        queries=body.queries,
        idempotency_key=body.idempotency_key,
    )


@courses_router.post("/{course_id}/generate/submit", status_code=202)
async def submit_generate(
    course_id: Annotated[str, Depends(course_id_param)],
    body: dto.GenerateSubmitRequest,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, str]:
    """Submit block generation; refused while a required question is open."""
    return await controller.submit_generate(
        ctx, course_id=course_id, idempotency_key=body.idempotency_key
    )


@sources_router.post("/upload", status_code=201)
async def upload_source(
    file: UploadFile,
    media: str = "text",
    course_id: str | None = None,
    licence: str = "",
    as_of: str | None = None,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, str]:
    """Upload a source file: stored by sha256, chunked into passages."""
    content = await file.read()
    return await controller.upload_source(
        ctx,
        filename=file.filename or "upload",
        media=media,
        content=content,
        course_id=course_id,
        licence=licence,
        as_of=as_of,
    )


@courses_router.post("/{course_id}/translate", status_code=202)
async def translate_course(
    course_id: Annotated[str, Depends(course_id_param)],
    body: dto.TranslateRequest,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, str]:
    """Translate the course into the given languages as an operation."""
    return await controller.translate_course(
        ctx,
        course_id=course_id,
        languages=body.languages,
        glossary=body.glossary,
        idempotency_key=body.idempotency_key,
    )


@courses_router.get("/{course_id}/learner")
async def get_learner_course(
    course_id: Annotated[str, Depends(course_id_param)],
    request: Request,
    lang: str | None = None,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, Any]:
    """Learner view in ?lang= / Accept-Language, with fallbacks."""
    language = lang
    if language is None:
        header = (
            str(request.headers.get("accept-language", "")) if hasattr(request, "headers") else ""
        )
        if header:
            language = header.split(",")[0].strip().split("-")[0]
    return await controller.get_learner_course(ctx, course_id=course_id, language=language or "en")


@sources_router.patch("/{source_id}/language")
async def set_source_language(
    source_id: Annotated[str, Depends(source_id_param)],
    body: dto.SourceLanguageRequest,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, Any]:
    """Store a human language override on a source's passages."""
    return await controller.set_source_language(
        ctx, source_id=source_id, language=body.language, passage_id=body.passage_id
    )


@sources_router.post("/{source_id}/reject")
async def reject_source(
    source_id: Annotated[str, Depends(source_id_param)],
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, bool]:
    """Reject a source: pending bindings are held out of retrieval."""
    return await controller.reject_source(ctx, source_id=source_id)


@sources_router.post("/fetch", status_code=201)
async def fetch_source(
    body: dto.SourceFetchRequest,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, str]:
    """Intake a URL: fetched without redirects or queued for a human."""
    return await controller.fetch_source(ctx, url=body.url, course_id=body.course_id)


@sources_router.post("/{source_id}/approve")
async def approve_source(
    source_id: Annotated[str, Depends(source_id_param)],
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, bool]:
    """Human approval moves a source from pending to approved."""
    return await controller.approve_source(ctx, source_id=source_id)


@sources_router.post("/candidates/{candidate_id}/approve", status_code=201)
async def approve_candidate(
    candidate_id: Annotated[str, Depends(candidate_id_param)],
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, str]:
    """Approve a web candidate: fetch, ingest, then approve."""
    return await controller.approve_candidate(ctx, candidate_id=candidate_id)


@sources_router.post("/{source_id}/transcribe", status_code=202)
async def transcribe_source(
    source_id: Annotated[str, Depends(source_id_param)],
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, Any]:
    """Transcribe an audio source into timestamped passages."""
    return await controller.transcribe_source(ctx, source_id=source_id)


@runs_router.get("/{run_id}")
async def get_run(
    run_id: Annotated[str, Depends(run_id_param)],
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, Any]:
    """One run with its steps in DAG order."""
    return await controller.get_run(ctx, run_id=run_id)


@runs_router.post("/{run_id}/resume", status_code=202)
async def resume_run(
    run_id: Annotated[str, Depends(run_id_param)],
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, str]:
    """Resume a stopped run from its last good step."""
    return await controller.resume_run(ctx, run_id=run_id)


@runs_router.post("/{run_id}/cancel")
async def cancel_run(
    run_id: Annotated[str, Depends(run_id_param)],
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, bool]:
    """Cooperative cancel: the in-flight call finishes, no new step starts."""
    return await controller.cancel_run(ctx, run_id=run_id)


@blocks_router.post("/{block_id}/rewrite", status_code=202)
async def rewrite_block(
    block_id: Annotated[str, Depends(block_id_param)],
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, str]:
    """Rewrite one held block as an operation."""
    return await controller.rewrite_block(ctx, block_id=block_id)


@blocks_router.post("/{block_id}/accept")
async def accept_block(
    block_id: Annotated[str, Depends(block_id_param)],
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, bool]:
    """Accept a held block after human review."""
    return await controller.accept_block(ctx, block_id=block_id)


@blocks_router.patch("/{block_id}")
async def patch_block(
    block_id: Annotated[str, Depends(block_id_param)],
    body: dto.BlockBriefRequest,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, bool]:
    """Edit a block brief in place."""
    return await controller.patch_block(ctx, block_id=block_id, brief=body.brief)


@human_queue_router.get("")
async def list_human_queue(
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> list[dict[str, Any]]:
    """Open human-queue items for the caller's courses."""
    return await controller.list_human_queue(ctx)


@human_queue_router.post("/{item_id}/resolve")
async def resolve_human_queue(
    item_id: Annotated[str, Depends(item_id_param)],
    body: dto.HumanQueueResolveRequest,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, Any]:
    """Resolve one queued URL: approve ingests it, dismiss drops it."""
    return await controller.resolve_human_queue(ctx, item_id=item_id, body={"action": body.action})


@methodologies_router.get("")
async def list_methodologies(
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> list[dict[str, Any]]:
    """Registered methodologies: code, name, one-line description (profile picker)."""
    return await controller.list_methodologies(ctx)
