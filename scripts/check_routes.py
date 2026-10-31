"""Route gate: every /api/v1 route carries ctx and a permission code.

For each route under /api/v1 (except the auth bootstrap endpoints below)
the check asserts:

- the endpoint depends on `ctx` (the get_ctx dependency or a
  RequestContext parameter), and
- the handler resolves, through its controller into services/rbac, to at
  least one require("code") literal with a code from the RBAC catalogue.

Exempt (never carry a code): /health, /config/public (signed-out privacy line),
/auth/login, /auth/callback, /auth/csrf, and POST /invites/{token}/accept.
Self-scoped (the caller's own session, principal, stream, or owner-scoped
read): /auth/logout, /auth/me,
/events, and GET /operations/{operation_id}.

Usage: python scripts/check_routes.py
Prints the route inventory table. Exit 1 per violation, exit 0 when clean.
"""

from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from app.auth.ctx import RequestContext, get_ctx  # noqa: E402
from app.rbac.catalogue import codes as catalogue_codes  # noqa: E402

EXEMPT = {
    ("GET", "/api/v1/health"),
    ("GET", "/api/v1/config/public"),
    ("GET", "/api/v1/auth/login"),
    ("GET", "/api/v1/auth/callback"),
    ("GET", "/api/v1/auth/csrf"),
    ("POST", "/api/v1/invites/{token}/accept"),
}

SELF_SCOPED = {
    # Destroys only the caller's own session.
    "/api/v1/auth/logout": "own session",
    # Returns only the caller's own principal.
    "/api/v1/auth/me": "own principal",
    # Streams only the caller's own notifications.
    "/api/v1/events": "own stream",
    # Owner-scoped read; anyone else's operation answers 404.
    "/api/v1/operations/{operation_id}": "owner-scoped read",
    # Returns only the caller's own notifications.
    "/api/v1/notifications": "own notifications",
    # Marks only the caller's own notification as read.
    "/api/v1/notifications/{notification_id}/read": "own notification",
}

INDEXED_PACKAGES = ("controllers", "services", "rbac")


class _PrefixedRoute:
    """A sub-router route seen under its include prefix."""

    def __init__(self, route: object, prefix: str) -> None:
        self._route = route
        base = getattr(route, "path", "") or ""
        self.path = prefix + base

    def __getattr__(self, name: str) -> object:
        return getattr(self._route, name)


def iter_routes(app: object) -> list[object]:
    """Leaf routes, unwrapping included routers with their prefix."""
    leaves: list[object] = []
    for entry in getattr(app, "routes", []):
        path = getattr(entry, "path", None)
        if path:
            leaves.append(entry)
            continue
        router = getattr(entry, "original_router", None)
        context = getattr(entry, "include_context", None)
        prefix = getattr(context, "prefix", "") or "" if context else ""
        sub_routes = getattr(router, "routes", None) or []
        for sub in sub_routes:
            leaves.append(_PrefixedRoute(sub, prefix))
    return leaves


def route_missing_ctx(route: object) -> bool:
    """True when the route does not depend on the request context."""
    dependant = getattr(route, "dependant", None)
    if dependant is not None and _depends_on_ctx(dependant):
        return False
    endpoint = getattr(route, "endpoint", None)
    if endpoint is not None:
        try:
            params = inspect.signature(endpoint).parameters.values()
        except (TypeError, ValueError):
            return True
        if any(param.annotation is RequestContext for param in params):
            return False
    return True


def _depends_on_ctx(dependant: object) -> bool:
    dependencies = getattr(dependant, "dependencies", None) or []
    for dep in dependencies:
        if getattr(dep, "call", None) is get_ctx:
            return True
        if _depends_on_ctx(dep):
            return True
    return False


def index_package(src: Path) -> dict[tuple[str, str], tuple[set[str], set[tuple[str, str]]]]:
    """Map (module, function) -> (require() literal codes, app callees)."""
    index: dict[tuple[str, str], tuple[set[str], set[tuple[str, str]]]] = {}
    for package in INDEXED_PACKAGES:
        for path in sorted((src / "app" / package).rglob("*.py")):
            module = ".".join(path.relative_to(src).with_suffix("").parts)
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in tree.body:
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                aliases = _func_aliases(tree, node)
                codes: set[str] = set()
                callees: set[tuple[str, str]] = set()
                for child in ast.walk(node):
                    if not isinstance(child, ast.Call):
                        continue
                    func = child.func
                    if isinstance(func, ast.Name):
                        if func.id == "require":
                            codes |= _string_args(child)
                        elif func.id in aliases:
                            callees.add(aliases[func.id])
                index[(module, node.name)] = (codes, callees)
    return index


def _string_args(call: ast.Call) -> set[str]:
    """Every string-constant argument (require takes the code second)."""
    found: set[str] = set()
    for arg in call.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            found.add(arg.value)
    for keyword in call.keywords:
        value = keyword.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            found.add(value.value)
    return found


