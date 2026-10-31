"""T02b probes: addendum 14-18, LOW hardening, denied-actor mutants."""

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


def _settings(issuer_url: str, **extra: object) -> object:
    from app.config import Settings

    return Settings(
        database_url=test_database_url(),
        google_oauth_client_id="test-client-id",
        google_oauth_client_secret="test-client-secret",  # type: ignore[arg-type]
        session_secure_cookie=False,
        session_ttl_s=3600,
        oidc_issuer=issuer_url,
        oidc_redirect_url="http://test/api/v1/auth/callback",
        superadmin_emails="",
        **extra,  # type: ignore[arg-type]
    )


def _override(app: object) -> None:
    from app import db as db_module

    async def _test_session():  # type: ignore[no-untyped-def]
        sess = make_session_factory()()  # type: ignore[operator]
        try:
            yield sess
            await sess.commit()
        except Exception:
            await sess.rollback()
            raise
        finally:
            await sess.close()

    app.dependency_overrides[db_module.get_session] = _test_session  # type: ignore[attr-defined]


async def _login(client: httpx.AsyncClient, session: AsyncSession, subject: str) -> None:
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
    done = await client.get(
        "/api/v1/auth/callback", params={"state": state, "code": code}, follow_redirects=False
    )
    assert done.status_code == 303, done.text


async def _csrf(client: httpx.AsyncClient) -> str:
    response = await client.get("/api/v1/auth/csrf")
    assert response.status_code == 200
    return response.json()["csrf_token"]  # type: ignore[no-any-return]


async def _promote(session: AsyncSession, email: str) -> None:
    await session.execute(
        sa.text("UPDATE users SET is_superadmin = true WHERE email = :e"), {"e": email}
    )
    await session.commit()


async def test_callback_redirects_to_relative_next_url(
    session: AsyncSession, issuer_url: str
) -> None:
    """Item 14: the callback redirects the browser, defaulting to /."""
    from app.main import create_app
    from app.rbac.catalogue import sync_catalogue

    await sync_catalogue(session)
    await session.commit()
    app = create_app(_settings(issuer_url))  # type: ignore[arg-type]
    _override(app)
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        start = await client.get(
            "/api/v1/auth/login", params={"provider": "google", "next": "/courses/abc"}
        )
        assert start.status_code == 307
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
        stored = (
            await session.execute(
                sa.text("SELECT nonce, code_verifier FROM auth_states WHERE state = :s"),
                {"s": state},
            )
        ).one()
        code = register_code("redirect-user", "test-client-id", stored[0])
        stored_verifier_slot(code)["code_verifier"] = stored[1]
        done = await client.get(
            "/api/v1/auth/callback",
            params={"state": state, "code": code},
            follow_redirects=False,
        )
        assert done.status_code == 303
        assert done.headers["location"] == "/courses/abc"

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as fresh:
            start2 = await fresh.get("/api/v1/auth/login", params={"provider": "google"})
            state2 = parse_qs(urlparse(start2.headers["location"]).query)["state"][0]
            stored2 = (
                await session.execute(
                    sa.text("SELECT nonce, code_verifier FROM auth_states WHERE state = :s"),
                    {"s": state2},
                )
            ).one()
            code2 = register_code("redirect-user", "test-client-id", stored2[0])
            stored_verifier_slot(code2)["code_verifier"] = stored2[1]
            done2 = await fresh.get(
                "/api/v1/auth/callback",
                params={"state": state2, "code": code2},
                follow_redirects=False,
            )
            assert done2.status_code == 303
            assert done2.headers["location"] == "/"


