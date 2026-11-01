#!/usr/bin/env bash
# Runs the jobs of .github/workflows/ci.yml, in the same order, on a fresh
# clone of HEAD (tracked files only: no .env, no ignored prompts/docs) with its
# own database. Stops at the first failing job. Push only after this is green.
#
# Env: CI_LOCAL_PG (default postgresql://muse:muse@localhost:5434/postgres) is the
# maintenance URL of a Postgres 16; a throwaway database is created and dropped.
set -euo pipefail

REPO="$(git rev-parse --show-toplevel)"
PG_ADMIN="${CI_LOCAL_PG:-postgresql://muse:muse@localhost:5434/postgres}"
DB="muse_ci_$$"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/muse-ci.XXXXXX")"

if [ -n "$(git -C "$REPO" status --porcelain --untracked-files=no)" ]; then
  echo "ci_local: tracked files have uncommitted changes; CI tests only what is committed" >&2
fi

dropdb() {
  PG_ADMIN="$PG_ADMIN" DB="$DB" uv run --project "$REPO" python - <<'PY' || true
import os, psycopg
with psycopg.connect(os.environ["PG_ADMIN"], autocommit=True) as c:
    c.execute(f'DROP DATABASE IF EXISTS "{os.environ["DB"]}" WITH (FORCE)')
PY
}
cleanup() { dropdb; rm -rf "$WORK"; }
trap cleanup EXIT

step() { printf '\n=== ci_local: %s ===\n' "$1"; }

step "fresh clone of HEAD ($(git -C "$REPO" rev-parse --short HEAD))"
git clone -q --no-local "$REPO" "$WORK/src"
cd "$WORK/src"

PG_ADMIN="$PG_ADMIN" DB="$DB" uv run --project "$REPO" python - <<'PY'
import os, psycopg
with psycopg.connect(os.environ["PG_ADMIN"], autocommit=True) as c:
    c.execute(f'CREATE DATABASE "{os.environ["DB"]}"')
PY
export TEST_DATABASE_URL="postgresql+asyncpg://${PG_ADMIN#postgresql://}"
export TEST_DATABASE_URL="$(printf '%s' "$TEST_DATABASE_URL" | sed -E "s#/[^/]*\$#/$DB#")"
unset MODEL_API_KEY DATABASE_URL

step "uv sync"
uv sync --frozen

step "job ruff"
uv run ruff check src tests migrations scripts
uv run ruff format --check src tests migrations scripts

step "job mypy"
uv run mypy src

step "job pytest"
uv run pytest -m "not live"

step "job mechanical gates"
uv run pytest -m "not live" tests/gates/
uv run python scripts/run_gates.py

step "job mutation (changed lines against origin/dev, 9-minute budget)"
uv run python scripts/mutate_changed.py --base origin/dev --budget 540

step "job gitleaks (history of the clone, then the tree)"
if command -v gitleaks >/dev/null 2>&1; then
  gitleaks detect --source . --config .gitleaks.toml --redact --no-banner
  gitleaks detect --source . --config .gitleaks.toml --redact --no-banner --no-git
else
  echo "ci_local: gitleaks not installed (brew install gitleaks)" >&2
  exit 1
fi

step "job check_public_tree"
uv run python scripts/check_public_tree.py

step "job check-frontend"
if [ -f frontend/package.json ]; then
  (cd frontend && npm ci)
  make check-frontend
else
  echo "no frontend/package.json; skipped"
fi

step "ALL JOBS GREEN"
