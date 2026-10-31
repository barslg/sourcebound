"""Idempotent Procrastinate schema bootstrap for the one-shot migrate step.

`procrastinate schema --apply` is not re-runnable, so the migrate
service cannot call it blindly on every deploy. ensure_procrastinate_schema()
applies the bundled schema only when `procrastinate_jobs` is absent.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from app.config import get_settings
from app.errors import ConfigurationError
from app.tasks.worker import psycopg_dsn

APP_DOTTED_PATH = "app.tasks.jobs.app"


def schema_present(dsn: str) -> bool:
    """True when the Procrastinate tables already exist on this database."""
    import psycopg

    with psycopg.connect(dsn, connect_timeout=5) as conn:
        row = conn.execute("select to_regclass('public.procrastinate_jobs')").fetchone()
    return row is not None and row[0] is not None


def ensure_procrastinate_schema(dsn: str | None = None) -> None:
    """Apply the bundled Procrastinate schema once; no-op when present."""
    resolved_dsn = dsn or psycopg_dsn(get_settings().database_url)
    if schema_present(resolved_dsn):
        return
    exe = shutil.which("procrastinate")
    if exe is None:
        raise ConfigurationError("procrastinate CLI is not on PATH")
    env = dict(os.environ)
    async_url = resolved_dsn
    if async_url.startswith("postgresql://"):
        async_url = "postgresql+asyncpg://" + async_url[len("postgresql://") :]
    env["DATABASE_URL"] = async_url
    root = Path(__file__).resolve().parent.parent.parent.parent
    env["PYTHONPATH"] = str(root / "src") + os.pathsep + env.get("PYTHONPATH", "")
    subprocess.run(
        [exe, "--app", APP_DOTTED_PATH, "schema", "--apply"],
        check=True,
        env=env,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )


def main() -> None:
    """Entrypoint for the compose migrate service."""
    ensure_procrastinate_schema()


if __name__ == "__main__":
    main()