def _func_aliases(
    tree: ast.Module, func_node: ast.FunctionDef | ast.AsyncFunctionDef
) -> dict[str, tuple[str, str]]:
    """Module-level from-app imports, with the function's own imports winning."""
    aliases: dict[str, tuple[str, str]] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            _add_aliases(aliases, node)
    for node in ast.walk(func_node):
        if isinstance(node, ast.ImportFrom):
            _add_aliases(aliases, node)
    return aliases


def _add_aliases(aliases: dict[str, tuple[str, str]], node: ast.ImportFrom) -> None:
    """Record `from app.* import ...` local names into the alias map."""
    if not (node.module or "").startswith("app."):
        return
    for alias in node.names:
        aliases[alias.asname or alias.name] = (node.module or "", alias.name)


def _import_aliases(tree: ast.Module) -> dict[str, tuple[str, str]]:
    """Local name -> (module, object) for `from app.* import ...`."""
    aliases: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app."):
            for alias in node.names:
                aliases[alias.asname or alias.name] = (node.module or "", alias.name)
    return aliases


def route_targets(route: object, src: Path) -> set[tuple[str, str]]:
    """(module, function) pairs the route endpoint delegates to."""
    endpoint = getattr(route, "endpoint", None)
    if endpoint is None:
        return set()
    try:
        filename = inspect.getsourcefile(endpoint)
    except (OSError, TypeError):
        return set()
    if not filename:
        return set()
    try:
        tree = ast.parse(Path(filename).read_text(encoding="utf-8"), filename=filename)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return set()
    name = getattr(endpoint, "__name__", "")
    func_node = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
        ),
        None,
    )
    if func_node is None:
        return set()
    aliases = _import_aliases(tree)
    targets: set[tuple[str, str]] = set()
    for node in ast.walk(func_node):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            key = func.value.id
            if key in aliases and aliases[key][0].startswith("app.controllers"):
                module, member = aliases[key]
                targets.add((f"{module}.{member}", func.attr))
        elif isinstance(func, ast.Name) and func.id in aliases:
            module, member = aliases[func.id]
            if module.startswith("app.controllers"):
                targets.add((module, member))
    return targets


def resolve_codes(
    targets: set[tuple[str, str]],
    index: dict[tuple[str, str], tuple[set[str], set[tuple[str, str]]]],
) -> set[str]:
    """Transitive require() literals reachable from the targets."""
    found: set[str] = set()
    visited: set[tuple[str, str]] = set()
    stack = list(targets)
    while stack:
        key = stack.pop()
        if key in visited or key not in index:
            continue
        visited.add(key)
        codes, callees = index[key]
        found |= codes
        stack.extend(callees - visited)
    return found


def check_app(app: object, src: Path) -> list[str]:
    """Every route violation as a human-readable line."""
    index = index_package(src)
    catalogue = catalogue_codes()
    violations: list[str] = []
    for route in iter_routes(app):
        path = getattr(route, "path", "")
        if not path.startswith("/api/v1"):
            continue
        methods = sorted(getattr(route, "methods", None) or ())
        for method in methods:
            if (method, path) in EXEMPT:
                continue
            if route_missing_ctx(route):
                violations.append(f"{method} {path}: route without ctx")
                continue
            if path in SELF_SCOPED:
                continue
            found = resolve_codes(route_targets(route, src), index)
            unknown = found - catalogue
            if unknown:
                violations.append(f"{method} {path}: unknown code(s) {sorted(unknown)}")
            elif not found:
                violations.append(f"{method} {path}: no catalogue permission code")
    return sorted(violations)


def inventory(app: object, src: Path) -> list[str]:
    """Printable METHOD PATH CTX CODES rows for the route table."""
    index = index_package(src)
    rows: list[str] = []
    for route in iter_routes(app):
        path = getattr(route, "path", "")
        if not path.startswith("/api/v1"):
            continue
        methods = sorted(getattr(route, "methods", None) or ())
        for method in methods:
            if (method, path) in EXEMPT:
                rows.append(f"{method:6} {path}  ctx=n/a  codes=exempt")
                continue
            ctx = "no" if route_missing_ctx(route) else "yes"
            if path in SELF_SCOPED:
                rows.append(f"{method:6} {path}  ctx={ctx}  codes=self ({SELF_SCOPED[path]})")
                continue
            found = sorted(resolve_codes(route_targets(route, src), index))
            rows.append(f"{method:6} {path}  ctx={ctx}  codes={','.join(found) or 'NONE'}")
    return sorted(rows)


def main() -> int:
    """Build the app, print the inventory, exit 1 on violations."""
    from app.main import create_app

    app = create_app(sync_on_startup=False)
    src = REPO / "src"
    for row in inventory(app, src):
        print(row)
    violations = check_app(app, src)
    for line in violations:
        print(f"VIOLATION {line}")
    if violations:
        print(f"{len(violations)} route violation(s)", file=sys.stderr)
        return 1
    print("routes OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
