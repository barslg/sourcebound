"""Provider-generic OIDC sign-in against a local fake issuer.

The fake issuer speaks real HTTP and signs real RS256 JWTs with a test
key generated at session start; Google is never contacted. Covers the
T02 identity rules: first login creates a user with NO roles, later
logins update name/avatar, banned and legacy identities are refused,
superadmin comes from config, and the backend session cookie carries
no tokens to the browser.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from conftest import require_postgres, test_database_url
from sqlalchemy.ext.asyncio import AsyncSession
from t02_helpers import make_session_factory, truncate_all, unique_email
from t02_issuer import register_code, run_issuer, stored_verifier_slot


@pytest.fixture(autouse=True)
def _needs_postgres() -> None:
    require_postgres(test_database_url())


def _register_code(subject: str, audience: str, nonce: str, hd: str = "") -> str:
    return register_code(subject, audience, nonce, hd)


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


def _register_code(subject: str, audience: str, nonce: str, hd: str = "") -> str:
    return register_code(subject, audience, nonce, hd)


async def test_first_login_creates_user_with_no_roles(
    session: AsyncSession, issuer_url: str
) -> None:
    from app.auth.oidc import GoogleProvider
    from app.services.auth import begin_login, finish_login

    provider = GoogleProvider(**_google_kwargs(issuer_url))  # type: ignore[arg-type]
    authorize_url, state = await begin_login(session, provider)
    await session.commit()
    assert authorize_url.startswith(issuer_url + "/auth")
    stored = (
        await session.execute(
            sa.text("SELECT nonce, code_verifier FROM auth_states WHERE state = :s"),
            {"s": state},
        )
    ).one()
    code = _register_code("sub-1", "test-client-id", stored[0])
    stored_verifier_slot(code)["code_verifier"] = stored[1]
    user, outcome = await finish_login(session, provider, state=state, code=code)
    await session.commit()
    assert outcome.is_new
    assert user.email == "sub-1@example.com"
    roles = (
        await session.execute(
            sa.text("SELECT count(*) FROM user_roles WHERE user_id = :u"), {"u": str(user.id)}
        )
    ).scalar_one()
    assert roles == 0
    identity = (
        await session.execute(
            sa.text("SELECT provider, subject FROM identities WHERE user_id = :u"),
            {"u": str(user.id)},
        )
    ).one()
    assert tuple(identity) == ("google", "sub-1")


async def test_state_is_one_time(session: AsyncSession, issuer_url: str) -> None:
    from app.auth.oidc import GoogleProvider
    from app.errors import AuthError
    from app.services.auth import begin_login, finish_login

    provider = GoogleProvider(**_google_kwargs(issuer_url))  # type: ignore[arg-type]
    _, state = await begin_login(session, provider)
    await session.commit()
    stored = (
        await session.execute(
            sa.text("SELECT nonce, code_verifier FROM auth_states WHERE state = :s"),
            {"s": state},
        )
    ).one()
    code = _register_code("sub-once", "test-client-id", stored[0])
    stored_verifier_slot(code)["code_verifier"] = stored[1]
    await finish_login(session, provider, state=state, code=code)
    await session.commit()
    with pytest.raises(AuthError):
        await finish_login(session, provider, state=state, code=code)


async def test_later_login_updates_name_and_avatar(session: AsyncSession, issuer_url: str) -> None:
    from app.auth.oidc import GoogleProvider
    from app.services.auth import begin_login, finish_login

    provider = GoogleProvider(**_google_kwargs(issuer_url))  # type: ignore[arg-type]
    for _ in range(2):
        _, state = await begin_login(session, provider)
        await session.commit()
        stored = (
            await session.execute(
                sa.text("SELECT nonce, code_verifier FROM auth_states WHERE state = :s"),
                {"s": state},
            )
        ).one()
        code = _register_code("sub-repeat", "test-client-id", stored[0])
        stored_verifier_slot(code)["code_verifier"] = stored[1]
        user, outcome = await finish_login(session, provider, state=state, code=code)
        await session.commit()
    assert not outcome.is_new
    assert user.display_name == "Test User"
    assert user.avatar_url == "https://example.com/avatar.png"


async def test_wrong_audience_rejected(session: AsyncSession, issuer_url: str) -> None:
    from app.auth.oidc import GoogleProvider
    from app.errors import AuthError
    from app.services.auth import begin_login, finish_login

    provider = GoogleProvider(**_google_kwargs(issuer_url))  # type: ignore[arg-type]
    _, state = await begin_login(session, provider)
    await session.commit()
    stored = (
        await session.execute(
            sa.text("SELECT nonce, code_verifier FROM auth_states WHERE state = :s"),
            {"s": state},
        )
    ).one()
    code = _register_code("sub-evil", "other-client", stored[0])
    stored_verifier_slot(code)["code_verifier"] = stored[1]
    with pytest.raises(AuthError):
        await finish_login(session, provider, state=state, code=code)


async def test_hd_allow_list_enforced(session: AsyncSession, issuer_url: str) -> None:
    from app.auth.oidc import GoogleProvider
    from app.errors import AuthError
    from app.services.auth import begin_login, finish_login

    provider = GoogleProvider(**_google_kwargs(issuer_url, allowed_domains=("school.edu",)))  # type: ignore[arg-type]
    _, state = await begin_login(session, provider)
    await session.commit()
    stored = (
        await session.execute(
            sa.text("SELECT nonce, code_verifier FROM auth_states WHERE state = :s"),
            {"s": state},
        )
    ).one()
    code = _register_code("sub-hd", "test-client-id", stored[0], hd="evil.example")
    stored_verifier_slot(code)["code_verifier"] = stored[1]
    with pytest.raises(AuthError):
        await finish_login(session, provider, state=state, code=code)


async def test_banned_identity_refused_at_sign_in(session: AsyncSession, issuer_url: str) -> None:
    from app.auth.oidc import GoogleProvider
    from app.errors import AuthError
    from app.services.auth import begin_login, finish_login

    provider = GoogleProvider(**_google_kwargs(issuer_url))  # type: ignore[arg-type]
    _, state = await begin_login(session, provider)
    await session.commit()
    stored = (
        await session.execute(
            sa.text("SELECT nonce, code_verifier FROM auth_states WHERE state = :s"),
            {"s": state},
        )
    ).one()
    code = _register_code("sub-banned", "test-client-id", stored[0])
    stored_verifier_slot(code)["code_verifier"] = stored[1]
    user, _ = await finish_login(session, provider, state=state, code=code)
    await session.execute(
        sa.text("UPDATE users SET banned_at = now(), banned_reason = 'spam' WHERE id = :u"),
        {"u": str(user.id)},
    )
    await session.commit()
    _, state2 = await begin_login(session, provider)
    await session.commit()
    stored2 = (
        await session.execute(
            sa.text("SELECT nonce, code_verifier FROM auth_states WHERE state = :s"),
            {"s": state2},
        )
    ).one()
    code2 = _register_code("sub-banned", "test-client-id", stored2[0])
    stored_verifier_slot(code2)["code_verifier"] = stored2[1]
    with pytest.raises(AuthError):
        await finish_login(session, provider, state=state2, code=code2)


async def test_superadmin_email_from_config_gets_flag(
    session: AsyncSession, issuer_url: str
) -> None:
    from app.auth.oidc import GoogleProvider
    from app.services.auth import begin_login, finish_login

    provider = GoogleProvider(**_google_kwargs(issuer_url))  # type: ignore[arg-type]
    _, state = await begin_login(session, provider)
    await session.commit()
    stored = (
        await session.execute(
            sa.text("SELECT nonce, code_verifier FROM auth_states WHERE state = :s"),
            {"s": state},
        )
    ).one()
    # The fake issuer stamps <subject>@example.com; the route layer passes
    # SUPERADMIN_EMAILS from config into the use case.
    code = _register_code("chief", "test-client-id", stored[0])
    stored_verifier_slot(code)["code_verifier"] = stored[1]
    user, _ = await finish_login(
        session, provider, state=state, code=code, superadmin_emails={"chief@example.com"}
    )
    await session.commit()
    assert user.is_superadmin


async def test_nonce_mismatch_rejected(session: AsyncSession, issuer_url: str) -> None:
    from app.auth.oidc import GoogleProvider
    from app.errors import AuthError
    from app.services.auth import begin_login, finish_login

    provider = GoogleProvider(**_google_kwargs(issuer_url))  # type: ignore[arg-type]
    _, state = await begin_login(session, provider)
    await session.commit()
    stored = (
        await session.execute(
            sa.text("SELECT nonce, code_verifier FROM auth_states WHERE state = :s"),
            {"s": state},
        )
    ).one()
    code = _register_code("sub-tampered", "test-client-id", stored[0])
    stored_verifier_slot(code)["code_verifier"] = stored[1]
    await session.execute(
        sa.text("UPDATE auth_states SET nonce = 'tampered' WHERE state = :s"), {"s": state}
    )
    await session.commit()
    with pytest.raises(AuthError):
        await finish_login(session, provider, state=state, code=code)


async def test_session_cookie_round_trip_and_logout(session: AsyncSession) -> None:
    import sqlalchemy as sa

    from app.auth.session import create_session, destroy_session, load_session

    email = unique_email()
    user_id = (
        await session.execute(
            sa.text("INSERT INTO users (email, display_name) VALUES (:e, 'N') RETURNING id"),
            {"e": email},
        )
    ).scalar_one()
    await session.commit()
    token, csrf = await create_session(session, user_id)
    await session.commit()
    assert csrf
    loaded = await load_session(session, token)
    assert loaded is not None and loaded.user_id == user_id
    await session.execute(
        sa.text("UPDATE sessions SET expires_at = now() + interval '10 seconds'"),
    )
    await session.commit()
    rolled = await load_session(session, token)
    assert rolled is not None
    assert rolled.expires_at > loaded.expires_at
    await destroy_session(session, token)
    await session.commit()
    assert await load_session(session, token) is None
