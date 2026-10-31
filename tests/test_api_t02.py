"""HTTP surface for T02: auth, users, roles, invites, operations, events.

Routes parse and validate only; controllers orchestrate; services hold
the use cases. Covers anti-escalation over HTTP (nobody grants what
they lack, nobody edits their own role), out-of-scope objects answering
404, the signed-out invite flow (pending invite stored, redirect to
sign-in, applied after callback), and the 202 plus SSE event stream.
"""

from __future__ import annotations

import uuid
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import sqlalchemy as sa
from conftest import test_database_url
from sqlalchemy.ext.asyncio import AsyncSession
from t02_helpers import make_session_factory, truncate_all, unique_email
from t02_issuer import register_code, run_issuer, stored_verifier_slot


@pytest.fixture(scope="module")
def issuer_url() -> str:
    with run_issuer() as url:
        yield url


@pytest.fixture()
async def session() -> AsyncSession:
    factory = make_session_factory()
    async with factory() as sess:
        await truncate_all(sess)
        yield sess
        await sess.rollback()


def _test_settings(issuer_url: str) -> object:
    from app.config import Settings

    return Settings(
        database_url=test_database_url(),
        google_oauth_client_id="test-client-id",
        google_oauth_client_secret="test-client-secret",  # type: ignore[arg-type]
        session_secret_key="test-session-secret",  # type: ignore[arg-type]
        session_secure_cookie=False,
        session_ttl_s=3600,
        oidc_issuer=issuer_url,
        oidc_redirect_url="http://test/api/v1/auth/callback",
        superadmin_emails="",
    )


def _override_session(app: object, factory: object) -> None:
    from app import db as db_module

    async def _test_session():  # type: ignore[no-untyped-def]
        sess = factory()  # type: ignore[operator]
        try:
            yield sess
            await sess.commit()
        except Exception:
            await sess.rollback()
            raise
        finally:
            await sess.close()

    app.dependency_overrides[db_module.get_session] = _test_session  # type: ignore[attr-defined]


async def _client_for(app: object) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _login(client: httpx.AsyncClient, session: AsyncSession, subject: str) -> None:
    """Complete a full OIDC login through the HTTP routes for one subject."""
    start = await client.get("/api/v1/auth/login", params={"provider": "google"})
    assert start.status_code == 307
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    stored = (
        await session.execute(
            sa.text("SELECT nonce, code_verifier FROM auth_states WHERE state = :s"),
            {"s": state},
        )
    ).one()
    code = register_code(subject, "test-client-id", stored[0])
    stored_verifier_slot(code)["code_verifier"] = stored[1]
    done = await client.get("/api/v1/auth/callback", params={"state": state, "code": code})
    assert done.status_code == 200, done.text


async def _csrf(client: httpx.AsyncClient) -> str:
    response = await client.get("/api/v1/auth/csrf")
    assert response.status_code == 200
    return response.json()["csrf_token"]  # type: ignore[no-any-return]


async def test_me_reports_permissions_for_signed_in_user(
    session: AsyncSession, issuer_url: str
) -> None:
    from app.main import create_app
    from app.rbac.catalogue import sync_catalogue

    await sync_catalogue(session)
    await session.commit()
    app = create_app(_test_settings(issuer_url))  # type: ignore[arg-type]
    _override_session(app, make_session_factory())
    async with await _client_for(app) as client:
        anonymous = await client.get("/api/v1/auth/me")
        assert anonymous.status_code == 401
        await _login(client, session, "api-me-user")
        me = await client.get("/api/v1/auth/me")
        assert me.status_code == 200
        body = me.json()
        assert body["email"] == "api-me-user@example.com"
        assert body["permissions"] == []
        assert body["roles"] == []


