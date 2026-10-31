"""T02b security probes, MEDIUM items 5-13 (fail before the fix, green after)."""

from __future__ import annotations

import asyncio
import uuid

import pytest
import sqlalchemy as sa
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


def _google_kwargs(issuer_url: str, **extra: object) -> dict[str, object]:
    return {
        "client_id": "test-client-id",
        "client_secret": "test-client-secret",
        "redirect_url": "http://localhost:8000/api/v1/auth/callback",
        "issuer": issuer_url,
        "allowed_domains": (),
        **extra,
    }


async def _user(session: AsyncSession, email: str = "") -> uuid.UUID:
    row = (
        await session.execute(
            sa.text("INSERT INTO users (email, display_name) VALUES (:e, 'N') RETURNING id"),
            {"e": email or unique_email()},
        )
    ).one()
    await session.commit()
    return uuid.UUID(str(row[0]))


def _superadmin(user_id: uuid.UUID) -> object:
    from app.rbac.principal import Principal

    return Principal(
        user_id=user_id,
        email="admin@example.com",
        roles=("administrator",),
        permissions=frozenset(),
        scope_tags=(),
        is_superadmin=True,
    )


async def test_login_state_expires_after_ten_minutes(
    session: AsyncSession, issuer_url: str
) -> None:
    """Item 5: a stale login state is refused."""
    from app.auth.oidc import GoogleProvider
    from app.errors import AuthError
    from app.services.auth import begin_login, finish_login

    provider = GoogleProvider(**_google_kwargs(issuer_url))  # type: ignore[arg-type]
    _, state = await begin_login(session, provider)
    await session.commit()
    await session.execute(
        sa.text("UPDATE auth_states SET created_at = now() - interval '11 minutes'"),
    )
    await session.commit()
    stored = (
        await session.execute(
            sa.text("SELECT nonce, code_verifier FROM auth_states WHERE state = :s"),
            {"s": state},
        )
    ).one()
    code = register_code("t02b-stale", "test-client-id", stored[0])
    stored_verifier_slot(code)["code_verifier"] = stored[1]
    with pytest.raises(AuthError):
        await finish_login(session, provider, state=state, code=code)


async def test_login_state_bound_to_pre_login_session(
    session: AsyncSession, issuer_url: str
) -> None:
    """Item 5: a state started in one session fails in another."""
    from app.auth.oidc import GoogleProvider
    from app.auth.session import create_session, session_id_for_token
    from app.errors import AuthError
    from app.services.auth import begin_login, finish_login

    provider = GoogleProvider(**_google_kwargs(issuer_url))  # type: ignore[arg-type]
    first_token, _ = await create_session(session, None)
    second_token, _ = await create_session(session, None)
    await session.commit()
    first_id = await session_id_for_token(session, first_token)
    second_id = await session_id_for_token(session, second_token)
    assert first_id and second_id and first_id != second_id
    _, state = await begin_login(session, provider, session_id=first_id)
    await session.commit()
    stored = (
        await session.execute(
            sa.text("SELECT nonce, code_verifier FROM auth_states WHERE state = :s"),
            {"s": state},
        )
    ).one()
    code = register_code("t02b-bound", "test-client-id", stored[0])
    stored_verifier_slot(code)["code_verifier"] = stored[1]
    with pytest.raises(AuthError):
        await finish_login(session, provider, state=state, code=code, expected_session_id=second_id)
    await session.rollback()


async def test_absolute_next_url_rejected_at_login_start(
    session: AsyncSession, issuer_url: str
) -> None:
    """next_url must be relative; absolute URLs are refused, not stored."""
    from app.auth.oidc import GoogleProvider
    from app.errors import ValidationError
    from app.services.auth import begin_login

    provider = GoogleProvider(**_google_kwargs(issuer_url))  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        await begin_login(session, provider, next_url="https://evil.example/phish")
    await session.rollback()


