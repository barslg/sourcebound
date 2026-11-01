"""Caller gate: every function and method under src/app has a production caller.

"Who calls this?" answered by name resolution over imports, not keyword
grep. A module-level function or a method of a module-level class counts
as called when it is reached from production code:

- functions: another module under src/app (or migrations/) imports it
  (`from M import f`, `import M as m; m.f`, a package re-export chain, a
  dotted `app.x.y.f` access) or fetches it by `import_module(f"...")`
  followed by `.f`;
- methods: an attribute access with the method's name in another module
  (receiver types are not inferred, so this is nominal for methods);
- either kind: a call from another function of its own module (a helper
  that something in production reaches). Only the symbol's own body
  (recursion) and tests do not count; a symbol only tests call is
  unfinished and is reported.

Exempt without a list entry: dunders, functions with a registering
decorator (route, task, fixture, validator), Protocol members, functions
referenced as a dict value in their own module (handler maps) and
entrypoints named by compose, Dockerfile or pyproject (`pkg.mod:fn`).
Everything else needs a reason in ALLOWLIST.

Usage: python scripts/check_callers.py [--root DIR]
Prints "check_callers: N symbols checked"; exit 1 with the symbol list
when any symbol has no caller or N is 0.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# "module:qualname" -> reason. A reason names the real consumer.
ALLOWLIST: dict[str, str] = {
    "app.agents.librarian:_SpecParser.handle_starttag": "html.parser.HTMLParser callback",
    "app.agents.librarian:_SpecParser.handle_endtag": "html.parser.HTMLParser callback",
    "app.agents.librarian:_SpecParser.handle_data": "html.parser.HTMLParser callback",
    "app.rbac.catalogue:codes": (
        "read by scripts/check_routes.py and scripts/export_permissions.py "
        "(the route gate and the generated client permission list)"
    ),
}

PLAIN_DECORATORS = {
    "staticmethod",
    "classmethod",
    "property",
    "cached_property",
    "lru_cache",
    "cache",
    "contextmanager",
    "asynccontextmanager",
    "wraps",
    "override",
}


def module_name(path: Path, src: Path) -> str:
    """Dotted module name; a package __init__ is the package itself."""
    parts = list(path.relative_to(src).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _decorator_name(node: ast.expr) -> str:
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Attribute):
        return target.attr
    if isinstance(target, ast.Name):
        return target.id
    return ""


def _registered(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True when a registering decorator (route, task, fixture, validator) wraps it."""
    return any(_decorator_name(d) not in PLAIN_DECORATORS for d in node.decorator_list)


class Module:
    """One parsed module: definitions, imports and references."""

    def __init__(self, name: str, path: Path, is_package: bool) -> None:
        self.name = name
        self.path = path
        self.is_package = is_package
        self.tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        self.symbol_imports: dict[str, set[tuple[str, str]]] = defaultdict(set)
        self.module_aliases: dict[str, set[str]] = defaultdict(set)
        self.attr_names: set[str] = set()
        self.dict_value_names: set[str] = set()
        self.local_loads: set[tuple[str, str]] = set()
        self.dynamic: list[tuple[str, str]] = []

    def absolute(self, level: int, module: str | None) -> str:
        """Resolve a possibly relative `from` target."""
        if level == 0:
            return module or ""
        base = self.name.split(".")
        if not self.is_package:
            base = base[:-1]
        base = base[: len(base) - (level - 1)]
        return ".".join(base + ([module] if module else []))


def load_modules(src: Path, extra: list[Path]) -> dict[str, Module]:
    """Parse every .py under src/app and the extra production roots."""
    modules: dict[str, Module] = {}
    roots = [src / "app", *extra]
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            name = module_name(path, src if root == src / "app" else root.parent)
            modules[name] = Module(name, path, path.name == "__init__.py")
    return modules


