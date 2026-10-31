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
    from app.services.courses import blocking_questions
    from app.services.courses import get_course as get_use_case

    actor = require_auth(ctx)
    require(actor, "course.edit")
    course = await get_use_case(ctx.db, course_id=UUID(course_id))
    _owner_or_404(course, actor.user_id, actor.is_superadmin)
    questions = await blocking_questions(ctx.db, course_id=UUID(course_id))
    return {"blocking_questions": questions, "can_generate": not questions}


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


async def get_run(ctx: RequestContext, *, run_id: str) -> dict[str, Any]:
    from app.runs.executor import get_run as get_use_case

    actor = require_auth(ctx)
    require(actor, "run.start")
    return await get_use_case(ctx.db, run_id=UUID(run_id))


async def resume_run(ctx: RequestContext, *, run_id: str) -> dict[str, str]:
    from app.services.planruns import resume_run as resume_use_case
    from app.tasks.jobs import enqueue_operation

    actor = require_auth(ctx)
    resumed_id = await resume_use_case(
        ctx.db,
        actor=actor,
        run_id=UUID(run_id),
        defer=lambda op: enqueue_operation(op, ctx.settings.database_url),
    )
    return {"run_id": str(resumed_id)}


async def upload_source(
    ctx: RequestContext, *, filename: str, media: str, content: bytes
) -> dict[str, str]:
    from app.services.library import (
        chunk_markdown,
        chunk_text,
        ingest_passages,
        store_upload,
    )

    actor = require_auth(ctx)
    require(actor, "source.upload")
    source_id = await store_upload(
        ctx.db,
        settings=ctx.settings,
        created_by=actor.user_id,
        filename=filename,
        media=media,
        content=content,
    )
    text = content.decode("utf-8", errors="replace")
    if media == "markdown":
        passages = chunk_markdown(text)
    else:
        passages = chunk_text(text, kind="pdf")
    await ingest_passages(ctx.db, source_id=source_id, passages=passages, ts_config="english")
    await ctx.db.commit()
    return {"source_id": str(source_id)}


async def fetch_source(ctx: RequestContext, *, url: str, course_id: str | None) -> dict[str, str]:
    from app.services.library import intake_url

    actor = require_auth(ctx)
    require(actor, "source.upload")
    result = await intake_url(
        ctx.db,
        settings=ctx.settings,
        url=url,
        course_id=UUID(course_id) if course_id else None,
    )
    await ctx.db.commit()
    return result


async def approve_source(ctx: RequestContext, *, source_id: str) -> dict[str, bool]:
    from app.services.library import approve_source as approve_use_case

    actor = require_auth(ctx)
    require(actor, "source.approve")
    await approve_use_case(ctx.db, source_id=UUID(source_id), approved_by=actor.user_id)
    await ctx.db.commit()
    return {"ok": True}
