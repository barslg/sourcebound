"""Contract gate: the frontend's API calls against the backend's OpenAPI.

The frontend names every path in src/api/endpoints.ts and calls it through
`request(path, { method, body, query })`. This gate reads that table and
those call sites (a bracket-aware scanner, no TypeScript toolchain), builds
the app's OpenAPI in-process (no server, no database) and fails when:

- a call names a method+path the backend does not serve;
- a request body literal sends a field the schema does not have, or omits a
  required one (a body passed as a variable cannot be read and is counted
  as unknown, not as passing);
- a `query` key is not a declared query parameter of the route;
- a backend route has no frontend caller and is not in NO_FRONTEND_CALLER
  with a reason.

Usage: python scripts/check_contract.py [--frontend DIR]
The frontend tree is DIR, else ./frontend, else ../Muse-wt-frontend/frontend.
With none of them the gate prints "skipped" (CI of a tree without a
frontend); once the frontend is merged it always runs. Also usable from the
frontend tree as `make check-contract` after the merge.
Prints "check_contract: N contract items checked"; exit 1 per violation.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

# "METHOD /api/v1/path" -> reason the frontend does not call it. An entry for a
# route that is called, or no longer exists, fails the gate (no rotting list).
NO_FRONTEND_CALLER: dict[str, str] = {
    "GET /api/v1/health": "liveness probe for compose and CI, not a screen",
    "GET /api/v1/config/public": "signed-out privacy line, read by the learn shell (T06)",
    "GET /api/v1/auth/callback": "Google redirects the browser here, never a fetch",
    "GET /api/v1/courses/{course_id}/report": "teacher report: no screen built yet",
    "GET /api/v1/invites/{token}/preview": "signed-out preview: the accept view does not call it",
    "GET /api/v1/learn/courses/{course_id}": "learn routes: screens arrive with T06",
    "GET /api/v1/learn/passages/{passage_id}": "learn routes: screens arrive with T06",
    "GET /api/v1/learn/topics/{topic_id}": "learn routes: screens arrive with T06",
    "POST /api/v1/learn/blocks/{block_id}/answer": "learn routes: screens arrive with T06",
    "POST /api/v1/learn/courses/{course_id}/ask": "learn routes: screens arrive with T06",
    "POST /api/v1/learn/topics/{topic_id}/submit": "learn routes: screens arrive with T06",
    "GET /api/v1/methodologies": "the frontend keeps its own static code list",
    "PATCH /api/v1/sources/{source_id}/language": "no source-language editor screen yet",
    "POST /api/v1/operations/echo": "harness smoke route, listed only in LIVE_ENDPOINTS",
    "POST /api/v1/roles/{name}/revoke": "roleRevoke() is defined in endpoints.ts and never called",
    "POST /api/v1/sources/candidates/{candidate_id}/approve": "no candidate screen yet",
    "POST /api/v1/sources/{source_id}/transcribe": "no transcription screen yet",
}

# "METHOD /api/v1/path" the frontend calls and this tree's backend does not serve
# yet: the route ships on the feat/frontend branch (T04). Remove the entry when
# that branch merges; the gate fails if the route appears and the entry stays.
PENDING_BACKEND: dict[str, str] = {
    "GET /api/v1/tags": "scope picker; route lives on feat/frontend",
    "POST /api/v1/users/{}/unban": "users view; route lives on feat/frontend",
    "GET /api/v1/courses/{}/runs": "course runs view; route lives on feat/frontend",
}

HTTP_METHODS = ("get", "post", "put", "patch", "delete")
SKIP_FILES = {"endpoints.ts", "client.ts"}
GENERATED = {"endpoints.ts", "permissions.generated.ts"}


@dataclass
class Call:
    """One frontend call site resolved to method, path and the literals it sends."""

    file: str
    line: int
    method: str
    path: str
    body_keys: set[str] | None = None
    body_open: bool = False
    has_body: bool = False
    query_keys: set[str] | None = None


@dataclass
class Frontend:
    """Everything read from the frontend tree."""

    calls: list[Call] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    referenced: set[str] = field(default_factory=set)


def _template_parts(template: str) -> list[str]:
    """Split a template literal body into text and `${...}` expression pieces."""
    parts: list[str] = []
    i = 0
    buf = ""
    while i < len(template):
        if template.startswith("${", i):
            depth = 1
            j = i + 2
            while j < len(template) and depth:
                depth += {"{": 1, "}": -1}.get(template[j], 0)
                j += 1
            parts.append(buf)
            parts.append("\0" + template[i + 2 : j - 1])
            buf = ""
            i = j
        else:
            buf += template[i]
            i += 1
    parts.append(buf)
    return parts


def parse_endpoints(text: str) -> dict[str, str]:
    """Symbol name -> path pattern, with `{}` for each encoded argument."""
    raw: dict[str, str] = {}
    for key, template in re.findall(r"^\s{2}(\w+): `([^`]*)`", text, re.M):
        raw[f"ENDPOINTS.{key}"] = template
    for name, template in re.findall(
        r"^export const (\w+)\s*=\s*(?:\([^)]*\)\s*(?::\s*\w+)?\s*=>\s*)?`([^`]*)`", text, re.M
    ):
        raw[name] = template
    for name, template in re.findall(
        r"^export function (\w+)\([^)]*\)[^{]*\{[^`]*`([^`]*)`", text, re.M
    ):
        raw[name] = template
    resolved: dict[str, str] = {}

    def resolve(name: str, stack: tuple[str, ...] = ()) -> str:
        if name in resolved:
            return resolved[name]
        if name in stack or name not in raw:
            raise ValueError(f"cannot resolve endpoint symbol {name}")
        out = ""
        for piece in _template_parts(raw[name].split("?", 1)[0]):
            if not piece.startswith("\0"):
                out += piece
                continue
            expr = piece[1:].strip()
            if expr == "API":
                out += "/api/v1"
            elif expr.startswith(("enc(", "encodeURIComponent(")):
                out += "{}"
            elif m := re.match(r"(ENDPOINTS\.\w+)$", expr):
                out += resolve(m.group(1), (*stack, name))
            elif m := re.match(r"(\w+)(?:\(.*\))?$", expr):
                out += resolve(m.group(1), (*stack, name))
            else:
                raise ValueError(f"cannot resolve {expr!r} in {name}")
        resolved[name] = out.split("?", 1)[0]
        return resolved[name]

    for name in raw:
        resolve(name)
    return resolved


def _matching(text: str, start: int, open_ch: str, close_ch: str) -> int:
    """Index of the bracket closing the one at text[start], skipping string literals."""
    depth = 0
    quote = ""
    i = start
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = ""
        elif ch in "'\"`":
            quote = ch
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("unbalanced brackets")


def _split_top(text: str) -> list[str]:
    """Split on commas that are not inside brackets or strings."""
    parts: list[str] = []
    depth = 0
    quote = ""
    buf = ""
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            buf += ch
            if ch == "\\":
                i += 1
                buf += text[i]
            elif ch == quote:
                quote = ""
        elif ch in "'\"`":
            quote = ch
            buf += ch
        elif ch in "([{":
            depth += 1
            buf += ch
        elif ch in ")]}":
            depth -= 1
            buf += ch
        elif ch == "," and depth == 0:
            parts.append(buf)
            buf = ""
        else:
            buf += ch
        i += 1
    if buf.strip():
        parts.append(buf)
    return parts


def literal_keys(literal: str) -> tuple[set[str], bool]:
    """(keys, open) of an object literal `{ a: 1, b, 'c': 2, ...rest }`."""
    inner = literal.strip()[1:-1]
    keys: set[str] = set()
    is_open = False
    for entry in _split_top(inner):
        entry = entry.strip()
        if not entry:
            continue
        if entry.startswith("..."):
            is_open = True
        elif m := re.match(r"""(?:(\w+)|'([^']+)'|"([^"]+)")\s*(?::|$)""", entry):
            keys.add(m.group(1) or m.group(2) or m.group(3))
        else:
            is_open = True
    return keys, is_open


