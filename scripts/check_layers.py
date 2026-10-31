"""Layer gate: AST import check enforcing the PRINCIPLES layer map.

Rules (docs/PRINCIPLES.md, build/tasks/T08a.md):

- Calls only downward: routes -> controllers -> services -> providers ->
  clients. Cross-layer skips are violations. Side modules (schemas, auth,
  rbac, llm, runs, tasks, helpers, db, config, errors, logging, redact)
  are importable from anywhere.
- models/ is imported only by services, providers, llm, runs, auth,
  rbac (and models itself); never by routes.
- openai/httpx/httpx2 are imported only under clients/, plus the
  designated Google OIDC I/O module auth/oidc.py (PRINCIPLES: exactly one
  module owns each external system's I/O).
- os.environ/os.getenv appear only in config.py, plus tasks/schema.py
  (forwards the environment to the procrastinate CLI subprocess; every
  value it needs comes from get_settings()).
- generate_json( is called only from app/runs/executor.py. That file does
  not exist yet, so any call anywhere else fails until it lands.
- uuid4/datetime.now/random are forbidden under agents/ and pipelines/
  (those packages do not exist yet; the rule is enforced when they do).

Usage: python scripts/check_layers.py [--root DIR]
Exit 1 with file:line per violation, exit 0 when clean.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

LAYER_OF = {
    "routes": "routes",
    "controllers": "controllers",
    "services": "services",
    "providers": "providers",
    "clients": "clients",
}

# Layer dir -> forbidden app.* module prefixes (side modules are free).
LAYER_FORBIDDEN = {
    "routes": ("app.services", "app.providers", "app.clients", "app.models"),
    "controllers": ("app.providers", "app.clients", "app.models"),
    "services": ("app.routes", "app.controllers", "app.clients"),
    "providers": ("app.routes", "app.controllers", "app.services"),
    "clients": ("app.routes", "app.controllers", "app.services", "app.providers"),
}

# layer dir -> modules allowed despite LAYER_FORBIDDEN.
LAYER_EXCEPTIONS = {
    # routes/ops.py sse_stream subscribes to the event bus directly.
    "routes": ("app.services.events",),
}

# Top-level packages under src/app allowed to import app.models.*.
MODELS_IMPORTERS = {"services", "providers", "llm", "runs", "auth", "rbac", "models"}

# Files allowed to import openai/httpx/httpx2, with the reason each needs it.
HTTP_ALLOWED = {
    # The only runtime Meta Model API client.
    "src/app/clients/meta.py": "Meta Model API client",
    # Whole clients/ package (adapters for self-hosted open models).
    "src/app/clients/": "LLM client adapters",
    # Designated Google OIDC I/O module (one module per external system).
    "src/app/auth/oidc.py": "Google OIDC I/O module",
    # Classifies SDK error shapes for the retry policy; performs no I/O.
    "src/app/llm/retry.py": "error-shape classification only",
}

# Files allowed to touch os.environ/os.getenv (repo-relative).
ENV_ALLOWED = {"src/app/config.py", "src/app/tasks/schema.py"}

# The only file allowed to call generate_json( (repo-relative).
GENERATE_JSON_OWNER = "src/app/runs/executor.py"

# Dirs where uuid4/datetime.now/random are forbidden (repo-relative).
NONDETERMINISM_FORBIDDEN = ("src/app/agents", "src/app/pipelines")

HTTP_LIBS = {"openai", "httpx", "httpx2"}


def _module_of(node: ast.ImportFrom) -> str:
    return node.module or ""


def iter_py_files(app_root: Path) -> list[Path]:
    """Every Python file under src/app, sorted for stable output."""
    return sorted(app_root.rglob("*.py"))


def check_file(path: Path, repo: Path) -> list[str]:
    """All violations in one file, as file:line strings."""
    rel = path.relative_to(repo).as_posix()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
    except (SyntaxError, UnicodeDecodeError) as exc:
        return [f"{rel}:1: cannot parse ({exc})"]
    parts = rel.split("/")
    layer = LAYER_OF.get(parts[2]) if len(parts) > 2 and parts[1] == "app" else None
    top = parts[2] if len(parts) > 2 and parts[1] == "app" else ""
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                violations += _check_import(alias.name, node.lineno, rel, layer, top)
        elif isinstance(node, ast.ImportFrom):
            mod = _module_of(node)
            for alias in node.names:
                full = f"{mod}.{alias.name}" if mod else alias.name
                violations += _check_import(full, node.lineno, rel, layer, top)
            if mod == "os":
                violations += _check_os_names([a.name for a in node.names], node.lineno, rel)
            if mod in ("uuid", "random") or mod.startswith("random."):
                violations += _check_nondet_import(
                    mod, [a.name for a in node.names], node.lineno, rel
                )
        elif isinstance(node, ast.Attribute):
            violations += _check_attribute(node, rel)
        elif isinstance(node, ast.Call):
            violations += _check_call(node, rel)
    return violations


def _check_import(
    module: str,
    lineno: int,
    rel: str,
    layer: str | None,
    top: str,
) -> list[str]:
    out: list[str] = []
    if (
        layer
        and any(module == ban or module.startswith(ban + ".") for ban in LAYER_FORBIDDEN[layer])
        and not _is_exception(layer, module)
    ):
        out.append(f"{rel}:{lineno}: {layer}/ must not import {module}")
    if module == "app.models" or module.startswith("app.models."):
        if top not in MODELS_IMPORTERS:
            out.append(f"{rel}:{lineno}: app.models imported from {top or '?'}")
    root_lib = module.split(".")[0]
    if root_lib in HTTP_LIBS and not _is_http_allowed(rel):
        out.append(f"{rel}:{lineno}: {root_lib} imported outside clients/")
    return out


def _is_http_allowed(rel: str) -> bool:
    """True when the file is a designated HTTP owner (see HTTP_ALLOWED)."""
    return any(
        rel == allowed or allowed.endswith("/") and rel.startswith(allowed)
        for allowed in HTTP_ALLOWED
    )


def _is_exception(layer: str, module: str) -> bool:
    """True when the module is allow-listed for this layer."""
    return any(
        module == exc or module.startswith(exc + ".") for exc in LAYER_EXCEPTIONS.get(layer, ())
    )


def _check_os_names(names: list[str], lineno: int, rel: str) -> list[str]:
    if any(name in ("environ", "getenv") for name in names) and rel not in ENV_ALLOWED:
        return [f"{rel}:{lineno}: os.environ/os.getenv only in config.py"]
    return []


def _check_nondet_import(module: str, names: list[str], lineno: int, rel: str) -> list[str]:
    if not rel.startswith(NONDETERMINISM_FORBIDDEN):
        return []
    if module == "uuid" and "uuid4" in names:
        return [f"{rel}:{lineno}: uuid4 forbidden under agents/ and pipelines/"]
    if module.split(".")[0] == "random":
        return [f"{rel}:{lineno}: random forbidden under agents/ and pipelines/"]
    return []


def _check_attribute(node: ast.Attribute, rel: str) -> list[str]:
    out: list[str] = []
    if (
        isinstance(node.value, ast.Name)
        and node.value.id == "os"
        and node.attr in ("environ", "getenv")
        and rel not in ENV_ALLOWED
    ):
        out.append(f"{rel}:{node.lineno}: os.environ/os.getenv only in config.py")
    if node.attr == "uuid4" and rel.startswith(NONDETERMINISM_FORBIDDEN):
        out.append(f"{rel}:{node.lineno}: uuid4 forbidden under agents/ and pipelines/")
    if node.attr == "now" and rel.startswith(NONDETERMINISM_FORBIDDEN) and _is_datetime(node.value):
        out.append(f"{rel}:{node.lineno}: datetime.now forbidden under agents/ and pipelines/")
    if (
        isinstance(node.value, ast.Name)
        and node.value.id == "random"
        and rel.startswith(NONDETERMINISM_FORBIDDEN)
    ):
        out.append(f"{rel}:{node.lineno}: random forbidden under agents/ and pipelines/")
    return out


def _is_datetime(node: ast.AST) -> bool:
    return (isinstance(node, ast.Name) and node.id == "datetime") or (
        isinstance(node, ast.Attribute)
        and node.attr == "datetime"
        and isinstance(node.value, ast.Name)
        and node.value.id == "datetime"
    )


def _check_call(node: ast.Call, rel: str) -> list[str]:
    func = node.func
    name = ""
    if isinstance(func, ast.Name):
        name = func.id
    elif isinstance(func, ast.Attribute):
        name = func.attr
    if name == "generate_json" and rel != GENERATE_JSON_OWNER:
        return [f"{rel}:{node.lineno}: generate_json( only from {GENERATE_JSON_OWNER}"]
    return []


def check_tree(repo: Path) -> list[str]:
    """Every layer violation under <repo>/src/app."""
    app_root = repo / "src" / "app"
    if not app_root.is_dir():
        return [f"{repo}:1: no src/app package found"]
    violations: list[str] = []
    for path in iter_py_files(app_root):
        violations += check_file(path, repo)
    return sorted(violations)


def main(argv: list[str] | None = None) -> int:
    """Parse args, print violations, exit 1 when any exist."""
    parser = argparse.ArgumentParser(description="AST layer gate (see module docstring).")
    parser.add_argument("--root", default=None, help="Repo root (default: script's parent).")
    args = parser.parse_args(argv)
    repo = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent
    violations = check_tree(repo)
    for line in violations:
        print(line)
    if violations:
        print(f"{len(violations)} layer violation(s)", file=sys.stderr)
        return 1
    print("layers OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
