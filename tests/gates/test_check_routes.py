"""Gates for scripts/check_routes.py.

A fixture route without the ctx dependency MUST fail the check;
the real app MUST pass it with a printed inventory table.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_routes.py"


def load_checker():  # type: ignore[no-untyped-def]
    """Import the checker module without executing its CLI entrypoint."""
    spec = importlib.util.spec_from_file_location("check_routes", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_route_without_ctx_fails() -> None:
    """A /api/v1 route that skips the ctx dependency is a violation."""
    checker = load_checker()
    app = FastAPI()

    @app.get("/api/v1/widgets")
    def list_widgets() -> list[str]:
        return []

    route_paths = [getattr(route, "path", "") for route in app.routes]
    assert "/api/v1/widgets" in route_paths
    widgets = app.routes[route_paths.index("/api/v1/widgets")]
    assert checker.route_missing_ctx(widgets)


def test_real_app_passes_with_inventory() -> None:
    """Every real /api/v1 route carries ctx and a code (exit 0)."""
    import os

    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "/api/v1" in proc.stdout