def _scoped_walk(tree: ast.Module) -> list[tuple[ast.AST, str]]:
    """Every node with the qualname of its enclosing top-level function or method."""
    out: list[tuple[ast.AST, str]] = []

    def visit(node: ast.AST, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            out.append((child, scope))
            visit(child, scope)

    for top in tree.body:
        if isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((top, top.name))
            visit(top, top.name)
        elif isinstance(top, ast.ClassDef):
            out.append((top, ""))
            for item in top.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.append((item, f"{top.name}.{item.name}"))
                    visit(item, f"{top.name}.{item.name}")
                else:
                    out.append((item, ""))
                    visit(item, "")
        else:
            out.append((top, ""))
            visit(top, "")
    return out


def _dynamic_pattern(call: ast.Call) -> tuple[str, str] | None:
    """(prefix, suffix) of `import_module(f"prefix{x}suffix")`, else None."""
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    if name != "import_module" or not call.args or not isinstance(call.args[0], ast.JoinedStr):
        return None
    values = call.args[0].values
    first, last = values[0], values[-1]
    prefix = first.value if isinstance(first, ast.Constant) else ""
    suffix = last.value if isinstance(last, ast.Constant) and len(values) > 1 else ""
    return (str(prefix), str(suffix))


def index_references(modules: dict[str, Module]) -> None:
    """Fill each module's import maps and attribute/name usage sets."""
    for mod in modules.values():
        for node, scope in _scoped_walk(mod.tree):
            if isinstance(node, ast.ImportFrom):
                target = mod.absolute(node.level, node.module)
                for alias in node.names:
                    local = alias.asname or alias.name
                    if f"{target}.{alias.name}" in modules:
                        mod.module_aliases[local].add(f"{target}.{alias.name}")
                    else:
                        mod.symbol_imports[local].add((target, alias.name))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        mod.module_aliases[alias.asname].add(alias.name)
                    else:
                        mod.module_aliases[alias.name.split(".")[0]].add(alias.name.split(".")[0])
            elif isinstance(node, ast.Attribute):
                mod.attr_names.add(node.attr)
                mod.local_loads.add((node.attr, scope))
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                mod.local_loads.add((node.id, scope))
            elif isinstance(node, ast.Dict):
                for value in node.values:
                    if isinstance(value, ast.Name):
                        mod.dict_value_names.add(value.id)
            elif isinstance(node, ast.Call):
                pattern = _dynamic_pattern(node)
                if pattern is not None:
                    mod.dynamic.append(pattern)


def _dotted(node: ast.expr) -> list[str] | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return list(reversed(parts))
    return None


def resolve_symbol(modules: dict[str, Module], target: str, name: str) -> tuple[str, str]:
    """Follow package re-exports to the module that defines the symbol."""
    seen: set[tuple[str, str]] = set()
    while (target, name) not in seen and target in modules:
        seen.add((target, name))
        hops = modules[target].symbol_imports.get(name)
        if not hops:
            break
        target, name = sorted(hops)[0]
    return target, name


def function_uses(modules: dict[str, Module]) -> dict[tuple[str, str], set[str]]:
    """(defining module, name) -> the set of modules that reference it."""
    uses: dict[tuple[str, str], set[str]] = defaultdict(set)
    for mod in modules.values():
        for hops in mod.symbol_imports.values():
            for target, name in hops:
                uses[resolve_symbol(modules, target, name)].add(mod.name)
        for node in ast.walk(mod.tree):
            if not isinstance(node, ast.Attribute):
                continue
            chain = _dotted(node)
            if not chain:
                continue
            for base in mod.module_aliases.get(chain[0], ()):
                resolved = base.split(".") + chain[1:]
                for cut in range(len(resolved) - 1, 0, -1):
                    prefix = ".".join(resolved[:cut])
                    if prefix in modules:
                        uses[resolve_symbol(modules, prefix, resolved[cut])].add(mod.name)
                        break
    return uses


def _dynamic_called(modules: dict[str, Module], mod: Module, name: str) -> bool:
    """True when some module imports this one by a pattern and reads `.name`."""
    for other in modules.values():
        if name not in other.attr_names:
            continue
        for prefix, suffix in other.dynamic:
            if mod.name.startswith(prefix) and mod.name.endswith(suffix.lstrip(".") or ""):
                return True
    return False


def entrypoint_strings(root: Path) -> set[str]:
    """`pkg.mod:fn` and `pkg.mod.fn` tokens named by compose, Dockerfile, pyproject."""
    found: set[str] = set()
    for pattern in ("docker-compose*.y*ml", "Dockerfile*", "pyproject.toml", "Makefile"):
        for path in root.glob(pattern):
            text = path.read_text(encoding="utf-8", errors="ignore")
            for mod, _, attr in re.findall(r"\b(app(?:\.\w+)+)([:.])(\w+)\b", text):
                found.add(f"{mod}:{attr}")
    return found


def check(root: Path) -> tuple[int, list[str]]:
    """Return (symbols checked, violation lines)."""
    src = root / "src"
    modules = load_modules(src, [root / "migrations"])
    index_references(modules)
    uses = function_uses(modules)
    entry = entrypoint_strings(root)
    checked = 0
    problems: list[str] = []
    app_modules = [m for m in modules.values() if m.name.startswith("app")]
    for mod in app_modules:
        others = [m for m in modules.values() if m.name != mod.name]
        for node in mod.tree.body:
            defs: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef, bool]] = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defs.append((node.name, node, False))
            elif isinstance(node, ast.ClassDef):
                protocol = any(_decorator_name(b) == "Protocol" for b in node.bases)
                if protocol:
                    continue
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        defs.append((f"{node.name}.{item.name}", item, True))
            for qual, fn, is_method in defs:
                short = fn.name
                if short.startswith("__") and short.endswith("__"):
                    continue
                if _registered(fn) or f"{mod.name}:{short}" in entry:
                    continue
                if not is_method and short in mod.dict_value_names:
                    continue
                key = f"{mod.name}:{qual}"
                if key in ALLOWLIST:
                    continue
                checked += 1
                if is_method:
                    called = any(short in o.attr_names for o in others)
                else:
                    called = bool(uses.get((mod.name, short), set()) - {mod.name})
                    if not called:
                        called = _dynamic_called(modules, mod, short)
                if not called:
                    called = any(n == short and sc != qual for n, sc in mod.local_loads)
                if not called:
                    problems.append(
                        f"{mod.path.relative_to(root)}:{fn.lineno}: {key} has no caller"
                    )
    return checked, problems


def main(argv: list[str]) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=REPO)
    args = parser.parse_args(argv)
    checked, problems = check(args.root)
    print(f"check_callers: {checked} symbols checked")
    for line in problems:
        print(line)
    return 1 if problems or checked == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
