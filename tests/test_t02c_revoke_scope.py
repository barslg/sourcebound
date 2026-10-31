"""T02c scope mutant killer: cross-course role revoke refused by SCOPE.

A course-a holder of role.assign plus invite.create revoking an
invite.create role from a user who holds it only in course-b must be
refused by the SCOPE check in revoke_role. The existing administrator
revoke test passes on the codes check first, so the scope mutant
survives without this test.
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


async def _catalogue(session: AsyncSession) -> None:
    from app.rbac.catalogue import sync_catalogue

    await sync_catalogue(session)
    await session.commit()


async def _user(session: AsyncSession, email: str) -> uuid.UUID:
    row = (
        await session.execute(
            sa.text("INSERT INTO users (email, display_name) VALUES (:e, 'N') RETURNING id"),
            {"e": email},
        )
    ).one()
    await session.commit()
    return uuid.UUID(str(row[0]))


async def test_t02c_scoped_holder_cannot_revoke_other_course_grant(
    session: AsyncSession,
) -> None:
    """Cross-course revoke dies on scope even though the codes check passes."""
    from app.errors import ForbiddenError
    from app.rbac.principal import load_principal
    from app.rbac.roles import assign_role, create_role, revoke_role

    await _catalogue(session)
    await create_role(
        session,
        name="course-manager",
        permission_codes=["role.assign", "invite.create"],
        actor=None,
    )
    await create_role(session, name="inviter", permission_codes=["invite.create"], actor=None)
    await session.commit()
    holder_id = await _user(session, unique_email("holder"))
    await assign_role(session, holder_id, "course-manager", scope_tags=["course-a"], actor=None)
    target_id = await _user(session, unique_email("target"))
    await assign_role(session, target_id, "inviter", scope_tags=["course-b"], actor=None)
    await session.commit()
    holder = await load_principal(session, holder_id)
    with pytest.raises(ForbiddenError):
        await revoke_role(session, target_id, "inviter", actor=holder)  # type: ignore[arg-type]
    await session.rollback()
    peer_id = await _user(session, unique_email("peer"))
    await assign_role(session, peer_id, "inviter", scope_tags=["course-a"], actor=None)
    await session.commit()
    await revoke_role(session, peer_id, "inviter", actor=holder)  # type: ignore[arg-type]
    await session.commit()