async def test_non_superadmin_cannot_ban_superadmin(session: AsyncSession) -> None:
    """Item 6: a non-superadmin never acts on a superadmin account."""
    from app.errors import ForbiddenError
    from app.rbac.principal import Principal
    from app.services.users import ban_user

    chief = await _user(session, unique_email("chief"))
    await session.execute(
        sa.text("UPDATE users SET is_superadmin = true WHERE id = :u"), {"u": str(chief)}
    )
    await session.commit()
    staff_id = await _user(session, unique_email("staff"))
    staff = Principal(
        user_id=staff_id,
        email="staff@example.com",
        roles=(),
        permissions=frozenset({"user.ban"}),
        scope_tags=(),
        is_superadmin=False,
    )
    with pytest.raises(ForbiddenError):
        await ban_user(session, actor=staff, user_id=chief, reason="coup")
    await session.rollback()


async def test_invite_tags_outside_creator_scope_refused(session: AsyncSession) -> None:
    """Item 6: a tag outside the creator's scope cannot be invited."""
    from app.errors import NotFoundError
    from app.rbac.catalogue import sync_catalogue
    from app.rbac.roles import assign_role, create_role
    from app.services.invites import create_invite

    await sync_catalogue(session)
    await create_role(session, name="inviter", permission_codes=["invite.create"], actor=None)
    await session.commit()
    staff_id = await _user(session, unique_email("staff"))
    await assign_role(session, staff_id, "inviter", scope_tags=["course-a"], actor=None)
    await session.commit()
    from app.rbac.principal import load_principal

    staff = await load_principal(session, staff_id)
    with pytest.raises(NotFoundError):
        await create_invite(session, actor=staff, course_tags=["course-b"])
    await session.rollback()


async def test_operation_view_scope(session: AsyncSession) -> None:
    """Item 6: another user's operation answers 404 without operation.view."""
    from app.errors import NotFoundError
    from app.rbac.principal import Principal
    from app.services.operations import get_operation, submit_echo

    owner_id = await _user(session)
    owner = Principal(
        user_id=owner_id,
        email="o@example.com",
        roles=(),
        permissions=frozenset({"operation.run"}),
        scope_tags=(),
        is_superadmin=False,
    )
    stranger_id = await _user(session)
    stranger = Principal(
        user_id=stranger_id,
        email="s@example.com",
        roles=(),
        permissions=frozenset(),
        scope_tags=(),
        is_superadmin=False,
    )
    operation_id = await submit_echo(session, actor=owner, payload={"message": "hi"}, defer=None)
    await session.commit()
    with pytest.raises(NotFoundError):
        await get_operation(session, actor=stranger, operation_id=operation_id)
    viewer = Principal(
        user_id=stranger_id,
        email="s@example.com",
        roles=(),
        permissions=frozenset({"operation.view"}),
        scope_tags=(),
        is_superadmin=False,
    )
    found = await get_operation(session, actor=viewer, operation_id=operation_id)
    assert found["operation_id"] == str(operation_id)


async def test_invite_token_never_stored_in_plain_text(session: AsyncSession) -> None:
    """Item 7: sha256 only; audit carries the invite id, never the token."""
    from app.rbac.catalogue import sync_catalogue
    from app.services.invites import create_invite, hash_invite_token

    await sync_catalogue(session)
    await session.commit()
    admin = _superadmin(await _user(session, unique_email("admin")))
    raw = await create_invite(session, actor=admin, course_tags=["course-a"])  # type: ignore[arg-type]
    await session.commit()
    columns = (
        await session.execute(
            sa.text(
                "SELECT string_agg(value::text, ' ') FROM (SELECT token_hash AS value"
                " FROM invites UNION ALL SELECT entity_id FROM audit_log"
                " UNION ALL SELECT meta::text FROM audit_log) AS blobs"
            )
        )
    ).scalar_one() or ""
    assert raw not in columns
    assert hash_invite_token(raw) in columns
    invite_id = (await session.execute(sa.text("SELECT id FROM invites LIMIT 1"))).scalar_one()
    audit_entity = (
        await session.execute(
            sa.text("SELECT entity_id FROM audit_log WHERE action = 'invite.create' LIMIT 1")
        )
    ).scalar_one()
    assert str(audit_entity) == str(invite_id)


