"""Gate (permanent): a test module never imports a name that pytest would collect as a test.

`from conftest import test_database_url` put a helper named like a test into
test_sentinels.py's namespace, and pytest ran it as a test case. Import such
names under an alias that does not start with `test`. Caller: pytest.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]


def offenders(root: Path) -> list[str]:
    """`file:line name` of every unaliased import of a test-looking name in a test module."""
    found: list[str] = []
    for path in sorted(root.glob("test_*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.ImportFrom) or node.col_offset != 0:
                continue
            for alias in node.names:
                visible = alias.asname or alias.name
                if visible.startswith("test"):
                    found.append(f"{path.name}:{node.lineno} {visible}")
    return found


def test_no_test_module_imports_a_collectable_name() -> None:
    assert offenders(TESTS) == []