async def test_me_reports_roles_with_scope_and_revoke_route(
    session: AsyncSession, issuer_url: str
) -> None:
    """Item 15: /me carries roles with scope tags; a grant revokes by route."""
    from app.main import create_app
    from app.rbac.catalogue import sync_catalogue
    from app.rbac.roles import assign_role, create_role

    await sync_catalogue(session)
    await session.commit()
    app = create_app(_settings(issuer_url))  # type: ignore[arg-type]
    _override(app)
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, session, "roles-user")
        await _promote(session, "roles-user@example.com")
        user_id = (
            await session.execute(
                sa.text("SELECT id FROM users WHERE email = 'roles-user@example.com'")
            )
        ).scalar_one()
        await create_role(session, name="tutors", permission_codes=["section.courses"], actor=None)
        await assign_role(
            session, uuid.UUID(str(user_id)), "tutors", scope_tags=["course-t"], actor=None
        )
        await session.commit()
        me = await client.get("/api/v1/auth/me")
        assert me.status_code == 200
        body = me.json()
        assert body["roles"] == ["tutors"]
        users = await client.get("/api/v1/users")
        assert users.status_code == 200
        entry = next(row for row in users.json() if row["email"] == "roles-user@example.com")
        assert entry["roles"] == [{"name": "tutors", "scope_tags": ["course-t"]}]
        token = await _csrf(client)
        headers = {"X-CSRF-Token": token}
        revoked = await client.delete(f"/api/v1/users/{user_id}/roles/tutors", headers=headers)
        assert revoked.status_code == 200, revoked.text
        me2 = await client.get("/api/v1/auth/me")
        assert me2.json()["roles"] == []


async def test_permissions_catalogue_route(session: AsyncSession, issuer_url: str) -> None:
    """Item 16: the roles editor reads the catalogue with categories."""
    from app.main import create_app
    from app.rbac.catalogue import CATALOGUE, sync_catalogue

    await sync_catalogue(session)
    await session.commit()
    app = create_app(_settings(issuer_url))  # type: ignore[arg-type]
    _override(app)
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, session, "perm-user")
        denied = await client.get("/api/v1/permissions")
        assert denied.status_code == 403
        await _promote(session, "perm-user@example.com")
        answer = await client.get("/api/v1/permissions")
        assert answer.status_code == 200
        codes = {entry["code"] for entry in answer.json()}
        assert {entry.code for entry in CATALOGUE} <= codes
        assert all(entry["category"] for entry in answer.json())


async def test_notifications_and_invites_list_routes(
    session: AsyncSession, issuer_url: str
) -> None:
    """Item 17: the bell and the invites screen survive a reload."""
    import json as json_mod

    from app.main import create_app
    from app.rbac.catalogue import sync_catalogue

    await sync_catalogue(session)
    await session.commit()
    app = create_app(_settings(issuer_url))  # type: ignore[arg-type]
    _override(app)
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, session, "bell-user")
        await _promote(session, "bell-user@example.com")
        user_id = (
            await session.execute(
                sa.text("SELECT id FROM users WHERE email = 'bell-user@example.com'")
            )
        ).scalar_one()
        for number in range(3):
            await session.execute(
                sa.text(
                    "INSERT INTO notifications (user_id, kind, payload, created_at)"
                    " VALUES (:u, 'operation.done', CAST(:p AS jsonb),"
                    " now() - make_interval(secs => :skew))"
                ),
                {"u": str(user_id), "p": json_mod.dumps({"n": number}), "skew": 10 - number},
            )
        await session.execute(
            sa.text("UPDATE notifications SET read_at = now() WHERE payload = '{\"n\": 0}'")
        )
        await session.commit()
        notes = await client.get("/api/v1/notifications")
        assert notes.status_code == 200
        kinds = notes.json()
        assert len(kinds) == 3
        assert [note["payload"]["n"] for note in kinds] == [2, 1, 0]
        assert [note["read"] for note in kinds] == [False, False, True]
        token = await _csrf(client)
        headers = {"X-CSRF-Token": token}
        created = await client.post(
            "/api/v1/invites", json={"course_tags": ["course-n"]}, headers=headers
        )
        assert created.status_code == 201, created.text
        listed = await client.get("/api/v1/invites")
        assert listed.status_code == 200
        assert len(listed.json()) == 1
        assert listed.json()[0]["course_tags"] == ["course-n"]
        first_id = kinds[0]["id"]
        marked = await client.post(f"/api/v1/notifications/{first_id}/read", headers=headers)
        assert marked.status_code == 200
        again = await client.get("/api/v1/notifications")
        assert next(note for note in again.json() if note["id"] == first_id)["read"] is True


