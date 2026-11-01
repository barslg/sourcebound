#!/bin/sh
# Refuse to start the api, the worker or the migration without the agent system prompts.
# A fresh clone has no prompts/ (private until publication); the product never runs on stubs.
set -eu
if ! python -c 'from app.agents.base import validate_agents; validate_agents()' 2>/tmp/prompts.err; then
  echo "muse: the agent system prompts are missing or incomplete in this image." >&2
  echo "muse: prompts/<agent>/system.md ships with the published repository; add the" >&2
  echo "muse: prompts/ directory next to the Dockerfile and rebuild (docker compose build)." >&2
  echo "muse: detail: $(tail -n 1 /tmp/prompts.err)" >&2
  exit 1
fi
exec "$@"