async def test_pending_invite_stored_hashed(session: AsyncSession) -> None:
    """Item 7: sessions.pending_invite_token holds the hash, not the token."""
    from app.auth.session import create_session, session_id_for_token, set_pending_invite

    token, _ = await create_session(session, None)
    await session.commit()
    session_id = await session_id_for_token(session, token)
    assert session_id is not None
    await set_pending_invite(session, session_id, "digest-hex-value")
    await session.commit()
    stored = (
        await session.execute(
            sa.text("SELECT pending_invite_token FROM sessions WHERE id = :sid"),
            {"sid": str(session_id)},
        )
    ).scalar_one()
    assert stored == "digest-hex-value"


async def test_worker_sees_ban_immediately(session: AsyncSession) -> None:
    """Item 8: banning after submit makes the worker fail the operation."""
    from conftest import test_database_url

    from app.rbac.catalogue import sync_catalogue
    from app.rbac.principal import load_principal
    from app.rbac.roles import assign_role, create_role
    from app.services.operations import execute_operation, submit_echo
    from app.services.users import ban_user

    await sync_catalogue(session)
    await create_role(session, name="operator", permission_codes=["operation.run"], actor=None)
    user_id = await _user(session)
    await assign_role(session, user_id, "operator", scope_tags=[], actor=None)
    await session.commit()
    actor = await load_principal(session, user_id)
    operation_id = await submit_echo(session, actor=actor, payload={"message": "hi"}, defer=None)
    await session.commit()
    admin = _superadmin(await _user(session, unique_email("admin")))
    await ban_user(session, actor=admin, user_id=user_id, reason="spam")  # type: ignore[arg-type]
    await session.commit()
    await execute_operation(operation_id, database_url=test_database_url())
    async with make_session_factory()() as check:
        status = (
            await check.execute(
                sa.text("SELECT status FROM operations WHERE id = :o"),
                {"o": str(operation_id)},
            )
        ).scalar_one()
        assert status == "failed"


async def test_worker_app_registers_operation_tasks() -> None:
    """Item 9: the compose worker app knows the operations.run task."""
    from app.tasks.jobs import app as jobs_app
    from app.tasks.worker import app as worker_app

    assert "operations.run" in set(worker_app.tasks)
    assert "operations.run" in set(jobs_app.tasks)


async def test_ban_then_delete_blocks_fresh_sign_in(session: AsyncSession, issuer_url: str) -> None:
    """Item 10: a ban survives deletion; the same identity cannot sign in fresh."""
    from app.auth.oidc import GoogleProvider
    from app.errors import AuthError
    from app.services.auth import begin_login, finish_login
    from app.services.users import ban_user, delete_user

    provider = GoogleProvider(**_google_kwargs(issuer_url))  # type: ignore[arg-type]
    _, state = await begin_login(session, provider)
    await session.commit()
    stored = (
        await session.execute(
            sa.text("SELECT nonce, code_verifier FROM auth_states WHERE state = :s"),
            {"s": state},
        )
    ).one()
    code = register_code("t02b-doomed", "test-client-id", stored[0])
    stored_verifier_slot(code)["code_verifier"] = stored[1]
    user, _ = await finish_login(session, provider, state=state, code=code)
    await session.commit()
    admin = _superadmin(await _user(session, unique_email("admin")))
    await ban_user(session, actor=admin, user_id=user.id, reason="spam")  # type: ignore[arg-type]
    await session.commit()
    await delete_user(session, actor=admin, user_id=user.id)  # type: ignore[arg-type]
    await session.commit()
    _, state2 = await begin_login(session, provider)
    await session.commit()
    stored2 = (
        await session.execute(
            sa.text("SELECT nonce, code_verifier FROM auth_states WHERE state = :s"),
            {"s": state2},
        )
    ).one()
    code2 = register_code("t02b-doomed", "test-client-id", stored2[0])
    stored_verifier_slot(code2)["code_verifier"] = stored2[1]
    with pytest.raises(AuthError):
        await finish_login(session, provider, state=state2, code=code2)


