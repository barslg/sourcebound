"""T03 controllers: orchestration only; services own the use cases."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.auth.ctx import RequestContext, require_auth
from app.rbac.require import require


def _owner_or_404(course: dict[str, Any], user_id: UUID, is_superadmin: bool) -> None:
    from app.errors import NotFoundError

    if not is_superadmin and course["owner_id"] != str(user_id):
        raise NotFoundError("course not found")


async def create_course(ctx: RequestContext, *, body: Any) -> dict[str, str]:
    from app.services.courses import create_course as create_use_case

    actor = require_auth(ctx)
    require(actor, "course.create")
    course_id = await create_use_case(
        ctx.db,
        owner_id=actor.user_id,
        title=body.title,
        language=body.language,
        scope_tags=body.scope_tags,
    )
    await ctx.db.commit()
    return {"course_id": str(course_id)}


async def get_course(ctx: RequestContext, *, course_id: str) -> dict[str, Any]:
    from app.services.courses import get_course as get_use_case

    actor = require_auth(ctx)
    require(actor, "course.edit")
    course = await get_use_case(ctx.db, course_id=UUID(course_id))
    _owner_or_404(course, actor.user_id, actor.is_superadmin)
    return course


async def set_profile(ctx: RequestContext, *, course_id: str, body: Any) -> dict[str, bool]:
    from app.services.courses import get_course as get_use_case
    from app.services.courses import set_profile as set_use_case

    actor = require_auth(ctx)
    require(actor, "course.edit")
    course = await get_use_case(ctx.db, course_id=UUID(course_id))
    _owner_or_404(course, actor.user_id, actor.is_superadmin)
    await set_use_case(
        ctx.db,
        course_id=UUID(course_id),
        methodology_codes=body.methodology_codes,
        bloom_level=body.bloom_level,
        audience=body.audience,
        dreyfus_start=body.dreyfus_start,
        constraints=body.constraints,
    )
    await ctx.db.commit()
    return {"ok": True}


async def edit_plan(
    ctx: RequestContext, *, course_id: str, operations: list[dict[str, Any]]
) -> dict[str, Any]:
    from app.services.courses import edit_plan as edit_use_case
    from app.services.courses import get_course as get_use_case

    actor = require_auth(ctx)
    require(actor, "course.edit")
    course = await get_use_case(ctx.db, course_id=UUID(course_id))
    _owner_or_404(course, actor.user_id, actor.is_superadmin)
    tree = await edit_use_case(ctx.db, course_id=UUID(course_id), operations=operations)
    await ctx.db.commit()
    return tree


async def validate_plan(ctx: RequestContext, *, course_id: str) -> dict[str, Any]:
    from app.services.courses import blocking_questions, validate_course_tree
    from app.services.courses import get_course as get_use_case

    actor = require_auth(ctx)
    require(actor, "course.edit")
    course = await get_use_case(ctx.db, course_id=UUID(course_id))
    _owner_or_404(course, actor.user_id, actor.is_superadmin)
    questions = await blocking_questions(ctx.db, course_id=UUID(course_id))
    tree_errors = await validate_course_tree(ctx.db, course_id=UUID(course_id))
    return {
        "blocking_questions": questions,
        "tree_errors": tree_errors,
        "can_generate": not questions,
    }


async def submit_plan(
    ctx: RequestContext,
    *,
    course_id: str,
    queries: list[str],
    idempotency_key: str | None,
) -> dict[str, str]:
    from app.services.courses import get_course as get_use_case
    from app.services.planruns import submit_plan as submit_use_case
    from app.tasks.jobs import enqueue_operation

    actor = require_auth(ctx)
    require(actor, "course.plan")
    course = await get_use_case(ctx.db, course_id=UUID(course_id))
    _owner_or_404(course, actor.user_id, actor.is_superadmin)
    operation_id = await submit_use_case(
        ctx.db,
        actor=actor,
        course_id=UUID(course_id),
        queries=queries,
        idempotency_key=idempotency_key,
        defer=lambda op: enqueue_operation(op, ctx.settings.database_url),
    )
    return {"operation_id": str(operation_id)}


async def submit_generate(
    ctx: RequestContext, *, course_id: str, idempotency_key: str | None
) -> dict[str, str]:
    from app.services.courses import get_course as get_use_case
    from app.services.planruns import submit_generate as submit_use_case
    from app.tasks.jobs import enqueue_operation

    actor = require_auth(ctx)
    require(actor, "course.generate")
    course = await get_use_case(ctx.db, course_id=UUID(course_id))
    _owner_or_404(course, actor.user_id, actor.is_superadmin)
    operation_id = await submit_use_case(
        ctx.db,
        actor=actor,
        course_id=UUID(course_id),
        idempotency_key=idempotency_key,
        defer=lambda op: enqueue_operation(op, ctx.settings.database_url),
    )
    return {"operation_id": str(operation_id)}


async def _run_in_scope(ctx: RequestContext, *, run_id: UUID) -> dict[str, Any]:
    """Load a run only when its course is in the actor's scope (else 404)."""
    from app.runs.executor import get_run as get_use_case
    from app.services.courses import get_course as get_course_use_case

    actor = require_auth(ctx)
    run = await get_use_case(ctx.db, run_id=run_id)
    if run.get("course_id") is not None:
        course = await get_course_use_case(ctx.db, course_id=UUID(str(run["course_id"])))
        _owner_or_404(course, actor.user_id, actor.is_superadmin)
    return run


