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
    ctx: RequestContext,
    *,
    course_id: str,
    operations: list[dict[str, Any]],
    base_version: int | None = None,
) -> dict[str, Any]:
    from app.services.courses import edit_plan as edit_use_case
    from app.services.courses import get_course as get_use_case

    actor = require_auth(ctx)
    require(actor, "course.edit")
    course = await get_use_case(ctx.db, course_id=UUID(course_id))
    _owner_or_404(course, actor.user_id, actor.is_superadmin)
    tree = await edit_use_case(
        ctx.db, course_id=UUID(course_id), operations=operations, base_version=base_version
    )
    await ctx.db.commit()
    return tree


async def list_courses(ctx: RequestContext) -> list[dict[str, Any]]:
    from app.services.courses import list_courses as list_use_case

    actor = require_auth(ctx)
    require(actor, "course.edit")
    return await list_use_case(ctx.db, owner_id=actor.user_id, is_superadmin=actor.is_superadmin)


async def get_plan(ctx: RequestContext, *, course_id: str) -> dict[str, Any]:
    from app.services.courses import get_course as get_use_case
    from app.services.courses import read_tree

    actor = require_auth(ctx)
    require(actor, "course.edit")
    course = await get_use_case(ctx.db, course_id=UUID(course_id))
    _owner_or_404(course, actor.user_id, actor.is_superadmin)
    return await read_tree(ctx.db, course_id=UUID(course_id))


async def get_estimate(ctx: RequestContext, *, course_id: str) -> dict[str, Any]:
    from app.services.courses import course_estimate as estimate_use_case
    from app.services.courses import get_course as get_use_case

    actor = require_auth(ctx)
    require(actor, "course.edit")
    course = await get_use_case(ctx.db, course_id=UUID(course_id))
    _owner_or_404(course, actor.user_id, actor.is_superadmin)
    return await estimate_use_case(ctx.db, course_id=UUID(course_id))


async def list_sources(ctx: RequestContext, *, course_id: str) -> list[dict[str, Any]]:
    from app.services.courses import get_course as get_use_case
    from app.services.library import list_course_sources

    actor = require_auth(ctx)
    require(actor, "course.edit")
    course = await get_use_case(ctx.db, course_id=UUID(course_id))
    _owner_or_404(course, actor.user_id, actor.is_superadmin)
    return await list_course_sources(ctx.db, course_id=UUID(course_id))


async def reject_source(ctx: RequestContext, *, source_id: str) -> dict[str, bool]:
    from app.services.courses import get_course as get_course_use_case
    from app.services.courses import owns_any_course
    from app.services.library import reject_source as reject_use_case
    from app.services.library import source_course_ids

    actor = require_auth(ctx)
    require(actor, "source.approve")
    bound = await source_course_ids(ctx.db, source_id=UUID(source_id))
    if bound and not actor.is_superadmin:
        if not await owns_any_course(ctx.db, owner_id=actor.user_id, course_ids=bound):
            from app.errors import NotFoundError

            raise NotFoundError("source not found")
        for course in bound:
            row = await get_course_use_case(ctx.db, course_id=course)
            _owner_or_404(row, actor.user_id, actor.is_superadmin)
    await reject_use_case(ctx.db, source_id=UUID(source_id))
    await ctx.db.commit()
    return {"ok": True}


async def cancel_run(ctx: RequestContext, *, run_id: str) -> dict[str, bool]:
    from app.runs.executor import cancel_run as cancel_use_case

    actor = require_auth(ctx)
    require(actor, "run.cancel")
    await _run_in_scope(ctx, run_id=UUID(run_id))
    await cancel_use_case(ctx.db, run_id=UUID(run_id))
    return {"ok": True}


async def translate_course(
    ctx: RequestContext,
    *,
    course_id: str,
    languages: list[str],
    glossary: dict[str, str] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, str]:
    from app.services.courses import get_course as get_use_case
    from app.services.planruns import submit_translate as submit_use_case
    from app.tasks.jobs import enqueue_operation

    actor = require_auth(ctx)
    require(actor, "course.generate")
    course = await get_use_case(ctx.db, course_id=UUID(course_id))
    _owner_or_404(course, actor.user_id, actor.is_superadmin)
    operation_id = await submit_use_case(
        ctx.db,
        actor=actor,
        course_id=UUID(course_id),
        languages=languages,
        glossary=glossary,
        idempotency_key=idempotency_key,
        defer=lambda op: enqueue_operation(op, ctx.settings.database_url),
    )
    return {"operation_id": str(operation_id)}


async def get_learner_course(
    ctx: RequestContext, *, course_id: str, language: str
) -> dict[str, Any]:
    from app.services.courses import get_course as get_use_case
    from app.services.planruns import learner_course_view

    actor = require_auth(ctx)
    require(actor, "learner.view")
    course = await get_use_case(ctx.db, course_id=UUID(course_id))
    _owner_or_404(course, actor.user_id, actor.is_superadmin)
    return await learner_course_view(ctx.db, course_id=UUID(course_id), language=language)


