"""Budgeted mutation testing of the lines a push changed, against the permanent suite.

Why in-house: no mutation tool is a dependency of the project and the build
rules forbid adding one without the owner; this runner needs only the
standard library. It mutates the changed lines of src/app/**/*.py (diff
against --base), one mutant at a time, in a throw-away copy of the tree,
and runs the permanent suite (tests/ minus tests/gates, which test the
gates) against each. A mutant the suite does not kill is a SURVIVOR: the
changed line is not guarded, and the run fails unless the survivor is in
scripts/mutation_allowlist.txt as `path:line:operator reason`.

Mutations (one per site): comparison flipped (== != < <= > >= in is),
`and`/`or` swapped, `+`/`-` swapped, True/False swapped, `not` dropped,
integer constant + 1.

Budget: --budget seconds (default 540). Mutants not reached are counted and
printed; they are not survivors. A run whose changed lines hold mutable
sites but ran none fails (an empty gate is not a pass).

Usage: python scripts/mutate_changed.py [--base REF] [--root DIR]
       [--budget SEC] [--test-cmd CMD]
Prints "mutate_changed: R of M mutants run, K killed, S survived".
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ALLOWLIST_FILE = "scripts/mutation_allowlist.txt"
DEFAULT_TEST_CMD = (
    "python -m pytest -x -q -m 'not live' tests --ignore=tests/gates -p no:cacheprovider"
)

COMPARE_FLIP = {
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.Lt: ast.GtE,
    ast.GtE: ast.Lt,
    ast.Gt: ast.LtE,
    ast.LtE: ast.Gt,
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
}


@dataclass(frozen=True)
class Mutant:
    """One mutation site: the n-th mutable node of a file, on a given line."""

    path: str
    line: int
    index: int
    operator: str


def sites(tree: ast.AST) -> list[tuple[ast.AST, int, str]]:
    """(node, line, operator) of every mutable node, in a stable order."""
    found: list[tuple[ast.AST, int, str]] = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Compare) and type(node.ops[0]) in COMPARE_FLIP:
            found.append((node, line, f"cmp:{type(node.ops[0]).__name__}"))
        elif isinstance(node, ast.BoolOp):
            found.append((node, line, f"bool:{type(node.op).__name__}"))
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
            found.append((node, line, f"arith:{type(node.op).__name__}"))
        elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
            found.append((node, line, f"const:{node.value}"))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            found.append((node, line, "not"))
        elif isinstance(node, ast.Constant) and type(node.value) is int and node.value > 1:
            found.append((node, line, "int+1"))
    return found


def apply(source: str, index: int) -> str:
    """The source with the index-th site mutated."""
    tree = ast.parse(source)
    target = sites(tree)[index][0]
    if isinstance(target, ast.Compare):
        target.ops[0] = COMPARE_FLIP[type(target.ops[0])]()
    elif isinstance(target, ast.BoolOp):
        target.op = ast.Or() if isinstance(target.op, ast.And) else ast.And()
    elif isinstance(target, ast.BinOp):
        target.op = ast.Sub() if isinstance(target.op, ast.Add) else ast.Add()
    elif isinstance(target, ast.UnaryOp):
        for parent in ast.walk(tree):
            for name, value in ast.iter_fields(parent):
                if value is target:
                    setattr(parent, name, target.operand)
                elif isinstance(value, list):
                    setattr(parent, name, [target.operand if v is target else v for v in value])
    elif isinstance(target, ast.Constant):
        target.value = (not target.value) if isinstance(target.value, bool) else target.value + 1
    return ast.unparse(ast.fix_missing_locations(tree))


def changed_lines(root: Path, base: str) -> dict[str, set[int]]:
    """src/app python file -> its added/changed line numbers versus base."""
    out = subprocess.run(
        ["git", "diff", "-U0", f"{base}...HEAD", "--", "src/app"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if out.returncode != 0:
        out = subprocess.run(
            ["git", "diff", "-U0", base, "--", "src/app"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
    result: dict[str, set[int]] = {}
    current = ""
    for row in out.stdout.splitlines():
        if row.startswith("+++ b/"):
            current = row[6:]
        elif row.startswith("@@") and current.endswith(".py"):
            m = re.search(r"\+(\d+)(?:,(\d+))?", row)
            if m:
                start, count = int(m.group(1)), int(m.group(2) or 1)
                result.setdefault(current, set()).update(range(start, start + count))
    return result


def plan(root: Path, lines: dict[str, set[int]]) -> list[Mutant]:
    """Every mutant whose site sits on a changed line."""
    mutants: list[Mutant] = []
    for rel, wanted in sorted(lines.items()):
        path = root / rel
        if not path.exists():
            continue
        for index, (_, line, operator) in enumerate(sites(ast.parse(path.read_text("utf-8")))):
            if line in wanted:
                mutants.append(Mutant(rel, line, index, operator))
    return mutants


def allowed(root: Path) -> dict[str, str]:
    """`path:line:operator` -> reason."""
    file = root / ALLOWLIST_FILE
    entries: dict[str, str] = {}
    if file.exists():
        for row in file.read_text("utf-8").splitlines():
            if row.strip() and not row.startswith("#"):
                key, _, reason = row.partition(" ")
                entries[key] = reason.strip()
    return entries


def run_suite(work: Path, test_cmd: str) -> subprocess.CompletedProcess[str]:
    """Run the permanent suite once in the working copy."""
    return subprocess.run(
        shlex.split(test_cmd),
        cwd=work,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


def run_mutant(work: Path, mutant: Mutant, test_cmd: str) -> bool:
    """True when the suite fails on the mutant (killed)."""
    target = work / mutant.path
    original = target.read_text("utf-8")
    try:
        target.write_text(apply(original, mutant.index), "utf-8")
        return run_suite(work, test_cmd).returncode != 0
    finally:
        target.write_text(original, "utf-8")


def main(argv: list[str]) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="origin/dev")
    parser.add_argument("--root", type=Path, default=REPO)
    parser.add_argument("--budget", type=float, default=540.0)
    parser.add_argument("--test-cmd", default=DEFAULT_TEST_CMD)
    args = parser.parse_args(argv)
    root: Path = args.root.resolve()
    lines = changed_lines(root, args.base)
    mutants = plan(root, lines)
    if not mutants:
        print(f"mutate_changed: 0 of 0 mutants run: no mutable changed lines against {args.base}")
        return 0
    skip = allowed(root)
    started = time.monotonic()
    survivors: list[str] = []
    killed = ran = 0
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "tree"
        shutil.copytree(
            root,
            work,
            ignore=shutil.ignore_patterns(".git", ".venv", "node_modules", "tmp", "__pycache__"),
        )
        if run_suite(work, args.test_cmd).returncode != 0:
            print("mutate_changed: the suite is red before any mutation; fix it first")
            return 2
        for mutant in mutants:
            if time.monotonic() - started > args.budget:
                break
            ran += 1
            key = f"{mutant.path}:{mutant.line}:{mutant.operator}"
            if run_mutant(work, mutant, args.test_cmd):
                killed += 1
            elif key in skip:
                print(f"allowed survivor {key}: {skip[key]}")
            else:
                survivors.append(f"SURVIVED {key}: no test fails when this line is changed")
    for row in survivors:
        print(row)
    print(
        f"mutate_changed: {ran} of {len(mutants)} mutants run, {killed} killed,"
        f" {len(survivors)} survived"
        f" ({time.monotonic() - started:.0f}s of {args.budget:.0f}s budget)"
    )
    return 1 if survivors or ran == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
