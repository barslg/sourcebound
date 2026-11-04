"""Thin job wrappers: deferred work enters through the services layer.

The Procrastinate task only carries the operation id; execute_operation()
opens a fresh session per task (jobs never reuse request sessions),
re-authorises, and emits audit plus notifications.
"""

from __future__ import annotations

from uuid import UUID

from procrastinate import App, PsycopgConnector

from app.agents.base import validate_agents
from app.tasks.worker import app, psycopg_dsn

# The worker process imports this module at start (procrastinate import_paths):
# prompts, strict schemas and the closed vocabularies are checked here, as the
# API lifespan checks them, so a drifted enum stops the worker instead of
# failing a paid run.
validate_agents()


async def run_operation_job(operation_id: str) -> None:
    """Run one operation to completion inside the worker process."""
    from app.services.operations import execute_operation

    await execute_operation(UUID(operation_id))


operations_run = app.task(queue="operations", name="operations.run")(run_operation_job)


async def enqueue_operation(operation_id: UUID, database_url: str) -> None:
    """Enqueue an operation run on the given database (no shared state)."""
    ephemeral = App(connector=PsycopgConnector(conninfo=psycopg_dsn(database_url)))
    task = ephemeral.task(queue="operations", name="operations.run")(run_operation_job)
    async with ephemeral.open_async():
        await task.defer_async(operation_id=str(operation_id))


@app.periodic(cron="* * * * *")
@app.task(queue="operations", name="builds.revive", queueing_lock="builds.revive")
async def revive_builds_job(timestamp: int) -> None:
    """Every minute: re-queue course builds that say running while no worker drives them."""
    from app.config import get_settings
    from app.db import create_session_factory
    from app.services.build import revive_stranded_builds

    url = get_settings().database_url
    factory = create_session_factory(url)
    try:
        async with factory() as session:
            await revive_stranded_builds(
                session, defer=lambda operation_id: enqueue_operation(operation_id, url)
            )
    finally:
        bind = factory.kw.get("bind")
        if bind is not None:
            await bind.dispose()