async def test_delete_heir_must_be_usable(session: AsyncSession) -> None:
    """Item 10: a banned or legacy heir is refused."""
    from app.errors import ValidationError
    from app.services.users import ban_user, delete_user

    owner = await _user(session)
    await session.execute(
        sa.text(
            "INSERT INTO invites (scope, created_by, token_hash)"
            " VALUES ('t02b-heir', :u, 't02b-h-heir')"
        ),
        {"u": str(owner)},
    )
    await session.commit()
    banned_heir = await _user(session)
    legacy_heir = await _user(session)
    admin = _superadmin(await _user(session, unique_email("admin")))
    await ban_user(session, actor=admin, user_id=banned_heir, reason="spam")  # type: ignore[arg-type]
    await session.execute(
        sa.text("UPDATE users SET is_legacy = true WHERE id = :u"), {"u": str(legacy_heir)}
    )
    await session.commit()
    with pytest.raises(ValidationError):
        await delete_user(session, actor=admin, user_id=owner, heir_id=banned_heir)  # type: ignore[arg-type]
    await session.rollback()
    with pytest.raises(ValidationError):
        await delete_user(session, actor=admin, user_id=owner, heir_id=legacy_heir)  # type: ignore[arg-type]
    await session.rollback()


async def test_legacy_holder_is_stripped(session: AsyncSession) -> None:
    """Item 10: legacy keeps materials but loses identities, roles, and self."""
    from app.errors import AuthError
    from app.rbac.catalogue import sync_catalogue
    from app.rbac.roles import assign_role, create_role
    from app.services.auth import refuse_if_unusable
    from app.services.users import delete_user

    await sync_catalogue(session)
    await create_role(session, name="keeper", permission_codes=["section.courses"], actor=None)
    owner = await _user(session)
    await session.execute(
        sa.text(
            "INSERT INTO identities (user_id, provider, subject, email)"
            " VALUES (:u, 'google', 'keep-sub', 'keep@example.com')"
        ),
        {"u": str(owner)},
    )
    await assign_role(session, owner, "keeper", scope_tags=["course-a"], actor=None)
    await session.execute(
        sa.text(
            "INSERT INTO invites (scope, created_by, token_hash)"
            " VALUES ('t02b-keep', :u, 't02b-h-keep')"
        ),
        {"u": str(owner)},
    )
    await session.commit()
    admin = _superadmin(await _user(session, unique_email("admin")))
    await delete_user(session, actor=admin, user_id=owner, legacy=True)  # type: ignore[arg-type]
    await session.commit()
    row = (
        await session.execute(
            sa.text(
                "SELECT email, display_name, avatar_url, is_legacy, is_superadmin"
                " FROM users WHERE id = :u"
            ),
            {"u": str(owner)},
        )
    ).one()
    assert row[0] != "keep@example.com" and "keep@example.com" not in row[0]
    assert row[1] == "" and row[2] == ""
    assert row[3] is True
    for table, column in (("identities", "user_id"), ("user_roles", "user_id")):
        count = (
            await session.execute(
                sa.text(f"SELECT count(*) FROM {table} WHERE {column} = :u"),
                {"u": str(owner)},
            )
        ).scalar_one()
        assert count == 0
    assert (
        await session.execute(sa.text("SELECT count(*) FROM invites WHERE scope = 't02b-keep'"))
    ).scalar_one() == 1
    with pytest.raises(AuthError):
        await refuse_if_unusable(session, owner)


async def test_audit_actor_survives_user_deletion(session: AsyncSession) -> None:
    """Item 10: audit rows keep their author after the author row is gone."""
    from app.services.users import delete_user

    doomed_admin = await _user(session, unique_email("doomed"))
    target = await _user(session)
    await session.commit()
    from app.rbac.principal import Principal

    doomed = Principal(
        user_id=doomed_admin,
        email="doomed@example.com",
        roles=(),
        permissions=frozenset({"user.delete"}),
        scope_tags=(),
        is_superadmin=True,
    )
    await delete_user(session, actor=doomed, user_id=target)
    await session.commit()
    admin = _superadmin(await _user(session, unique_email("admin")))
    await delete_user(session, actor=admin, user_id=doomed_admin)  # type: ignore[arg-type]
    await session.commit()
    rows = (
        await session.execute(
            sa.text("SELECT actor_id FROM audit_log WHERE entity_id = :e"),
            {"e": str(target)},
        )
    ).all()
    assert rows and all(row[0] is not None for row in rows)
    assert str(rows[0][0]) == str(doomed_admin)