async def test_cli_worker_completes_operation(session: AsyncSession, issuer_url: str) -> None:
    """Item 9/18: the procrastinate worker one-shot finishes an operation."""
    from conftest import test_database_url

    from app.rbac.catalogue import sync_catalogue
    from app.rbac.principal import load_principal
    from app.rbac.roles import assign_role, create_role
    from app.services.operations import submit_echo

    await sync_catalogue(session)
    await create_role(session, name="operator", permission_codes=["operation.run"], actor=None)
    row = (
        await session.execute(
            sa.text("INSERT INTO users (email, display_name) VALUES (:e, 'W') RETURNING id"),
            {"e": unique_email("worker")},
        )
    ).scalar_one()
    user_id = uuid.UUID(str(row))
    await assign_role(session, user_id, "operator", scope_tags=[], actor=None)
    await session.commit()
    actor = await load_principal(session, user_id)
    url = test_database_url()
    operation_id = await submit_echo(
        session, actor=actor, payload={"message": "via-worker"}, defer=None
    )
    await session.commit()

    from procrastinate import App, PsycopgConnector

    from app.tasks.jobs import run_operation_job
    from app.tasks.worker import psycopg_dsn as _dsn

    test_app = App(connector=PsycopgConnector(conninfo=_dsn(url)))
    test_task = test_app.task(queue="operations", name="operations.run")(run_operation_job)
    seen: list[dict[str, object]] = []
    from app.services import events as events_mod

    real_publish = events_mod.publish_event

    async def _spy(target: uuid.UUID, payload: dict[str, object]) -> None:
        seen.append({"user_id": str(target), **payload})
        await real_publish(target, payload)

    import asyncio as asyncio_mod

    from app.config import Settings as _AppSettings
    from app.services import operations as operations_mod

    real_settings = operations_mod.get_settings
    operations_mod.get_settings = lambda: _AppSettings(database_url=url)  # type: ignore[assignment]
    events_mod.publish_event = _spy  # type: ignore[method-assign]
    try:
        async with test_app.open_async():
            await test_task.defer_async(operation_id=str(operation_id))
            worker = asyncio_mod.ensure_future(test_app.run_worker_async(queues=["operations"]))
            try:
                async with make_session_factory()() as poll:
                    for _ in range(200):
                        status = (
                            await poll.execute(
                                sa.text("SELECT status FROM operations WHERE id = :o"),
                                {"o": str(operation_id)},
                            )
                        ).scalar_one()
                        if status in ("done", "failed"):
                            break
                        await poll.rollback()
                        await asyncio_mod.sleep(0.1)
            finally:
                worker.cancel()
                try:
                    await worker
                except BaseException:
                    pass
    finally:
        events_mod.publish_event = real_publish
        operations_mod.get_settings = real_settings  # type: ignore[method-assign]
    async with make_session_factory()() as check:
        status, result = (
            await check.execute(
                sa.text("SELECT status, result FROM operations WHERE id = :o"),
                {"o": str(operation_id)},
            )
        ).one()
        assert status == "done"
        assert result["echo"] == "via-worker"
    assert seen and seen[0].get("operation_id") == str(operation_id)
    assert str(seen[0].get("user_id")) == str(user_id)


async def test_issuer_bare_form_accepted(session: AsyncSession, issuer_url: str) -> None:
    """LOW: tokens carrying the bare Google issuer form verify."""
    from app.auth.oidc import GoogleProvider, _issuer_candidates

    assert _issuer_candidates("https://accounts.google.com") == [
        "accounts.google.com",
        "https://accounts.google.com",
    ]
    provider = GoogleProvider(
        **{  # type: ignore[arg-type]
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
            "redirect_url": "http://localhost:8000/api/v1/auth/callback",
            "issuer": issuer_url,
            "allowed_domains": (),
        }
    )
    from t02_issuer import _mint_id_token

    token = _mint_id_token(
        issuer_url.replace("http://", ""),
        "test-client-id",
        "nonce-bare",
        "bare-user",
        "",
    )
    identity = await provider.verify_id_token(token, nonce="nonce-bare")
    assert identity.email == "bare-user@example.com"


