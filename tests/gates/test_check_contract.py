"""Gates for scripts/check_contract.py: a frontend call the backend cannot answer is red."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from gate_tree import ROOT, load_script, write_tree

checker = load_script("check_contract")

ENDPOINTS_TS = """const API = '/api/v1'
export const ENDPOINTS = {
  items: `${API}/items`,
  health: `${API}/health`,
} as const
const enc = encodeURIComponent
export const itemOne = (id: string): string => `${ENDPOINTS.items}/${enc(id)}`
export const itemClose = (id: string): string => `${itemOne(id)}/close`
export const itemOpen = (id: string): string => `${itemOne(id)}/open`
"""

SPEC: dict[str, Any] = {
    "paths": {
        "/api/v1/health": {"get": {}},
        "/api/v1/items": {
            "get": {},
            "post": {
                "requestBody": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/New"}}
                    }
                }
            },
        },
        "/api/v1/items/{item_id}": {"delete": {"parameters": [{"in": "query", "name": "hard"}]}},
        "/api/v1/items/{item_id}/close": {"post": {}},
        "/api/v1/items/{item_id}/open": {"post": {}},
    },
    "components": {
        "schemas": {
            "New": {
                "properties": {"title": {}, "tag": {}},
                "required": ["title"],
            }
        }
    },
}

VIEW = """import { request } from '@/api/client'
import { ENDPOINTS, itemOne, itemClose, itemOpen } from '@/api/endpoints'
await request(ENDPOINTS.items)
await request(ENDPOINTS.health)
await request(ENDPOINTS.items, { method: 'POST', body: { title: t, tag } })
await request(itemOne(id), { method: 'DELETE', query: { hard: true } })
await request(flag ? itemClose(id) : itemOpen(id), { method: 'POST' })
"""


@pytest.fixture(autouse=True)
def _empty_allowlists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(checker, "NO_FRONTEND_CALLER", {})
    monkeypatch.setattr(checker, "PENDING_BACKEND", {})


def _run(tmp_path: Path, view: str, spec: dict[str, Any] = SPEC) -> list[str]:
    root = write_tree(
        tmp_path,
        {"src/api/endpoints.ts": ENDPOINTS_TS, "src/api/client.ts": "", "src/views/V.vue": view},
    )
    return checker.compare(checker.read_frontend(root), checker.backend_operations(spec))


def test_consistent_frontend_is_green(tmp_path: Path) -> None:
    """Calls, a ternary of two endpoints, body fields and a query key all line up."""
    assert _run(tmp_path, VIEW) == []


def test_call_to_a_missing_route_is_red(tmp_path: Path) -> None:
    """The unban case: the frontend calls a route the backend does not serve."""
    problems = _run(tmp_path, VIEW + "await request(itemOne(id), { method: 'PATCH' })\n")
    assert any("PATCH /api/v1/items/{}" in p and "no such route" in p for p in problems)


def test_unknown_body_field_is_red(tmp_path: Path) -> None:
    """A body field the schema does not declare."""
    view = VIEW.replace("{ title: t, tag }", "{ title: t, colour }")
    assert any("colour" in p for p in _run(tmp_path, view))


def test_missing_required_body_field_is_red(tmp_path: Path) -> None:
    """A body literal that omits a required field."""
    view = VIEW.replace("{ title: t, tag }", "{ tag }")
    assert any("omits required ['title']" in p for p in _run(tmp_path, view))


def test_undeclared_query_key_is_red(tmp_path: Path) -> None:
    """A query key the route does not declare."""
    view = VIEW.replace("{ hard: true }", "{ soft: true }")
    assert any("query key(s) ['soft']" in p for p in _run(tmp_path, view))


def test_route_without_caller_is_red(tmp_path: Path) -> None:
    """A backend route nobody calls and nobody listed fails."""
    view = VIEW.replace("itemClose(id) : itemOpen(id)", "itemOpen(id)")
    assert any(
        "POST /api/v1/items/{item_id}/close has no frontend caller" in p
        for p in _run(tmp_path, view)
    )


def test_stale_allowlist_entry_is_red(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An allowlist reason for a route that is now called must be removed."""
    monkeypatch.setattr(checker, "NO_FRONTEND_CALLER", {"POST /api/v1/items/{item_id}/open": "x"})
    assert any("stale" in p for p in _run(tmp_path, VIEW))


def test_variable_path_is_reported_not_passed(tmp_path: Path) -> None:
    """A call whose path names no endpoint symbol is reported as unresolved."""
    problems = _run(tmp_path, VIEW + "await request(somePath)\n")
    assert any("unresolved call site" in p for p in problems)


def test_tree_without_frontend_is_skipped(tmp_path: Path) -> None:
    """No endpoints.ts anywhere: the gate says so instead of passing silently."""
    assert checker.find_frontend(tmp_path) is None


def test_real_frontend_is_consistent_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """The sibling frontend tree, when checked out, matches this backend."""
    monkeypatch.undo()
    frontend = checker.find_frontend(None)
    if frontend is None:
        pytest.skip("no frontend tree next to this one")
    from app.main import create_app

    ops = checker.backend_operations(create_app(sync_on_startup=False).openapi())
    front = checker.read_frontend(frontend)
    assert front.calls
    assert checker.compare(front, ops) == []
    assert len(front.calls) > 40, ROOT