async def test_delete_cancels_operations_and_moves_invites(session: AsyncSession) -> None:
    """Item 10: operations are cancelled, never reassigned; invites move to the heir."""
    from app.rbac.catalogue import sync_catalogue
    from app.rbac.principal import Principal
    from app.services.operations import submit_echo
    from app.services.users import delete_user

    await sync_catalogue(session)
    await session.commit()
    owner_id = await _user(session)
    heir_id = await _user(session)
    owner = Principal(
        user_id=owner_id,
        email="o@example.com",
        roles=(),
        permissions=frozenset({"operation.run"}),
        scope_tags=(),
        is_superadmin=False,
    )
    operation_id = await submit_echo(session, actor=owner, payload={"message": "hi"}, defer=None)
    await session.execute(
        sa.text(
            "INSERT INTO invites (scope, created_by, token_hash)"
            " VALUES ('t02b-move', :u, 't02b-h-move')"
        ),
        {"u": str(owner_id)},
    )
    await session.commit()
    admin = _superadmin(await _user(session, unique_email("admin")))
    await delete_user(session, actor=admin, user_id=owner_id, heir_id=heir_id)  # type: ignore[arg-type]
    await session.commit()
    status, kept_actor = (
        await session.execute(
            sa.text("SELECT status, actor_id FROM operations WHERE id = :o"),
            {"o": str(operation_id)},
        )
    ).one()
    assert status == "cancelled"
    assert str(kept_actor) == str(owner_id)
    moved = (
        await session.execute(sa.text("SELECT created_by FROM invites WHERE scope = 't02b-move'"))
    ).scalar_one()
    assert str(moved) == str(heir_id)


async def test_ban_revokes_invite_uses(session: AsyncSession) -> None:
    """Item 10: banning revokes the invite uses and invites of the account."""
    from app.rbac.catalogue import sync_catalogue
    from app.services.invites import accept_invite, create_invite, hash_invite_token
    from app.services.users import ban_user

    await sync_catalogue(session)
    await session.execute(sa.text("DELETE FROM role_permissions"))
    await session.execute(
        sa.text("INSERT INTO roles (name) VALUES ('student') ON CONFLICT DO NOTHING")
    )
    await session.commit()
    creator = await _user(session)
    student = await _user(session)
    from app.rbac.principal import Principal

    staff = Principal(
        user_id=creator,
        email="staff@example.com",
        roles=(),
        permissions=frozenset({"invite.create"}),
        scope_tags=(),
        is_superadmin=False,
    )
    raw = await create_invite(session, actor=staff, course_tags=["course-a"])
    await session.commit()
    await accept_invite(session, user_id=student, token_hash=hash_invite_token(raw))
    await session.commit()
    admin = _superadmin(await _user(session, unique_email("admin")))
    await ban_user(session, actor=admin, user_id=student, reason="spam")  # type: ignore[arg-type]
    await session.commit()
    uses = (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM invite_uses WHERE invite_id ="
                " (SELECT id FROM invites WHERE token_hash = :h)"
            ),
            {"h": hash_invite_token(raw)},
        )
    ).scalar_one()
    assert uses == 0
    await ban_user(session, actor=admin, user_id=creator, reason="spam")  # type: ignore[arg-type]
    await session.commit()
    revoked = (
        await session.execute(
            sa.text("SELECT revoked_at FROM invites WHERE token_hash = :h"),
            {"h": hash_invite_token(raw)},
        )
    ).scalar_one()
    assert revoked is not None


async def test_second_invite_unions_scope(session: AsyncSession) -> None:
    """Item 11: a second invite adds scope instead of replacing the first."""
    from app.rbac.catalogue import sync_catalogue
    from app.rbac.principal import load_principal
    from app.services.invites import accept_invite, create_invite, hash_invite_token

    await sync_catalogue(session)
    await session.execute(sa.text("DELETE FROM role_permissions"))
    await session.execute(
        sa.text("INSERT INTO roles (name) VALUES ('student') ON CONFLICT DO NOTHING")
    )
    await session.commit()
    creator = await _user(session)
    student = await _user(session)
    from app.rbac.principal import Principal

    staff = Principal(
        user_id=creator,
        email="staff@example.com",
        roles=(),
        permissions=frozenset({"invite.create"}),
        scope_tags=(),
        is_superadmin=False,
    )
    first = await create_invite(session, actor=staff, course_tags=["course-a"])
    second = await create_invite(session, actor=staff, course_tags=["course-b"])
    await session.commit()
    await accept_invite(session, user_id=student, token_hash=hash_invite_token(first))
    await session.commit()
    await accept_invite(session, user_id=student, token_hash=hash_invite_token(second))
    await session.commit()
    principal = await load_principal(session, student)
    assert sorted(principal.scope_tags) == ["course-a", "course-b"]


