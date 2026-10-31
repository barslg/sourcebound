"""T04f: repeated sign-in, repeated callback, and the two callback refusals."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import sqlalchemy as sa
from conftest import test_database_url
from sqlalchemy.ext.asyncio import AsyncSession
from t02_helpers import make_session_factory, truncate_all
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


def _app(issuer_url: str) -> object:
    from app import db as db_module
    from app.config import Settings
    from app.main import create_app

    app = create_app(
        Settings(
            database_url=test_database_url(),
            google_oauth_client_id="test-client-id",
            google_oauth_client_secret="test-client-secret",  # type: ignore[arg-type]
            session_secure_cookie=False,
            session_ttl_s=3600,
            oidc_issuer=issuer_url,
            oidc_redirect_url="http://test/api/v1/auth/callback",
        )
    )

    async def _test_session():  # type: ignore[no-untyped-def]
        sess = make_session_factory()()
        try:
            yield sess
            await sess.commit()
        except Exception:
            await sess.rollback()
            raise
        finally:
            await sess.close()

    app.dependency_overrides[db_module.get_session] = _test_session
    return app


def _client(app: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")  # type: ignore[arg-type]


async def _start(client: httpx.AsyncClient, session: AsyncSession, subject: str) -> tuple[str, str]:
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
    return state, code


async def _finish(client: httpx.AsyncClient, state: str, code: str) -> httpx.Response:
    return await client.get(
        "/api/v1/auth/callback", params={"state": state, "code": code}, follow_redirects=False
    )


async def test_repeated_callback_of_a_signed_in_browser_redirects_home(
    session: AsyncSession, issuer_url: str
) -> None:
    async with _client(_app(issuer_url)) as client:
        state, code = await _start(client, session, "t04f-dup")
        first = await _finish(client, state, code)
        assert first.status_code == 303
        again = await _finish(client, state, code)
        assert again.status_code == 303
        assert again.headers["location"] == "/"
        assert "set-cookie" not in again.headers
        assert (await client.get("/api/v1/auth/me")).status_code == 200


async def test_spent_state_without_a_session_is_still_refused(
    session: AsyncSession, issuer_url: str
) -> None:
    app = _app(issuer_url)
    async with _client(app) as client:
        state, code = await _start(client, session, "t04f-anon")
        assert (await _finish(client, state, code)).status_code == 303
    async with _client(app) as bare:
        refused = await _finish(bare, state, code)
        assert refused.status_code == 401


async def test_second_sign_in_after_logout_and_while_signed_in(
    session: AsyncSession, issuer_url: str
) -> None:
    async with _client(_app(issuer_url)) as client:
        state, code = await _start(client, session, "t04f-again")
        assert (await _finish(client, state, code)).status_code == 303
        csrf = (await client.get("/api/v1/auth/csrf")).json()["csrf_token"]
        out = await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
        assert out.status_code == 200
        assert (await client.get("/api/v1/auth/me")).status_code == 401
        state, code = await _start(client, session, "t04f-again")
        assert (await _finish(client, state, code)).status_code == 303
        assert (await client.get("/api/v1/auth/me")).status_code == 200
        state, code = await _start(client, session, "t04f-again")
        assert (await _finish(client, state, code)).status_code == 303
        assert (await client.get("/api/v1/auth/me")).status_code == 200


async def test_callback_without_cookie_and_with_another_cookie_say_which(
    session: AsyncSession, issuer_url: str
) -> None:
    app = _app(issuer_url)
    async with _client(app) as owner, _client(app) as bare, _client(app) as other:
        state, code = await _start(owner, session, "t04f-owner")
        none = await _finish(bare, state, code)
        assert none.status_code == 401
        assert "without its session cookie" in none.json()["message"]
        o_state, o_code = await _start(other, session, "t04f-other")
        assert (await _finish(other, o_state, o_code)).status_code == 303
        foreign = await _finish(other, state, code)
        assert foreign.status_code == 401
        assert "another session" in foreign.json()["message"]
        assert (await _finish(owner, state, code)).status_code == 303