async def set_source_language(
    ctx: RequestContext,
    *,
    source_id: str,
    language: str,
    passage_id: str | None = None,
) -> dict[str, Any]:
    from app.services.library import set_passage_language_override

    actor = require_auth(ctx)
    require(actor, "source.edit")
    import sqlalchemy as sa

    if passage_id:
        config = await set_passage_language_override(
            ctx.db, passage_id=passage_id, language=language
        )
        await ctx.db.commit()
        return {"ok": True, "ts_config": config, "passages": 1}
    rows = (
        await ctx.db.execute(
            sa.text("SELECT id FROM passages WHERE source_id = :source"),
            {"source": source_id},
        )
    ).all()
    last_config: str | None = None
    for row in rows:
        last_config = await set_passage_language_override(
            ctx.db, passage_id=str(row[0]), language=language
        )
    await ctx.db.commit()
    return {"ok": True, "ts_config": last_config, "passages": len(rows)}


async def get_held(ctx: RequestContext, *, course_id: str) -> dict[str, Any]:
    from app.services.courses import get_course as get_use_case
    from app.services.courses import held_blocks

    actor = require_auth(ctx)
    require(actor, "course.edit")
    course = await get_use_case(ctx.db, course_id=UUID(course_id))
    _owner_or_404(course, actor.user_id, actor.is_superadmin)
    return await held_blocks(ctx.db, course_id=UUID(course_id))


async def _block_in_scope(ctx: RequestContext, *, block_id: UUID) -> UUID:
    """The block's course id, 404 when outside the actor's scope."""
    from app.services.courses import block_course_id
    from app.services.courses import get_course as get_use_case

    actor = require_auth(ctx)
    course_id = await block_course_id(ctx.db, block_id=block_id)
    course = await get_use_case(ctx.db, course_id=course_id)
    _owner_or_404(course, actor.user_id, actor.is_superadmin)
    return course_id


async def rewrite_block(ctx: RequestContext, *, block_id: str) -> dict[str, str]:
    from app.services.courses import submit_rewrite_operation
    from app.tasks.jobs import enqueue_operation

    actor = require_auth(ctx)
    require(actor, "course.generate")
    course_id = await _block_in_scope(ctx, block_id=UUID(block_id))
    operation_id = await submit_rewrite_operation(
        ctx.db,
        actor=actor,
        kind="rewrite_block",
        params={"course_id": str(course_id), "block_id": block_id},
        defer=lambda op: enqueue_operation(op, ctx.settings.database_url),
    )
    await ctx.db.commit()
    return {"operation_id": str(operation_id)}


async def accept_block(ctx: RequestContext, *, block_id: str) -> dict[str, bool]:
    from app.services.courses import accept_block as accept_use_case

    actor = require_auth(ctx)
    require(actor, "course.edit")
    await _block_in_scope(ctx, block_id=UUID(block_id))
    await accept_use_case(ctx.db, block_id=UUID(block_id))
    await ctx.db.commit()
    return {"ok": True}


async def patch_block(ctx: RequestContext, *, block_id: str, brief: str) -> dict[str, bool]:
    from app.services.courses import set_block_brief

    actor = require_auth(ctx)
    require(actor, "course.edit")
    await _block_in_scope(ctx, block_id=UUID(block_id))
    await set_block_brief(ctx.db, block_id=UUID(block_id), brief=brief)
    await ctx.db.commit()
    return {"ok": True}


async def rewrite_held(ctx: RequestContext, *, course_id: str) -> dict[str, str]:
    from app.services.courses import get_course as get_use_case
    from app.services.courses import held_blocks, submit_rewrite_operation
    from app.tasks.jobs import enqueue_operation

    actor = require_auth(ctx)
    require(actor, "course.generate")
    course = await get_use_case(ctx.db, course_id=UUID(course_id))
    _owner_or_404(course, actor.user_id, actor.is_superadmin)
    held = await held_blocks(ctx.db, course_id=UUID(course_id))
    operation_id = await submit_rewrite_operation(
        ctx.db,
        actor=actor,
        kind="rewrite_held_batch",
        params={
            "course_id": course_id,
            "block_ids": [block["id"] for block in held["blocks"]],
        },
        defer=lambda op: enqueue_operation(op, ctx.settings.database_url),
    )
    await ctx.db.commit()
    return {"operation_id": str(operation_id)}


