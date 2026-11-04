"""Public courses: who may flip the flag, who may read what, and what the tutor may spend.

A public course is a published course whose author turned the flag on;
every signed-in user (first sign-in gives the student role with no course
tags) then reads, answers and asks without an invite. Everything runs on
the real routes and the real sign-in; the world is the sentinel world.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from t06_helpers import World, cookie_for, http, seed_world, sign_in_new_user

from app.llm.calls import log_call
from app.rbac.roles import assign_role


async def _flip(world: World, who: str, course: str, value: bool) -> tuple[int, str]:
    auth = world.cookies[who]
    assert auth is not None
    async with http(auth[0]) as client:
        reply = await client.patch(
            f"/api/v1/courses/{course}/visibility",
            json={"public": value},
            headers={"X-CSRF-Token": auth[1]},
        )
    return reply.status_code, reply.text


async def _as(session: AsyncSession, user: uuid.UUID, method: str, url: str) -> int:
    cookies, csrf = await cookie_for(session, user)
    async with http(cookies) as client:
        reply = await client.request(method, url, headers={"X-CSRF-Token": csrf})
    return reply.status_code


async def test_only_the_owner_with_publish_flips_the_flag_and_only_on_a_published_course(
    world: World,
) -> None:
    """Student 403, another school's owner 404, the owner 200; a draft refuses with a reason."""
    mine = world.course
    assert (await _flip(world, "student", mine, True))[0] == 403
    assert (await _flip(world, "other_owner", mine, True))[0] == 404
    status, text = await _flip(world, "owner", mine, True)
    assert status == 200 and '"is_public":true' in text.replace(" ", ""), text

    draft = await seed_world(world.s, prefix="t06d", publish=False)
    await assign_role(world.s, draft["user_id"], "t06i-author")
    await world.s.commit()
    cookies, csrf = await cookie_for(world.s, draft["user_id"])
    async with http(cookies) as client:
        refused = await client.patch(
            f"/api/v1/courses/{draft['course_id']}/visibility",
            json={"public": True},
            headers={"X-CSRF-Token": csrf},
        )
    assert refused.status_code == 422, refused.text
    assert "published" in refused.json()["message"].lower(), refused.text


async def test_a_visitor_sees_only_courses_flagged_public_and_never_everything(
    world: World,
) -> None:
    """Empty-scope student: nothing before the flag, the public course after, never the others."""
    mine, theirs = world.course, str(world.other["course_id"])
    draft = await seed_world(world.s, prefix="t06v", publish=False)
    visitor = await sign_in_new_user(world.s, "visitor")
    cookies, _ = await cookie_for(world.s, visitor)

    async def listed() -> list[dict[str, object]]:
        async with http(cookies) as client:
            reply = await client.get("/api/v1/learn/courses")
        assert reply.status_code == 200, reply.text
        return list(reply.json())

    assert await listed() == [], "a student with no tags is enrolled in nothing"
    assert await _as(world.s, visitor, "GET", f"/api/v1/learn/courses/{mine}") == 404

    assert (await _flip(world, "owner", mine, True))[0] == 200
    rows = await listed()
    assert [r["id"] for r in rows] == [mine], "only the flagged course, not the other school's"
    assert rows[0]["is_public"] is True
    assert await _as(world.s, visitor, "GET", f"/api/v1/learn/courses/{mine}") == 200
    assert (
        await _as(world.s, visitor, "GET", f"/api/v1/learn/topics/{world.w['lesson_topic']}") == 200
    )
    assert await _as(world.s, visitor, "GET", f"/api/v1/learn/courses/{theirs}") == 404
    topic = f"/api/v1/learn/topics/{world.other['lesson_topic']}"
    assert await _as(world.s, visitor, "GET", topic) == 404
    assert (
        await _as(world.s, visitor, "GET", f"/api/v1/learn/topics/{draft['lesson_topic']}") == 404
    )
    assert await _as(world.s, visitor, "GET", f"/api/v1/learn/courses/{draft['course_id']}") == 404

    assert (await _flip(world, "owner", mine, False))[0] == 200
    assert await listed() == []
    assert await _as(world.s, visitor, "GET", f"/api/v1/learn/courses/{mine}") == 404


async def test_a_public_learner_who_answered_is_in_the_teachers_report(world: World) -> None:
    """A public learner who answered is in the report, without the email they never shared."""
    assert (await _flip(world, "owner", world.course, True))[0] == 200
    visitor = await sign_in_new_user(world.s, "reported")
    cookies, csrf = await cookie_for(world.s, visitor)
    reading = world.w["by_type"]["reading"]["block_id"]
    async with http(cookies) as client:
        answered = await client.post(
            f"/api/v1/learn/blocks/{reading}/answer",
            json={"acknowledged": True},
            headers={"X-CSRF-Token": csrf},
        )
    assert answered.status_code == 200, answered.text
    owner = world.cookies["owner"]
    assert owner is not None
    async with http(owner[0]) as client:
        report = await client.get(f"/api/v1/courses/{world.course}/report")
    assert report.status_code == 200, report.text
    email = (
        await world.s.execute(sa.text("SELECT email FROM users WHERE id = :u"), {"u": str(visitor)})
    ).scalar_one()
    learners = report.json()["learners"]
    assert str(visitor) in [row["user_id"] for row in learners]
    assert email not in [row["email"] for row in learners]


async def test_the_tutor_stops_at_twenty_paid_questions_a_day_per_user(world: World) -> None:
    """Twenty tutor calls in a day close the tutor; older calls and other users do not count."""
    assert (await _flip(world, "owner", world.course, True))[0] == 200
    visitor = await sign_in_new_user(world.s, "asker")
    bystander = await sign_in_new_user(world.s, "bystander")
    url = f"/api/v1/learn/courses/{world.course}/ask"

    async def ask(user: uuid.UUID) -> tuple[int, str]:
        cookies, csrf = await cookie_for(world.s, user)
        async with http(cookies) as client:
            reply = await client.post(
                url, json={"question": "penguins"}, headers={"X-CSRF-Token": csrf}
            )
        return reply.status_code, reply.text

    async def paid_call(user: uuid.UUID) -> uuid.UUID:
        call = await log_call(
            world.s,
            agent="tutor",
            model="muse-spark-1.3",
            prompt_version="v1",
            input_tokens=100,
            cached_tokens=0,
            output_tokens=50,
            duration_ms=900,
            request={"input": "q"},
            response={"quotes": []},
            subject_user_id=user,
        )
        await world.s.commit()
        return call

    for _ in range(19):
        await paid_call(visitor)
    assert (await ask(visitor))[0] == 200, "nineteen paid questions: still open"
    last = await paid_call(visitor)
    status, text = await ask(visitor)
    assert status == 429, (status, text)
    assert "20" in text and "tomorrow" in text.lower(), text
    assert (await ask(bystander))[0] == 200, "the limit is per user"

    await world.s.execute(
        sa.text("UPDATE llm_calls SET created_at = now() - interval '2 days' WHERE id = :i"),
        {"i": str(last)},
    )
    await world.s.commit()
    assert (await ask(visitor))[0] == 200, "a call older than a day no longer counts"


async def test_the_student_role_never_gains_a_code_outside_learning(world: World) -> None:
    """Every sign-in holds the student role with no tags, so its codes would be global."""
    import pytest

    from app.errors import ValidationError
    from app.rbac.roles import update_role

    await sign_in_new_user(world.s, "frozen")
    with pytest.raises(ValidationError):
        await update_role(
            world.s,
            name="student",
            permission_codes=["learn.read", "learn.answer", "learn.ask", "user.list"],
        )