async def test_unsafe_method_without_csrf_is_refused(
    session: AsyncSession, issuer_url: str
) -> None:
    from app.main import create_app
    from app.rbac.catalogue import sync_catalogue

    await sync_catalogue(session)
    await session.commit()
    app = create_app(_test_settings(issuer_url))  # type: ignore[arg-type]
    _override_session(app, make_session_factory())
    async with await _client_for(app) as client:
        await _login(client, session, "csrf-user")
        refused = await client.post("/api/v1/auth/logout")
        assert refused.status_code == 403
        token = await _csrf(client)
        ok = await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": token})
        assert ok.status_code == 200
        assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_role_antiescalation_over_http(session: AsyncSession, issuer_url: str) -> None:
    from app.main import create_app
    from app.rbac.catalogue import sync_catalogue
    from app.rbac.principal import bump_version
    from app.rbac.roles import assign_role, create_role

    await sync_catalogue(session)
    await session.commit()
    app = create_app(_test_settings(issuer_url))  # type: ignore[arg-type]
    _override_session(app, make_session_factory())
    async with await _client_for(app) as client:
        await _login(client, session, "role-manager")
        manager_id = uuid.UUID(
            str(
                (
                    await session.execute(
                        sa.text("SELECT id FROM users WHERE email = 'role-manager@example.com'")
                    )
                ).scalar_one()
            )
        )
        await create_role(session, name="manager", permission_codes=["role.edit"], actor=None)
        await assign_role(session, manager_id, "manager", scope_tags=[], actor=None)
        await session.commit()
        bump_version()
        token = await _csrf(client)
        headers = {"X-CSRF-Token": token}
        # Nobody grants what they lack.
        escalate = await client.post(
            "/api/v1/roles",
            json={"name": "evil", "permission_codes": ["user.delete"]},
            headers=headers,
        )
        assert escalate.status_code == 403
        # Nobody edits their own role.
        own = await client.put(
            "/api/v1/roles/manager",
            json={"permission_codes": ["role.edit"]},
            headers=headers,
        )
        assert own.status_code == 403
        allowed = await client.post(
            "/api/v1/roles",
            json={"name": "scoped", "permission_codes": ["role.edit"]},
            headers=headers,
        )
        assert allowed.status_code == 201, allowed.text


async def test_operation_of_another_user_answers_404(
    session: AsyncSession, issuer_url: str
) -> None:
    from app.main import create_app
    from app.rbac.catalogue import sync_catalogue
    from app.rbac.principal import bump_version
    from app.rbac.roles import assign_role, create_role

    await sync_catalogue(session)
    await session.commit()
    app = create_app(_test_settings(issuer_url))  # type: ignore[arg-type]
    _override_session(app, make_session_factory())
    async with await _client_for(app) as first:
        await _login(first, session, "op-owner")
        owner_id = uuid.UUID(
            str(
                (
                    await session.execute(
                        sa.text("SELECT id FROM users WHERE email = 'op-owner@example.com'")
                    )
                ).scalar_one()
            )
        )
        await create_role(session, name="operator", permission_codes=["operation.run"], actor=None)
        await assign_role(session, owner_id, "operator", scope_tags=[], actor=None)
        await session.commit()
        bump_version()
        token = await _csrf(first)
        created = await first.post(
            "/api/v1/operations/echo",
            json={"message": "secret"},
            headers={"X-CSRF-Token": token},
        )
        assert created.status_code == 202, created.text
        operation_id = created.json()["operation_id"]
        own = await first.get(f"/api/v1/operations/{operation_id}")
        assert own.status_code == 200
        assert own.json()["operation_id"] == operation_id
    async with await _client_for(app) as second:
        await _login(second, session, "op-stranger")
        missing = await second.get(f"/api/v1/operations/{operation_id}")
        assert missing.status_code == 404


async def test_signed_out_invite_flow_applies_after_login(
    session: AsyncSession, issuer_url: str
) -> None:
    from app.main import create_app
    from app.rbac.catalogue import sync_catalogue
    from app.services.invites import create_invite

    await sync_catalogue(session)
    creator = (
        await session.execute(
            sa.text("INSERT INTO users (email, display_name) VALUES (:e, 'C') RETURNING id"),
            {"e": unique_email("creator")},
        )
    ).scalar_one()
    await session.commit()
    from app.rbac.principal import Principal

    actor = Principal(
        user_id=uuid.UUID(str(creator)),
        email="creator@example.com",
        roles=(),
        permissions=frozenset({"invite.create"}),
        scope_tags=(),
        is_superadmin=True,
    )
    token = await create_invite(
        session,
        actor=actor,
        course_tags=["course-z"],
        max_uses=5,  # type: ignore[arg-type]
    )
    await session.commit()
    app = create_app(_test_settings(issuer_url))  # type: ignore[arg-type]
    _override_session(app, make_session_factory())
    async with await _client_for(app) as client:
        accept = await client.get(f"/api/v1/invites/{token}/accept", follow_redirects=False)
        assert accept.status_code == 307
        assert "/api/v1/auth/login" in accept.headers["location"]
        start = await client.get(accept.headers["location"])
        assert start.status_code == 307
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
        stored = (
            await session.execute(
                sa.text("SELECT nonce, code_verifier FROM auth_states WHERE state = :s"),
                {"s": state},
            )
        ).one()
        code = register_code("invited-student", "test-client-id", stored[0])
        stored_verifier_slot(code)["code_verifier"] = stored[1]
        done = await client.get("/api/v1/auth/callback", params={"state": state, "code": code})
        assert done.status_code == 200, done.text
        assert done.json()["applied_invite_scopes"] == ["course-z"]
        me = await client.get("/api/v1/auth/me")
        assert "student" in me.json()["roles"]


