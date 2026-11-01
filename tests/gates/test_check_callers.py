"""Gates for scripts/check_callers.py: an orphan function is red, a wired one is green."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from gate_tree import ROOT, load_script, write_tree

checker = load_script("check_callers")

BASE = {
    "src/app/__init__.py": "",
    "src/app/svc/__init__.py": "",
    "src/app/svc/work.py": (
        "def used() -> int:\n    return 1\n\n\ndef orphan() -> int:\n    return 2\n"
    ),
    "src/app/main.py": "from app.svc.work import used\n\nvalue = used()\n",
}


def test_orphan_function_is_reported(tmp_path: Path) -> None:
    """A public function nobody imports fails the gate by name."""
    root = write_tree(tmp_path, BASE)
    checked, problems = checker.check(root)
    assert checked == 2
    assert [line.split(": ", 1)[1] for line in problems] == ["app.svc.work:orphan has no caller"]


def test_wired_function_passes(tmp_path: Path) -> None:
    """Once a production module imports it, the same tree is clean."""
    files = {
        **BASE,
        "src/app/main.py": "from app.svc.work import orphan, used\n\nx = orphan() + used()\n",
    }
    checked, problems = checker.check(write_tree(tmp_path, files))
    assert (checked, problems) == (2, [])


def test_module_attribute_call_counts(tmp_path: Path) -> None:
    """`from app.svc import work; work.orphan()` resolves through the module alias."""
    files = {
        **BASE,
        "src/app/main.py": "from app.svc import work\n\nx = work.orphan() + work.used()\n",
    }
    assert checker.check(write_tree(tmp_path, files))[1] == []


def test_reexport_chain_counts(tmp_path: Path) -> None:
    """A package re-export resolves to the defining module."""
    files = {
        **BASE,
        "src/app/svc/__init__.py": "from app.svc.work import orphan, used\n",
        "src/app/main.py": "from app.svc import orphan, used\n\nx = orphan() + used()\n",
    }
    assert checker.check(write_tree(tmp_path, files))[1] == []


def test_called_from_a_sibling_function_counts_recursion_does_not(tmp_path: Path) -> None:
    """A helper reached from another function of its module passes; self-recursion does not."""
    helper = (
        "def entry() -> int:\n    return _h() + helper()\n\n\n"
        "def helper() -> int:\n    return 1\n\n\n"
        "def _h() -> int:\n    return 1\n\n\n"
        "def loop(n: int) -> int:\n    return loop(n - 1)\n"
    )
    files = {
        **BASE,
        "src/app/svc/work.py": helper,
        "src/app/main.py": "from app.svc.work import entry\n\nx = entry()\n",
    }
    _, problems = checker.check(write_tree(tmp_path, files))
    assert [line.split(": ", 1)[1] for line in problems] == ["app.svc.work:loop has no caller"]


def test_test_only_caller_does_not_count(tmp_path: Path) -> None:
    """tests/ is not production: a symbol only a test calls is still an orphan."""
    files = {**BASE, "tests/test_work.py": "from app.svc.work import orphan\n\norphan()\n"}
    _, problems = checker.check(write_tree(tmp_path, files))
    assert len(problems) == 1


def test_method_without_attribute_use_is_reported(tmp_path: Path) -> None:
    """A public method no other module touches by name is an orphan."""
    files = {
        **BASE,
        "src/app/svc/work.py": (
            "class Box:\n    def open(self) -> int:\n        return 1\n\n"
            "    def lost(self) -> int:\n        return 2\n"
        ),
        "src/app/main.py": "from app.svc.work import Box\n\nx = Box().open()\n",
    }
    _, problems = checker.check(write_tree(tmp_path, files))
    assert [line.split(": ", 1)[1] for line in problems] == ["app.svc.work:Box.lost has no caller"]


def test_handler_map_and_decorated_are_exempt(tmp_path: Path) -> None:
    """A dict-value handler and a registered (decorated) function need no importer."""
    files = {
        **BASE,
        "src/app/svc/work.py": (
            "def step() -> int:\n    return 1\n\n\n"
            "HANDLERS = {'a': step}\n\n\n"
            "@router.get('/x')\ndef route() -> int:\n    return 1\n"
        ),
        "src/app/main.py": "x = 1\n",
    }
    checked, problems = checker.check(write_tree(tmp_path, files))
    assert (checked, problems) == (0, [])


def test_empty_tree_fails_the_run(tmp_path: Path) -> None:
    """Zero symbols checked is a failure, not a pass."""
    write_tree(tmp_path, {"src/app/__init__.py": ""})
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_callers.py"), "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1
    assert "0 symbols checked" in proc.stdout


def test_real_tree_is_clean() -> None:
    """The committed tree has no orphan and the count is non-trivial."""
    checked, problems = checker.check(ROOT)
    assert problems == []
    assert checked > 300
