"""Gate: the generated client permission file matches the backend catalogue and routes."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_generated_permissions_are_current() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "export_permissions.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