def _option(options: str, name: str) -> str | None:
    """The source text of `name: value` at the top level of an options literal."""
    for entry in _split_top(options.strip()[1:-1]):
        key, _, value = entry.strip().partition(":")
        if key.strip() == name:
            return value.strip()
    return None


def _symbols_in(expr: str, symbols: dict[str, str]) -> list[str]:
    """Endpoint symbols named in an expression, longest match first, no sub-matches."""
    names = [
        n
        for n in sorted(symbols, key=len, reverse=True)
        if re.search(rf"(?<![\w.]){re.escape(n)}(?![\w])", expr)
    ]
    return [n for n in names if not any(o != n and n in o for o in names)]


def scan_calls(path: Path, symbols: dict[str, str], root: Path) -> tuple[list[Call], list[str]]:
    """The `request(...)` and `EventSource(...)` call sites of one source file."""
    text = path.read_text(encoding="utf-8")
    calls: list[Call] = []
    unresolved: list[str] = []
    rel = str(path.relative_to(root))
    for m in re.finditer(r"(?<![\w.])(request|EventSource)\s*(?:<[^>(]*>)?\s*\(", text):
        before = text[max(0, m.start() - 20) : m.start()]
        if re.search(r"(function|const|async)\s+$", before):
            continue
        open_at = m.end() - 1
        args = _split_top(text[open_at + 1 : _matching(text, open_at, "(", ")")])
        line = text.count("\n", 0, m.start()) + 1
        names = _symbols_in(args[0] if args else "", symbols)
        ident = (args[0] if args else "").strip()
        if not names and re.fullmatch(r"\w+", ident):
            for rhs in re.findall(rf"\b{ident}\s*[:=]\s*([^\n]+)", text):
                names += [n for n in _symbols_in(rhs, symbols) if n not in names]
        if not names:
            unresolved.append(
                f"{rel}:{line}: {m.group(1)}({(args[0] if args else '').strip()[:50]})"
                " names no endpoint symbol"
            )
            continue
        options = args[1].strip() if len(args) > 1 else ""
        method = "GET"
        if options.startswith("{") and (mm := re.search(r"""method:\s*['"](\w+)['"]""", options)):
            method = mm.group(1).upper()
        body_keys: set[str] | None = None
        body_open = False
        has_body = False
        query_keys: set[str] | None = None
        if options.startswith("{"):
            body = _option(options, "body")
            if body is not None:
                has_body = True
                if body.startswith("{"):
                    body_keys, body_open = literal_keys(body)
            query = _option(options, "query")
            if query is not None and query.startswith("{"):
                query_keys, _ = literal_keys(query)
        for name in names:
            calls.append(
                Call(rel, line, method, symbols[name], body_keys, body_open, has_body, query_keys)
            )
    return calls, unresolved


