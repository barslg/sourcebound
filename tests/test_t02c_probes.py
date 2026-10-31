"""T02c probes: second-pass review of T02b (fail before the fix, green after).

Every item below was measured on the real database. Tests use the
isolated muse_test database; the worker and downgrade probes build a
scratch database so the shared one is never left migrated down.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from collections.abc import Iterator
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import sqlalchemy as sa
from conftest import test_database_url as _test_database_url
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


def _settings(issuer_url: str, **extra: object) -> object:
    from app.config import Settings

    return Settings(
        database_url=_test_database_url(),
        google_oauth_client_id="test-client-id",
        google_oauth_client_secret="test-client-secret",  # type: ignore[arg-type]
        session_secret_key="test-session-secret",  # type: ignore[arg-type]
        session_secure_cookie=False,
        session_ttl_s=3600,
        oidc_issuer=issuer_url,
        oidc_redirect_url="http://test/api/v1/auth/callback",
        superadmin_emails="",
        **extra,  # type: ignore[arg-type]
    )


def _override(app: object) -> None:
    from app import db as db_module

    async def _test_session():  # type: ignore[no-untyped-def]
        sess = make_session_factory()()  # type: ignore[operator]
        try:
            yield sess
            await sess.commit()
        except Exception:
            await sess.rollback()
            raise
        finally:
            await sess.close()

    app.dependency_overrides[db_module.get_session] = _test_session  # type: ignore[attr-defined]


async def _user(session: AsyncSession, email: str = "") -> uuid.UUID:
    row = (
        await session.execute(
            sa.text("INSERT INTO users (email, display_name) VALUES (:e, 'N') RETURNING id"),
            {"e": email or unique_email()},
        )
    ).one()
    await session.commit()
    return uuid.UUID(str(row[0]))


def _superadmin(user_id: uuid.UUID) -> object:
    from app.rbac.principal import Principal

    return Principal(
        user_id=user_id,
        email="admin@example.com",
        roles=("administrator",),
        permissions=frozenset(),
        scope_tags=(),
        is_superadmin=True,
    )


async def _catalogue(session: AsyncSession) -> None:
    from app.rbac.catalogue import ensure_admin_role, sync_catalogue

    await sync_catalogue(session)
    await ensure_admin_role(session)
    await session.commit()


async def _grant(
    session: AsyncSession, email: str, role: str, codes: list[str], tags: list[str]
) -> object:
    """Create a user with one role grant, return their live principal."""
    from app.rbac.principal import load_principal
    from app.rbac.roles import assign_role, create_role

    await _catalogue(session)
    await create_role(session, name=role, permission_codes=codes, actor=None)
    await session.commit()
    user_id = await _user(session, email)
    await assign_role(session, user_id, role, scope_tags=tags, actor=None)
    await session.commit()
    return await load_principal(session, user_id)


async def _login(client: httpx.AsyncClient, session: AsyncSession, subject: str) -> None:
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
    done = await client.get(
        "/api/v1/auth/callback", params={"state": state, "code": code}, follow_redirects=False
    )
    assert done.status_code == 303, done.text


async def _csrf(client: httpx.AsyncClient) -> str:
    response = await client.get("/api/v1/auth/csrf")
    assert response.status_code == 200
    return response.json()["csrf_token"]  # type: ignore[no-any-return]


def _procrastinate_exe() -> str:
    """The procrastinate CLI next to the running interpreter."""
    exe = os.path.join(os.path.dirname(sys.executable), "procrastinate")
    if os.path.exists(exe):
        return exe
    return shutil.which("procrastinate") or "procrastinate"


def _run_isolated(
    args: list[str], *, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess with src/ on the path from the repo root."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    child_env = dict(os.environ)
    child_env["PYTHONPATH"] = (
        os.path.join(root, "src") + os.pathsep + child_env.get("PYTHONPATH", "")
    )
    if env:
        child_env.update(env)
    return subprocess.run(
        args, capture_output=True, text=True, cwd=root, env=child_env, timeout=120
    )


async def test_t02c_jobs_and_worker_import_in_isolated_processes() -> None:
    """Item 1: both task modules import cleanly, in either order, fresh process."""
    first = _run_isolated([sys.executable, "-c", "import app.tasks.jobs"])
    assert first.returncode == 0, first.stderr
    second = _run_isolated([sys.executable, "-c", "import app.tasks.worker"])
    assert second.returncode == 0, second.stderr


def _fresh_db_url(name: str) -> str:
    """A scratch database URL derived from the isolated test URL."""
    import re

    return re.sub(r"/[^/?]*(\?.*)?$", f"/{name}\\1", _test_database_url())


@pytest.fixture()
def fresh_database() -> Iterator[str]:
    """A scratch database migrated to head with the procrastinate schema.

    Proves a fresh deploy starts: alembic upgrade plus the procrastinate
    bootstrap run against an empty database, the exact migrate-step path.
    """
    import psycopg

    bindir = os.path.dirname(sys.executable)
    old_path = os.environ.get("PATH", "")
    # `make check` runs under `uv run`, which puts the venv bin (and the
    # procrastinate CLI) on PATH; direct interpreter runs need it spelled out.
    if bindir not in old_path.split(os.pathsep):
        os.environ["PATH"] = bindir + os.pathsep + old_path
    name = f"muse_t02c_{os.getpid()}"
    base = _test_database_url().replace("postgresql+asyncpg://", "postgresql://", 1)
    maintenance = base.rsplit("/", 1)[0] + "/postgres"
    url = _fresh_db_url(name)
    with psycopg.connect(maintenance, connect_timeout=5, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}"')
        conn.execute(f'CREATE DATABASE "{name}"')
    try:
        from alembic import command
        from alembic.config import Config

        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, "head")
        from app.tasks.schema import ensure_procrastinate_schema
        from app.tasks.worker import psycopg_dsn

        ensure_procrastinate_schema(psycopg_dsn(url))
        yield url
    finally:
        os.environ["PATH"] = old_path
        with psycopg.connect(maintenance, connect_timeout=5, autocommit=True) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                f" WHERE datname = '{name}' AND pid <> pg_backend_pid()"
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{name}"')


async def test_t02c_cli_worker_one_shot_on_fresh_database(fresh_database: str) -> None:
    """Item 1: the compose worker entrypoint finishes an echo on a fresh deploy."""
    import asyncio as asyncio_mod

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.rbac.catalogue import sync_catalogue
    from app.rbac.principal import load_principal
    from app.rbac.roles import assign_role, create_role
    from app.services.operations import submit_echo

    engine = create_async_engine(fresh_database, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            await sync_catalogue(session)
            await create_role(
                session, name="operator", permission_codes=["operation.run"], actor=None
            )
            row = (
                await session.execute(
                    sa.text(
                        "INSERT INTO users (email, display_name) VALUES (:e, 'W') RETURNING id"
                    ),
                    {"e": unique_email("fresh-worker")},
                )
            ).scalar_one()
            user_id = uuid.UUID(str(row))
            await assign_role(session, user_id, "operator", scope_tags=[], actor=None)
            await session.commit()
            actor = await load_principal(session, user_id)
            operation_id = await submit_echo(
                session, actor=actor, payload={"message": "fresh-deploy"}, defer=None
            )
            await session.commit()
        from app.tasks.jobs import enqueue_operation

        await enqueue_operation(operation_id, fresh_database)
        async_url = fresh_database
        worker = _run_isolated(
            [_procrastinate_exe(), "--app", "app.tasks.worker.app", "worker", "--one-shot"],
            env={"DATABASE_URL": async_url},
        )
        assert worker.returncode == 0, worker.stderr[-2000:]
        async with factory() as check:
            for _ in range(100):
                status = (
                    await check.execute(
                        sa.text("SELECT status FROM operations WHERE id = :o"),
                        {"o": str(operation_id)},
                    )
                ).scalar_one()
                if status == "done":
                    break
                await check.rollback()
                await asyncio_mod.sleep(0.2)
            row = (
                await check.execute(
                    sa.text("SELECT status, result FROM operations WHERE id = :o"),
                    {"o": str(operation_id)},
                )
            ).one()
            assert row[0] == "done", row
            assert row[1]["echo"] == "fresh-deploy"
    finally:
        await engine.dispose()


async def test_t02c_echo_api_with_real_get_session(session: AsyncSession, issuer_url: str) -> None:
    """Item 1: POST /operations/echo through the production session path.

    No get_session override: the real dependency commits and rolls back,
    and the enqueue leaves a procrastinate row on the same database.
    """
    import app.db as db_module
    from app.main import create_app
    from app.rbac.catalogue import sync_catalogue

    await sync_catalogue(session)
    await session.commit()
    from app.rbac.roles import assign_role, create_role

    await create_role(session, name="operator", permission_codes=["operation.run"], actor=None)
    await session.commit()
    real_create_engine = db_module.create_engine
    test_url = _test_database_url()
    db_module._engine = None  # type: ignore[attr-defined]
    db_module._session_factory = None  # type: ignore[attr-defined]
    db_module.create_engine = lambda url=None: real_create_engine(test_url)  # type: ignore[method-assign]
    try:
        app = create_app(_settings(issuer_url), sync_on_startup=False)  # type: ignore[arg-type]
        transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await _login(client, session, "echo-real-session")
            user_id = (
                await session.execute(
                    sa.text("SELECT id FROM users WHERE email = 'echo-real-session@example.com'")
                )
            ).scalar_one()
            await assign_role(
                session, uuid.UUID(str(user_id)), "operator", scope_tags=[], actor=None
            )
            await session.commit()
            token = await _csrf(client)
            answer = await client.post(
                "/api/v1/operations/echo",
                json={"message": "via-real-session"},
                headers={"X-CSRF-Token": token},
            )
            assert answer.status_code == 202, answer.text
            operation_id = answer.json()["operation_id"]
    finally:
        db_module.create_engine = real_create_engine  # type: ignore[method-assign]
        db_module._engine = None  # type: ignore[attr-defined]
        db_module._session_factory = None  # type: ignore[attr-defined]
    async with make_session_factory()() as check:
        status = (
            await check.execute(
                sa.text("SELECT status FROM operations WHERE id = :o"),
                {"o": operation_id},
            )
        ).scalar_one()
        assert status in ("queued", "running", "done")
        queued = (
            await check.execute(sa.text("SELECT count(*) FROM procrastinate_jobs"))
        ).scalar_one()
        assert queued >= 1


async def test_t02c_get_session_rolls_back_on_error() -> None:
    """Mutant killer: get_session must roll back, never commit, on error."""
    import app.db as db_module

    real_create_engine = db_module.create_engine
    test_url = _test_database_url()
    db_module._engine = None  # type: ignore[attr-defined]
    db_module._session_factory = None  # type: ignore[attr-defined]
    db_module.create_engine = lambda url=None: real_create_engine(test_url)  # type: ignore[method-assign]
    marker = unique_email("rollback")
    try:
        agen = db_module.get_session()
        owned = await agen.__anext__()
        await owned.execute(
            sa.text("INSERT INTO users (email, display_name) VALUES (:e, 'R')"),
            {"e": marker},
        )
        with pytest.raises(RuntimeError):
            await agen.athrow(RuntimeError("boom"))
    finally:
        db_module.create_engine = real_create_engine  # type: ignore[method-assign]
        db_module._engine = None  # type: ignore[attr-defined]
        db_module._session_factory = None  # type: ignore[attr-defined]
    async with make_session_factory()() as check:
        count = (
            await check.execute(
                sa.text("SELECT count(*) FROM users WHERE email = :e"), {"e": marker}
            )
        ).scalar_one()
        assert count == 0


async def test_t02c_scoped_moderator_cannot_ban_global_admin(session: AsyncSession) -> None:
    """Item 2: a course-a moderator cannot ban a globally scoped administrator."""
    from app.errors import ForbiddenError, NotFoundError
    from app.rbac.roles import assign_role, create_role
    from app.services.users import ban_user

    await _catalogue(session)
    await create_role(session, name="moderator", permission_codes=["user.ban"], actor=None)
    await session.commit()
    moderator_id = await _user(session, unique_email("moderator"))
    await assign_role(session, moderator_id, "moderator", scope_tags=["course-a"], actor=None)
    global_admin_id = await _user(session, unique_email("global-admin"))
    await assign_role(session, global_admin_id, "administrator", scope_tags=[], actor=None)
    await session.commit()
    from app.rbac.principal import load_principal

    moderator = await load_principal(session, moderator_id)
    with pytest.raises((ForbiddenError, NotFoundError)):
        await ban_user(session, actor=moderator, user_id=global_admin_id, reason="takeover")  # type: ignore[arg-type]
    await session.rollback()


async def test_t02c_scoped_actor_cannot_delete_tagless_user(session: AsyncSession) -> None:
    """Item 2: a target with no grants is in scope for a global actor only."""
    from app.errors import ForbiddenError, NotFoundError
    from app.services.users import delete_user

    await _catalogue(session)
    from app.rbac.roles import assign_role, create_role

    await create_role(session, name="cleaner", permission_codes=["user.delete"], actor=None)
    await session.commit()
    cleaner_id = await _user(session, unique_email("cleaner"))
    await assign_role(session, cleaner_id, "cleaner", scope_tags=["course-a"], actor=None)
    tagless_id = await _user(session, unique_email("tagless"))
    await session.commit()
    from app.rbac.principal import load_principal

    cleaner = await load_principal(session, cleaner_id)
    with pytest.raises((ForbiddenError, NotFoundError)):
        await delete_user(session, actor=cleaner, user_id=tagless_id)  # type: ignore[arg-type]
    await session.rollback()


async def test_t02c_scoped_actor_cannot_revoke_global_admin_role(
    session: AsyncSession,
) -> None:
    """Item 2 (mutant killer): revoke_role carries the assign scope check."""
    from app.errors import ForbiddenError, NotFoundError
    from app.rbac.roles import assign_role, create_role, revoke_role

    await _catalogue(session)
    await create_role(session, name="moderator", permission_codes=["role.assign"], actor=None)
    await session.commit()
    moderator_id = await _user(session, unique_email("moderator"))
    await assign_role(session, moderator_id, "moderator", scope_tags=["course-a"], actor=None)
    global_admin_id = await _user(session, unique_email("global-admin"))
    await assign_role(session, global_admin_id, "administrator", scope_tags=[], actor=None)
    await session.commit()
    from app.rbac.principal import load_principal

    moderator = await load_principal(session, moderator_id)
    with pytest.raises((ForbiddenError, NotFoundError)):
        await revoke_role(session, global_admin_id, "administrator", actor=moderator)  # type: ignore[arg-type]
    await session.rollback()
    # The in-scope revoke still works.
    peer_id = await _user(session, unique_email("peer"))
    await assign_role(session, peer_id, "moderator", scope_tags=["course-a"], actor=None)
    await session.commit()
    await revoke_role(session, peer_id, "moderator", actor=moderator)  # type: ignore[arg-type]
    await session.commit()


async def test_t02c_assign_scope_is_per_permission(session: AsyncSession) -> None:
    """Item 3: role.assign in course-a plus student in course-b grants no course-b assign."""
    from app.errors import ForbiddenError
    from app.rbac.principal import load_principal
    from app.rbac.roles import assign_role, create_role

    await _catalogue(session)
    await create_role(session, name="assigner", permission_codes=["role.assign"], actor=None)
    await create_role(session, name="limited", permission_codes=["section.courses"], actor=None)
    await create_role(session, name="student", permission_codes=["section.courses"], actor=None)
    holder_id = await _user(session, unique_email("holder"))
    await assign_role(session, holder_id, "assigner", scope_tags=["course-a"], actor=None)
    await assign_role(session, holder_id, "student", scope_tags=["course-b"], actor=None)
    await session.commit()
    holder = await load_principal(session, holder_id)
    target_id = await _user(session, unique_email("target"))
    with pytest.raises(ForbiddenError):
        await assign_role(session, target_id, "limited", scope_tags=["course-b"], actor=holder)  # type: ignore[arg-type]
    await session.rollback()
    await assign_role(session, target_id, "limited", scope_tags=["course-a"], actor=holder)  # type: ignore[arg-type]
    await session.commit()


async def test_t02c_global_admin_sees_all_users_after_enrolling(session: AsyncSession) -> None:
    """Item 3: a globally scoped admin who joins course-b still lists everyone."""
    from app.rbac.roles import assign_role
    from app.services.users import list_users

    await _catalogue(session)
    from app.rbac.roles import create_role as _create_role

    await _create_role(session, name="student", permission_codes=["section.courses"], actor=None)
    admin_id = await _user(session, unique_email("admin"))
    await assign_role(session, admin_id, "administrator", scope_tags=[], actor=None)
    await assign_role(session, admin_id, "student", scope_tags=["course-b"], actor=None)
    await _user(session, unique_email("course-a-person"))
    course_a_id = await _user(session, unique_email("course-a-other"))
    from app.rbac.roles import create_role

    await create_role(session, name="tutors", permission_codes=["section.courses"], actor=None)
    await assign_role(session, course_a_id, "tutors", scope_tags=["course-a"], actor=None)
    tagless_id = await _user(session, unique_email("tagless"))
    await session.commit()
    from app.rbac.principal import load_principal

    admin = await load_principal(session, admin_id)
    assert admin.is_superadmin is False
    directory = await list_users(session, actor=admin)  # type: ignore[arg-type]
    seen = {row["id"] for row in directory}
    assert str(admin_id) in seen
    assert str(course_a_id) in seen
    assert str(tagless_id) in seen


async def test_t02c_invite_create_requires_containment(session: AsyncSession) -> None:
    """Item 4: overlap is not enough; every invited tag must lie in scope."""
    from app.errors import ForbiddenError, NotFoundError
    from app.services.invites import create_invite

    await _catalogue(session)
    from app.rbac.roles import assign_role, create_role

    await create_role(session, name="inviter", permission_codes=["invite.create"], actor=None)
    await session.commit()
    inviter_id = await _user(session, unique_email("inviter"))
    await assign_role(session, inviter_id, "inviter", scope_tags=["course-a"], actor=None)
    await session.commit()
    from app.rbac.principal import load_principal

    inviter = await load_principal(session, inviter_id)
    with pytest.raises((ForbiddenError, NotFoundError)):
        await create_invite(session, actor=inviter, course_tags=["course-a", "course-OTHER"])  # type: ignore[arg-type]
    await session.rollback()
    raw = await create_invite(session, actor=inviter, course_tags=["course-a"])  # type: ignore[arg-type]
    assert raw
    await session.commit()


async def test_t02c_operation_view_is_scoped(session: AsyncSession) -> None:
    """Item 5: operation.view reads in-scope owners' operations only."""
    from app.errors import NotFoundError
    from app.rbac.roles import assign_role, create_role
    from app.services.operations import get_operation, submit_echo

    await _catalogue(session)
    await create_role(session, name="viewer", permission_codes=["operation.view"], actor=None)
    await create_role(session, name="runner", permission_codes=["operation.run"], actor=None)
    await create_role(session, name="student", permission_codes=["section.courses"], actor=None)
    await session.commit()
    viewer_id = await _user(session, unique_email("viewer"))
    await assign_role(session, viewer_id, "viewer", scope_tags=["course-a"], actor=None)
    await assign_role(session, viewer_id, "student", scope_tags=["course-a"], actor=None)
    mate_id = await _user(session, unique_email("mate"))
    await assign_role(session, mate_id, "runner", scope_tags=["course-a"], actor=None)
    await assign_role(session, mate_id, "student", scope_tags=["course-a"], actor=None)
    stranger_id = await _user(session, unique_email("stranger"))
    await assign_role(session, stranger_id, "runner", scope_tags=["course-b"], actor=None)
    await assign_role(session, stranger_id, "student", scope_tags=["course-b"], actor=None)
    await session.commit()
    from app.rbac.principal import load_principal

    viewer = await load_principal(session, viewer_id)
    mate = await load_principal(session, mate_id)
    stranger = await load_principal(session, stranger_id)
    mate_op = await submit_echo(session, actor=mate, payload={"message": "mate"}, defer=None)  # type: ignore[arg-type]
    stranger_op = await submit_echo(  # type: ignore[arg-type]
        session, actor=stranger, payload={"message": "stranger"}, defer=None
    )
    await session.commit()
    found = await get_operation(session, actor=viewer, operation_id=mate_op)  # type: ignore[arg-type]
    assert found["operation_id"] == str(mate_op)
    with pytest.raises(NotFoundError):
        await get_operation(session, actor=viewer, operation_id=stranger_op)  # type: ignore[arg-type]
    await session.rollback()
    admin = _superadmin(await _user(session, unique_email("super")))
    seen = await get_operation(session, actor=admin, operation_id=stranger_op)  # type: ignore[arg-type]
    assert seen["operation_id"] == str(stranger_op)


