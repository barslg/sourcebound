"""Procrastinate schema bootstrap: applies once, never fails a redeploy."""

from __future__ import annotations


def test_ensure_schema_is_idempotent() -> None:
    from conftest import require_postgres, test_database_url

    from app.tasks import schema as schema_mod
    from app.tasks.worker import psycopg_dsn

    url = test_database_url()
    require_postgres(url)
    dsn = psycopg_dsn(url)
    schema_mod.ensure_procrastinate_schema(dsn)
    assert schema_mod.schema_present(dsn)
    schema_mod.ensure_procrastinate_schema(dsn)
    assert schema_mod.schema_present(dsn)