async def test_sse_ping_and_unsubscribe() -> None:
    import uuid as uuid_mod

    from app.routes.ops import sse_stream
    from app.services.events import publish_event, subscriber_count

    user_id = uuid_mod.uuid4()
    stream = sse_stream(user_id, ping_s=0.05)
    first = await stream.__anext__()
    assert first == ": ping\n\n"
    assert subscriber_count(user_id) == 1
    await publish_event(user_id, {"operation_id": "op-9"})
    second = await stream.__anext__()
    assert "op-9" in second
    await stream.aclose()
    assert subscriber_count(user_id) == 0


async def _promote_superadmin(session: AsyncSession, email: str) -> None:
    from app.rbac.principal import bump_version

    await session.execute(
        sa.text("UPDATE users SET is_superadmin = true WHERE email = :e"), {"e": email}
    )
    await session.commit()
    bump_version()


async def test_admin_users_flow_over_http(session: AsyncSession, issuer_url: str) -> None:
    from app.main import create_app
    from app.rbac.catalogue import sync_catalogue

    await sync_catalogue(session)
    await session.commit()
    app = create_app(_test_settings(issuer_url))  # type: ignore[arg-type]
    _override_session(app, make_session_factory())
    async with await _client_for(app) as admin:
        await _login(admin, session, "site-admin")
        denied = await admin.get("/api/v1/users")
        assert denied.status_code == 403
        await _promote_superadmin(session, "site-admin@example.com")
        listed = await admin.get("/api/v1/users")
        assert listed.status_code == 200
        assert any(row["email"] == "site-admin@example.com" for row in listed.json())
        token = await _csrf(admin)
        headers = {"X-CSRF-Token": token}
        async with await _client_for(app) as other:
            await _login(other, session, "victim-subject")
        victim_id = (
            await session.execute(
                sa.text("SELECT id FROM users WHERE email = 'victim-subject@example.com'")
            )
        ).scalar_one()
        banned = await admin.post(
            f"/api/v1/users/{victim_id}/ban", json={"reason": "spam"}, headers=headers
        )
        assert banned.status_code == 200, banned.text
        start = await admin.get("/api/v1/auth/login", params={"provider": "google"})
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
        stored = (
            await session.execute(
                sa.text("SELECT nonce, code_verifier FROM auth_states WHERE state = :s"),
                {"s": state},
            )
        ).one()
        code = register_code("victim-subject", "test-client-id", stored[0])
        stored_verifier_slot(code)["code_verifier"] = stored[1]
        async with await _client_for(app) as banned_client:
            refused = await banned_client.get(
                "/api/v1/auth/callback", params={"state": state, "code": code}
            )
            assert refused.status_code == 401
        deleted = await admin.delete(f"/api/v1/users/{victim_id}?legacy=true", headers=headers)
        assert deleted.status_code == 200, deleted.text