async def test_t02c_heir_must_hold_edit_rights_in_scope(session: AsyncSession) -> None:
    """Item 6: an heir without course rights cannot inherit course-scoped invites."""
    from app.errors import ValidationError
    from app.rbac.roles import assign_role, create_role
    from app.services.invites import create_invite
    from app.services.users import delete_user

    await _catalogue(session)
    await create_role(
        session,
        name="teacher",
        permission_codes=["course.edit", "user.delete", "invite.create"],
        actor=None,
    )
    await create_role(session, name="bystander", permission_codes=["section.courses"], actor=None)
    await create_role(session, name="student", permission_codes=["section.courses"], actor=None)
    await session.commit()
    teacher_id = await _user(session, unique_email("teacher"))
    await assign_role(session, teacher_id, "teacher", scope_tags=["course-a"], actor=None)
    owner_id = await _user(session, unique_email("owner"))
    await assign_role(session, owner_id, "student", scope_tags=["course-a"], actor=None)
    outsider_id = await _user(session, unique_email("outsider"))
    await assign_role(session, outsider_id, "bystander", scope_tags=["course-b"], actor=None)
    await session.commit()
    from app.rbac.principal import load_principal
    from app.services.invites import hash_invite_token

    teacher = await load_principal(session, teacher_id)
    raw = await create_invite(session, actor=teacher, course_tags=["course-a"])  # type: ignore[arg-type]
    assert raw
    await session.execute(
        sa.text("UPDATE invites SET created_by = :u WHERE token_hash = :h"),
        {"u": str(owner_id), "h": hash_invite_token(raw)},
    )
    await session.commit()
    with pytest.raises(ValidationError):
        await delete_user(session, actor=teacher, user_id=owner_id, heir_id=outsider_id)  # type: ignore[arg-type]
    await session.rollback()
    heir_id = await _user(session, unique_email("heir"))
    await assign_role(session, heir_id, "teacher", scope_tags=["course-a"], actor=None)
    await session.commit()
    await delete_user(session, actor=teacher, user_id=owner_id, heir_id=heir_id)  # type: ignore[arg-type]
    await session.commit()
    moved = (
        await session.execute(
            sa.text("SELECT created_by FROM invites WHERE created_by = :h"),
            {"h": str(heir_id)},
        )
    ).all()
    assert moved


