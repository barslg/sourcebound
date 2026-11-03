# src

Backend FastAPI service lives in `app/` (`src/` layout, hatchling, uv).
Layer order per `docs/PRINCIPLES.md`: routes → controllers → services →
providers → clients; SQL only in `models/`; migrations in `migrations/`.
