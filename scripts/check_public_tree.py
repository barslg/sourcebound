"""Public-tree gate: no secrets, dumps, or private markers in tracked files.

Fails on: any case-insensitive mention of another model vendor or its
assistant in any file (allow-listed ones included), /Users/ paths,
`scandic`, `LMS2`, `PowerDNS`, emails outside
example.com/example.org/example.net, *.sql/*.dump/*.sqlite files, any
`.env` variant except the sanctioned `.env.example` template,
build/logs artefacts, Google client secrets (GOCSPX-), and Meta Model API
key shapes (a long LLM- token or a MODEL_API_KEY assignment carrying a
real-looking value; fixture words like synthetic/test/fake never match).

Scope: files listed by `git ls-files`, with content read from the working
tree so uncommitted leaks fail too. Untracked working-tree files (a
developer's own .env with live keys) are out of scope: they are gitignored
and must never be committed. `--root DIR` scans a plain directory instead
(tests use it for fixtures).

Usage: python scripts/check_public_tree.py [--root DIR]
Exit 1 with file:line per violation, exit 0 when clean.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

LITERAL_MARKERS = ("/Users/", "scandic", "LMS2", "PowerDNS", "GOCSPX-")

# Hackathon rule: every runtime model is a Meta model, so no tracked file may
# name another model vendor or its assistant. The words are spelled in two
# halves so this file does not trip its own rule.
VENDOR_RE = re.compile("anthro" + "pic|clau" + "de", re.IGNORECASE)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
ALLOWED_EMAIL_DOMAINS = ("example.com", "example.org", "example.net")

# A long LLM- token, or MODEL_API_KEY given a real-looking value.
META_TOKEN_RE = re.compile(r"LLM-[A-Za-z0-9_\-]{24,}")
META_ASSIGNMENT_RE = re.compile(
    r"(?i)model[_-]?api[_-]?key['\"]?\s*[:=]\s*['\"]?"
    r"(?!synthetic|test|fake|dummy|example|placeholder|xxx|redacted)"
    r"([A-Za-z0-9_\-.~+/=]{16,})"
)

FORBIDDEN_SUFFIXES = (".sql", ".dump", ".sqlite")
FORBIDDEN_PATH_PREFIXES = ("build/logs/", "logs/")
FORBIDDEN_PATH_EXACT = {"build/state.json"}

# Files allowed to name secret shapes: the gitleaks rule file, this
# checker (it lists the markers it hunts), the redaction module (it
# masks the shapes it names), and the gate fixtures that plant markers
# on purpose. Content checks skip these paths by explicit allow-list;
# the markers themselves are never weakened. Path checks still apply
# to every file.
CONTENT_ALLOWLIST_EXACT = {
    ".gitleaks.toml",
    "scripts/check_public_tree.py",
    "src/app/redact.py",
}
CONTENT_ALLOWLIST_PREFIXES = ("tests/gates/",)


def is_allowlisted(name: str) -> bool:
    """True when content checks skip this path (see CONTENT_ALLOWLIST_*)."""
    return name in CONTENT_ALLOWLIST_EXACT or name.startswith(CONTENT_ALLOWLIST_PREFIXES)


def tracked_files(repo: Path) -> list[str]:
    """Tracked paths via git ls-files, null-separated for odd names."""
    proc = subprocess.run(
        ["git", "ls-files", "-z"],
        capture_output=True,
        text=True,
        cwd=repo,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git ls-files failed: {proc.stderr.strip()}")
    return [entry for entry in proc.stdout.split("\0") if entry]


def walk_files(root: Path) -> list[str]:
    """All files under a plain directory (fixture mode), minus .git."""
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(root).parts
    )


def check_path(name: str) -> str | None:
    """Path-based violations, or None when the name is fine."""
    lowered = name.lower()
    if any(lowered.endswith(suffix) for suffix in FORBIDDEN_SUFFIXES):
        return f"{name}:1: forbidden dump file"
    base = name.rsplit("/", 1)[-1]
    if (base == ".env" or base.startswith(".env.")) and base != ".env.example":
        return f"{name}:1: .env file must never be committed"
    if name in FORBIDDEN_PATH_EXACT or name.startswith(FORBIDDEN_PATH_PREFIXES):
        return f"{name}:1: build/log artefact must never be committed"
    return None


def check_vendor(name: str, text: str) -> list[str]:
    """Lines that name another model vendor; no file is exempt from this rule."""
    return [
        f"{name}:{lineno}: names a model vendor (Meta models only)"
        for lineno, line in enumerate(text.splitlines(), start=1)
        if VENDOR_RE.search(line)
    ]


def check_content(name: str, text: str) -> list[str]:
    """Content-based violations with line numbers."""
    violations: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for marker in LITERAL_MARKERS:
            if marker in line:
                violations.append(f"{name}:{lineno}: forbidden marker {marker!r}")
        for match in EMAIL_RE.finditer(line):
            domain = match.group(1).lower()
            if not domain.endswith(ALLOWED_EMAIL_DOMAINS):
                violations.append(f"{name}:{lineno}: non-example email {match.group(0)!r}")
        if META_TOKEN_RE.search(line):
            violations.append(f"{name}:{lineno}: Meta key shape (LLM- token)")
        if META_ASSIGNMENT_RE.search(line):
            violations.append(f"{name}:{lineno}: MODEL_API_KEY carries a value")
    return violations


def is_text(path: Path) -> bool:
    """False for binary files (a NUL byte in the first block)."""
    try:
        with path.open("rb") as handle:
            return b"\0" not in handle.read(8192)
    except OSError:
        return False


def check_tree(repo: Path, names: list[str]) -> list[str]:
    """Every public-tree violation for the given file list."""
    violations: list[str] = []
    for name in names:
        path_hit = check_path(name)
        if path_hit is not None:
            violations.append(path_hit)
        path = repo / name
        if path.is_file() and is_text(path):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                violations.append(f"{name}:1: unreadable ({exc})")
                continue
            violations += check_vendor(name, text)
            if not is_allowlisted(name):
                violations += check_content(name, text)
    return sorted(violations)


def main(argv: list[str] | None = None) -> int:
    """Parse args, print violations, exit 1 when any exist."""
    parser = argparse.ArgumentParser(description="Public-tree gate (see module docstring).")
    parser.add_argument("--root", default=None, help="Scan a directory, not git ls-files.")
    args = parser.parse_args(argv)
    if args.root:
        repo = Path(args.root).resolve()
        names = walk_files(repo)
    else:
        repo = Path(__file__).resolve().parent.parent
        names = tracked_files(repo)
    violations = check_tree(repo, names)
    for line in violations:
        print(line)
    if violations:
        print(f"{len(violations)} public-tree violation(s)", file=sys.stderr)
        return 1
    print("public tree OK")
    print(f"check_public_tree: {len(names)} files checked")
    return 0 if names else 1


if __name__ == "__main__":
    raise SystemExit(main())
