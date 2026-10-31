"""T03 routes: courses, plan editing, sources, runs. Parse and validate only."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, UploadFile

from app.auth.ctx import RequestContext, get_ctx
from app.controllers import t03 as controller
from app.schemas import t03 as dto

courses_router = APIRouter(prefix="/courses", tags=["courses"])
sources_router = APIRouter(prefix="/sources", tags=["sources"])
runs_router = APIRouter(prefix="/runs", tags=["runs"])


@courses_router.post("", status_code=201)
async def create_course(
    body: dto.CourseCreateRequest,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, str]:
    """Create a draft course."""
    return await controller.create_course(ctx, body=body)


@courses_router.get("/{course_id}")
async def get_course(
    course_id: str,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, Any]:
    """One course with its profile and plan validation state."""
    return await controller.get_course(ctx, course_id=course_id)


@courses_router.post("/{course_id}/profile")
async def set_profile(
    course_id: str,
    body: dto.ProfileRequest,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, bool]:
    """Replace the course profile with typed constraint rows."""
    return await controller.set_profile(ctx, course_id=course_id, body=body)


@courses_router.patch("/{course_id}/plan")
async def edit_plan(
    course_id: str,
    body: dto.PlanEditRequest,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, Any]:
    """Author edits: rename/move/delete modules and topics, constraints."""
    return await controller.edit_plan(ctx, course_id=course_id, operations=body.operations)


@courses_router.get("/{course_id}/plan/validate")
async def validate_plan(
    course_id: str,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, Any]:
    """List blocking questions; generation is refused while any is open."""
    return await controller.validate_plan(ctx, course_id=course_id)


@courses_router.post("/{course_id}/plan/submit", status_code=202)
async def submit_plan(
    course_id: str,
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
    course_id: str,
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
    )


@sources_router.post("/fetch", status_code=201)
async def fetch_source(
    body: dto.SourceFetchRequest,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, str]:
    """Intake a URL: fetched without redirects or queued for a human."""
    return await controller.fetch_source(ctx, url=body.url, course_id=body.course_id)


@sources_router.post("/{source_id}/approve")
async def approve_source(
    source_id: str,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, bool]:
    """Human approval moves a source from pending to approved."""
    return await controller.approve_source(ctx, source_id=source_id)


@sources_router.post("/candidates/{candidate_id}/approve", status_code=201)
async def approve_candidate(
    candidate_id: str,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, str]:
    """Approve a web candidate: fetch, ingest, then approve."""
    return await controller.approve_candidate(ctx, candidate_id=candidate_id)


@sources_router.post("/{source_id}/transcribe", status_code=202)
async def transcribe_source(
    source_id: str,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, Any]:
    """Transcribe an audio source into timestamped passages."""
    return await controller.transcribe_source(ctx, source_id=source_id)


@runs_router.get("/{run_id}")
async def get_run(
    run_id: str,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, Any]:
    """One run with its steps in DAG order."""
    return await controller.get_run(ctx, run_id=run_id)


@runs_router.post("/{run_id}/resume", status_code=202)
async def resume_run(
    run_id: str,
    ctx: RequestContext = Depends(get_ctx),  # noqa: B008 - FastAPI dependency injection idiom
) -> dict[str, str]:
    """Resume a stopped run from its last good step."""
    return await controller.resume_run(ctx, run_id=run_id)