async def test_t02c_administrator_role_is_immutable(session: AsyncSession) -> None:
    """Item 7 (mutant killer): role.edit cannot delete or shrink administrator."""
    from app.errors import ForbiddenError, ValidationError
    from app.rbac.catalogue import codes
    from app.rbac.roles import assign_role, create_role, delete_role, update_role

    await _catalogue(session)
    full = sorted(codes())
    assert full
    await create_role(session, name="editor", permission_codes=full, actor=None)
    await session.commit()
    editor_id = await _user(session, unique_email("editor"))
    await assign_role(session, editor_id, "editor", scope_tags=[], actor=None)
    await session.commit()
    from app.rbac.principal import load_principal

    editor = await load_principal(session, editor_id)
    assert editor.is_superadmin is False
    with pytest.raises(ForbiddenError):
        await update_role(
            session,
            name="administrator",
            permission_codes=full[1:],
            actor=editor,  # type: ignore[arg-type]
        )
    await session.rollback()
    with pytest.raises(ForbiddenError):
        await delete_role(session, name="administrator", actor=editor)  # type: ignore[arg-type]
    await session.rollback()
    admin = _superadmin(await _user(session, unique_email("super")))
    with pytest.raises(ValidationError):
        await update_role(
            session,
            name="administrator",
            permission_codes=full[1:],
            actor=admin,  # type: ignore[arg-type]
        )
    await session.rollback()
    with pytest.raises(ValidationError):
        await delete_role(session, name="administrator", actor=admin)  # type: ignore[arg-type]
    await session.rollback()


