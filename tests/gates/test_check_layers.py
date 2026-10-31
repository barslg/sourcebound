"""Gates for scripts/check_layers.py.

The fixture tree with a routes->clients import MUST fail the check;
the real tree MUST pass it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_layers.py"


def run_check(root: Path) -> subprocess.CompletedProcess[str]:
    """Run check_layers.py against a tree root."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


def write_pkg(base: Path, rel: str, content: str) -> None:
    """Write a file, creating parents and package markers as needed."""
    target = base / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def test_routes_importing_clients_fails(tmp_path: Path) -> None:
    """A routes module importing clients is a layer violation (exit 1)."""
    write_pkg(tmp_path, "src/app/__init__.py", "")
    write_pkg(tmp_path, "src/app/config.py", "X = 1\n")
    write_pkg(tmp_path, "src/app/routes/__init__.py", "")
    write_pkg(
        tmp_path,
        "src/app/routes/bad.py",
        "from app.clients.meta import MetaClient\n\n\ndef handler() -> None:\n    _ = MetaClient\n",
    )
    proc = run_check(tmp_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "bad.py" in proc.stdout


def test_agents_uuid4_fails(tmp_path: Path) -> None:
    """uuid4 under agents/ is forbidden (exit 1)."""
    write_pkg(tmp_path, "src/app/__init__.py", "")
    write_pkg(tmp_path, "src/app/agents/__init__.py", "")
    write_pkg(
        tmp_path,
        "src/app/agents/saboteur.py",
        "from uuid import uuid4\n\n\ndef new_id() -> str:\n    return str(uuid4())\n",
    )
    proc = run_check(tmp_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "saboteur.py" in proc.stdout


def test_real_tree_passes() -> None:
    """The real tree satisfies every layer rule (exit 0)."""
    proc = run_check(ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
