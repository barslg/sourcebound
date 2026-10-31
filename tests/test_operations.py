"""Long writes are operations: 202 plus worker completion plus SSE.

Covers PRINCIPLES.md writes/jobs/events: validate, authorise, persist
the operation row, commit, enqueue a Procrastinate job, answer 202 with
the operation id; the worker re-authorises with the stored actor,
updates progress, writes audit, and emits a notification row that the
SSE stream delivers. An audit failure rolls the change back.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
import sqlalchemy as sa
from conftest import test_database_url
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


def _actor(user_id: uuid.UUID, codes: frozenset[str] = frozenset({"operation.run"})) -> object:
    from app.rbac.principal import Principal

    return Principal(
        user_id=user_id,
        email="worker@example.com",
        roles=("operator",),
        permissions=codes,
        scope_tags=(),
        is_superadmin=False,
    )


async def test_submit_persists_queued_operation(session: AsyncSession) -> None:
    from app.services.operations import submit_echo

    user_id = await _user(session)
    operation_id = await submit_echo(
        session, actor=_actor(user_id), payload={"message": "hi"}, defer=None
    )
    await session.commit()
    row = (
        await session.execute(
            sa.text("SELECT status, progress, actor_id FROM operations WHERE id = :o"),
            {"o": str(operation_id)},
        )
    ).one()
    assert row[0] == "queued"
    assert float(row[1]) == 0
    assert str(row[2]) == str(user_id)


async def test_worker_completes_operation_and_notifies(session: AsyncSession) -> None:
    from app.rbac.catalogue import sync_catalogue
    from app.rbac.principal import bump_version, load_principal
    from app.rbac.roles import assign_role, create_role
    from app.services.operations import execute_operation, submit_echo

    await sync_catalogue(session)
    await create_role(session, name="operator", permission_codes=["operation.run"], actor=None)
    user_id = await _user(session)
    await assign_role(session, user_id, "operator", scope_tags=[], actor=None)
    await session.commit()
    bump_version()
    actor = await load_principal(session, user_id)
    operation_id = await submit_echo(session, actor=actor, payload={"message": "hi"}, defer=None)
    await session.commit()
    from app.services.events import subscribe

    received: list[dict[str, object]] = []

    async def _listen() -> None:
        async for event in subscribe(user_id):
            received.append(event)
            break

    listener = asyncio.ensure_future(_listen())
    await asyncio.sleep(0.05)
    await execute_operation(operation_id, database_url=test_database_url())
    await asyncio.wait_for(listener, timeout=10)
    async with make_session_factory()() as check:
        status, progress, result = (
            await check.execute(
                sa.text("SELECT status, progress, result FROM operations WHERE id = :o"),
                {"o": str(operation_id)},
            )
        ).one()
        assert status == "done"
        assert float(progress) == 1.0
        assert result["echo"] == "hi"
        audits = (
            await check.execute(
                sa.text("SELECT count(*) FROM audit_log WHERE entity_id = :o"),
                {"o": str(operation_id)},
            )
        ).scalar_one()
        assert audits >= 1
        notes = (
            await check.execute(
                sa.text("SELECT count(*) FROM notifications WHERE user_id = :u"),
                {"u": str(user_id)},
            )
        ).scalar_one()
        assert notes == 1
    assert received and received[0].get("operation_id") == str(operation_id)


async def test_worker_reauthorises_and_fails_without_rights(
    session: AsyncSession,
) -> None:
    from app.services.operations import execute_operation, submit_echo

    user_id = await _user(session)
    operation_id = await submit_echo(
        session, actor=_actor(user_id), payload={"message": "hi"}, defer=None
    )
    await session.commit()
    await session.execute(sa.text("DELETE FROM user_roles WHERE user_id = :u"), {"u": str(user_id)})
    await session.commit()
    from app.rbac.principal import bump_version

    bump_version()
    await execute_operation(
        operation_id,
        database_url=test_database_url(),
        required_code="operation.secret",
    )
    async with make_session_factory()() as check:
        status = (
            await check.execute(
                sa.text("SELECT status FROM operations WHERE id = :o"),
                {"o": str(operation_id)},
            )
        ).scalar_one()
        assert status == "failed"


async def test_audit_failure_rolls_back_the_change(session: AsyncSession) -> None:
    from app.rbac.catalogue import sync_catalogue
    from app.rbac.roles import create_role, list_roles

    await sync_catalogue(session)
    await session.commit()
    with pytest.raises(TypeError):
        await create_role(
            session,
            name="doomed-role",
            permission_codes=["section.courses"],
            actor=None,
            audit_meta={"poison": object()},
        )
    await session.rollback()
    names = [role["name"] for role in await list_roles(session)]
    assert "doomed-role" not in names


async def test_enqueue_registers_a_procrastinate_job(session: AsyncSession) -> None:
    from conftest import require_postgres, test_database_url

    from app.services.operations import submit_echo
    from app.tasks import schema as schema_mod
    from app.tasks.worker import psycopg_dsn

    url = test_database_url()
    require_postgres(url)
    schema_mod.ensure_procrastinate_schema(psycopg_dsn(url))
    user_id = await _user(session)

    deferred: list[uuid.UUID] = []

    async def _defer(operation_id: uuid.UUID) -> None:
        deferred.append(operation_id)
        from app.tasks.jobs import enqueue_operation

        await enqueue_operation(operation_id, url)

    operation_id = await submit_echo(
        session, actor=_actor(user_id), payload={"message": "hi"}, defer=_defer
    )
    await session.commit()
    assert deferred == [operation_id]
    async with make_session_factory()() as check:
        jobs = (
            await check.execute(sa.text("SELECT count(*) FROM procrastinate_jobs"))
        ).scalar_one()
        assert jobs >= 1
