"""Resumable build runner: task by task, gate each task, commit each task.

State lives in git (a done task = a commit starting with "build(<id>):")
and in build/state.json (the task in progress, attempt count, session).
Re-running after any interruption resumes the task in progress from the
current working tree instead of starting over.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "build" / "tasks.toml"
STATE = ROOT / "build" / "state.json"
LOGS = ROOT / "build" / "logs"
MAX_ATTEMPTS = 3


def sh(*args: str, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=ROOT, text=True, capture_output=capture, check=check)


def done_ids() -> set[str]:
    out = sh("git", "log", "--format=%s", check=False).stdout
    return {line.split(":", 1)[0][6:-1] for line in out.splitlines() if line.startswith("build(")}


def load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=2) + "\n")


def tasks() -> list[dict]:
    return tomllib.loads(LEDGER.read_text())["task"]


def worktree_dirty() -> bool:
    return bool(sh("git", "status", "--porcelain").stdout.strip())


def next_task(state: dict) -> dict | None:
    done = done_ids()
    if (cur := state.get("in_progress")) and cur["id"] not in done:
        return next(t for t in tasks() if t["id"] == cur["id"])
    for t in tasks():
        if t["id"] not in done and all(d in done for d in t.get("depends", [])):
            return t
    return None


def build_prompt(task: dict, state: dict, gate_output: str | None) -> str:
    base = (ROOT / task["prompt"]).read_text()
    cur = state.get("in_progress") or {}
    if cur.get("id") == task["id"] and (worktree_dirty() or gate_output):
        diff = sh("git", "status", "--short").stdout
        base += (
            "\n\n--- RESUME ---\nThis task was interrupted after partial work (attempt "
            f"{cur.get('attempt', 1)}). Do not start over: read `git status` and "
            "`git diff`, keep what is correct, finish the remaining steps, "
            f"and re-run the gate.\nCurrent working tree:\n{diff}\n"
        )
    if gate_output:
        base += (
            "\n\n--- GATE FAILED ---\nFix these, then re-run `make check`:\n"
            f"{gate_output[-6000:]}\n"
        )
    notes = ROOT / "build" / "notes" / f"{task['id']}.md"
    if notes.exists():
        base += f"\n\n--- REVIEW NOTES (fix every High and Medium) ---\n{notes.read_text()}\n"
    return base


def run_muse(task: dict, prompt: str) -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    prompt_file = LOGS / f"{task['id']}-{stamp}.prompt.md"
    prompt_file.write_text(prompt)
    log = LOGS / f"{task['id']}-{stamp}.jsonl"
    cmd = [
        "muse",
        "exec",
        "--trust-workspace",
        "--disable-approval",
        "--sandbox-network",
        "enabled",
        "--json",
        "--model",
        task.get("model", "muse-spark-1.3"),
        "--reasoning-effort",
        task.get("effort", "high"),
        "--max-model-steps",
        str(task.get("max_steps", 400)),
        "--prompt-file",
        str(prompt_file),
    ]
    with log.open("w") as fh:
        proc = subprocess.run(
            cmd, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT, text=True, check=False
        )
    return proc.returncode


E2E_ENV = {
    "E2E_DB_NAME": "muse_e2e",
    "E2E_BACKEND_PORT": "8011",
    "E2E_PREVIEW_PORT": "4174",
    "E2E_OIDC_PORT": "9020",
    "E2E_MODEL_PORT": "9030",
}


def gate() -> tuple[bool, str]:
    """make check, then the frontend checks and the e2e suite outside the Muse sandbox.

    Muse's sandbox cannot launch Chromium, so the runner (which runs on the host)
    owns the e2e job; a frontend task is green only when the whole suite is.
    """
    proc = sh("make", "check", check=False)
    out = proc.stdout + proc.stderr
    if proc.returncode != 0:
        return False, out
    if not (ROOT / "frontend" / "package.json").exists():
        return True, out
    env = {**os.environ, **E2E_ENV}
    for cmd in (["npm", "run", "check"], ["npm", "run", "e2e"]):
        fe = subprocess.run(
            cmd, cwd=ROOT / "frontend", env=env, capture_output=True, text=True, check=False
        )
        out += f"\n$ (frontend) {' '.join(cmd)}\n" + fe.stdout[-20000:] + fe.stderr[-6000:]
        if fe.returncode != 0:
            return False, out
    return True, out


def restart_stand() -> None:
    """The stand on 8010/4173 serves the committed tree; Muse's sandbox cannot restart it."""
    script = ROOT / "tmp" / "stand.sh"
    if not script.exists():
        return
    subprocess.run(["bash", str(script), "stop"], cwd=ROOT, check=False, capture_output=True)
    subprocess.Popen(
        ["bash", str(script), "start"],
        cwd=ROOT,
        stdout=open(ROOT / "tmp" / "stand-start.log", "w"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def commit(task: dict) -> None:
    sh("git", "add", "-A")
    # Muse may have committed its own work already; the marker commit still records the task.
    sh("git", "commit", "-q", "--allow-empty", "-m", f"build({task['id']}): {task['title']}")


def quota_reset_from_log(task: dict) -> str | None:
    """The reset time Muse reported when the subscription window ran out, else None."""
    logs = sorted(LOGS.glob(f"{task['id']}-*.jsonl"))
    if not logs:
        return None
    text = logs[-1].read_bytes().decode("utf-8", "replace")
    m = re.search(r"Subscription quota exhausted\. Your usage window resets at ([0-9T:\-]+Z)", text)
    return m.group(1) if m else None


def main() -> int:
    state = load_state()
    paused = state.get("paused_until")
    if paused and datetime.now(UTC) < datetime.fromisoformat(paused.replace("Z", "+00:00")):
        print(f"paused until {paused} (Muse subscription window)", file=sys.stderr)
        return 3
    state.pop("paused_until", None)
    task = next_task(state)
    if task is None:
        print("all tasks done")
        return 0
    cur = state.get("in_progress") or {}
    attempt = cur.get("attempt", 0) + 1 if cur.get("id") == task["id"] else 1
    if attempt > MAX_ATTEMPTS:
        print(
            f"{task['id']}: {MAX_ATTEMPTS} attempts failed; stop and inspect build/logs",
            file=sys.stderr,
        )
        return 2
    state["in_progress"] = {
        "id": task["id"],
        "attempt": attempt,
        "started": datetime.now(UTC).isoformat(),
    }
    save_state(state)
    gate_output = cur.get("gate_output") if cur.get("id") == task["id"] else None
    print(f"{task['id']} attempt {attempt}: {task['title']}")
    code = run_muse(task, build_prompt(task, state, gate_output))
    paused_until = quota_reset_from_log(task)
    if paused_until:
        # The subscription window is exhausted: not an attempt, just wait for the reset.
        state["in_progress"]["attempt"] = attempt - 1
        state["paused_until"] = paused_until
        save_state(state)
        print(f"{task['id']}: Muse quota exhausted; paused until {paused_until}", file=sys.stderr)
        return 3
    ok, out = gate()
    if code == 0 and ok:
        commit(task)
        state.pop("in_progress", None)
        save_state(state)
        restart_stand()
        print(f"{task['id']}: done and committed")
        return 0
    state["in_progress"]["gate_output"] = out
    save_state(state)
    print(
        f"{task['id']}: muse exit {code}, gate {'green' if ok else 'red'};"
        " re-run scripts/build.py to resume",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
