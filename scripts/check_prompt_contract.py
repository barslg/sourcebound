"""Prompt-contract gate: what code reads from a model reply is in the reply schema.

Part 1, reads. For every `generate_json(agent=..., ...)` call in
runs/executor.py the result variable is followed through the function
(assignments, loops, comprehensions, `.get()` chains), and so is the
`payload` parameter of each agents/*.check_* function. A key read straight
off the reply must be in that agent's schema `required`; a key read from a
value derived from it (a citation, a verdict row) must be a property named
somewhere in that agent's schema. A new read of a key the model is not
required to write fails the build, not the next paid run.

Part 2, prompts. Every `{placeholder}` in prompts/<agent>/system.md (the real
files when every agent has one, else the tracked stubs) must be a key
build_dynamic-style code fills: the system prompt is sent verbatim, so any
`{name}` left in it reaches the model unfilled. A file with none passes.

Usage: python scripts/check_prompt_contract.py
Prints "check_prompt_contract: N reads and placeholders checked"; exit 1 per violation.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

# agents/<module>.py -> response schema name in AGENT_SCHEMAS
CHECK_MODULES = {
    "architect": "architect",
    "outline": "outline",
    "researcher": "researcher",
    "reader": "reader",
    "saboteur": "saboteur",
    "translator": "translator",
    "tutor": "tutor",
    "generate_block": "generators",
    "blueprint": "blueprint",
    "lecture": "lecture",
    "corrector": "corrector",
}

# "path:function:key" -> reason, for a key code legitimately reads off a reply it wrote itself.
ALLOWLIST: dict[str, str] = {
    "src/app/runs/executor.py:read_topic_step:hold_reason": (
        "the executor writes hold_reason into the undecided verdict rows itself"
        " (_merge_undecided); the reader schema does not carry it"
    ),
}


def property_names(schema: Any) -> set[str]:
    """Every property name anywhere in a schema."""
    names: set[str] = set()
    if isinstance(schema, dict):
        props = schema.get("properties")
        if isinstance(props, dict):
            names |= set(props)
            for sub in props.values():
                names |= property_names(sub)
        for key in ("items", "anyOf", "oneOf", "allOf"):
            sub = schema.get(key)
            for item in sub if isinstance(sub, list) else [sub]:
                names |= property_names(item)
    return names


def schema_index() -> dict[str, tuple[set[str], set[str]]]:
    """Agent -> (top-level required keys, all property names), 'generators' for every generator."""
    from app.agents.base import AGENT_SCHEMAS
    from app.course.tree import BLOCK_GENERATOR_SCHEMAS

    index: dict[str, tuple[set[str], set[str]]] = {}
    for agent, schema in AGENT_SCHEMAS.items():
        index[agent] = (set(schema.get("required", [])), property_names(schema))  # type: ignore[call-overload]
    gens = list(BLOCK_GENERATOR_SCHEMAS.values())
    index["generators"] = (
        set.intersection(*(set(s.get("required", [])) for s in gens)),
        set().union(*(property_names(s) for s in gens)),
    )
    return index


def _key_of(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


class Reads(ast.NodeVisitor):
    """Keys read from reply-derived values inside one function."""

    def __init__(self, roots: dict[str, str]) -> None:
        self.root = dict(roots)  # name -> agent, names that ARE the reply
        self.derived: dict[str, str] = {}  # name -> agent, values taken out of the reply
        self.reads: list[tuple[int, str, str, bool]] = []  # (line, agent, key, direct)

    def _agent_of(self, node: ast.AST) -> tuple[str, bool] | None:
        """(agent, is_the_reply_itself) when the expression comes from a reply."""
        if isinstance(node, ast.Name):
            if node.id in self.root:
                return self.root[node.id], True
            if node.id in self.derived:
                return self.derived[node.id], False
        if isinstance(node, ast.Subscript):
            inner = self._agent_of(node.value)
            return (inner[0], False) if inner else None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("get", "items", "values", "setdefault", "pop"):
                inner = self._agent_of(node.func.value)
                return (inner[0], False) if inner else None
        if isinstance(node, (ast.Await, ast.Starred)):
            return self._agent_of(node.value)  # type: ignore[arg-type]
        return None

    def _taint(self, target: ast.AST, source: ast.AST) -> None:
        found = self._agent_of(source)
        for name in ast.walk(target):
            if isinstance(name, ast.Name) and name.id not in self.root:
                if found:
                    self.derived[name.id] = found[0]
                else:
                    self.derived.pop(name.id, None)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.generic_visit(node)
        for target in node.targets:
            self._taint(target, node.value)

    def visit_For(self, node: ast.For) -> None:
        self._taint(node.target, node.iter)
        self.generic_visit(node)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        self._taint(node.target, node.iter)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        self.generic_visit(node)
        key = _key_of(node.slice)
        found = self._agent_of(node.value)
        if key is not None and found and isinstance(node.ctx, ast.Load):
            self.reads.append((node.lineno, found[0], key, found[1]))

    def visit_Call(self, node: ast.Call) -> None:
        self.generic_visit(node)
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and (key := _key_of(node.args[0])) is not None
            and (found := self._agent_of(node.func.value))
        ):
            self.reads.append((node.lineno, found[0], key, found[1]))


def _roots_of_generate_json(func: ast.AST) -> dict[str, str]:
    roots: dict[str, str] = {}
    for node in ast.walk(func):
        value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
        if isinstance(value, ast.Await):
            value = value.value
        if not (isinstance(value, ast.Call) and getattr(value.func, "attr", "") == "generate_json"):
            continue
        agent = next((kw.value for kw in value.keywords if kw.arg == "agent"), None)
        name = _key_of(agent) if agent is not None else None
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]  # type: ignore[union-attr]
        for target in targets:
            if isinstance(target, ast.Name):
                roots[target.id] = name if name else "generators"
    return roots


def judge_reads(
    rel: str,
    func: str,
    reads: list[tuple[int, str, str, bool]],
    index: dict[str, tuple[set[str], set[str]]],
) -> list[str]:
    """Violation lines for the keys one function reads off a reply."""
    problems: list[str] = []
    for line, agent, key, direct in reads:
        required, props = index[agent]
        if f"{rel}:{func}:{key}" in ALLOWLIST:
            continue
        if direct and key not in required:
            problems.append(
                f"{rel}:{line}: {func} reads {key!r} off the {agent} reply, not required"
            )
        elif not direct and key not in props:
            problems.append(
                f"{rel}:{line}: {func} reads {key!r}, no {agent} schema property has it"
            )
    return problems


def check_reads() -> tuple[int, list[str]]:
    """Part 1."""
    index = schema_index()
    problems: list[str] = []
    checked = 0
    jobs: list[tuple[Path, ast.FunctionDef | ast.AsyncFunctionDef, dict[str, str]]] = []
    executor = REPO / "src" / "app" / "runs" / "executor.py"
    for fn in ast.walk(ast.parse(executor.read_text(encoding="utf-8"))):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            roots = _roots_of_generate_json(fn)
            if roots:
                jobs.append((executor, fn, roots))
    for module, agent in CHECK_MODULES.items():
        path = REPO / "src" / "app" / "agents" / f"{module}.py"
        for fn in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(fn, ast.FunctionDef) and fn.name.startswith("check_"):
                if fn.args.args and fn.args.args[0].arg == "payload":
                    jobs.append((path, fn, {"payload": agent}))
    for path, fn, roots in jobs:
        visitor = Reads(roots)
        visitor.visit(fn)
        checked += len(visitor.reads)
        problems += judge_reads(str(path.relative_to(REPO)), fn.name, visitor.reads, index)
    return checked, problems


def prompt_files() -> list[Path]:
    """The real system prompts when every agent has one, else the tracked stubs."""
    from app.agents.base import AGENT_SCHEMAS

    real = REPO / "prompts"
    root = (
        real
        if all((real / a / "system.md").exists() for a in AGENT_SCHEMAS)
        else REPO / "tests" / "fixtures" / "agent_prompts"
    )
    return sorted(root.glob("*/system.md"))


def check_placeholders() -> tuple[int, list[str]]:
    """Part 2: no unfilled {placeholder} survives in a system prompt."""
    problems: list[str] = []
    files = prompt_files()
    for path in files:
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"```.*?```", "", text, flags=re.S)
        for match in re.finditer(r"(?<![\w{$])\{([A-Za-z_][A-Za-z0-9_]*)\}(?!\})", text):
            line = text.count("\n", 0, match.start()) + 1
            problems.append(
                f"{path.relative_to(REPO)}:{line}: placeholder {{{match.group(1)}}} is never "
                "filled: system prompts are sent verbatim, dynamic input goes after them"
            )
    return len(files), problems


def main() -> int:
    """CLI entrypoint."""
    read_n, read_problems = check_reads()
    prompt_n, prompt_problems = check_placeholders()
    problems = read_problems + prompt_problems
    for line in problems:
        print(line)
    print(f"check_prompt_contract: {read_n + prompt_n} reads and placeholders checked")
    return 1 if problems or read_n == 0 or prompt_n == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