def read_frontend(frontend: Path) -> Frontend:
    """Parse the endpoint table and every call site under src/."""
    table = parse_endpoints((frontend / "src" / "api" / "endpoints.ts").read_text(encoding="utf-8"))
    result = Frontend()
    for path in sorted((frontend / "src").rglob("*")):
        if path.suffix not in (".ts", ".vue") or path.name in GENERATED:
            continue
        text = path.read_text(encoding="utf-8")
        body_only = re.sub(r"^import[^\n]*$", "", text, flags=re.M)
        for name in _symbols_in(body_only, table):
            result.referenced.add(table[name])
        if path.name in SKIP_FILES:
            continue
        calls, unresolved = scan_calls(path, table, frontend)
        result.calls += calls
        result.unresolved += unresolved
    return result


def _ref(spec: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    ref = node.get("$ref")
    if not ref:
        return node
    target: Any = spec
    for part in ref.removeprefix("#/").split("/"):
        target = target[part]
    return target  # type: ignore[no-any-return]


def backend_operations(spec: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    """(METHOD, normalised path) -> info about body fields and query parameters."""
    ops: dict[tuple[str, str], dict[str, Any]] = {}
    for path, item in spec["paths"].items():
        norm = re.sub(r"\{[^}]+\}", "{}", path)
        for method in HTTP_METHODS:
            op = item.get(method)
            if op is None:
                continue
            body = None
            if "requestBody" in op:
                content = op["requestBody"]["content"]
                media = next(iter(content))
                schema = _ref(spec, content[media]["schema"])
                body = {
                    "media": media,
                    "properties": set(schema.get("properties", {})),
                    "required": set(schema.get("required", [])),
                    "closed": "properties" in schema,
                }
            ops[(method.upper(), norm)] = {
                "path": path,
                "body": body,
                "query": {p["name"] for p in op.get("parameters", []) if p["in"] == "query"},
            }
    return ops


def compare(front: Frontend, ops: dict[tuple[str, str], dict[str, Any]]) -> list[str]:
    """Every contract violation between the frontend calls and the backend routes."""
    problems = [f"{u} (unresolved call site)" for u in front.unresolved]
    called: set[tuple[str, str]] = set()
    for call in front.calls:
        where = f"{call.file}:{call.line}: {call.method} {call.path}"
        op = ops.get((call.method, call.path))
        if op is None:
            if f"{call.method} {call.path}" not in PENDING_BACKEND:
                problems.append(f"{where}: the backend serves no such route")
            continue
        called.add((call.method, call.path))
        body = op["body"]
        if call.has_body and body is None:
            problems.append(f"{where}: sends a body, the route takes none")
        if call.body_keys is not None and body is not None and body["closed"]:
            extra = call.body_keys - body["properties"]
            if extra:
                problems.append(f"{where}: body field(s) {sorted(extra)} not in the schema")
            missing = body["required"] - call.body_keys
            if missing and not call.body_open:
                problems.append(f"{where}: body omits required {sorted(missing)}")
        if body is not None and body["required"] and not call.has_body:
            problems.append(f"{where}: no body, the schema requires {sorted(body['required'])}")
        if call.query_keys is not None:
            extra_q = call.query_keys - op["query"]
            if extra_q:
                problems.append(f"{where}: query key(s) {sorted(extra_q)} not declared")
    for (method, _), op in sorted(ops.items()):
        key = f"{method} {op['path']}"
        norm = re.sub(r"\{[^}]+\}", "{}", op["path"])
        if (method, norm) in called or (method == "GET" and norm in front.referenced):
            if key in NO_FRONTEND_CALLER:
                problems.append(f"allowlist entry {key} is stale: the frontend now calls it")
            continue
        if key in NO_FRONTEND_CALLER:
            continue
        problems.append(f"backend route {key} has no frontend caller and no allowlist reason")
    for key in PENDING_BACKEND:
        method, _, path = key.partition(" ")
        if (method, re.sub(r"\{[^}]+\}", "{}", path)) in ops:
            problems.append(f"PENDING_BACKEND entry {key} is stale: the backend serves it now")
    return problems


def find_frontend(explicit: Path | None) -> Path | None:
    """The frontend tree to check, or None when this tree has none."""
    candidates = (
        [explicit]
        if explicit
        else [REPO / "frontend", REPO.parent / "Muse-wt-frontend" / "frontend"]
    )
    for candidate in candidates:
        if candidate and (candidate / "src" / "api" / "endpoints.ts").exists():
            return candidate
    return None


def main(argv: list[str]) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--frontend", type=Path, default=None)
    args = parser.parse_args(argv)
    frontend = find_frontend(args.frontend)
    if frontend is None:
        print("check_contract: skipped, this tree has no frontend/src/api/endpoints.ts")
        return 0
    from app.main import create_app

    ops = backend_operations(create_app(sync_on_startup=False).openapi())
    front = read_frontend(frontend)
    problems = compare(front, ops)
    for line in problems:
        print(line)
    print(f"check_contract: {len(front.calls) + len(ops)} contract items checked")
    return 1 if problems or not front.calls else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