async def test_azp_mismatch_rejected(session: AsyncSession, issuer_url: str) -> None:
    """LOW: a foreign authorized party is refused."""
    from app.auth.oidc import GoogleProvider
    from app.errors import AuthError
    from app.services.auth import begin_login, finish_login

    provider = GoogleProvider(
        **{  # type: ignore[arg-type]
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
            "redirect_url": "http://localhost:8000/api/v1/auth/callback",
            "issuer": issuer_url,
            "allowed_domains": (),
        }
    )
    _, state = await begin_login(session, provider)
    await session.commit()
    stored = (
        await session.execute(
            sa.text("SELECT nonce, code_verifier FROM auth_states WHERE state = :s"),
            {"s": state},
        )
    ).one()
    code = register_code("azp-user", "test-client-id", stored[0], azp="foreign-client")
    stored_verifier_slot(code)["code_verifier"] = stored[1]
    with pytest.raises(AuthError):
        await finish_login(session, provider, state=state, code=code)
    await session.rollback()


async def test_expiry_leeway_sixty_seconds(session: AsyncSession, issuer_url: str) -> None:
    """LOW: ±60 s clock leeway accepts a 30 s-old expiry, not a 120 s one."""
    from app.auth.oidc import GoogleProvider
    from app.errors import AuthError
    from app.services.auth import begin_login, finish_login

    provider = GoogleProvider(
        **{  # type: ignore[arg-type]
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
            "redirect_url": "http://localhost:8000/api/v1/auth/callback",
            "issuer": issuer_url,
            "allowed_domains": (),
        }
    )
    for lifetime_s, ok in ((-30, True), (-120, False)):
        _, state = await begin_login(session, provider)
        await session.commit()
        stored = (
            await session.execute(
                sa.text("SELECT nonce, code_verifier FROM auth_states WHERE state = :s"),
                {"s": state},
            )
        ).one()
        code = register_code(
            f"leeway-{lifetime_s}", "test-client-id", stored[0], lifetime_s=lifetime_s
        )
        stored_verifier_slot(code)["code_verifier"] = stored[1]
        if ok:
            user, _ = await finish_login(session, provider, state=state, code=code)
            await session.commit()
            assert user.email == f"leeway-{lifetime_s}@example.com"
        else:
            with pytest.raises(AuthError):
                await finish_login(session, provider, state=state, code=code)
            await session.rollback()


async def test_malformed_uuid_answers_404(session: AsyncSession, issuer_url: str) -> None:
    """LOW: a malformed UUID in a path answers 404, never 500."""
    from app.main import create_app
    from app.rbac.catalogue import sync_catalogue

    await sync_catalogue(session)
    await session.commit()
    app = create_app(_settings(issuer_url))  # type: ignore[arg-type]
    _override(app)
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, session, "uuid-user")
        await _promote(session, "uuid-user@example.com")
        token = await _csrf(client)
        headers = {"X-CSRF-Token": token}
        answer = await client.delete("/api/v1/users/not-a-uuid", headers=headers)
        assert answer.status_code == 404
        missing_op = await client.get("/api/v1/operations/not-a-uuid")
        assert missing_op.status_code == 404


async def test_redact_knows_google_oauth_secrets() -> None:
    """LOW: redact masks Google OAuth client secrets."""
    from app.redact import redact

    prefix = "GOCSP" + "X-"
    assert prefix not in redact("secret " + prefix + "abcDEF1234567890 tail")


async def test_session_cookie_signature_enforced() -> None:
    """LOW: the session secret signs cookies; tampering is refused."""
    from app.auth.session import cookie_value_for, parse_session_cookie

    value = cookie_value_for("raw-token", "s3cr3t")
    assert value != "raw-token"
    assert parse_session_cookie(value, secret="s3cr3t") == "raw-token"
    assert parse_session_cookie(value + "x", secret="s3cr3t") is None
    assert parse_session_cookie("raw-token", secret="s3cr3t") is None
    assert parse_session_cookie("raw-token", secret=None) == "raw-token"


async def test_crud_matrix_verbs_enforced(session: AsyncSession) -> None:
    """LOW: the CRUD matrix accepts verbs only."""
    from app.errors import ValidationError
    from app.rbac.catalogue import sync_catalogue
    from app.rbac.roles import create_role

    await sync_catalogue(session)
    await session.commit()
    with pytest.raises(ValidationError):
        await create_role(
            session,
            name="weird",
            permission_codes=["section.courses"],
            crud={"course": ["fly"]},
            actor=None,
        )
    await session.rollback()
    await create_role(
        session,
        name="sane",
        permission_codes=["section.courses"],
        crud={"course": ["read", "update"]},
        actor=None,
    )
    await session.commit()


