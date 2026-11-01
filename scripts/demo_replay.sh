#!/usr/bin/env bash
# Build the demo course from the recorded ledger on a fresh database: no model key, no spend.
#
# Starts the compose database, recreates the database `muse_demo`, migrates it, and runs
# scripts/demo_replay.py against it with MODEL_REPLAY_LEDGER pointing at the recorded runs.
# Afterwards point DATABASE_URL at muse_demo (see README "Replay the demo without a key").
set -euo pipefail
cd "$(dirname "$0")/.."

DEMO_DB="${DEMO_DB:-muse_demo}"
ADMIN_URL="${DEMO_ADMIN_URL:-postgresql://muse:muse@localhost:5434/postgres}"
export DATABASE_URL="postgresql+asyncpg://muse:muse@localhost:5434/${DEMO_DB}"
export MODEL_API_KEY=""

docker compose up -d --wait db
uv run python - "$ADMIN_URL" "$DEMO_DB" <<'PY'
import sys, psycopg
admin, name = sys.argv[1], sys.argv[2]
with psycopg.connect(admin, autocommit=True) as conn:
    conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    conn.execute(f'CREATE DATABASE "{name}"')
PY
uv run alembic upgrade head
uv run python -m app.tasks.schema
uv run python scripts/demo_replay.py
