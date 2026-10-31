"""T03c author API: courses list, plan, estimate, sources, cancel, held, publish.

Red tests first: each route carries ctx + a catalogue code and answers
404 outside the caller's scope; sentinel answer keys are never serialized.
"""

from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.services import courses as course_service
from app.services import library

BASE = "https://api.meta.ai/v1"


def _settings(**overrides: Any) -> Settings:
    return Settings(
        _env_file=None,
        model_api_key="synthetic-test-key",
        model_api_base_url=BASE,
        llm_spend_ceiling_usd=50.0,
        source_store_path="/tmp/muse-t03c-sources",
        **overrides,
    )


async def _user(session: AsyncSession, tag: str) -> UUID:
    row = (
        await session.execute(
            sa.text("INSERT INTO users (email, display_name) VALUES (:e, 'N') RETURNING id"),
            {"e": f"t03c-{tag}-{uuid.uuid4().hex[:8]}@example.com"},
        )
    ).one()
    await session.commit()
    return UUID(str(row[0]))


def _actor(user_id: UUID, codes: frozenset[str], scope_tags: tuple[str, ...] = ()) -> Any:
    from app.rbac.principal import Principal

    return Principal(
        user_id=user_id,
        email="t03c@example.com",
        roles=("author",),
        permissions=codes,
        scope_tags=scope_tags,
        is_superadmin=False,
    )


def _ctx(session: AsyncSession, settings: Settings, principal: Any) -> Any:
    from app.auth.ctx import RequestContext

    return RequestContext(
        db=session,
        settings=settings,
        session_token=None,
        login_session=None,
        principal=principal,
    )


async def _course(
    session: AsyncSession, user_id: UUID, language: str = "en", title: str = "Course"
) -> UUID:
    course_id = await course_service.create_course(
        session, owner_id=user_id, title=title, language=language
    )
    await course_service.set_profile(
        session,
        course_id=course_id,
        methodology_codes=["direct_instruction"],
        bloom_level="understand",
        audience="municipal staff",
    )
    await session.commit()
    return course_id


async def _text_source(
    session: AsyncSession,
    settings: Settings,
    user_id: UUID,
    course_id: UUID,
    text: str,
    filename: str = "source.txt",
) -> UUID:
    source_id = await library.store_upload(
        session,
        settings=settings,
        created_by=user_id,
        filename=filename,
        media="text",
        content=text.encode("utf-8"),
        course_id=course_id,
    )
    await library.ingest_passages(session, source_id=source_id, passages=library.chunk_text(text))
    await library.approve_source(session, source_id=source_id, approved_by=user_id)
    await session.commit()
    return source_id


def _lesson_tree(node_prefix: str) -> Any:
    from app.course.tree import CourseTree

    return CourseTree.model_validate(
        {
            "modules": [
                {
                    "plan_node_id": f"{node_prefix}-m1",
                    "title": "Module",
                    "purpose": "Purpose.",
                    "entry_knowledge": "Entry.",
                    "exit_knowledge": "Exit.",
                    "topics": [
                        {
                            "plan_node_id": f"{node_prefix}-t1",
                            "kind": "lesson",
                            "title": "Topic",
                            "objectives": ["Name it."],
                            "through_line_note": "Note.",
                            "blocks": [
                                {
                                    "plan_node_id": f"{node_prefix}-b1",
                                    "role": "teach",
                                    "block_type": "reading",
                                    "brief": "The duty and who it binds",
                                    "source_refs": [{"handle": "P1"}],
                                },
                                {
                                    "plan_node_id": f"{node_prefix}-b2",
                                    "role": "example",
                                    "block_type": "reading",
                                    "brief": "An example of the duty",
                                    "source_refs": [{"handle": "P1"}],
                                },
                                {
                                    "plan_node_id": f"{node_prefix}-b3",
                                    "role": "check",
                                    "block_type": "choice",
                                    "brief": "Who must ensure it?",
                                    "source_refs": [{"handle": "P1"}],
                                },
                            ],
                        }
                    ],
                }
            ]
        }
    )


async def _course_with_tree(
    session: AsyncSession, user_id: UUID, prefix: str, title: str = "Course"
) -> UUID:
    course_id = await _course(session, user_id, title=title)
    await course_service.write_tree(
        session, course_id=course_id, tree=_lesson_tree(prefix), plan_digest="d"
    )
    await session.commit()
    return course_id