async def test_session_absolute_expiry_ends_use(session: AsyncSession) -> None:
    """Item 12: the absolute expiry caps rolling sessions."""
    from app.auth.session import create_session, load_session

    token, _ = await create_session(session, None, ttl_s=3600, absolute_ttl_s=3600)
    await session.commit()
    await session.execute(
        sa.text("UPDATE sessions SET absolute_expires_at = now() - interval '1 second'"),
    )
    await session.commit()
    assert await load_session(session, token) is None


async def test_cross_origin_unsafe_request_refused(session: AsyncSession, issuer_url: str) -> None:
    """Item 12: an Origin from another host fails unsafe methods."""
    from urllib.parse import parse_qs, urlparse

    import httpx
    from conftest import test_database_url
    from t02_issuer import register_code as _register
    from t02_issuer import stored_verifier_slot as _slot

    from app.config import Settings
    from app.main import create_app
    from app.rbac.catalogue import sync_catalogue

    await sync_catalogue(session)
    await session.commit()
    settings = Settings(
        database_url=test_database_url(),
        google_oauth_client_id="test-client-id",
        google_oauth_client_secret="test-client-secret",  # type: ignore[arg-type]
        session_secure_cookie=False,
        session_ttl_s=3600,
        oidc_issuer=issuer_url,
        oidc_redirect_url="http://test/api/v1/auth/callback",
        superadmin_emails="",
    )
    app = create_app(settings)  # type: ignore[arg-type]
    from t02_helpers import make_session_factory as _factory

    from app import db as db_module

    async def _test_session():  # type: ignore[no-untyped-def]
        sess = _factory()()  # type: ignore[operator]
        try:
            yield sess
            await sess.commit()
        except Exception:
            await sess.rollback()
            raise
        finally:
            await sess.close()

    app.dependency_overrides[db_module.get_session] = _test_session
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        start = await client.get("/api/v1/auth/login", params={"provider": "google"})
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
        stored = (
            await session.execute(
                sa.text("SELECT nonce, code_verifier FROM auth_states WHERE state = :s"),
                {"s": state},
            )
        ).one()
        code = _register("origin-user", "test-client-id", stored[0])
        _slot(code)["code_verifier"] = stored[1]
        await client.get(
            "/api/v1/auth/callback", params={"state": state, "code": code}, follow_redirects=False
        )
        csrf = (await client.get("/api/v1/auth/csrf")).json()["csrf_token"]
        forged = await client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": csrf, "Origin": "https://evil.example"},
        )
        assert forged.status_code == 403


async def test_secure_cookie_outside_local(issuer_url: str) -> None:
    """Item 12: outside local the cookie is __Host- prefixed and Secure."""
    import httpx
    from conftest import test_database_url
    from t02_helpers import make_session_factory as _factory

    from app.config import Settings
    from app.main import create_app

    settings = Settings(
        app_env="test",
        database_url=test_database_url(),
        google_oauth_client_id="test-client-id",
        google_oauth_client_secret="s3cr3t",  # type: ignore[arg-type]
        session_secret_key="another-secret",  # type: ignore[arg-type]
        session_ttl_s=3600,
        oidc_issuer=issuer_url,
        oidc_redirect_url="http://test/api/v1/auth/callback",
        superadmin_emails="",
    )
    app = create_app(settings)  # type: ignore[arg-type]
    from app import db as db_module

    async def _test_session():  # type: ignore[no-untyped-def]
        sess = _factory()()  # type: ignore[operator]
        try:
            yield sess
            await sess.commit()
        except Exception:
            await sess.rollback()
            raise
        finally:
            await sess.close()

    app.dependency_overrides[db_module.get_session] = _test_session
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        start = await client.get("/api/v1/auth/login", params={"provider": "google"})
        assert start.status_code == 307
        raw = start.headers.get("set-cookie", "")
        assert "__Host-muse_session" in raw
        assert "Secure" in raw