async def test_invite_create_revoke_over_http(session: AsyncSession, issuer_url: str) -> None:
    from app.main import create_app
    from app.rbac.catalogue import sync_catalogue

    await sync_catalogue(session)
    await session.commit()
    app = create_app(_test_settings(issuer_url))  # type: ignore[arg-type]
    _override_session(app, make_session_factory())
    async with await _client_for(app) as admin:
        await _login(admin, session, "invite-admin")
        await _promote_superadmin(session, "invite-admin@example.com")
        token = await _csrf(admin)
        headers = {"X-CSRF-Token": token}
        created = await admin.post(
            "/api/v1/invites",
            json={"course_tags": ["course-q"], "max_uses": 2},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        invite_token = created.json()["token"]
        revoked = await admin.post(f"/api/v1/invites/{invite_token}/revoke", headers=headers)
        assert revoked.status_code == 200
        answer = await admin.get(f"/api/v1/invites/{invite_token}/accept")
        assert answer.status_code == 409


async def test_role_update_delete_assign_over_http(session: AsyncSession, issuer_url: str) -> None:
    from app.main import create_app
    from app.rbac.catalogue import sync_catalogue

    await sync_catalogue(session)
    await session.commit()
    app = create_app(_test_settings(issuer_url))  # type: ignore[arg-type]
    _override_session(app, make_session_factory())
    async with await _client_for(app) as admin:
        await _login(admin, session, "roles-admin")
        await _promote_superadmin(session, "roles-admin@example.com")
        token = await _csrf(admin)
        headers = {"X-CSRF-Token": token}
        created = await admin.post(
            "/api/v1/roles",
            json={"name": "tutors", "permission_codes": ["section.courses"]},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        updated = await admin.put(
            "/api/v1/roles/tutors",
            json={"permission_codes": ["section.courses", "section.learners"]},
            headers=headers,
        )
        assert updated.status_code == 200, updated.text
        listed = await admin.get("/api/v1/roles")
        tutors = next(role for role in listed.json() if role["name"] == "tutors")
        assert tutors["permission_codes"] == ["section.courses", "section.learners"]
        tutor_id = (
            await session.execute(
                sa.text("INSERT INTO users (email, display_name) VALUES (:e, 'T') RETURNING id"),
                {"e": unique_email("tutor")},
            )
        ).scalar_one()
        await session.commit()
        assigned = await admin.post(
            "/api/v1/roles/tutors/assign",
            json={"user_id": str(tutor_id), "scope_tags": ["course-t"]},
            headers=headers,
        )
        assert assigned.status_code == 200, assigned.text
        unassigned = await admin.post(
            "/api/v1/roles/tutors/revoke",
            json={"user_id": str(tutor_id)},
            headers=headers,
        )
        assert unassigned.status_code == 200, unassigned.text
        deleted = await admin.delete("/api/v1/roles/tutors", headers=headers)
        assert deleted.status_code == 204, deleted.text


def test_sse_stream_delivers_operation_event(issuer_url: str) -> None:
    import threading
    import time

    from fastapi.testclient import TestClient

    from app.main import create_app

    # Lifespan startup targets the process database; tests sync explicitly.
    app = create_app(_test_settings(issuer_url), sync_on_startup=False)  # type: ignore[arg-type]
    _override_session(app, make_session_factory())
    with TestClient(app) as client:
        portal = client.portal

        async def _setup() -> uuid.UUID:
            from app.rbac.catalogue import sync_catalogue

            factory = make_session_factory()
            async with factory() as setup_session:
                await truncate_all(setup_session)
                await sync_catalogue(setup_session)
                await setup_session.commit()
            return uuid.uuid4()

        portal.call(_setup)

        start = client.get(
            "/api/v1/auth/login", params={"provider": "google"}, follow_redirects=False
        )
        assert start.status_code == 307
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]

        async def _nonce() -> tuple[str, str]:
            factory = make_session_factory()
            async with factory() as nonce_session:
                stored = (
                    await nonce_session.execute(
                        sa.text("SELECT nonce, code_verifier FROM auth_states WHERE state = :s"),
                        {"s": state},
                    )
                ).one()
                return str(stored[0]), str(stored[1])

        nonce, verifier = portal.call(_nonce)
        code = register_code("sse-user", "test-client-id", nonce)
        stored_verifier_slot(code)["code_verifier"] = verifier
        done = client.get("/api/v1/auth/callback", params={"state": state, "code": code})
        assert done.status_code == 200, done.text

        async def _user_id() -> uuid.UUID:
            factory = make_session_factory()
            async with factory() as user_session:
                found = (
                    await user_session.execute(
                        sa.text("SELECT id FROM users WHERE email = 'sse-user@example.com'")
                    )
                ).scalar_one()
                return uuid.UUID(str(found))

        user_id = portal.call(_user_id)
        received: list[str] = []

        def _read() -> None:
            with client.stream("GET", "/api/v1/events") as stream:
                assert stream.status_code == 200
                assert "text/event-stream" in stream.headers["content-type"]
                for line in stream.iter_lines():
                    if line.startswith("data:"):
                        received.append(line)
                        return

        reader = threading.Thread(target=_read, daemon=True)
        reader.start()
        time.sleep(0.5)

        async def _publish() -> None:
            from app.services.events import publish_event

            await publish_event(user_id, {"operation_id": "op-1", "status": "done"})

        portal.call(_publish)
        reader.join(timeout=10)
        assert received and "op-1" in received[0]
