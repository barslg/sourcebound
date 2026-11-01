"""Unban restores sign-in under the ban scope rules; the tag catalogue is scope-filtered.

The owner could ban but never lift a ban, and every form made authors
type scope tags by hand. These tests hold the two backend halves of the
fix: POST /users/{id}/unban (same permission code and scope check as
ban) and the tag catalogue behind the pickers.
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


def _principal(
    user_id: uuid.UUID, *, codes: set[str], tags: tuple[str, ...] = (), root: bool = False
):  # noqa: ANN202
    from app.rbac.principal import Principal

    return Principal(
        user_id=user_id,
        email="actor@example.com",
        roles=(),
        permissions=frozenset(codes),
        scope_tags=tags,
        is_superadmin=root,
    )


async def test_unban_restores_sign_in_and_writes_audit(session: AsyncSession) -> None:
    from app.auth.session import create_session, load_session
    from app.errors import AuthError
    from app.services.auth import refuse_if_unusable
    from app.services.users import ban_user, unban_user

    target = await _user(session)
    await session.execute(
        sa.text(
            "INSERT INTO identities (user_id, provider, subject, email)"
            " VALUES (:u, 'google', 's-unban', 'x@example.com')"
        ),
        {"u": str(target)},
    )
    await session.commit()
    admin = _principal(await _user(session), codes={"user.ban"}, root=True)
    await ban_user(session, actor=admin, user_id=target, reason="spam")  # type: ignore[arg-type]
    await session.commit()
    with pytest.raises(AuthError):
        await refuse_if_unusable(session, target)
    await unban_user(session, actor=admin, user_id=target)  # type: ignore[arg-type]
    await session.commit()
    await refuse_if_unusable(session, target)
    row = (
        await session.execute(
            sa.text("SELECT banned_at, banned_reason FROM users WHERE id = :u"), {"u": str(target)}
        )
    ).one()
    assert row[0] is None and row[1] == ""
    token, _ = await create_session(session, target)
    await session.commit()
    assert await load_session(session, token) is not None
    audit = (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM audit_log WHERE action = 'user.unban' AND entity_id = :u"
            ),
            {"u": str(target)},
        )
    ).scalar_one()
    assert audit == 1


async def test_unban_refuses_a_user_who_is_not_banned(session: AsyncSession) -> None:
    from app.errors import ConflictError
    from app.services.users import unban_user

    target = await _user(session)
    admin = _principal(await _user(session), codes={"user.ban"}, root=True)
    with pytest.raises(ConflictError):
        await unban_user(session, actor=admin, user_id=target)  # type: ignore[arg-type]
    await session.rollback()


async def test_course_scoped_moderator_cannot_unban_a_global_admin(session: AsyncSession) -> None:
    from app.errors import NotFoundError
    from app.rbac.catalogue import sync_catalogue
    from app.rbac.roles import assign_role, create_role
    from app.services.users import ban_user, unban_user

    await sync_catalogue(session)
    await create_role(session, name="chief", permission_codes=["user.ban"], actor=None)
    await session.commit()
    chief = await _user(session, unique_email("chief"))
    await assign_role(session, chief, "chief", scope_tags=[], actor=None)
    await session.commit()
    root = _principal(await _user(session), codes={"user.ban"}, root=True)
    await ban_user(session, actor=root, user_id=chief, reason="test")  # type: ignore[arg-type]
    await session.commit()
    moderator = _principal(await _user(session), codes={"user.ban"}, tags=("course-a",))
    with pytest.raises(NotFoundError):
        await unban_user(session, actor=moderator, user_id=chief)  # type: ignore[arg-type]
    await session.rollback()
    still = (
        await session.execute(
            sa.text("SELECT banned_at FROM users WHERE id = :u"), {"u": str(chief)}
        )
    ).one()
    assert still[0] is not None


async def test_unban_needs_the_ban_permission(session: AsyncSession) -> None:
    from app.errors import ForbiddenError
    from app.services.users import unban_user

    target = await _user(session)
    with pytest.raises(ForbiddenError):
        await unban_user(
            session, actor=_principal(await _user(session), codes=set()), user_id=target
        )  # type: ignore[arg-type]
    await session.rollback()


async def test_tag_catalogue_lists_courses_per_tag_and_respects_scope(
    session: AsyncSession,
) -> None:
    from app.services.courses import create_course, list_courses, list_tags

    owner = await _user(session, unique_email("owner"))
    other = await _user(session, unique_email("other"))
    a = await create_course(
        session, owner_id=owner, title="Alpha", scope_tags=["course-a", "shared"]
    )
    await create_course(session, owner_id=owner, title="Beta", scope_tags=["shared"])
    await create_course(session, owner_id=other, title="Gamma", scope_tags=["course-g"])
    await session.commit()

    root = _principal(owner, codes=set(), root=True)
    everything = await list_tags(session, actor=root)  # type: ignore[arg-type]
    assert [t["tag"] for t in everything] == ["course-a", "course-g", "shared"]
    shared = next(t for t in everything if t["tag"] == "shared")
    assert sorted(c["title"] for c in shared["courses"]) == ["Alpha", "Beta"]
    course_a = next(t for t in everything if t["tag"] == "course-a")
    assert {"id": str(a), "title": "Alpha"} in course_a["courses"]

    scoped = _principal(owner, codes={"course.edit"}, tags=("course-a", "shared"))
    mine = await list_tags(session, actor=scoped)  # type: ignore[arg-type]
    assert [t["tag"] for t in mine] == ["course-a", "shared"]
    assert all("Gamma" not in [c["title"] for c in t["courses"]] for t in mine)

    narrow = _principal(owner, codes={"course.edit"}, tags=("course-a",))
    only = await list_tags(session, actor=narrow)  # type: ignore[arg-type]
    assert [t["tag"] for t in only] == ["course-a"]

    rows = await list_courses(session, owner_id=owner, is_superadmin=False)
    assert {r["title"]: r["scope_tags"] for r in rows} == {
        "Alpha": ["course-a", "shared"],
        "Beta": ["shared"],
    }