async def test_t02c_signed_out_invite_flow_redirects_to_course(
    session: AsyncSession, issuer_url: str
) -> None:
    """Item 8: signed-out accept 303s to login with the invite id stashed, never the token."""
    from app.main import create_app
    from app.rbac.roles import assign_role, create_role
    from app.services.invites import create_invite

    await _catalogue(session)
    await create_role(session, name="inviter", permission_codes=["invite.create"], actor=None)
    await session.commit()
    inviter_id = await _user(session, unique_email("inviter"))
    await assign_role(session, inviter_id, "inviter", scope_tags=["course-a"], actor=None)
    await session.commit()
    from app.rbac.principal import load_principal

    inviter = await load_principal(session, inviter_id)
    raw = await create_invite(session, actor=inviter, course_tags=["course-a"])  # type: ignore[arg-type]
    await session.commit()
    invite_id = (await session.execute(sa.text("SELECT id FROM invites"))).scalar_one()
    app = create_app(_settings(issuer_url), sync_on_startup=False)  # type: ignore[arg-type]
    _override(app)
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        accept = await client.post(f"/api/v1/invites/{raw}/accept", follow_redirects=False)
        assert accept.status_code == 303, accept.text
        target = accept.headers["location"]
        assert raw not in target
        assert target.startswith("/api/v1/auth/login")
        assert "/api/v1/invites/" not in target
        stashed = (
            await session.execute(
                sa.text(
                    "SELECT pending_invite_id FROM sessions WHERE pending_invite_id IS NOT NULL"
                )
            )
        ).all()
        assert len(stashed) == 1 and str(stashed[0][0]) == str(invite_id)
        next_urls = (await session.execute(sa.text("SELECT next_url FROM auth_states"))).all()
        assert all(raw not in (row[0] or "") for row in next_urls)
        start = await client.get(target)
        assert start.status_code == 307, start.text
        assert raw not in start.headers["location"]
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
        stored = (
            await session.execute(
                sa.text("SELECT nonce, code_verifier FROM auth_states WHERE state = :s"),
                {"s": state},
            )
        ).one()
        code = register_code("invited-student", "test-client-id", stored[0])
        stored_verifier_slot(code)["code_verifier"] = stored[1]
        done = await client.get(
            "/api/v1/auth/callback",
            params={"state": state, "code": code},
            follow_redirects=False,
        )
        assert done.status_code == 303, done.text
        assert done.headers["location"] == "/courses/course-a"
        assert raw not in done.headers["location"]
        me = await client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert "student" in me.json()["roles"]
        assert "course-a" in me.json()["scope_tags"]
    stashed = (await session.execute(sa.text("SELECT pending_invite_id FROM sessions"))).all()
    assert stashed and all(str(row[0]) == str(invite_id) for row in stashed if row[0])


