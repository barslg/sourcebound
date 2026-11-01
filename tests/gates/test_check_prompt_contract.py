"""Gates for scripts/check_prompt_contract.py: a read the schema does not require is red."""

from __future__ import annotations

import ast
from pathlib import Path

from gate_tree import ROOT, load_script

checker = load_script("check_prompt_contract")


def _problems(source: str, roots: dict[str, str]) -> list[str]:
    func = ast.parse(source).body[0]
    visitor = checker.Reads(roots)
    visitor.visit(func)
    return checker.judge_reads("x.py", "f", visitor.reads, checker.schema_index())


def test_required_key_read_is_green() -> None:
    """The saboteur's required `mutated` may be read straight off the reply."""
    assert (
        _problems("def f(payload):\n    return payload['mutated']\n", {"payload": "saboteur"}) == []
    )


def test_read_of_a_key_the_schema_lacks_is_red() -> None:
    """`.get('confidence')` on a reply whose schema never asks for it."""
    problems = _problems(
        "def f(payload):\n    return payload.get('confidence')\n", {"payload": "saboteur"}
    )
    assert len(problems) == 1 and "confidence" in problems[0]


def test_nested_key_must_be_a_schema_property() -> None:
    """A verdict row key read in a loop is checked against the reader's properties."""
    ok = "def f(payload):\n    for v in payload['verdicts']:\n        v.get('plan_node_id')\n"
    bad = "def f(payload):\n    for v in payload['verdicts']:\n        v.get('hold_reason')\n"
    assert _problems(ok, {"payload": "reader"}) == []
    assert any("hold_reason" in p for p in _problems(bad, {"payload": "reader"}))


def test_name_reuse_for_code_built_values_is_not_a_read() -> None:
    """A loop variable rebound to a code-built list stops being reply-derived."""
    src = (
        "def f(payload):\n    for c in payload['verdicts']:\n        pass\n"
        "    for c in built:\n        c['quote_start']\n"
    )
    assert _problems(src, {"payload": "reader"}) == []


def test_unfilled_placeholder_in_a_system_prompt_is_red(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`{course_language}` in a system.md would reach the model verbatim."""
    agent = tmp_path / "architect"
    agent.mkdir()
    (agent / "system.md").write_text('Write in {course_language}.\n```json\n{"a": 1}\n```\n')
    monkeypatch.setattr(checker, "prompt_files", lambda: [agent / "system.md"])
    monkeypatch.setattr(checker, "REPO", tmp_path)
    count, problems = checker.check_placeholders()
    assert count == 1 and len(problems) == 1 and "course_language" in problems[0]


def test_real_tree_is_clean() -> None:
    """The committed executor and agent checkers read only schema keys."""
    checked, problems = checker.check_reads()
    assert problems == []
    assert checked > 20, ROOT
    assert checker.check_placeholders()[1] == []
