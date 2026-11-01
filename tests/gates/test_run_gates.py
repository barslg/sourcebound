"""Gates for scripts/run_gates.py: an empty or silent gate is a failure."""

from __future__ import annotations

from pathlib import Path

import pytest
from gate_tree import ROOT, load_script, write_tree

runner = load_script("run_gates")


def _fake_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    write_tree(tmp_path, {"scripts/fake_gate.py": body})
    monkeypatch.setattr(runner, "REPO", tmp_path)


def test_gate_with_items_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A gate that exits 0 and reports N > 0 items is green and its count is printed."""
    _fake_gate(tmp_path, monkeypatch, "print('fake_gate: 7 widgets checked')\n")
    ok, line, _ = runner.run_gate("fake_gate")
    assert ok and line.startswith("fake_gate: 7 widgets checked")


def test_gate_with_zero_items_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty gate (nothing checked) fails even with exit 0."""
    _fake_gate(tmp_path, monkeypatch, "print('fake_gate: 0 widgets checked')\n")
    ok, line, _ = runner.run_gate("fake_gate")
    assert not ok and "0 widgets checked" in line


def test_gate_without_count_line_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A gate that prints no count cannot prove it looked at anything."""
    _fake_gate(tmp_path, monkeypatch, "print('all fine')\n")
    ok, _, _ = runner.run_gate("fake_gate")
    assert not ok


def test_failing_gate_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-zero exit fails regardless of the count line."""
    _fake_gate(
        tmp_path, monkeypatch, "print('fake_gate: 3 widgets checked')\nraise SystemExit(1)\n"
    )
    ok, line, _ = runner.run_gate("fake_gate")
    assert not ok and "FAILED" in line


def test_every_gate_script_is_in_the_runner() -> None:
    """A scripts/check_*.py gate missing from GATES would never run in CI."""
    on_disk = {path.stem for path in (ROOT / "scripts").glob("check_*.py")}
    assert on_disk == set(runner.GATES)


def test_gate_that_names_its_missing_input_is_skipped_visibly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A gate whose input tree is absent says so; the line stays visible, not a silent pass."""
    _fake_gate(tmp_path, monkeypatch, "print('fake_gate: skipped, no frontend here')\n")
    ok, line, _ = runner.run_gate("fake_gate")
    assert ok and "SKIPPED" in line and "no frontend here" in line
