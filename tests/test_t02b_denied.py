"""Denied-actor guards: one test per guarded route (mutant coverage).

Each test drops the permission (or the require) path by acting with a
principal that lacks the code and asserting 403/denial. If a require
is ever removed from ban_user, delete_user, assign_role, or
submit_echo, the matching test turns red.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from t02_helpers import make_session_factory, truncate_all, unique_email


@pytest.fixture()
async def session() -> AsyncSession:
    factory = make_session_factory()
    async with factory() as sess:
        await truncate_all(sess)
        yield sess
        await sess.rollback()


def _principal(user_id: uuid.UUID, codes: frozenset[str]) -> object:
    from app.rbac.principal import Principal

    return Principal(
        user_id=user_id,
        email="denied@example.com",
        roles=(),
        permissions=codes,
        scope_tags=(),
        is_superadmin=False,
    )


async def _user(session: AsyncSession) -> uuid.UUID:
    row = (
        await session.execute(
            sa.text("INSERT INTO users (email, display_name) VALUES (:e, 'N') RETURNING id"),
            {"e": unique_email("denied")},
        )
    ).one()
    await session.commit()
    return uuid.UUID(str(row[0]))


async def _catalogue(session: AsyncSession) -> None:
    from app.rbac.catalogue import sync_catalogue

    await sync_catalogue(session)
    await session.commit()


async def test_denied_ban_user(session: AsyncSession) -> None:
    from app.errors import ForbiddenError
    from app.services.users import ban_user

    await _catalogue(session)
    with pytest.raises(ForbiddenError):
        await ban_user(
            session,
            actor=_principal(await _user(session), frozenset()),  # type: ignore[arg-type]
            user_id=await _user(session),
            reason="x",
        )
    await session.rollback()


async def test_denied_delete_user(session: AsyncSession) -> None:
    from app.errors import ForbiddenError
    from app.services.users import delete_user

    await _catalogue(session)
    with pytest.raises(ForbiddenError):
        await delete_user(
            session,
            actor=_principal(await _user(session), frozenset({"user.ban"})),  # type: ignore[arg-type]
            user_id=await _user(session),
        )
    await session.rollback()


async def test_denied_assign_role(session: AsyncSession) -> None:
    from app.errors import ForbiddenError
    from app.rbac.roles import assign_role, create_role

    await _catalogue(session)
    await create_role(session, name="clerks", permission_codes=["section.courses"], actor=None)
    await session.commit()
    # The actor could receive this grant (codes and scope fit) but lacks
    # role.assign, so only the require stands between them and the grant.
    with pytest.raises(ForbiddenError):
        await assign_role(
            session,
            await _user(session),
            "clerks",
            scope_tags=[],
            actor=_principal(await _user(session), frozenset({"section.courses"})),  # type: ignore[arg-type]
        )
    await session.rollback()


async def test_denied_revoke_role(session: AsyncSession) -> None:
    from app.errors import ForbiddenError
    from app.rbac.roles import create_role, revoke_role

    await _catalogue(session)
    await create_role(session, name="clerks", permission_codes=["section.courses"], actor=None)
    await session.commit()
    with pytest.raises(ForbiddenError):
        await revoke_role(
            session,
            await _user(session),
            "clerks",
            actor=_principal(await _user(session), frozenset()),  # type: ignore[arg-type]
        )
    await session.rollback()


async def test_denied_create_role(session: AsyncSession) -> None:
    from app.errors import ForbiddenError
    from app.rbac.roles import create_role

    await _catalogue(session)
    with pytest.raises(ForbiddenError):
        await create_role(
            session,
            name="evil",
            permission_codes=["section.courses"],
            actor=_principal(await _user(session), frozenset()),  # type: ignore[arg-type]
        )
    await session.rollback()


async def test_denied_update_and_delete_role(session: AsyncSession) -> None:
    from app.errors import ForbiddenError
    from app.rbac.roles import create_role, delete_role, update_role

    await _catalogue(session)
    await create_role(session, name="clerks", permission_codes=["section.courses"], actor=None)
    await session.commit()
    nobody = _principal(await _user(session), frozenset())
    with pytest.raises(ForbiddenError):
        await update_role(
            session, name="clerks", permission_codes=["section.courses"], actor=nobody
        )  # type: ignore[arg-type]
    await session.rollback()
    with pytest.raises(ForbiddenError):
        await delete_role(session, name="clerks", actor=nobody)  # type: ignore[arg-type]
    await session.rollback()


async def test_denied_submit_echo(session: AsyncSession) -> None:
    from app.errors import ForbiddenError
    from app.services.operations import submit_echo

    await _catalogue(session)
    with pytest.raises(ForbiddenError):
        await submit_echo(
            session,
            actor=_principal(await _user(session), frozenset()),  # type: ignore[arg-type]
            payload={"message": "hi"},
            defer=None,
        )
    await session.rollback()


async def test_denied_list_users(session: AsyncSession) -> None:
    from app.errors import ForbiddenError
    from app.services.users import list_users

    await _catalogue(session)
    with pytest.raises(ForbiddenError):
        await list_users(session, actor=_principal(await _user(session), frozenset()))  # type: ignore[arg-type]
    await session.rollback()


async def test_denied_create_and_revoke_invite(session: AsyncSession) -> None:
    from app.errors import ForbiddenError
    from app.services.invites import create_invite, revoke_invite

    await _catalogue(session)
    nobody = _principal(await _user(session), frozenset())
    with pytest.raises(ForbiddenError):
        await create_invite(session, actor=nobody, course_tags=["course-a"])  # type: ignore[arg-type]
    await session.rollback()
    with pytest.raises(ForbiddenError):
        await revoke_invite(session, actor=nobody, token_hash="dead")  # type: ignore[arg-type]
    await session.rollback()


async def test_denied_list_invites_and_roles(session: AsyncSession) -> None:
    from app.errors import ForbiddenError
    from app.rbac.catalogue import sync_catalogue
    from app.rbac.roles import list_roles as _list_roles
    from app.services.invites import list_invites

    await sync_catalogue(session)
    await session.commit()
    nobody = _principal(await _user(session), frozenset())
    with pytest.raises(ForbiddenError):
        await list_invites(session, actor=nobody)  # type: ignore[arg-type]
    await session.rollback()
    # list_roles has no require at service level; the route requires auth.
    assert isinstance(await _list_roles(session), list)


async def test_denied_every_guarded_route_over_http(session: AsyncSession) -> None:
    """One denied actor across every guarded HTTP route: all refuse."""
    from urllib.parse import parse_qs, urlparse

    import httpx
    from conftest import test_database_url
    from t02_helpers import make_session_factory
    from t02_issuer import register_code, run_issuer, stored_verifier_slot

    from app.config import Settings
    from app.main import create_app
    from app.rbac.catalogue import sync_catalogue

    await sync_catalogue(session)
    await session.commit()
    with run_issuer() as issuer_url:
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
            sess = make_session_factory()()  # type: ignore[operator]
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
            code = register_code("nobody-user", "test-client-id", stored[0])
            stored_verifier_slot(code)["code_verifier"] = stored[1]
            done = await client.get(
                "/api/v1/auth/callback",
                params={"state": state, "code": code},
                follow_redirects=False,
            )
            assert done.status_code == 303
            csrf = (await client.get("/api/v1/auth/csrf")).json()["csrf_token"]
            headers = {"X-CSRF-Token": csrf}
            victim = await _user(session)
            checks = [
                client.get("/api/v1/users"),
                client.post(f"/api/v1/users/{victim}/ban", json={"reason": "x"}, headers=headers),
                client.delete(f"/api/v1/users/{victim}", headers=headers),
                client.get("/api/v1/roles"),
                client.post(
                    "/api/v1/roles", json={"name": "z", "permission_codes": []}, headers=headers
                ),
                client.post(
                    "/api/v1/roles/clerks/assign",
                    json={"user_id": str(victim), "scope_tags": []},
                    headers=headers,
                ),
                client.post("/api/v1/invites", json={"course_tags": ["c"]}, headers=headers),
                client.get("/api/v1/invites"),
                client.get("/api/v1/permissions"),
                client.post("/api/v1/operations/echo", json={"message": "hi"}, headers=headers),
            ]
            for check in checks:
                response = await check
                assert response.status_code == 403, (response.url, response.status_code)
