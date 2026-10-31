"""T02b security probes, HIGH items 1-4 (must fail before the fix)."""

from __future__ import annotations

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


async def _login(
    session: AsyncSession,
    issuer_url: str,
    subject: str,
    *,
    email: str | None = None,
    email_verified: bool = True,
    superadmin_emails: set[str] = frozenset(),  # type: ignore[assignment]
) -> object:
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
    code = register_code(
        subject, "test-client-id", stored[0], email=email, email_verified=email_verified
    )
    stored_verifier_slot(code)["code_verifier"] = stored[1]
    user, _ = await finish_login(
        session, provider, state=state, code=code, superadmin_emails=superadmin_emails
    )
    await session.commit()
    return user


async def test_unknown_sub_with_existing_email_is_refused(
    session: AsyncSession, issuer_url: str
) -> None:
    """Item 1: a new (provider, sub) must never attach to someone else's email."""
    from app.auth.oidc import GoogleProvider
    from app.errors import AuthError
    from app.services.auth import begin_login, finish_login

    victim = await _login(session, issuer_url, "t02b-victim")
    assert victim.email == "t02b-victim@example.com"

    provider = GoogleProvider(**_google_kwargs(issuer_url))  # type: ignore[arg-type]
    _, state = await begin_login(session, provider)
    await session.commit()
    stored = (
        await session.execute(
            sa.text("SELECT nonce, code_verifier FROM auth_states WHERE state = :s"),
            {"s": state},
        )
    ).one()
    code = register_code(
        "t02b-attacker",
        "test-client-id",
        stored[0],
        email="t02b-victim@example.com",
    )
    stored_verifier_slot(code)["code_verifier"] = stored[1]
    with pytest.raises(AuthError):
        await finish_login(session, provider, state=state, code=code)


async def test_superadmin_login_requires_verified_email(
    session: AsyncSession, issuer_url: str
) -> None:
    """Item 2a: an unverified superadmin email is refused, never flagged."""
    from app.errors import AuthError

    with pytest.raises(AuthError):
        await _login(
            session,
            issuer_url,
            "t02b-unverified-chief",
            email_verified=False,
            superadmin_emails={"t02b-unverified-chief@example.com"},
        )
    await session.rollback()


async def test_superadmin_sub_is_pinned(session: AsyncSession, issuer_url: str) -> None:
    """Item 2b: a different sub for the same superadmin email is refused."""
    from app.auth.oidc import GoogleProvider
    from app.errors import AuthError
    from app.services.auth import begin_login, finish_login

    chief = await _login(
        session,
        issuer_url,
        "t02b-chief",
        superadmin_emails={"t02b-chief@example.com"},
    )
    assert chief.is_superadmin

    provider = GoogleProvider(**_google_kwargs(issuer_url))  # type: ignore[arg-type]
    _, state = await begin_login(session, provider)
    await session.commit()
    stored = (
        await session.execute(
            sa.text("SELECT nonce, code_verifier FROM auth_states WHERE state = :s"),
            {"s": state},
        )
    ).one()
    code = register_code(
        "t02b-impostor", "test-client-id", stored[0], email="t02b-chief@example.com"
    )
    stored_verifier_slot(code)["code_verifier"] = stored[1]
    with pytest.raises(AuthError):
        await finish_login(
            session,
            provider,
            state=state,
            code=code,
            superadmin_emails={"t02b-chief@example.com"},
        )


async def _scoped_actor(session: AsyncSession) -> object:
    from app.rbac.catalogue import ensure_admin_role, sync_catalogue
    from app.rbac.principal import Principal
    from app.rbac.roles import create_role

    await sync_catalogue(session)
    await ensure_admin_role(session)
    await create_role(session, name="limited", permission_codes=["section.courses"], actor=None)
    await create_role(
        session, name="assigner", permission_codes=["role.assign", "section.courses"], actor=None
    )
    row = (
        await session.execute(
            sa.text("INSERT INTO users (email, display_name) VALUES (:e, 'A') RETURNING id"),
            {"e": unique_email("assigner")},
        )
    ).one()
    await session.commit()
    return Principal(
        user_id=uuid.UUID(str(row[0])),
        email="assigner@example.com",
        roles=("assigner",),
        permissions=frozenset({"role.assign", "section.courses"}),
        scope_tags=("course-a",),
        is_superadmin=False,
    )


