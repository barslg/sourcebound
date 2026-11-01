"""Access guards for every route (permanent), one table per role.

1. The route table: every /api/v1 route of the live app, taken from the
   router itself, must refuse an anonymous caller and a signed-in user
   with no role; a route missing from the guarded set fails the inventory.
2. The course table: an enrolled student gets 200, a student of another
   course 404, the anonymous caller 401, the owner of another school 404;
   the teacher report is the owner's (a student gets 403).
"""

from __future__ import annotations

import re
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession
from t06_helpers import World, cookie_for, http, make_user

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from check_routes import EXEMPT, iter_routes  # noqa: E402

SKIPPED = {
    ("GET", "/api/v1/events"): "SSE stream: never ends, scoped to the caller's own id",
}


def _app() -> Any:
    from t06_helpers import app_settings

    from app.main import create_app

    return create_app(app_settings(), sync_on_startup=False)


def _guarded(app: Any) -> list[tuple[str, str]]:
    found = [
        (method, route.path)
        for route in iter_routes(app)
        if route.path.startswith("/api/v1")
        for method in route.methods
        if method not in ("HEAD", "OPTIONS")
    ]
    return sorted(
        item
        for item in found
        if item not in EXEMPT and item not in SKIPPED and "logout" not in item[1]
    )


def _dummy(schema: dict[str, Any], defs: dict[str, Any], name: str = "") -> Any:
    """A schema-valid placeholder, so the permission check is what answers."""
    if "$ref" in schema:
        return _dummy(defs[schema["$ref"].split("/")[-1]], defs, name)
    pattern = re.match(r"\^\(([^|)]+)", schema.get("pattern", ""))
    if pattern:
        return pattern.group(1)
    if "enum" in schema:
        return schema["enum"][0]
    for key in ("anyOf", "oneOf"):
        if key in schema:
            return _dummy(schema[key][0], defs, name)
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        props = schema.get("properties", {})
        return {n: _dummy(props[n], defs, n) for n in schema.get("required", [])}
    if kind == "array":
        return [_dummy(schema.get("items", {}), defs)] * int(schema.get("minItems", 0))
    if kind == "integer":
        return max(1, int(schema.get("minimum", 1)))
    if kind == "number":
        return 1.0
    if kind == "boolean":
        return False
    if name.endswith("_id") or schema.get("format") == "uuid":
        return str(uuid.uuid4())
    return "x" * max(1, int(schema.get("minLength", 1)))


def _body_for(app: Any, method: str, path: str) -> dict[str, Any]:
    for route in iter_routes(app):
        if route.path == path and method in route.methods:
            field = getattr(route, "body_field", None)
            if field is None:
                return {}
            schema = TypeAdapter(field.field_info.annotation).json_schema()
            return dict(_dummy(schema, schema.get("$defs", {})))
    return {}


def _url(path: str) -> str:
    return re.sub(r"\{[^}]*\}", str(uuid.uuid4()), path)


@pytest.mark.parametrize("role", ["anonymous", "no_role"])
async def test_every_guarded_route_refuses_the_role(role: str, t03_session: AsyncSession) -> None:
    app = _app()
    routes = _guarded(app)
    assert len(routes) >= 50, "the route table was read from the live app"
    user = await make_user(t03_session, "nobody")
    cookies, csrf = await cookie_for(t03_session, user)
    own_scope = {
        "/api/v1/auth/me",
        "/api/v1/notifications",
        "/api/v1/operations/{operation_id}",
        "/api/v1/notifications/{notification_id}/read",
    }
    for method, path in routes:
        async with http(cookies if role == "no_role" else None) as client:
            headers = {"X-CSRF-Token": csrf} if role == "no_role" else {}
            kwargs: dict[str, Any] = {}
            if method not in ("GET", "DELETE"):
                kwargs["json"] = _body_for(app, method, path)
            if path.endswith("/upload"):
                kwargs = {"files": {"file": ("a.txt", b"x", "text/plain")}}
            response = await client.request(method, _url(path), headers=headers, **kwargs)
        if role == "anonymous":
            allowed = {401, 403} if method != "GET" else {401}
        elif path in own_scope:
            allowed = {200, 404}
        else:
            allowed = {403}
        assert response.status_code in allowed, (role, method, path, response.status_code)
        assert response.status_code != 422, (role, method, path, response.text)


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
        (
            "runs",
            "GET",
            f"/api/v1/courses/{w.course}/runs",
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


async def _tag_both_courses(world: World, tag: str) -> None:
    for course in (world.course, str(world.other["course_id"])):
        await world.s.execute(
            sa.text("UPDATE courses SET scope_tags = ARRAY[:t] WHERE id = :c"),
            {"t": tag, "c": course},
        )
    await world.s.commit()


async def _invite(world: World, who: str, tags: list[str]) -> Any:
    auth = world.cookies[who]
    assert auth is not None
    async with http(auth[0]) as client:
        return await client.post(
            "/api/v1/invites", json={"course_tags": tags}, headers={"X-CSRF-Token": auth[1]}
        )


async def _enrol_new_student(world: World, token: str) -> dict[str, str]:
    user = await make_user(world.s, "xo-student")
    cookies, csrf = await cookie_for(world.s, user)
    async with http(cookies) as client:
        reply = await client.post(f"/api/v1/invites/{token}/accept", headers={"X-CSRF-Token": csrf})
    assert reply.status_code == 200, reply.text
    assert all(":" not in tag for tag in reply.json()["scopes"]), ("owner key leaked", reply.text)
    return cookies


async def test_two_owners_are_walled_off_on_invites_and_group_tags(world: World) -> None:
    """Owner B cannot invite to, list, or revoke owner A's course; two owners' "oslo" never meet."""
    mine, theirs = world.course, str(world.other["course_id"])
    assert (await _invite(world, "other_owner", [mine])).status_code == 404
    assert (await _invite(world, "owner", ["not-my-tag"])).status_code == 404
    created = await _invite(world, "owner", [mine])
    assert created.status_code == 201, created.text
    for who, sees in (("owner", True), ("other_owner", False)):
        auth = world.cookies[who]
        assert auth is not None
        async with http(auth[0]) as client:
            rows = (await client.get("/api/v1/invites")).json()
        assert any(mine in row["course_tags"] for row in rows) is sees, (who, rows)
    async with http(world.cookies["owner"][0]) as client:  # type: ignore[index]
        invite_id = next(r["id"] for r in (await client.get("/api/v1/invites")).json())
    foreign = world.cookies["other_owner"]
    assert foreign is not None
    async with http(foreign[0]) as client:
        revoked = await client.post(
            f"/api/v1/invites/{invite_id}/revoke", headers={"X-CSRF-Token": foreign[1]}
        )
    assert revoked.status_code == 404, revoked.text

    await _tag_both_courses(world, "oslo")
    token = (await _invite(world, "owner", ["oslo"])).json()["token"]
    async with http(None) as client:
        preview = (await client.get(f"/api/v1/invites/{token}/preview")).json()
    title = (
        await world.s.execute(sa.text("SELECT title FROM courses WHERE id = :c"), {"c": mine})
    ).scalar_one()
    assert preview["course_titles"] == [title], "a group invite names only its owner's course"
    cookies = await _enrol_new_student(world, token)
    async with http(cookies) as client:
        assert (await client.get(f"/api/v1/learn/courses/{mine}")).status_code == 200
        assert (await client.get(f"/api/v1/learn/courses/{theirs}")).status_code == 404
        assert [c["id"] for c in (await client.get("/api/v1/learn/courses")).json()] == [mine]