async def _hold_first_block(session: AsyncSession, course_id: UUID, reason: str) -> UUID:
    row = (
        await session.execute(
            sa.text(
                "SELECT b.id FROM blocks b JOIN topics t ON t.id = b.topic_id"
                " JOIN modules m ON m.id = t.module_id"
                " WHERE m.course_id = :course ORDER BY b.ordinal LIMIT 1"
            ),
            {"course": str(course_id)},
        )
    ).one()
    block_id = UUID(str(row[0]))
    await session.execute(
        sa.text("UPDATE blocks SET status = 'held', hold_reason = :reason WHERE id = :block"),
        {"reason": reason, "block": str(block_id)},
    )
    await session.commit()
    return block_id


FULL = frozenset(
    {
        "course.create",
        "course.edit",
        "course.plan",
        "course.generate",
        "course.publish",
        "source.upload",
        "source.approve",
        "run.start",
        "run.cancel",
        "run.retry",
    }
)


async def test_list_courses_scoped_to_caller(t03_session: AsyncSession) -> None:
    """Each caller lists only their own courses with profile/plan/question flags."""
    from app.controllers import t03 as controller

    settings = _settings()
    user_a = await _user(t03_session, "la")
    user_b = await _user(t03_session, "lb")
    await _course(t03_session, user_a, title="A1")
    await _course_with_tree(t03_session, user_a, "la", title="A2")
    await _course(t03_session, user_b, title="B1")
    rows = await controller.list_courses(_ctx(t03_session, settings, _actor(user_a, FULL)))
    titles = sorted(row["title"] for row in rows)
    assert titles == ["A1", "A2"]
    for row in rows:
        assert set(row) == {
            "id",
            "title",
            "language",
            "status",
            "has_profile",
            "has_plan",
            "open_questions",
        }
        assert row["has_profile"] is True
    plain = next(row for row in rows if row["title"] == "A1")
    planned = next(row for row in rows if row["title"] == "A2")
    assert plain["has_plan"] is False
    assert planned["has_plan"] is True
    assert plain["open_questions"] == 0


async def test_get_plan_matches_patch_shape(t03_session: AsyncSession) -> None:
    """GET plan returns the same tree as PATCH, with per-block source handles."""
    from app.controllers import t03 as controller

    settings = _settings()
    user = await _user(t03_session, "gp")
    course_id = await _course_with_tree(t03_session, user, "gp")
    ctx = _ctx(t03_session, settings, _actor(user, FULL))
    plan = await controller.get_plan(ctx, course_id=str(course_id))
    blocks = plan["modules"][0]["topics"][0]["blocks"]
    assert len(blocks) == 3
    assert all(block["source_handles"] == ["P1"] for block in blocks)
    assert "answer_key" not in str(plan)
    course = await course_service.get_course(t03_session, course_id=course_id)
    patched = await controller.edit_plan(
        ctx,
        course_id=str(course_id),
        operations=[],
        base_version=course["row_version"],
    )
    assert patched == plan


async def test_estimate_is_a_forecast(t03_session: AsyncSession) -> None:
    """GET estimate forecasts from the block count, never a limit."""
    from app.controllers import t03 as controller

    settings = _settings()
    user = await _user(t03_session, "es")
    course_id = await _course_with_tree(t03_session, user, "es")
    estimate = await controller.get_estimate(
        _ctx(t03_session, settings, _actor(user, FULL)), course_id=str(course_id)
    )
    assert estimate["blocks"] == 3
    assert estimate["calls"] == 6
    assert estimate["tokens"] == 6 * 1500
    assert estimate["note"] == "Forecast, not a limit."
    assert float(estimate["cost_usd"]) > 0
    assert estimate["seconds"] == round(6 * 8.0, 1)


