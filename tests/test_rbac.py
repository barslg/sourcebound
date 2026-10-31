"""RBAC catalogue, principal cache, require(), and scope 404s.

Covers PRINCIPLES.md authorisation: catalogue declared in code and
synced at startup, administrator rewritten to all codes, new
permissions granted to nobody, unknown codes rejected on save,
one permission error message, out-of-scope objects answer 404.
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


async def _user_id(sess: AsyncSession, email: str) -> uuid.UUID:
    row = (
        await sess.execute(
            sa.text("INSERT INTO users (email, display_name) VALUES (:e, :n) RETURNING id"),
            {"e": email, "n": "Test"},
        )
    ).one()
    await sess.commit()
    return uuid.UUID(str(row[0]))


async def test_startup_sync_registers_catalogue_and_admin(session: AsyncSession) -> None:
    from app.rbac.catalogue import CATALOGUE
    from app.startup import startup_sync

    await startup_sync(session)
    await session.commit()
    count = (await session.execute(sa.text("SELECT count(*) FROM permissions"))).scalar_one()
    assert count == len(CATALOGUE)
    admin_grants = (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM role_permissions WHERE role_id ="
                " (SELECT id FROM roles WHERE name = 'administrator')"
            )
        )
    ).scalar_one()
    assert admin_grants == len(CATALOGUE)


async def test_catalogue_sync_registers_every_code(session: AsyncSession) -> None:
    from app.rbac.catalogue import CATALOGUE, sync_catalogue

    await sync_catalogue(session)
    codes = {
        row[0] for row in (await session.execute(sa.text("SELECT code FROM permissions"))).all()
    }
    assert {entry.code for entry in CATALOGUE} <= codes
    assert any(entry.code.startswith("section.") for entry in CATALOGUE)


async def test_admin_role_rewritten_to_all_codes_on_sync(session: AsyncSession) -> None:
    from app.rbac.catalogue import CATALOGUE, ensure_admin_role, sync_catalogue

    await sync_catalogue(session)
    admin_id = await ensure_admin_role(session)
    await session.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE role_id = :r AND permission_code = "
            "(SELECT permission_code FROM role_permissions WHERE role_id = :r LIMIT 1)"
        ),
        {"r": str(admin_id)},
    )
    await session.commit()
    await ensure_admin_role(session)
    count = (
        await session.execute(
            sa.text("SELECT count(*) FROM role_permissions WHERE role_id = :r"),
            {"r": str(admin_id)},
        )
    ).scalar_one()
    assert count == len(CATALOGUE)


async def test_new_permission_granted_to_no_existing_role(session: AsyncSession) -> None:
    from app.rbac.catalogue import PermissionDef, ensure_admin_role, sync_catalogue
    from app.rbac.roles import create_role

    await sync_catalogue(session)
    role_id = await create_role(
        session,
        name="editors",
        permission_codes=["section.courses"],
        actor=None,
    )
    await session.commit()
    extra = PermissionDef(
        code="course.archive",
        category="courses",
        label="Archive courses",
        description="Move a course to the archive.",
    )
    await sync_catalogue(session, extra=[extra])
    rows = (
        await session.execute(
            sa.text(
                "SELECT permission_code FROM role_permissions WHERE role_id = :r",
            ),
            {"r": str(role_id)},
        )
    ).all()
    assert "course.archive" not in {row[0] for row in rows}
    admin_id = await ensure_admin_role(session)
    admin_codes = {
        row[0]
        for row in (
            await session.execute(
                sa.text("SELECT permission_code FROM role_permissions WHERE role_id = :r"),
                {"r": str(admin_id)},
            )
        ).all()
    }
    assert "course.archive" in admin_codes


async def test_unknown_permission_code_rejected_on_role_save(session: AsyncSession) -> None:
    from app.errors import ValidationError
    from app.rbac.catalogue import sync_catalogue
    from app.rbac.roles import create_role

    await sync_catalogue(session)
    with pytest.raises(ValidationError):
        await create_role(session, name="bad", permission_codes=["nope.missing"], actor=None)


async def test_require_raises_the_one_permission_error(session: AsyncSession) -> None:
    from app.errors import ForbiddenError
    from app.rbac.catalogue import sync_catalogue
    from app.rbac.principal import load_principal
    from app.rbac.require import require

    await sync_catalogue(session)
    user_id = await _user_id(session, unique_email())
    principal = await load_principal(session, user_id)
    with pytest.raises(ForbiddenError) as exc_info:
        require(principal, "section.courses")
    assert str(exc_info.value) == "You do not have permission for this action."
    assert exc_info.value.status_code == 403


async def test_superadmin_from_config_has_everything(session: AsyncSession) -> None:
    from app.rbac.catalogue import CATALOGUE, sync_catalogue
    from app.rbac.principal import load_principal
    from app.rbac.require import require

    await sync_catalogue(session)
    email = unique_email("boss")
    row = (
        await session.execute(
            sa.text(
                "INSERT INTO users (email, display_name, is_superadmin)"
                " VALUES (:e, :n, true) RETURNING id"
            ),
            {"e": email, "n": "Boss"},
        )
    ).one()
    await session.commit()
    principal = await load_principal(session, uuid.UUID(str(row[0])))
    assert principal.is_superadmin
    for entry in CATALOGUE:
        require(principal, entry.code)


async def test_object_outside_scope_answers_404(session: AsyncSession) -> None:
    from app.errors import NotFoundError
    from app.rbac.catalogue import sync_catalogue
    from app.rbac.principal import load_principal
    from app.rbac.require import require_scope
    from app.rbac.roles import assign_role, create_role

    await sync_catalogue(session)
    await create_role(session, name="student", permission_codes=["section.courses"], actor=None)
    user_id = await _user_id(session, unique_email())
    await assign_role(session, user_id, "student", scope_tags=["course-a"], actor=None)
    await session.commit()
    principal = await load_principal(session, user_id)
    require_scope(principal, ["course-a"])
    with pytest.raises(NotFoundError):
        require_scope(principal, ["course-b"])


async def test_principal_cache_bumped_on_role_change(session: AsyncSession) -> None:
    from app.rbac.catalogue import sync_catalogue
    from app.rbac.principal import bump_version, load_principal
    from app.rbac.roles import assign_role, create_role

    await sync_catalogue(session)
    await create_role(session, name="student", permission_codes=["section.courses"], actor=None)
    user_id = await _user_id(session, unique_email())
    before = await load_principal(session, user_id)
    assert "section.courses" not in before.permissions
    await assign_role(session, user_id, "student", scope_tags=["course-a"], actor=None)
    await session.commit()
    bump_version()
    after = await load_principal(session, user_id)
    assert "section.courses" in after.permissions
    assert after.scope_tags == ("course-a",)
