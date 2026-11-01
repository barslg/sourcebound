"""Helpers for gate tests: build a throw-away repo tree from a path -> text map."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]


def write_tree(root: Path, files: dict[str, str]) -> Path:
    """Write the files under root and return it."""
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def load_script(name: str) -> ModuleType:
    """Import scripts/<name>.py as a module (its CLI entrypoint does not run)."""
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
