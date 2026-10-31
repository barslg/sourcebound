"""User administration: ban, delete with heir, delete as legacy holder.

Ban sets banned_at plus a reason, refuses later sign-ins, and revokes
sessions. Delete cascades personal data (sessions, identities, invite
uses) and, when the account owns objects, either moves ownership to a
named heir or turns the account into a legacy holder that can never
sign in again.
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


async def _user(session: AsyncSession, email: str = "") -> uuid.UUID:
    row = (
        await session.execute(
            sa.text("INSERT INTO users (email, display_name) VALUES (:e, 'N') RETURNING id"),
            {"e": email or unique_email()},
        )
    ).one()
    await session.commit()
    return uuid.UUID(str(row[0]))


async def _admin(session: AsyncSession) -> object:
    from app.rbac.principal import Principal

    admin_id = await _user(session, unique_email("admin"))
    return Principal(
        user_id=admin_id,
        email="admin@example.com",
        roles=("administrator",),
        permissions=frozenset({"user.ban", "user.delete", "user.list"}),
        scope_tags=(),
        is_superadmin=True,
    )


async def test_ban_blocks_sign_in_and_revokes_sessions(session: AsyncSession) -> None:
    from app.auth.session import create_session, load_session
    from app.errors import AuthError
    from app.services.auth import refuse_if_unusable
    from app.services.users import ban_user

    user_id = await _user(session)
    token, _ = await create_session(session, user_id)
    await session.commit()
    assert await load_session(session, token) is not None
    await ban_user(session, actor=await _admin(session), user_id=user_id, reason="spam")
    await session.commit()
    assert await load_session(session, token) is None
    row = (
        await session.execute(
            sa.text("SELECT banned_reason FROM users WHERE id = :u"), {"u": str(user_id)}
        )
    ).one()
    assert row[0] == "spam"
    with pytest.raises(AuthError):
        await refuse_if_unusable(session, user_id)


async def test_delete_cascades_personal_data(session: AsyncSession) -> None:
    from app.services.users import delete_user

    user_id = await _user(session)
    await session.execute(
        sa.text(
            "INSERT INTO identities (user_id, provider, subject, email)"
            " VALUES (:u, 'google', 's-1', 'a@example.com')"
        ),
        {"u": str(user_id)},
    )
    await session.execute(
        sa.text(
            "INSERT INTO sessions (token_hash, user_id, csrf_token, expires_at)"
            " VALUES ('h', :u, 'c', now() + interval '1 day')"
        ),
        {"u": str(user_id)},
    )
    await session.commit()
    await delete_user(session, actor=await _admin(session), user_id=user_id)
    await session.commit()
    for table in ("sessions", "identities", "invite_uses"):
        count = (
            await session.execute(
                sa.text(f"SELECT count(*) FROM {table} WHERE user_id = :u"), {"u": str(user_id)}
            )
        ).scalar_one()
        assert count == 0
    assert (
        await session.execute(
            sa.text("SELECT count(*) FROM users WHERE id = :u"), {"u": str(user_id)}
        )
    ).scalar_one() == 0


async def test_delete_with_owner_rows_requires_heir_or_legacy(session: AsyncSession) -> None:
    from app.errors import ConflictError
    from app.services.users import delete_user

    owner = await _user(session)
    heir = await _user(session)
    await session.execute(
        sa.text(
            "INSERT INTO invites (scope, created_by) VALUES ('course-a', :u)",
        ),
        {"u": str(owner)},
    )
    await session.commit()
    with pytest.raises(ConflictError):
        await delete_user(session, actor=await _admin(session), user_id=owner)
    await session.rollback()
    await delete_user(session, actor=await _admin(session), user_id=owner, heir_id=heir)
    await session.commit()
    kept = (
        await session.execute(sa.text("SELECT created_by FROM invites WHERE scope = 'course-a'"))
    ).one()
    assert str(kept[0]) == str(heir)


async def test_legacy_holder_keeps_materials_but_cannot_sign_in(
    session: AsyncSession,
) -> None:
    from app.errors import AuthError
    from app.services.auth import refuse_if_unusable
    from app.services.users import delete_user

    owner = await _user(session)
    await session.execute(
        sa.text("INSERT INTO invites (scope, created_by) VALUES ('c', :u)"), {"u": str(owner)}
    )
    await session.commit()
    await delete_user(session, actor=await _admin(session), user_id=owner, legacy=True)
    await session.commit()
    row = (
        await session.execute(
            sa.text("SELECT is_legacy FROM users WHERE id = :u"), {"u": str(owner)}
        )
    ).one()
    assert row[0] is True
    assert (
        await session.execute(sa.text("SELECT count(*) FROM invites WHERE scope = 'c'"))
    ).scalar_one() == 1
    with pytest.raises(AuthError):
        await refuse_if_unusable(session, owner)