async def test_t02c_revoke_invite_by_id(session: AsyncSession, issuer_url: str) -> None:
    """Item 9: invites revoke by id (what GET /invites returns), not by raw token."""
    from app.errors import NotFoundError
    from app.main import create_app
    from app.rbac.roles import assign_role, create_role
    from app.services.invites import accept_invite, create_invite, hash_invite_token

    await _catalogue(session)
    await create_role(
        session, name="staff", permission_codes=["invite.create", "invite.revoke"], actor=None
    )
    await session.commit()
    staff_id = await _user(session, unique_email("staff"))
    await assign_role(session, staff_id, "staff", scope_tags=["course-a"], actor=None)
    await session.commit()
    from app.rbac.principal import load_principal

    staff = await load_principal(session, staff_id)
    raw = await create_invite(session, actor=staff, course_tags=["course-a"])  # type: ignore[arg-type]
    await session.commit()
    app = create_app(_settings(issuer_url), sync_on_startup=False)  # type: ignore[arg-type]
    _override(app)
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, session, "reload-staff")
        await session.execute(
            sa.text(
                "UPDATE users SET is_superadmin = true WHERE email = 'reload-staff@example.com'"
            )
        )
        await session.commit()
        token = await _csrf(client)
        headers = {"X-CSRF-Token": token}
        listed = await client.get("/api/v1/invites")
        assert listed.status_code == 200
        invite_id = next(row["id"] for row in listed.json() if row["course_tags"] == ["course-a"])
        revoked = await client.post(f"/api/v1/invites/{invite_id}/revoke", headers=headers)
        assert revoked.status_code == 200, revoked.text
        student_id = await _user(session, unique_email("late-student"))
        from app.errors import ConflictError

        with pytest.raises(ConflictError):
            await accept_invite(session, user_id=student_id, token_hash=hash_invite_token(raw))
        await session.rollback()
        missing = await client.post(f"/api/v1/invites/{raw}/revoke", headers=headers)
        assert missing.status_code == 404
    with pytest.raises(NotFoundError):
        from app.services.invites import revoke_invite

        await revoke_invite(session, actor=staff, invite_id=uuid.uuid4())  # type: ignore[arg-type]
    await session.rollback()