async def test_invite_accept_get_does_not_change_state(
    session: AsyncSession, issuer_url: str
) -> None:
    """Item 12: accepting is POST-only; GET never grants or stashes."""
    import httpx
    from conftest import test_database_url
    from t02_helpers import make_session_factory as _factory

    from app.config import Settings
    from app.main import create_app
    from app.rbac.catalogue import sync_catalogue

    await sync_catalogue(session)
    await session.commit()
    creator = await _user(session)
    from app.rbac.principal import Principal
    from app.services.invites import create_invite

    staff = Principal(
        user_id=creator,
        email="staff@example.com",
        roles=(),
        permissions=frozenset({"invite.create"}),
        scope_tags=(),
        is_superadmin=True,
    )
    raw = await create_invite(session, actor=staff, course_tags=["course-a"])
    await session.commit()
    settings = Settings(
        database_url=test_database_url(),
        google_oauth_client_id="test-client-id",
        google_oauth_client_secret="test-client-secret",  # type: ignore[arg-type]
        session_secure_cookie=False,
        session_ttl_s=3600,
        oidc_issuer=issuer_url,
        oidc_redirect_url="http://test/api/v1/auth/callback",
        superadmin_emails="",
    )
    app = create_app(settings)  # type: ignore[arg-type]
    from app import db as db_module

    async def _test_session():  # type: ignore[no-untyped-def]
        sess = _factory()()  # type: ignore[operator]
        try:
            yield sess
            await sess.commit()
        except Exception:
            await sess.rollback()
            raise
        finally:
            await sess.close()

    app.dependency_overrides[db_module.get_session] = _test_session
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    before = (await session.execute(sa.text("SELECT count(*) FROM sessions"))).scalar_one()
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        answer = await client.get(f"/api/v1/invites/{raw}/accept", follow_redirects=False)
        assert answer.status_code == 405
        stashed = (await session.execute(sa.text("SELECT count(*) FROM sessions"))).scalar_one()
        assert stashed == before


async def test_procrastinate_schema_created_by_conftest() -> None:
    """Item 13: the test database carries the procrastinate schema."""
    async with make_session_factory()() as check:
        present = (
            await check.execute(sa.text("select to_regclass('public.procrastinate_jobs')"))
        ).scalar_one()
        assert present == "procrastinate_jobs"


async def test_auth_oidc_has_no_module_level_skip() -> None:
    """Item 13: the OIDC module skips per-test, never at import time."""
    import ast
    from pathlib import Path

    source = await asyncio.to_thread(Path("tests/test_auth_oidc.py").read_text, encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            func = node.value.func
            name = getattr(func, "id", getattr(func, "attr", ""))
            assert name != "require_postgres", "module-level require_postgres is forbidden"


async def test_production_session_commits_and_rolls_back() -> None:
    """Item 13: the production get_session commits on success, rolls back on error."""
    from app import db as db_module

    factory = make_session_factory()
    db_module._session_factory = factory  # type: ignore[attr-defined]
    try:
        email = unique_email("prod-session")
        agen = db_module.get_session()
        sess = await agen.__anext__()
        await sess.execute(
            sa.text("INSERT INTO users (email, display_name) VALUES (:e, 'P')"),
            {"e": email},
        )
        try:
            await agen.__anext__()
        except StopAsyncIteration:
            pass
        else:
            raise AssertionError("the session generator should have finished")
        async with factory() as check:
            assert (
                await check.execute(
                    sa.text("SELECT count(*) FROM users WHERE email = :e"), {"e": email}
                )
            ).scalar_one() == 1

        bad_email = unique_email("prod-rollback")
        bgen = db_module.get_session()
        bsess = await bgen.__anext__()
        await bsess.execute(
            sa.text("INSERT INTO users (email, display_name) VALUES (:e, 'P')"),
            {"e": bad_email},
        )
        with pytest.raises(RuntimeError, match="boom"):
            await bgen.athrow(RuntimeError("boom"))
        async with factory() as check:
            assert (
                await check.execute(
                    sa.text("SELECT count(*) FROM users WHERE email = :e"), {"e": bad_email}
                )
            ).scalar_one() == 0
    finally:
        db_module._session_factory = None  # type: ignore[attr-defined]
