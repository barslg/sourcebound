"""Gates for scripts/check_schema_enums.py: a drifted or missing enum is red."""

from __future__ import annotations

import copy
import subprocess
import sys
from typing import Any

import pytest
from gate_tree import ROOT, load_script

checker = load_script("check_schema_enums")


@pytest.fixture()
def schemas() -> dict[str, dict[str, Any]]:
    from app.agents.base import AGENT_SCHEMAS
    from app.course.tree import BLOCK_GENERATOR_SCHEMAS

    out: dict[str, dict[str, Any]] = {a: copy.deepcopy(dict(s)) for a, s in AGENT_SCHEMAS.items()}
    out.update(
        {f"generator:{t}": copy.deepcopy(dict(s)) for t, s in BLOCK_GENERATOR_SCHEMAS.items()}
    )
    return out


def _problems(schemas: dict[str, dict[str, Any]]) -> list[str]:
    return checker.check_schemas(schemas, checker.vocabularies())[2]


def test_real_schemas_are_closed(schemas: dict[str, dict[str, Any]]) -> None:
    """Every bound field of every real schema carries exactly its vocabulary."""
    checked, seen, problems = checker.check_schemas(schemas, checker.vocabularies())
    assert problems == []
    assert checked > 20 and {"block_type", "role", "verdict", "mutation_kind"} <= seen


def test_dropped_enum_value_is_red(schemas: dict[str, dict[str, Any]]) -> None:
    """A saboteur kind enum missing one mutation kind."""
    schemas["saboteur"]["properties"]["kind"]["enum"].pop()
    assert any("saboteur" in p and "mutation_kind" in p for p in _problems(schemas))


def test_extra_enum_value_is_red(schemas: dict[str, dict[str, Any]]) -> None:
    """A generator schema that allows a block type the registry does not have."""
    schemas["generator:reading"]["properties"]["block_type"]["enum"].append("numeric")
    assert any("generator:reading" in p for p in _problems(schemas))


def test_missing_enum_is_red(schemas: dict[str, dict[str, Any]]) -> None:
    """A role field that is a free string."""
    block = schemas["architect"]["properties"]["modules"]["items"]["properties"]["topics"]["items"]
    block["properties"]["blocks"]["items"]["properties"]["role"].pop("enum")
    assert any("role field has no enum" in p for p in _problems(schemas))


def test_unbound_enum_is_red(schemas: dict[str, dict[str, Any]]) -> None:
    """A closed list that lives only in a schema literal."""
    schemas["reader"]["properties"]["verdicts"]["items"]["properties"]["plan_node_id"]["enum"] = [
        "a",
        "b",
    ]
    assert any("bound to no vocabulary" in p for p in _problems(schemas))


def test_rubric_item_drift_is_red(schemas: dict[str, dict[str, Any]]) -> None:
    """A rubric item added to the schema but not to RUBRIC_ITEMS."""
    items = schemas["reader"]["properties"]["verdicts"]["items"]["properties"]["items"]
    items["properties"]["tone"] = copy.deepcopy(items["properties"]["task_fit"])
    items["required"].append("tone")
    assert checker.check_rubric(schemas["reader"])


def test_code_maps_are_consistent() -> None:
    """Planting habits, block types per kind and methodologies match their vocabularies."""
    checked, problems = checker.check_code_maps(checker.vocabularies())
    assert problems == [] and checked > 10


def test_ghost_block_type_in_code_map_is_red(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pre-T08g state: tree.BLOCK_TYPES named types no generator exists for."""
    from app.course import tree

    monkeypatch.setitem(tree.BLOCK_TYPES, "lesson", (*tree.BLOCK_TYPES["lesson"], "numeric"))
    _, problems = checker.check_code_maps(checker.vocabularies())
    assert any("numeric" in p for p in problems)


def test_script_runs_green_on_the_tree() -> None:
    """The CLI exits 0 and reports a non-zero count (validate_agents included)."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_schema_enums.py")],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "vocabulary bindings checked" in proc.stdout