async def test_t02c_origin_null_is_rejected(session: AsyncSession, issuer_url: str) -> None:
    """LOW: Origin: null never authorises an unsafe request."""
    from app.main import create_app
    from app.rbac.roles import assign_role, create_role

    await _catalogue(session)
    await create_role(session, name="operator", permission_codes=["operation.run"], actor=None)
    await session.commit()
    app = create_app(_settings(issuer_url), sync_on_startup=False)  # type: ignore[arg-type]
    _override(app)
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, session, "null-origin-user")
        user_id = (
            await session.execute(
                sa.text("SELECT id FROM users WHERE email = 'null-origin-user@example.com'")
            )
        ).scalar_one()
        await assign_role(session, uuid.UUID(str(user_id)), "operator", scope_tags=[], actor=None)
        await session.commit()
        token = await _csrf(client)
        same_origin = await client.post(
            "/api/v1/operations/echo",
            json={"message": "same-origin"},
            headers={"X-CSRF-Token": token, "Origin": "http://test"},
        )
        assert same_origin.status_code == 202, same_origin.text
        nulled = await client.post(
            "/api/v1/operations/echo",
            json={"message": "null-origin"},
            headers={"X-CSRF-Token": token, "Origin": "null"},
        )
        assert nulled.status_code == 403, nulled.text


