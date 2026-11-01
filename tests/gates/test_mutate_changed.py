"""Gates for scripts/mutate_changed.py: an unguarded changed line survives and fails the run."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from gate_tree import ROOT, load_script, write_tree

mutator = load_script("mutate_changed")

LIB = "def limit(n):\n    if n > 3:\n        return 'big'\n    return 'small'\n"
TEST_ALL = (
    "from app.lib import limit\n\n\ndef test_both():\n"
    "    assert limit(4) == 'big'\n    assert limit(1) == 'small'\n"
)
TEST_ONE = "from app.lib import limit\n\n\ndef test_small():\n    assert limit(1) == 'small'\n"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _project(tmp_path: Path, test: str) -> Path:
    """A repo whose last commit changes src/app/lib.py, with a one-file suite."""
    root = write_tree(
        tmp_path,
        {"src/app/__init__.py": "", "src/app/lib.py": "x = 1\n", "tests/test_lib.py": test},
    )
    for args in (
        ("init", "-q"),
        ("add", "."),
        ("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "base"),
    ):
        _git(root, *args)
    (root / "src/app/lib.py").write_text(LIB)
    _git(root, "add", ".")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "change")
    return root


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    cmd = "python -m pytest -x -q tests -p no:cacheprovider"
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "mutate_changed.py"),
            "--root",
            str(root),
            "--base",
            "HEAD~1",
            "--test-cmd",
            cmd.replace("python", sys.executable, 1),
        ],
        capture_output=True,
        text=True,
        check=False,
        env={"PYTHONPATH": "src", "PATH": "/usr/bin:/bin"},
    )


def test_sites_and_application() -> None:
    """The comparison on line 2 is a site and flips to <=."""
    found = mutator.sites(mutator.ast.parse(LIB))
    assert [(line, op) for _, line, op in found] == [(2, "cmp:Gt"), (2, "int+1")]
    assert "n <= 3" in mutator.apply(LIB, 0)
    assert "n > 4" in mutator.apply(LIB, 1)


def test_unguarded_changed_line_survives_and_fails(tmp_path: Path) -> None:
    """A suite that never takes the `big` branch leaves the comparison mutant alive."""
    proc = _run(_project(tmp_path, TEST_ONE))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "SURVIVED src/app/lib.py:2:" in proc.stdout


def test_guarded_changed_line_is_killed(tmp_path: Path) -> None:
    """A suite that checks both branches kills every mutant of the changed lines."""
    proc = _run(_project(tmp_path, TEST_ALL))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "2 of 2 mutants run, 2 killed, 0 survived" in proc.stdout


def test_no_changed_source_is_reported_not_silent(tmp_path: Path) -> None:
    """Nothing mutable changed: the line says so (the CI log shows why nothing ran)."""
    root = _project(tmp_path, TEST_ALL)
    (root / "README.txt").write_text("x")
    _git(root, "add", ".")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "docs")
    proc = _run(root)
    assert proc.returncode == 0 and "0 of 0 mutants run" in proc.stdout
