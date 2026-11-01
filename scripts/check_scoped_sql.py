"""SQL scope gate: no statement addresses course content by a model-chosen key alone.

Every SQL string under src/app that reads or writes blocks, block_versions,
citations, translation_blocks, run_steps, learner_answers or
learner_block_views must carry one of:

- a course scope: `course_id`, `course_version_id`, or a join through
  topics / modules / courses; or
- an identity key of one row or one parent: `id`, `block_id`,
  `block_version_id`, `run_id`, `translation_id`, `topic_id`, `version_id`,
  `current_version_id` compared with a bound parameter (`= :x`, `IN (:x)`,
  `= ANY(:x)`), or such a parent column in an INSERT column list.

`plan_node_id` is chosen by the model and repeats in every course, so a
statement keyed by it alone (the 2026-11 T05b defect: a reader hold in
course B held course A's block) fails. What this proves: no statement can
reach another course's rows through a repeated label. What it does not
prove: that the caller checked the owner of an identity key; that is the
route gate's job (check_routes + caller_scope).

Statements are found as string expressions (constants, f-strings, `+`
concatenations) that start with a SQL verb. An exception needs a reason in
ALLOWLIST, keyed `module:function:table`.

Usage: python scripts/check_scoped_sql.py [--root DIR]
Prints "check_scoped_sql: N statements checked"; exit 1 per violation or
when N is 0.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

GUARDED = (
    "blocks",
    "block_versions",
    "citations",
    "translation_blocks",
    "run_steps",
    "learner_answers",
    "learner_block_views",
)

# "module:function:table" -> reason.
ALLOWLIST: dict[str, str] = {}

TABLE_RE = re.compile(rf"\b(?:FROM|JOIN|INTO|UPDATE)\s+({'|'.join(GUARDED)})\b", re.I)
VERB_RE = re.compile(r"^\s*(?:WITH|SELECT|INSERT|UPDATE|DELETE)\b", re.I)
COURSE_RE = re.compile(r"\b(?:course_id|course_version_id|topics|modules|courses)\b", re.I)
IDENTITY = (
    "id|block_id|block_version_id|run_id|translation_id|topic_id|version_id|current_version_id"
)
PREDICATE_RE = re.compile(
    rf"\b(?:\w+\.)?(?:{IDENTITY})\b(?:::text)?\s*(?:=|IN\b)\s*(?:ANY\s*)?\(?\s*:",
    re.I,
)
INSERT_FK_RE = re.compile(
    r"INSERT\s+INTO\s+\w+\s*\([^)]*\b(?:block_id|block_version_id|run_id|translation_id|topic_id)\b",
    re.I,
)


def _flatten(node: ast.AST) -> str | None:
    """The text of a string expression, '{}' for any dynamic piece; None if not a string."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        return "".join(
            v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "{}"
            for v in node.values
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _flatten(node.left), _flatten(node.right)
        if left is None and right is None:
            return None
        return (left if left is not None else "{}") + (right if right is not None else "{}")
    return None


def statements(path: Path) -> list[tuple[int, str, str]]:
    """(line, enclosing function, text) of each SQL string expression in the file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str, str]] = []

    def visit(node: ast.AST, func: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = (
                child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else func
            )
            text = (
                _flatten(child)
                if isinstance(child, (ast.Constant, ast.JoinedStr, ast.BinOp))
                else None
            )
            if text is not None and VERB_RE.match(text):
                found.append((child.lineno, func, text))
                continue
            if text is not None and isinstance(child, (ast.JoinedStr, ast.BinOp)):
                continue
            visit(child, name)

    visit(tree, "<module>")
    return found


def judge(text: str) -> str | None:
    """None when the statement is scoped, else the reason it is not."""
    if COURSE_RE.search(text) or PREDICATE_RE.search(text) or INSERT_FK_RE.search(text):
        return None
    if re.search(r"\bplan_node_id\b", text, re.I):
        return "keyed by plan_node_id (model-chosen, repeats per course) without course or id scope"
    return "no course scope and no identity key"


def check(root: Path) -> tuple[int, list[str]]:
    """Return (guarded statements checked, violation lines)."""
    checked = 0
    problems: list[str] = []
    app_root = root / "src" / "app"
    for path in sorted(app_root.rglob("*.py")):
        module = ".".join(path.relative_to(root / "src").with_suffix("").parts)
        for line, func, text in statements(path):
            tables = sorted({m.lower() for m in TABLE_RE.findall(text)})
            if not tables:
                continue
            checked += 1
            reason = judge(text)
            if reason is None:
                continue
            for table in tables:
                if f"{module}:{func}:{table}" in ALLOWLIST:
                    continue
                problems.append(
                    f"{path.relative_to(root)}:{line}: {func} touches {table}: {reason}"
                )
                break
    return checked, problems


def main(argv: list[str]) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=REPO)
    args = parser.parse_args(argv)
    checked, problems = check(args.root)
    for line in problems:
        print(line)
    print(f"check_scoped_sql: {checked} statements checked")
    return 1 if problems or checked == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
