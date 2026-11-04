"""T03 static gates: model-call confinement and banned APIs.

`generate_json(` may appear only in the provider definition and the
runs layer's step modules (executor, lecture steps); agents/ and pipelines/ must not
mint uuids, read clocks, or roll randomness — ids and digests belong
to the runs layer, determinism to tests.
"""

from __future__ import annotations

import pathlib
import re

SRC = pathlib.Path("src/app")


def _occurrences(filename: str, pattern: str) -> list[str]:
    hits: list[str] = []
    for path in sorted(SRC.rglob(filename)):
        if "__pycache__" in path.parts:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(pattern, line):
                hits.append(f"{path}:{number}")
    return hits


def test_generate_json_call_site_is_only_the_executor() -> None:
    hits = _occurrences("*.py", r"generate_json\(")
    allowed_files = {
        "src/app/providers/llm.py",
        "src/app/runs/executor.py",
        "src/app/runs/lecture_steps.py",
    }
    offenders = [hit for hit in hits if hit.rsplit(":", 1)[0] not in allowed_files]
    assert offenders == []


def test_no_uuid_now_or_random_under_agents_and_pipelines() -> None:
    offenders: list[str] = []
    for package in ("agents", "pipelines"):
        root = SRC / package
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            for pattern in (
                r"uuid4\s*\(",
                r"datetime\.now\s*\(",
                r"\bnow\s*\(\s*\)",
                r"random\.",
                r"time\.time\s*\(",
            ):
                if re.search(pattern, text):
                    offenders.append(f"{path}: {pattern}")
    assert offenders == []