async def test_sources_list_and_reject(t03_session: AsyncSession) -> None:
    """Sources list shows binding approval and progress; reject holds the source."""
    from app.controllers import t03 as controller

    settings = _settings()
    user = await _user(t03_session, "sl")
    course_id = await _course(t03_session, user, title="SL")
    source_id = await library.store_upload(
        t03_session,
        settings=settings,
        created_by=user,
        filename="pending.txt",
        media="text",
        content=b"Pending text about duties. " * 20,
        course_id=course_id,
    )
    await library.ingest_passages(
        t03_session,
        source_id=source_id,
        passages=library.chunk_text("Pending text about duties. " * 20),
    )
    await t03_session.commit()
    ctx = _ctx(t03_session, settings, _actor(user, FULL))
    rows = await controller.list_sources(ctx, course_id=str(course_id))
    assert len(rows) == 1
    assert set(rows[0]) == {
        "id",
        "title",
        "media",
        "licence",
        "as_of",
        "approval",
        "progress",
        "ingest_status",
        "ingest_message",
    }
    assert rows[0]["ingest_status"] == "ready"
    assert rows[0]["approval"] == "pending"
    assert rows[0]["progress"] >= 1
    assert (await controller.reject_source(ctx, source_id=str(source_id))) == {"ok": True}
    rows = await controller.list_sources(ctx, course_id=str(course_id))
    assert rows[0]["approval"] == "held"


