"""Deploy-env gate: the production .env the deploy workflow writes matches the settings catalogue.

Reads the `NAME=value` lines of the heredoc in .github/workflows/deploy.yml and
fails when a name is not in .env.example (a renamed setting the deploy would
still write under the old name), when a setting production refuses to boot
without (`NAME must be set` in src/app/config.py) or one the Google sign-in
needs is not written, when APP_ENV is not prod, or when OIDC_REDIRECT_URL does
not end in the auth callback path the API mounts.

Usage: python scripts/check_deploy_env.py [--root DIR]
Exit 1 with one line per problem, exit 0 when clean.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = ".github/workflows/deploy.yml"
AUTH_NAMES = (
    "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "OIDC_REDIRECT_URL",
    "SUPERADMIN_EMAILS",
)
CALLBACK_PATH = "/api/v1/auth/callback"

ASSIGNMENT_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)=(.*)$")
MUST_BE_SET_RE = re.compile(r"\"([A-Z][A-Z0-9_]*) must be set")


def written_names(workflow_text: str) -> dict[str, str]:
    """NAME -> value for every assignment line of the workflow's .env heredoc."""
    out: dict[str, str] = {}
    for line in workflow_text.splitlines():
        match = ASSIGNMENT_RE.match(line)
        if match:
            out[match.group(1)] = match.group(2).strip()
    return out


def catalogue_names(env_example: str) -> set[str]:
    """Every setting name .env.example lists."""
    return {m.group(1) for m in map(ASSIGNMENT_RE.match, env_example.splitlines()) if m}


def required_names(config_text: str) -> set[str]:
    """Settings production refuses to boot without, plus the sign-in set."""
    return set(MUST_BE_SET_RE.findall(config_text)) | set(AUTH_NAMES)


def check(root: Path) -> tuple[int, list[str]]:
    """Return (names checked, problems) for the tree at root."""
    written = written_names((root / WORKFLOW).read_text(encoding="utf-8"))
    catalogue = catalogue_names((root / ".env.example").read_text(encoding="utf-8"))
    required = required_names((root / "src/app/config.py").read_text(encoding="utf-8"))
    problems = [
        f"{WORKFLOW}: {name} is not in .env.example" for name in sorted(set(written) - catalogue)
    ]
    problems += [
        f"{WORKFLOW}: {name} is required in production and not written"
        for name in sorted(required - set(written))
    ]
    if written.get("APP_ENV") != "prod":
        problems.append(f"{WORKFLOW}: APP_ENV must be prod, got {written.get('APP_ENV')!r}")
    if not written.get("OIDC_REDIRECT_URL", "").endswith(CALLBACK_PATH):
        problems.append(f"{WORKFLOW}: OIDC_REDIRECT_URL must end with {CALLBACK_PATH}")
    return len(written) + len(required), problems


def main(argv: list[str]) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=REPO)
    args = parser.parse_args(argv)
    checked, problems = check(args.root)
    for line in problems:
        print(line)
    print(f"check_deploy_env: {checked} names checked")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