async def test_assign_role_applies_escalation_check(session: AsyncSession) -> None:
    """Item 3: role.assign alone must not grant administrator or out-of-scope roles."""
    from app.errors import ForbiddenError
    from app.rbac.roles import assign_role

    actor = await _scoped_actor(session)
    target = (
        await session.execute(
            sa.text("INSERT INTO users (email, display_name) VALUES (:e, 'T') RETURNING id"),
            {"e": unique_email("target")},
        )
    ).scalar_one()
    await session.commit()
    target_id = uuid.UUID(str(target))

    # Codes outside the actor's own grants are refused.
    with pytest.raises(ForbiddenError):
        await assign_role(session, target_id, "administrator", scope_tags=["course-a"], actor=actor)  # type: ignore[arg-type]
    await session.rollback()
    # Never onto oneself.
    with pytest.raises(ForbiddenError):
        await assign_role(session, actor.user_id, "limited", scope_tags=["course-a"], actor=actor)  # type: ignore[arg-type]
    await session.rollback()
    # Tags outside the actor's scope are refused.
    with pytest.raises(ForbiddenError):
        await assign_role(session, target_id, "limited", scope_tags=["course-b"], actor=actor)  # type: ignore[arg-type]
    await session.rollback()
    # A scoped actor cannot hand out an empty (global) scope.
    with pytest.raises(ForbiddenError):
        await assign_role(session, target_id, "limited", scope_tags=[], actor=actor)  # type: ignore[arg-type]
    await session.rollback()
    # The in-scope grant still works.
    await assign_role(session, target_id, "limited", scope_tags=["course-a"], actor=actor)  # type: ignore[arg-type]
    await session.commit()


async def test_invite_accept_always_grants_student(session: AsyncSession) -> None:
    """Item 4: the client must not choose the granted role; invites grant student."""
    from app.rbac.catalogue import sync_catalogue
    from app.rbac.principal import Principal, load_principal
    from app.services.invites import accept_invite, create_invite, hash_invite_token

    await sync_catalogue(session)
    await session.execute(sa.text("DELETE FROM role_permissions"))
    await session.execute(
        sa.text("INSERT INTO roles (name) VALUES ('student') ON CONFLICT DO NOTHING")
    )
    await session.commit()
    creator = (
        await session.execute(
            sa.text("INSERT INTO users (email, display_name) VALUES (:e, 'C') RETURNING id"),
            {"e": unique_email("creator")},
        )
    ).scalar_one()
    await session.commit()
    actor = Principal(
        user_id=uuid.UUID(str(creator)),
        email="creator@example.com",
        roles=(),
        permissions=frozenset({"invite.create"}),
        scope_tags=(),
        is_superadmin=True,
    )
    student = (
        await session.execute(
            sa.text("INSERT INTO users (email, display_name) VALUES (:e, 'S') RETURNING id"),
            {"e": unique_email("student")},
        )
    ).scalar_one()
    await session.commit()
    # The request schema no longer carries a role choice at all.
    from app.schemas import t02 as dto

    assert "granted_role" not in dto.InviteRequest.model_fields
    raw_token = await create_invite(session, actor=actor, course_tags=["course-a"])
    await session.commit()
    stored = (
        await session.execute(
            sa.text("SELECT token_hash FROM invites WHERE token_hash = :h"),
            {"h": hash_invite_token(raw_token)},
        )
    ).scalar_one()
    assert stored == hash_invite_token(raw_token)
    await accept_invite(
        session, user_id=uuid.UUID(str(student)), token_hash=hash_invite_token(raw_token)
    )
    await session.commit()
    principal = await load_principal(session, uuid.UUID(str(student)))
    assert principal.roles == ("student",)
