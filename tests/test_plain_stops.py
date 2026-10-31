"""Plan and generate refuse an empty library before any model call; stops speak plainly.

The owner pressed "Build plan" with no approved source: the researcher
and the architect were paid, then the run stopped with a Python class
name and a schema path on screen. These tests hold the two fixes: the
guard in submit_plan / submit_generate, and the plain stop sentence
(technical text stays in run_steps only).
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import httpx2
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from test_planruns import (
    _actor,
    _architect_output,
    _completed,
    _course_with_source,
    _drive,
    _settings,
)

from app.errors import UnprocessableError
from app.services import courses as course_service
from app.services import planruns

CODES = frozenset({"run.start", "operation.run", "course.edit", "course.plan", "course.generate"})


async def _bare_course(session: AsyncSession) -> tuple[UUID, UUID]:
    user_id, course_id = await _course_with_source(session)
    await session.execute(
        sa.text("UPDATE course_sources SET approval = 'pending' WHERE course_id = :c"),
        {"c": str(course_id)},
    )
    await session.commit()
    return user_id, course_id


async def _count(session: AsyncSession, table: str) -> int:
    return int((await session.execute(sa.text(f"SELECT count(*) FROM {table}"))).scalar_one())


async def test_plan_without_an_approved_source_is_refused_before_any_work(
    t03_session: AsyncSession,
) -> None:
    user_id, course_id = await _bare_course(t03_session)
    operations_before = await _count(t03_session, "operations")
    calls_before = await _count(t03_session, "llm_calls")
    with pytest.raises(UnprocessableError, match="Approve at least one source"):
        await planruns.submit_plan(
            t03_session,
            actor=_actor(user_id, CODES),
            course_id=course_id,
            queries=["literacy duty"],
            defer=None,
        )
    assert await _count(t03_session, "operations") == operations_before
    assert await _count(t03_session, "llm_calls") == calls_before == 0


async def test_plan_with_an_approved_source_with_passages_proceeds(
    t03_session: AsyncSession,
) -> None:
    user_id, course_id = await _course_with_source(t03_session)
    operation_id = await planruns.submit_plan(
        t03_session,
        actor=_actor(user_id, CODES),
        course_id=course_id,
        queries=["literacy duty"],
        defer=None,
    )
    assert operation_id


async def test_generate_without_sources_or_plan_is_refused(t03_session: AsyncSession) -> None:
    user_id, course_id = await _bare_course(t03_session)
    actor = _actor(user_id, CODES)
    with pytest.raises(UnprocessableError, match="Approve at least one source"):
        await planruns.submit_generate(t03_session, actor=actor, course_id=course_id, defer=None)
    await t03_session.execute(
        sa.text("UPDATE course_sources SET approval = 'approved' WHERE course_id = :c"),
        {"c": str(course_id)},
    )
    await t03_session.commit()
    with pytest.raises(UnprocessableError, match="plan is empty"):
        await planruns.submit_generate(t03_session, actor=actor, course_id=course_id, defer=None)


def _empty_modules_handler() -> Any:
    def handler(request: httpx2.Request) -> httpx2.Response:
        prompt = str(json.loads(request.content.decode("utf-8")).get("input", ""))
        if prompt.startswith("# Researcher"):
            dynamic = json.loads(prompt.split("## Task input (typed, assembled by code)\n")[1])
            reply = {
                "query": dynamic["query"],
                "rephrased": dynamic["rephrased"],
                "ranked": [],
                "web_candidates": [],
            }
            return httpx2.Response(200, json=_completed(json.dumps(reply)))
        empty = {**_architect_output(), "modules": []}
        return httpx2.Response(200, json=_completed(json.dumps(empty)))

    return handler


async def test_a_schema_rejection_stops_with_a_plain_sentence(t03_session: AsyncSession) -> None:
    user_id, course_id = await _course_with_source(t03_session)
    operation_id = await planruns.submit_plan(
        t03_session,
        actor=_actor(user_id, CODES),
        course_id=course_id,
        queries=["literacy duty"],
        defer=None,
    )
    await t03_session.commit()
    detail = await planruns.run_plan_operation(t03_session, operation_id)
    run_id = UUID(str(detail["run_id"]))
    stopped = await _drive(t03_session, run_id, _empty_modules_handler(), _settings())
    assert stopped["status"] == "stopped"
    shown = json.dumps(stopped)
    for leak in ("ValidationError", "Error:", "('modules',)", "schema:", "after validation"):
        assert leak not in shown, leak
    assert stopped["stop_marker"] == (
        "The planner returned no modules. Add sources with more content and try again."
    )
    assert stopped["stop_code"] == "empty_plan"
    technical = (
        await t03_session.execute(
            sa.text(
                "SELECT error FROM run_steps WHERE run_id = :r AND step_key = 'architect:plan'"
            ),
            {"r": str(run_id)},
        )
    ).scalar_one()
    assert "modules" in technical and "Error" in technical
    _ = course_service
