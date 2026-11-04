# Third-party components

The project's own code is MIT (see `LICENSE`). Backend runtime dependencies, versions as locked in `uv.lock`
(2026-11-01); the frontend lane records its own packages in this table when it merges.

| Component | Version | Licence | Used for |
|---|---|---|---|
| FastAPI | 0.162.0 | MIT | HTTP routes |
| Uvicorn | 0.54.0 | BSD-3-Clause | ASGI server |
| Pydantic / pydantic-settings | 2.14.2 / 2.16.2 | MIT | schemas, settings |
| SQLAlchemy | 2.1.6 | MIT | SQL core (no ORM models, text SQL in services) |
| asyncpg | 0.32.0 | Apache-2.0 | Postgres driver (request path) |
| psycopg | 3.3.6 | LGPL-3.0-only | Postgres driver (Procrastinate worker, test setup); used as an unmodified library |
| Alembic | 1.20.1 | MIT | migrations |
| Procrastinate | 3.10.0 | MIT | Postgres job queue |
| openai (SDK) | 3.42.0 | Apache-2.0 | Responses API client, pointed at the Meta Model API |
| httpx / httpx2 | 0.28.1 / 2.16.0 | BSD-3-Clause | HTTP (source fetching, transport guard in tests) |
| structlog | 26.1.0 | MIT OR Apache-2.0 | logging |
| python-multipart | 0.0.32 | Apache-2.0 | uploads |
| PyJWT | 2.15.1 | MIT | ID-token verification |
| pypdf | 6.21.4 | BSD-3-Clause | PDF text extraction |
| Fraunces (`@fontsource-variable/fraunces`) | 5.3.0 | OFL-1.1 | display type (page and course titles), self-hosted |
| Source Serif 4 (`@fontsource-variable/source-serif-4`) | 5.3.1 | OFL-1.1 | learner reading type, self-hosted |
| Inter (`@fontsource-variable/inter`) | 5.3.0 | OFL-1.1 | interface type, self-hosted |
| markdown-it (+ `@types/markdown-it`) | 14.3.3 / 14.2.0 | MIT | the learner's reading: the writers' markdown (paragraphs, lists, tables, code) rendered with HTML disabled |
| mermaid | 12.3.0 | MIT | the learner's diagram block: the browser draws a flowchart that code compiles from the writer's labelled nodes and edges (strict security level) |
| PostgreSQL | 16 | PostgreSQL licence | database (docker image `postgres:16`) |

Demo source text: `tests/fixtures/eurlex_excerpt.md`, an excerpt of Regulation (EU) 2024/1689 from EUR-Lex, reused with
acknowledgement under Commission Decision 2011/833/EU. `tests/fixtures/nist_ai_rmf_excerpt.md` excerpts a NIST publication
(US federal work, public domain). Only the Official Journal is authentic.