async def test_t02c_string_email_verified_does_not_verify(issuer_url: str) -> None:
    """LOW: the string "false" is not a verified email."""
    from t02_issuer import _mint_id_token

    from app.auth.oidc import GoogleProvider

    provider = GoogleProvider(
        client_id="test-client-id",
        client_secret="test-client-secret",
        redirect_url="http://test/api/v1/auth/callback",
        issuer=issuer_url,
        allowed_domains=(),
    )
    token = _mint_id_token(
        issuer_url,
        "test-client-id",
        "nonce-string-verified",
        "string-verified-user",
        "",
        email_verified="false",  # type: ignore[arg-type]
    )
    identity = await provider.verify_id_token(token, nonce="nonce-string-verified")
    assert identity.email_verified is False


async def test_t02c_ban_decrements_used_count(session: AsyncSession) -> None:
    """LOW: banning removes invite uses and gives the seats back."""
    from app.rbac.roles import assign_role, create_role
    from app.services.invites import accept_invite, create_invite, hash_invite_token
    from app.services.users import ban_user

    await _catalogue(session)
    await create_role(session, name="student", permission_codes=["section.courses"], actor=None)
    await create_role(session, name="staff", permission_codes=["invite.create"], actor=None)
    await session.commit()
    creator = await _user(session, unique_email("creator"))
    student = await _user(session, unique_email("student"))
    from app.rbac.principal import load_principal

    await assign_role(session, creator, "staff", scope_tags=[], actor=None)
    await session.commit()
    staff = await load_principal(session, creator)
    raw = await create_invite(session, actor=staff, course_tags=["course-a"], max_uses=2)  # type: ignore[arg-type]
    await session.commit()
    await accept_invite(session, user_id=student, token_hash=hash_invite_token(raw))
    await session.commit()
    before = (
        await session.execute(
            sa.text("SELECT used_count FROM invites WHERE token_hash = :h"),
            {"h": hash_invite_token(raw)},
        )
    ).scalar_one()
    assert before == 1
    admin = _superadmin(await _user(session, unique_email("admin")))
    await ban_user(session, actor=admin, user_id=student, reason="spam")  # type: ignore[arg-type]
    await session.commit()
    after = (
        await session.execute(
            sa.text("SELECT used_count FROM invites WHERE token_hash = :h"),
            {"h": hash_invite_token(raw)},
        )
    ).scalar_one()
    assert after == 0


