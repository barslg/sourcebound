"""Gates for scripts/check_public_tree.py.

A fixture file carrying a /Users/ path MUST fail the check;
the real tree MUST pass it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_public_tree.py"


def run_check(root: Path) -> subprocess.CompletedProcess[str]:
    """Run check_public_tree.py against a fixture root."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_users_path_fails(tmp_path: Path) -> None:
    """A leaked /Users/ path fails the public-tree check (exit 1)."""
    leaked = tmp_path / "notes.txt"
    leaked.write_text("config lives at /Users/someone/secret\n", encoding="utf-8")
    proc = run_check(tmp_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "notes.txt" in proc.stdout


def test_gocspx_key_fails(tmp_path: Path) -> None:
    """A Google client-secret shape fails the public-tree check (exit 1)."""
    leaked = tmp_path / "key.txt"
    leaked.write_text("secret=GOCSPX-abcdef0123456789\n", encoding="utf-8")
    proc = run_check(tmp_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "key.txt" in proc.stdout


def test_rule_files_are_allowlisted(tmp_path: Path) -> None:
    """Rule files naming secret shapes pass via the explicit allow-list."""
    gitleaks = tmp_path / ".gitleaks.toml"
    gitleaks.write_text("regex = '''GOCSPX-[A-Za-z0-9_-]{8,}'''\n", encoding="utf-8")
    checker = tmp_path / "scripts" / "check_public_tree.py"
    checker.parent.mkdir(parents=True, exist_ok=True)
    checker.write_text('MARKERS = ("/Users/", "GOCSPX-", "LMS2")\n', encoding="utf-8")
    fixture = tmp_path / "tests" / "gates" / "test_gate.py"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fixture.write_text('leaked.write_text("secret=GOCSPX-abcdef0123456789\\n")\n', encoding="utf-8")
    proc = run_check(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_real_tree_passes() -> None:
    """The real tracked tree carries no forbidden markers (exit 0)."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