async def test_cancel_run_is_cooperative(t03_session: AsyncSession) -> None:
    """Cancel sets a stop marker; the drive starts no new step afterwards."""
    from app.controllers import t03 as controller
    from app.runs import executor

    settings = _settings()
    user = await _user(t03_session, "cx")
    course_id = await _course(t03_session, user, title="CX")
    run_id = await executor.new_run(
        t03_session,
        course_id=course_id,
        kind="plan",
        dag=[{"step_key": "researcher:q", "handler": "never", "depends_on": []}],
    )
    await t03_session.commit()
    ctx = _ctx(t03_session, settings, _actor(user, FULL))
    assert (await controller.cancel_run(ctx, run_id=str(run_id))) == {"ok": True}
    run = await controller.get_run(ctx, run_id=str(run_id))
    assert run["status"] == "running"
    assert run["stop_marker"] == "cancel requested"

    async def _never(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("cancelled run must start no new step")

    from conftest import test_database_url

    from app.db import create_session_factory

    factory = create_session_factory(test_database_url())
    final = await executor.drive_run(
        factory, run_id=run_id, provider_factory=lambda s: None, handlers={"never": _never}
    )
    assert final["status"] == "stopped"


async def test_held_accept_rewrite_and_brief(t03_session: AsyncSession) -> None:
    """Held blocks list passages, accept clears them, rewrite enqueues, brief edits."""
    from app.controllers import t03 as controller

    settings = _settings()
    user = await _user(t03_session, "hb")
    course_id = await _course_with_tree(t03_session, user, "hb")
    block_id = await _hold_first_block(t03_session, course_id, "quote is not verbatim")
    ctx = _ctx(t03_session, settings, _actor(user, FULL))
    held = await controller.get_held(ctx, course_id=str(course_id))
    assert len(held["blocks"]) == 1
    entry = held["blocks"][0]
    assert set(entry) == {"id", "topic_title", "brief", "hold_reason", "passage", "address"}
    assert entry["id"] == str(block_id)
    assert entry["topic_title"] == "Topic"
    assert entry["hold_reason"] == "quote is not verbatim"
    assert float(held["batch_quote_usd"]) > 0
    single = await controller.rewrite_block(ctx, block_id=str(block_id))
    assert "operation_id" in single
    batch = await controller.rewrite_held(ctx, course_id=str(course_id))
    assert "operation_id" in batch
    assert (await controller.patch_block(ctx, block_id=str(block_id), brief="Fixed brief")) == {
        "ok": True
    }
    held = await controller.get_held(ctx, course_id=str(course_id))
    assert held["blocks"][0]["brief"] == "Fixed brief"
    assert (await controller.accept_block(ctx, block_id=str(block_id))) == {"ok": True}
    held = await controller.get_held(ctx, course_id=str(course_id))
    assert held["blocks"] == []


async def test_publish_refuses_held_blocks(t03_session: AsyncSession) -> None:
    """Publish answers 422 while a held block stands; accepted blocks publish."""
    from app.controllers import t03 as controller
    from app.errors import UnprocessableError

    settings = _settings()
    user = await _user(t03_session, "pb")
    course_id = await _course_with_tree(t03_session, user, "pb")
    block_id = await _hold_first_block(t03_session, course_id, "bad quote")
    ctx = _ctx(t03_session, settings, _actor(user, FULL))
    with pytest.raises(UnprocessableError):
        await controller.publish_course(ctx, course_id=str(course_id))
    assert (await controller.accept_block(ctx, block_id=str(block_id))) == {"ok": True}
    result = await controller.publish_course(ctx, course_id=str(course_id))
    assert "version_id" in result
    course = await course_service.get_course(t03_session, course_id=course_id)
    assert course["status"] == "published"
    assert course["published_version_id"] == result["version_id"]


async def test_patch_plan_base_version_conflicts(t03_session: AsyncSession) -> None:
    """A stale base_version answers 409; the current one applies cleanly."""
    from app.controllers import t03 as controller
    from app.errors import ConflictError

    settings = _settings()
    user = await _user(t03_session, "bv")
    course_id = await _course_with_tree(t03_session, user, "bv")
    ctx = _ctx(t03_session, settings, _actor(user, FULL))
    with pytest.raises(ConflictError):
        await controller.edit_plan(ctx, course_id=str(course_id), operations=[], base_version=-1)
    course = await course_service.get_course(t03_session, course_id=course_id)
    plan = await controller.edit_plan(
        ctx, course_id=str(course_id), operations=[], base_version=course["row_version"]
    )
    assert len(plan["modules"]) == 1


async def test_upload_carries_licence_and_as_of(t03_session: AsyncSession) -> None:
    """Upload stores the licence grant and the visible as-of date."""
    from app.controllers import t03 as controller

    settings = _settings()
    user = await _user(t03_session, "ul")
    course_id = await _course(t03_session, user, title="UL")
    ctx = _ctx(t03_session, settings, _actor(user, FULL))
    result = await controller.upload_source(
        ctx,
        filename="grant.txt",
        media="text",
        content=b"Licensed text about duties. " * 20,
        course_id=str(course_id),
        licence="CC-BY-4.0",
        as_of="2026-01-15",
    )
    row = (
        await t03_session.execute(
            sa.text("SELECT licence, as_of FROM sources WHERE id = :source"),
            {"source": result["source_id"]},
        )
    ).one()
    assert row[0] == "CC-BY-4.0"
    assert str(row[1]) == "2026-01-15"


async def test_run_carries_cost_and_topics(t03_session: AsyncSession) -> None:
    """Run reads expose cost_so_far from the ledger and per-topic held/checking."""
    from app.controllers import t03 as controller
    from app.runs import executor

    settings = _settings()
    user = await _user(t03_session, "rc")
    course_id = await _course_with_tree(t03_session, user, "rc")
    await _hold_first_block(t03_session, course_id, "bad quote")
    run_id = await executor.new_run(
        t03_session,
        course_id=course_id,
        kind="plan",
        dag=[{"step_key": "researcher:q", "handler": "h", "depends_on": []}],
    )
    await t03_session.execute(
        sa.text(
            "INSERT INTO llm_calls (agent, model, prompt_version, tokens_input,"
            " tokens_output, cost_usd, duration_ms, request, run_id)"
            " VALUES ('researcher', 'm', 'v', 10, 5, 0.000123, 7, '{}', :run)"
        ),
        {"run": str(run_id)},
    )
    await t03_session.commit()
    run = await controller.get_run(
        _ctx(t03_session, settings, _actor(user, FULL)), run_id=str(run_id)
    )
    assert run["course_id"] == str(course_id)
    assert abs(float(run["cost_so_far"]) - 0.000123) < 1e-9
    assert run["topics"] == [
        {"topic_id": run["topics"][0]["topic_id"], "title": "Topic", "status": "held"}
    ]


async def test_out_of_scope_answers_404(t03_session: AsyncSession) -> None:
    """Owner B sees none of owner A's course reads, writes, or publish."""
    from app.controllers import t03 as controller
    from app.errors import NotFoundError

    settings = _settings()
    user_a = await _user(t03_session, "o1")
    user_b = await _user(t03_session, "o2")
    course_a = await _course_with_tree(t03_session, user_a, "o1")
    source_a = await _text_source(
        t03_session, settings, user_a, course_a, "Owner A text about duties. " * 5
    )
    ctx_b = _ctx(t03_session, settings, _actor(user_b, FULL))
    with pytest.raises(NotFoundError):
        await controller.get_plan(ctx_b, course_id=str(course_a))
    with pytest.raises(NotFoundError):
        await controller.get_estimate(ctx_b, course_id=str(course_a))
    with pytest.raises(NotFoundError):
        await controller.list_sources(ctx_b, course_id=str(course_a))
    with pytest.raises(NotFoundError):
        await controller.get_held(ctx_b, course_id=str(course_a))
    with pytest.raises(NotFoundError):
        await controller.publish_course(ctx_b, course_id=str(course_a))
    with pytest.raises(NotFoundError):
        await controller.reject_source(ctx_b, source_id=str(source_a))


async def test_item8_invites_notifications_permissions(t03_session: AsyncSession) -> None:
    """Invites list is scoped, notifications newest-first with read flag, catalogue listed."""
    from app.controllers import t02 as controller

    settings = _settings()
    user = await _user(t03_session, "i8")
    actor = _actor(
        user,
        frozenset({"invite.create", "invite.revoke", "role.edit", "user.list"}),
    )
    ctx = _ctx(t03_session, settings, actor)
    before = {invite.id for invite in await controller.list_invites(ctx)}
    from app.services import invites as invite_service

    await invite_service.create_invite(
        t03_session, actor=actor, course_tags=["tag-a"], max_uses=1, expires_at=None
    )
    await t03_session.commit()
    invites = await controller.list_invites(ctx)
    fresh = [invite for invite in invites if invite.id not in before]
    assert len(fresh) == 1
    assert fresh[0].course_tags == ["tag-a"]
    import json as json_lib

    first = (
        await t03_session.execute(
            sa.text(
                "INSERT INTO notifications (user_id, kind, payload)"
                " VALUES (:user, 'k1', CAST(:payload AS jsonb)) RETURNING id"
            ),
            {"user": str(user), "payload": json_lib.dumps({"n": 1})},
        )
    ).one()[0]
    second = (
        await t03_session.execute(
            sa.text(
                "INSERT INTO notifications (user_id, kind, payload)"
                " VALUES (:user, 'k2', CAST(:payload AS jsonb)) RETURNING id"
            ),
            {"user": str(user), "payload": json_lib.dumps({"n": 2})},
        )
    ).one()[0]
    await t03_session.execute(
        sa.text("UPDATE notifications SET created_at = now() - interval '1 hour' WHERE id = :note"),
        {"note": str(first)},
    )
    await t03_session.commit()
    notes = await controller.list_notifications(ctx)
    assert [note.id for note in notes] == [str(second), str(first)]
    assert all(note.read is False for note in notes)
    await controller.mark_notification_read(ctx, notification_id=str(second))
    notes = await controller.list_notifications(ctx)
    assert notes[0].read is True
    assert notes[1].read is False
    catalogue = await controller.list_permissions(ctx)
    assert {entry.code for entry in catalogue} >= {"course.publish", "run.cancel"}
    assert all(entry.category for entry in catalogue)


async def test_user_roles_carry_scope_tags(t03_session: AsyncSession) -> None:
    """UserResponse roles include scope tags (contract fix already shipped)."""
    from app.controllers import t02 as controller

    settings = _settings()
    user = await _user(t03_session, "ur")
    other = await _user(t03_session, "ur2")
    actor = _actor(
        user,
        frozenset({"user.list", "role.edit", "role.assign", "course.edit"}),
        ("tag-a",),
    )
    ctx = _ctx(t03_session, settings, actor)
    from app.rbac.catalogue import sync_catalogue
    from app.schemas import t02 as dto

    await sync_catalogue(t03_session)
    await t03_session.commit()
    await controller.create_role(
        ctx,
        body=dto.RoleRequest(
            name="author-scoped", permission_codes=["course.edit"], scope_tags=["tag-a"]
        ),
    )
    await controller.assign_role(
        ctx,
        name="author-scoped",
        body=dto.AssignRequest(user_id=str(other), scope_tags=["tag-a"]),
    )
    rows = await controller.list_users(ctx)
    me = next(row for row in rows if row.id == str(other))
    assert {"name": "author-scoped", "scope_tags": ["tag-a"]} in [
        {"name": grant.name, "scope_tags": list(grant.scope_tags)} for grant in me.roles
    ]