async def publish_course(ctx: RequestContext, *, course_id: str) -> dict[str, str]:
    from app.services.courses import get_course as get_use_case
    from app.services.courses import publish_course as publish_use_case

    actor = require_auth(ctx)
    require(actor, "course.publish")
    course = await get_use_case(ctx.db, course_id=UUID(course_id))
    _owner_or_404(course, actor.user_id, actor.is_superadmin)
    version_id = await publish_use_case(ctx.db, course_id=UUID(course_id))
    await ctx.db.commit()
    return {"version_id": str(version_id)}


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
    """Load a run only when it is in the actor's scope (else 404).

    Course runs scope through their course owner. Runs without a
    course (pre-0005) scope through the operations that drove them:
    only an actor who owns such an operation — or a superadmin —
    may read or resume the run.
    """
    import sqlalchemy as sa

    from app.errors import NotFoundError
    from app.runs.executor import get_run as get_use_case
    from app.services.courses import get_course as get_course_use_case

    actor = require_auth(ctx)
    run = await get_use_case(ctx.db, run_id=run_id)
    if run.get("course_id") is not None:
        course = await get_course_use_case(ctx.db, course_id=UUID(str(run["course_id"])))
        _owner_or_404(course, actor.user_id, actor.is_superadmin)
        return run
    if actor.is_superadmin:
        return run
    owners = (
        await ctx.db.execute(
            sa.text("SELECT actor_id FROM operations WHERE params->>'run_id' = :run"),
            {"run": str(run_id)},
        )
    ).all()
    if not any(str(owner[0]) == str(actor.user_id) for owner in owners):
        raise NotFoundError("run not found")
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
    licence: str = "",
    as_of: str | None = None,
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
        licence=licence,
        course_id=course_uuid,
    )
    await ingest_passages(ctx.db, source_id=source_id, passages=passages)
    if media == "audio":
        await queue_transcription(ctx.db, source_id=source_id)
    from datetime import date

    from app.errors import ValidationError

    if as_of is None:
        day = date.today()
    else:
        try:
            day = date.fromisoformat(as_of)
        except ValueError as exc:
            raise ValidationError(f"as_of must be an ISO date, got {as_of!r}") from exc
    await set_source_as_of(ctx.db, source_id=source_id, as_of=day)
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
    from app.services.library import approve_candidate as approve_candidate_use_case
    from app.services.library import get_candidate

    actor = require_auth(ctx)
    require(actor, "source.approve")
    candidate = await get_candidate(ctx.db, candidate_id=UUID(candidate_id))
    if candidate["course_id"] is not None:
        course = await get_use_case(ctx.db, course_id=UUID(str(candidate["course_id"])))
        _owner_or_404(course, actor.user_id, actor.is_superadmin)
    source_id = await approve_candidate_use_case(
        ctx.db,
        candidate_id=UUID(candidate_id),
        approved_by=actor.user_id,
        settings=ctx.settings,
    )
    await ctx.db.commit()
    return {"source_id": str(source_id)}


async def transcribe_source(ctx: RequestContext, *, source_id: str) -> dict[str, Any]:
    from app.services.courses import owns_any_course
    from app.services.library import source_course_ids, submit_transcribe
    from app.tasks.jobs import enqueue_operation

    actor = require_auth(ctx)
    require(actor, "source.upload")
    bound = await source_course_ids(ctx.db, source_id=UUID(source_id))
    if bound and not actor.is_superadmin:
        if not await owns_any_course(ctx.db, owner_id=actor.user_id, course_ids=bound):
            from app.errors import NotFoundError

            raise NotFoundError("source not found")
    operation_id = await submit_transcribe(
        ctx.db,
        actor=actor,
        source_id=UUID(source_id),
        defer=lambda op: enqueue_operation(op, ctx.settings.database_url),
    )
    await ctx.db.commit()
    return {"operation_id": str(operation_id)}


async def _owned_course_ids(ctx: RequestContext) -> list[UUID] | None:
    """Courses the actor may see queued items for; None is the superadmin view."""
    from app.services.courses import list_courses as list_use_case

    actor = require_auth(ctx)
    if actor.is_superadmin:
        return None
    rows = await list_use_case(ctx.db, owner_id=actor.user_id, is_superadmin=False)
    return [UUID(str(row["id"])) for row in rows]


async def list_human_queue(ctx: RequestContext) -> list[dict[str, Any]]:
    from app.services.library import list_human_queue as list_use_case

    actor = require_auth(ctx)
    require(actor, "source.approve")
    return await list_use_case(ctx.db, course_ids=await _owned_course_ids(ctx))


async def resolve_human_queue(ctx: RequestContext, *, item_id: str, body: Any) -> dict[str, Any]:
    import sqlalchemy as sa

    from app.errors import NotFoundError
    from app.services.library import resolve_human_queue as resolve_use_case

    actor = require_auth(ctx)
    require(actor, "source.approve")
    owned = await _owned_course_ids(ctx)
    if owned is not None:
        row = (
            await ctx.db.execute(
                sa.text("SELECT payload FROM human_queue WHERE id = :item AND status = 'open'"),
                {"item": item_id},
            )
        ).first()
        if row is None:
            raise NotFoundError("queue item not found")
        course = dict(row[0] or {}).get("course_id")
        if course is None or UUID(str(course)) not in owned:
            raise NotFoundError("queue item not found")
    action = str((body or {}).get("action", ""))
    result = await resolve_use_case(
        ctx.db,
        item_id=UUID(item_id),
        action=action,
        settings=ctx.settings,
        approved_by=actor.user_id,
    )
    await ctx.db.commit()
    return result


async def list_tags(ctx: RequestContext) -> list[dict[str, Any]]:
    from app.services.courses import list_tags as list_use_case

    actor = require_auth(ctx)
    require(actor, "course.edit")
    return await list_use_case(ctx.db, actor=actor)