async def test_role_edit_holder_cannot_empty_administrator(session: AsyncSession) -> None:
    """LOW: nobody empties the administrator role, not even a superadmin."""
    from app.errors import ValidationError
    from app.rbac.catalogue import ensure_admin_role, sync_catalogue
    from app.rbac.principal import Principal
    from app.rbac.roles import update_role

    await sync_catalogue(session)
    await ensure_admin_role(session)
    await session.commit()
    admin_id = uuid.uuid4()
    actor = Principal(
        user_id=admin_id,
        email="root@example.com",
        roles=("administrator",),
        permissions=frozenset(),
        scope_tags=(),
        is_superadmin=True,
    )
    with pytest.raises(ValidationError):
        await update_role(session, name="administrator", permission_codes=[], actor=actor)
    await session.rollback()


async def test_submit_idempotency_key(session: AsyncSession) -> None:
    """LOW: a repeated submit with the same key returns the first operation."""
    from app.rbac.principal import Principal
    from app.services.operations import submit_echo

    user_id = uuid.uuid4()
    actor = Principal(
        user_id=user_id,
        email="idem@example.com",
        roles=(),
        permissions=frozenset({"operation.run"}),
        scope_tags=(),
        is_superadmin=False,
    )
    first = await submit_echo(
        session, actor=actor, payload={"message": "hi"}, defer=None, idempotency_key="k-1"
    )
    second = await submit_echo(
        session, actor=actor, payload={"message": "hi"}, defer=None, idempotency_key="k-1"
    )
    assert first == second
    await session.commit()
    count = (
        await session.execute(
            sa.text("SELECT count(*) FROM operations WHERE actor_id = :u"),
            {"u": str(user_id)},
        )
    ).scalar_one()
    assert count == 1


async def test_sse_rechecks_session_and_caps_streams(
    session: AsyncSession, issuer_url: str
) -> None:
    """LOW: dead sessions end the stream; a sixth stream is refused."""
    import uuid as uuid_mod

    from conftest import test_database_url

    from app.auth.session import create_session, hash_token
    from app.routes.ops import MAX_STREAMS_PER_USER, sse_stream
    from app.services.events import publish_event, subscriber_count

    user_id = (
        await session.execute(
            sa.text("INSERT INTO users (email, display_name) VALUES (:e, 'S') RETURNING id"),
            {"e": unique_email("sse")},
        )
    ).scalar_one()
    user_id = uuid.UUID(str(user_id))
    token, _ = await create_session(session, user_id)
    await session.commit()
    digest = hash_token(token)
    url = test_database_url()
    stream = sse_stream(
        user_id, ping_s=0.05, session_token_hash=digest, database_url=url, recheck_s=0.05
    )
    assert await stream.__anext__() == ": ping\n\n"
    await session.execute(sa.text("DELETE FROM sessions"))
    await session.commit()
    await publish_event(uuid_mod.uuid4(), {"operation_id": "elsewhere"})
    rest = []
    try:
        while len(rest) < 30:
            try:
                rest.append(await stream.__anext__())
            except StopAsyncIteration:
                break
    finally:
        await stream.aclose()
    assert subscriber_count(user_id) == 0

    from app.main import create_app
    from app.rbac.catalogue import sync_catalogue

    await sync_catalogue(session)
    await session.commit()
    app = create_app(_settings(issuer_url))  # type: ignore[arg-type]
    _override(app)
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, session, "stream-user")
        row = (
            await session.execute(
                sa.text("SELECT id FROM users WHERE email = 'stream-user@example.com'")
            )
        ).scalar_one()
        holder = uuid.UUID(str(row))
        keep = [sse_stream(holder, ping_s=0.05) for _ in range(MAX_STREAMS_PER_USER)]
        for generator in keep:
            await generator.__anext__()
        try:
            capped = await client.get("/api/v1/events")
            assert capped.status_code == 429
        finally:
            for generator in keep:
                await generator.aclose()
        assert subscriber_count(holder) == 0
