"""A superadmin names the owner of a group invite; a bare tag never fans out to every owner."""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from t02_helpers import make_session_factory, truncate_all, unique_email

from app.errors import NotFoundError, ValidationError
from app.rbac.principal import Principal
from app.services import courses as course_service
from app.services.invites import create_invite


@pytest.fixture()
async def session() -> AsyncSession:
    factory = make_session_factory()
    async with factory() as sess:
        await truncate_all(sess)
        yield sess
        await sess.rollback()


async def _owner_with_group(session: AsyncSession, tag: str) -> uuid.UUID:
    row = (
        await session.execute(
            sa.text("INSERT INTO users (email, display_name) VALUES (:e, 'O') RETURNING id"),
            {"e": unique_email()},
        )
    ).one()
    owner = uuid.UUID(str(row[0]))
    await course_service.create_course(
        session, owner_id=owner, title="Group course", language="en", scope_tags=[tag]
    )
    await session.commit()
    return owner


async def _root(session: AsyncSession) -> Principal:
    row = (
        await session.execute(
            sa.text("INSERT INTO users (email, display_name) VALUES (:e, 'R') RETURNING id"),
            {"e": unique_email()},
        )
    ).one()
    await session.commit()
    return Principal(
        user_id=uuid.UUID(str(row[0])),
        email="root@example.com",
        roles=(),
        permissions=frozenset({"invite.create"}),
        scope_tags=(),
        is_superadmin=True,
    )


async def _stored(session: AsyncSession) -> list[list[str]]:
    rows = (await session.execute(sa.text("SELECT course_tags FROM invites"))).all()
    return [list(r[0]) for r in rows]


async def test_bare_group_tag_from_a_superadmin_is_refused(session: AsyncSession) -> None:
    await _owner_with_group(session, "oslo")
    await _owner_with_group(session, "oslo")
    with pytest.raises(ValidationError, match="owner"):
        await create_invite(session, actor=await _root(session), course_tags=["oslo"])
    assert await _stored(session) == []


async def test_explicit_owner_tag_opens_only_that_owners_group(session: AsyncSession) -> None:
    first = await _owner_with_group(session, "oslo")
    await _owner_with_group(session, "oslo")
    await create_invite(session, actor=await _root(session), course_tags=[f"{first}:oslo"])
    assert await _stored(session) == [[f"{first}:oslo"]]


async def test_explicit_owner_tag_that_exists_nowhere_is_not_found(session: AsyncSession) -> None:
    await _owner_with_group(session, "oslo")
    with pytest.raises(NotFoundError):
        await create_invite(
            session, actor=await _root(session), course_tags=[f"{uuid.uuid4()}:oslo"]
        )