async def get_run(ctx: RequestContext, *, run_id: str) -> dict[str, Any]:
    actor = require_auth(ctx)
    require(actor, "run.start")
    return await _run_in_scope(ctx, run_id=UUID(run_id))


async def resume_run(ctx: RequestContext, *, run_id: str) -> dict[str, str]:
    from app.services.planruns import resume_run as resume_use_case
    from app.tasks.jobs import enqueue_operation

    actor = require_auth(ctx)
    require(actor, "run.retry")
    await _run_in_scope(ctx, run_id=UUID(run_id))
    resumed_id = await resume_use_case(
        ctx.db,
        actor=actor,
        run_id=UUID(run_id),
        defer=lambda op: enqueue_operation(op, ctx.settings.database_url),
    )
    return {"run_id": str(resumed_id)}


async def upload_source(
    ctx: RequestContext,
    *,
    filename: str,
    media: str,
    content: bytes,
    course_id: str | None = None,
) -> dict[str, str]:
    from app.services.courses import get_course as get_use_case
    from app.services.library import (
        chunk_upload,
        ingest_passages,
        queue_transcription,
        set_source_as_of,
        store_upload,
    )

    actor = require_auth(ctx)
    require(actor, "source.upload")
    course_uuid = UUID(course_id) if course_id else None
    if course_uuid is not None:
        course = await get_use_case(ctx.db, course_id=course_uuid)
        _owner_or_404(course, actor.user_id, actor.is_superadmin)
    passages = chunk_upload(content, media=media, filename=filename)
    source_id = await store_upload(
        ctx.db,
        settings=ctx.settings,
        created_by=actor.user_id,
        filename=filename,
        media=media,
        content=content,
        course_id=course_uuid,
    )
    await ingest_passages(ctx.db, source_id=source_id, passages=passages)
    if media == "audio":
        await queue_transcription(ctx.db, source_id=source_id)
    from datetime import date

    await set_source_as_of(ctx.db, source_id=source_id, as_of=date.today())
    await ctx.db.commit()
    return {"source_id": str(source_id)}


async def fetch_source(ctx: RequestContext, *, url: str, course_id: str | None) -> dict[str, str]:
    from app.services.courses import get_course as get_use_case
    from app.services.library import intake_url

    actor = require_auth(ctx)
    require(actor, "source.upload")
    course_uuid = UUID(course_id) if course_id else None
    if course_uuid is not None:
        course = await get_use_case(ctx.db, course_id=course_uuid)
        _owner_or_404(course, actor.user_id, actor.is_superadmin)
    result = await intake_url(
        ctx.db,
        settings=ctx.settings,
        url=url,
        course_id=course_uuid,
    )
    await ctx.db.commit()
    return result


async def approve_source(ctx: RequestContext, *, source_id: str) -> dict[str, bool]:
    from app.services.courses import get_course as get_course_use_case
    from app.services.courses import owns_any_course
    from app.services.library import approve_source as approve_use_case
    from app.services.library import source_course_ids

    actor = require_auth(ctx)
    require(actor, "source.approve")
    bound = await source_course_ids(ctx.db, source_id=UUID(source_id))
    if bound and not actor.is_superadmin:
        if not await owns_any_course(ctx.db, owner_id=actor.user_id, course_ids=bound):
            from app.errors import NotFoundError

            raise NotFoundError("source not found")
        # The actor approves within their own courses, never another owner's.
        owned: list[UUID] = []
        for course in bound:
            row = await get_course_use_case(ctx.db, course_id=course)
            if row["owner_id"] == str(actor.user_id):
                owned.append(course)
        for course in owned:
            await approve_use_case(
                ctx.db,
                source_id=UUID(source_id),
                approved_by=actor.user_id,
                course_id=course,
            )
        await ctx.db.commit()
        return {"ok": True}
    await approve_use_case(ctx.db, source_id=UUID(source_id), approved_by=actor.user_id)
    await ctx.db.commit()
    return {"ok": True}


async def approve_candidate(ctx: RequestContext, *, candidate_id: str) -> dict[str, str]:
    from app.services.courses import get_course as get_use_case
    from app.services.library import approve_candidate as approve_use_case
    from app.services.library import get_candidate

    actor = require_auth(ctx)
    require(actor, "source.approve")
    candidate = await get_candidate(ctx.db, candidate_id=UUID(candidate_id))
    if candidate["course_id"] is not None:
        course = await get_use_case(ctx.db, course_id=UUID(str(candidate["course_id"])))
        _owner_or_404(course, actor.user_id, actor.is_superadmin)
    source_id = await approve_use_case(
        ctx.db,
        candidate_id=UUID(candidate_id),
        approved_by=actor.user_id,
        settings=ctx.settings,
    )
    await ctx.db.commit()
    return {"source_id": str(source_id)}


async def transcribe_source(ctx: RequestContext, *, source_id: str) -> dict[str, Any]:
    from app.services.courses import owns_any_course
    from app.services.library import source_course_ids
    from app.services.library import transcribe_source as transcribe_use_case

    actor = require_auth(ctx)
    require(actor, "source.upload")
    bound = await source_course_ids(ctx.db, source_id=UUID(source_id))
    if bound and not actor.is_superadmin:
        if not await owns_any_course(ctx.db, owner_id=actor.user_id, course_ids=bound):
            from app.errors import NotFoundError

            raise NotFoundError("source not found")
    count = await transcribe_use_case(ctx.db, source_id=UUID(source_id), settings=ctx.settings)
    await ctx.db.commit()
    return {"source_id": source_id, "passages": count}