def test_t02c_downgrade_survives_orphaned_audit_and_pending(fresh_database: str) -> None:
    """LOW: migration 0007 downgrades past deleted authors and stale pending state.

    Sync like the other migration round-trips: Alembic runs its own
    event loop and cannot share the async test loop.
    """
    import psycopg

    sync_dsn = fresh_database.replace("postgresql+asyncpg://", "postgresql://", 1)
    with psycopg.connect(sync_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO users (id, email, display_name) VALUES (gen_random_uuid(),"
            " 't02c-doomed@example.com', 'Doomed')"
        )
        doomed = conn.execute(
            "SELECT id FROM users WHERE email = 't02c-doomed@example.com'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO audit_log (actor_id, action, entity, entity_id)"
            " VALUES (%s, 'user.ban', 'user', gen_random_uuid())",
            (str(doomed),),
        )
        conn.execute("DELETE FROM users WHERE id = %s", (str(doomed),))
        conn.execute(
            "INSERT INTO sessions (token_hash, csrf_token, expires_at,"
            " absolute_expires_at, pending_invite_id)"
            " VALUES ('t02c-stale', 'csrf', now() + interval '1 hour',"
            " now() + interval '1 day', 'not-a-uuid')"
        )
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", fresh_database)
    command.downgrade(cfg, "0006")
    command.upgrade(cfg, "head")
    with psycopg.connect(sync_dsn) as conn:
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        assert version == "0011"
        orphans = conn.execute(
            "SELECT count(*) FROM audit_log WHERE actor_id IS NOT NULL"
            " AND actor_id NOT IN (SELECT id FROM users)"
        ).fetchone()[0]
        assert orphans == 0
