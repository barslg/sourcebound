"""Invite links: creation, revocation, accept, and atomic seat counts.

An invite lives on a course or on a course group (a tag), carries an
optional seat count and an optional expiry, and is created by a
permission holder. Accepting while signed in grants the student role
scoped to those courses; the seat count holds under concurrency: two
simultaneous accepts on a one-seat invite leave exactly one winner.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

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


async def _user(session: AsyncSession) -> uuid.UUID:
    row = (
        await session.execute(
            sa.text("INSERT INTO users (email, display_name) VALUES (:e, 'N') RETURNING id"),
            {"e": unique_email()},
        )
    ).one()
    await session.commit()
    return uuid.UUID(str(row[0]))


def _actor(user_id: uuid.UUID) -> object:
    from app.rbac.principal import Principal

    return Principal(
        user_id=user_id,
        email="staff@example.com",
        roles=("manager",),
        permissions=frozenset({"invite.create", "invite.revoke"}),
        scope_tags=(),
        is_superadmin=False,
    )


async def _setup_catalogue(session: AsyncSession) -> None:
    from app.rbac.catalogue import sync_catalogue

    await sync_catalogue(session)
    await session.execute(sa.text("DELETE FROM role_permissions"))
    await session.execute(
        sa.text("INSERT INTO roles (name) VALUES ('student') ON CONFLICT DO NOTHING")
    )
    await session.commit()


async def test_create_and_revoke_invite(session: AsyncSession) -> None:
    from app.services.invites import create_invite, revoke_invite

    await _setup_catalogue(session)
    creator = await _user(session)
    token = await create_invite(
        session,
        actor=_actor(creator),
        course_tags=["course-a"],
        max_uses=3,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    await session.commit()
    assert token
    await revoke_invite(session, actor=_actor(creator), token=token)
    await session.commit()
    revoked = (
        await session.execute(
            sa.text("SELECT revoked_at FROM invites WHERE token = :t"), {"t": str(token)}
        )
    ).scalar_one()
    assert revoked is not None


async def test_accept_while_signed_in_grants_scoped_student_role(
    session: AsyncSession,
) -> None:
    from app.services.invites import accept_invite, create_invite

    await _setup_catalogue(session)
    creator = await _user(session)
    student = await _user(session)
    token = await create_invite(
        session, actor=_actor(creator), course_tags=["course-a", "course-b"], max_uses=None
    )
    await session.commit()
    scopes = await accept_invite(session, user_id=student, token=token)
    await session.commit()
    assert sorted(scopes) == ["course-a", "course-b"]
    from app.rbac.principal import bump_version, load_principal

    bump_version()
    principal = await load_principal(session, student)
    assert "student" in principal.roles
    assert sorted(principal.scope_tags) == ["course-a", "course-b"]


async def test_two_simultaneous_accepts_on_one_seat_leave_one_winner() -> None:
    from app.errors import ConflictError
    from app.services.invites import accept_invite, create_invite

    factory = make_session_factory()
    async with factory() as setup:
        await truncate_all(setup)
        await _setup_catalogue(setup)
        creator = await _user(setup)
        first = await _user(setup)
        second = await _user(setup)
        token = await create_invite(
            session=setup, actor=_actor(creator), course_tags=["course-a"], max_uses=1
        )
        await setup.commit()

    async def _accept(user_id: uuid.UUID) -> bool:
        async with factory() as sess:
            try:
                async with sess.begin():
                    await accept_invite(sess, user_id=user_id, token=token)
                return True
            except ConflictError:
                return False

    results = await asyncio.gather(_accept(first), _accept(second))
    assert sorted(results) == [False, True]
    async with factory() as check:
        used = (
            await check.execute(
                sa.text("SELECT used_count FROM invites WHERE token = :t"), {"t": str(token)}
            )
        ).scalar_one()
        assert used == 1
        uses = (
            await check.execute(
                sa.text(
                    "SELECT count(*) FROM invite_uses WHERE invite_id ="
                    " (SELECT id FROM invites WHERE token = :t)"
                ),
                {"t": str(token)},
            )
        ).scalar_one()
        assert uses == 1


async def test_expired_and_revoked_invites_refused(session: AsyncSession) -> None:
    from app.errors import ConflictError
    from app.services.invites import accept_invite, create_invite

    await _setup_catalogue(session)
    creator = await _user(session)
    student = await _user(session)
    expired = await create_invite(
        session,
        actor=_actor(creator),
        course_tags=["course-a"],
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    await session.commit()
    with pytest.raises(ConflictError):
        await accept_invite(session, user_id=student, token=expired)
    await session.rollback()


async def test_invite_without_rights_refused(session: AsyncSession) -> None:
    from app.errors import ForbiddenError
    from app.rbac.principal import Principal
    from app.services.invites import create_invite

    await _setup_catalogue(session)
    nobody = Principal(
        user_id=await _user(session),
        email="nobody@example.com",
        roles=(),
        permissions=frozenset(),
        scope_tags=(),
        is_superadmin=False,
    )
    with pytest.raises(ForbiddenError):
        await create_invite(session, actor=nobody, course_tags=["course-a"])
