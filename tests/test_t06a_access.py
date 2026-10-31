"""Access guards for every learner and teacher-report route (permanent).

One table of routes with who may open each: an enrolled student gets
200, a student of another course gets 404, the anonymous caller 401;
the report is the owner's (a student gets 403, another author 404).
A second test is the answer-key guard: no learner response carries a
sentinel answer key or a planted position, whatever the route or type.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from t06_helpers import SENTINELS, cookie_for, enrol, http, make_user, seed_world

LEAKS = (
    "answer_key",
    "planted_",
    "mapping_by_index",
    "labels_by_index",
    "passages_by_index",
    "true_sentence",
    "fully_correct",
    "translated_answer_key",
)


class World:
    async def build(self, session: AsyncSession) -> World:
        from app.rbac.catalogue import sync_catalogue
        from app.rbac.roles import assign_role, create_role

        self.s = session
        self.w = await seed_world(session, prefix="t06g")
        self.other = await seed_world(session, prefix="t06h")
        await session.execute(
            sa.text("DELETE FROM course_sources WHERE course_id = :c"),
            {"c": str(self.other["course_id"])},
        )
        self.course = str(self.w["course_id"])
        self.student = await make_user(session, "g-student")
        await enrol(session, owner_id=self.w["user_id"], user_id=self.student, tag=self.course)
        self.other_student = await make_user(session, "g-other")
        await enrol(
            session,
            owner_id=self.other["user_id"],
            user_id=self.other_student,
            tag=str(self.other["course_id"]),
        )
        await sync_catalogue(session)
        if (
            await session.execute(sa.text("SELECT 1 FROM roles WHERE name = 't06g-author'"))
        ).first() is None:
            await create_role(session, name="t06g-author", permission_codes=["course.edit"])
        for owner in (self.w["user_id"], self.other["user_id"]):
            await assign_role(session, owner, "t06g-author")
        await session.commit()
        self.cookies: dict[str, tuple[dict[str, str], str] | None] = {
            "anon": None,
            "student": await cookie_for(session, self.student),
            "other_student": await cookie_for(session, self.other_student),
            "owner": await cookie_for(session, self.w["user_id"]),
            "other_owner": await cookie_for(session, self.other["user_id"]),
        }
        return self


@pytest.fixture()
async def world(t03_session: AsyncSession) -> World:
    return await World().build(t03_session)


def _cases(w: World) -> list[tuple[str, str, str, dict[str, Any], dict[str, int]]]:
    reading = w.w["by_type"]["reading"]["block_id"]
    passage = next(p["id"] for p in w.w["passages"] if ">Article 4/" in p["address"])
    learner = {"student": 200, "other_student": 404, "anon": 401, "owner": 403}
    return [
        ("list", "GET", "/api/v1/learn/courses", {}, {**learner, "other_student": 200}),
        ("outline", "GET", f"/api/v1/learn/courses/{w.course}", {}, learner),
        ("topic", "GET", f"/api/v1/learn/topics/{w.w['lesson_topic']}", {}, learner),
        ("passage", "GET", f"/api/v1/learn/passages/{passage}", {}, learner),
        (
            "answer",
            "POST",
            f"/api/v1/learn/blocks/{reading}/answer",
            {"acknowledged": True},
            learner,
        ),
        (
            "submit",
            "POST",
            f"/api/v1/learn/topics/{w.w['test_topic']}/submit",
            {"answers": {}},
            learner,
        ),
        ("ask", "POST", f"/api/v1/learn/courses/{w.course}/ask", {"question": "penguins"}, learner),
        (
            "report",
            "GET",
            f"/api/v1/courses/{w.course}/report",
            {},
            {"owner": 200, "other_owner": 404, "student": 403, "anon": 401},
        ),
    ]


async def test_every_route_answers_by_enrolment_and_ownership(world: World) -> None:
    for name, method, url, body, expected in _cases(world):
        for actor, wanted in expected.items():
            status = 403 if actor == "anon" and method == "POST" else wanted  # CSRF gate first
            auth = world.cookies[actor]
            async with http(auth[0] if auth else None) as client:
                headers = {"X-CSRF-Token": auth[1]} if auth else {}
                response = (
                    await client.get(url)
                    if method == "GET"
                    else await client.post(url, json=body, headers=headers)
                )
            assert response.status_code == status, (
                name,
                actor,
                response.status_code,
                response.text,
            )


async def test_no_learner_response_carries_a_key_or_a_planted_position(world: World) -> None:
    auth = world.cookies["student"]
    assert auth is not None
    texts: list[str] = []
    async with http(auth[0]) as client:
        topics = []
        for topic in (world.w["lesson_topic"], world.w["test_topic"]):
            response = await client.get(f"/api/v1/learn/topics/{topic}")
            texts.append(response.text)
            topics += response.json()["blocks"]
        texts.append((await client.get(f"/api/v1/learn/courses/{world.course}")).text)
        wrong: dict[str, dict[str, Any]] = {
            "choice": {"picked": 1},
            "matching": {"mapping": {"Provider": "oversee use"}},
            "cloze": {"blanks": ["x", "y"]},
            "scenario_practice": {"labels": {}, "passages": {}},
            "spot_the_error": {"sentence_id": 0, "kind": "scope"},
            "reading": {"acknowledged": False},
            "figure": {"acknowledged": False},
        }
        for block in topics:
            reply = await client.post(
                f"/api/v1/learn/blocks/{block['id']}/answer",
                json=wrong[block["block_type"]],
                headers={"X-CSRF-Token": auth[1]},
            )
            assert reply.status_code == 200, reply.text
            texts.append(reply.text)
    joined = "\n".join(texts)
    assert {b["block_type"] for b in topics} == set(wrong), "all seven types were served"
    for sentinel in SENTINELS:
        assert sentinel not in joined, sentinel
    for leak in LEAKS:
        assert leak not in joined, leak
    assert UUID(world.course)
