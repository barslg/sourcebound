"""Run every mechanical gate and print one line per gate with its checked count.

A gate that checked nothing is a failure: its last stdout line must read
`<gate>: <n> <unit> checked` with n > 0, and its exit code must be 0.
`make gates` and the CI "mechanical gates" job call this one script, so a
local run and CI cannot drift.

Usage: python scripts/run_gates.py [--only NAME ...]
Exit 1 when any gate fails or checked nothing.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

GATES = (
    "check_layers",
    "check_public_tree",
    "check_routes",
    "check_callers",
    "check_scoped_sql",
    "check_contract",
    "check_schema_enums",
    "check_prompt_contract",
)

SUMMARY_RE = re.compile(r"^(?P<name>\w+): (?P<count>\d+) (?P<unit>[\w ]+?) checked$")


def run_gate(name: str) -> tuple[bool, str, str]:
    """Run one gate; return (ok, one-line summary, captured output on failure)."""
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, str(REPO / "scripts" / f"{name}.py")],
        capture_output=True,
        text=True,
        cwd=REPO,
        check=False,
    )
    seconds = time.monotonic() - started
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    match = SUMMARY_RE.match(lines[-1]) if lines else None
    if proc.returncode != 0:
        return (
            False,
            f"{name}: FAILED (exit {proc.returncode}, {seconds:.1f}s)",
            proc.stdout + proc.stderr,
        )
    if lines and re.match(rf"^{name}: skipped, ", lines[-1]):
        return True, f"{name}: SKIPPED - {lines[-1].split('skipped, ', 1)[1]}", ""
    if match is None:
        return False, f"{name}: FAILED (no 'N units checked' line)", proc.stdout
    if int(match["count"]) == 0:
        return False, f"{name}: FAILED (0 {match['unit']} checked)", proc.stdout
    return True, f"{name}: {match['count']} {match['unit']} checked ({seconds:.1f}s)", ""


def main(argv: list[str]) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*", default=None)
    args = parser.parse_args(argv)
    failed = 0
    for name in GATES:
        if args.only and name not in args.only:
            continue
        ok, line, detail = run_gate(name)
        print(line, flush=True)
        if not ok:
            failed += 1
            print(detail)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
