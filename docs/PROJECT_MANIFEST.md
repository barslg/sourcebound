# Muse project manifest

## What it is

**Grounded Course Studio** (working name; "Grounded Tutor" in the concept doc): an authoring system that turns verified sources into a
structured, checkable course, and teaches learners to catch AI when it is wrong. Entry for the Meta Global AI Developer Hackathon
(Cerebral Valley, 26 Oct – 4 Nov 2026), Indie track, theme Build for Your World. Owner: Oleksandr Lytvynov, solo.

Thesis in one sentence: the unit of output is a course (plan → modules → topics → typed blocks), the source of truth is the approved
sources (every claim has an address in a source), and when the model is wrong the system catches it (reader holds the block, tutor says
"not in the materials", learners train on "find the AI error"). Not a chatbot, not a RAG app: see `docs/HARNESS.md` §1.

## Stack

- Backend: Python 3.12, FastAPI, SQLAlchemy 2 async + asyncpg, Alembic, PostgreSQL 16 (+ full-text search; no vector embeddings —
  no non-Meta model may run at runtime), Procrastinate (jobs on the same Postgres), structlog. Packaging: pyproject + hatchling, uv; ruff, mypy strict, pytest.
- LLM: Meta Model API through the `openai` SDK (Responses API): system prompts as `instructions`, strict `json_schema` output,
  `prompt_cache_key` + 24h retention, `reasoning.effort` per step, the `web_search` tool for the researcher's gaps, background
  mode on 504. Models per step in one registry (`muse-spark-1.3` default, `muse-wonder-1.0` for the scenario; `*-contributor`
  only for dev replays on synthetic data).
- Frontend: Vue 3 + TypeScript + Vite + PrimeVue, by the admin-app shape in `docs/PRINCIPLES.md`.
- Auth: Google OIDC sign-in (provider-generic design; GitHub/Apple may follow), backend session cookie, CSRF on unsafe methods.
- Delivery: one Docker image, compose with postgres + one-shot migrate + api + worker, health-gated. The frontend on a Cloudflare Worker with static assets at `sourcebound.io`, the worker proxying `/api` to the API host (README, Deploy).

## How it runs and deploys

- Local: `docker compose up -d --build`; Postgres on host port 5434 (5432/5433 are taken on this machine).
- Branches: `main` (what Cloudflare Workers Builds deploys; the `deploy` workflow ships the API host from it after a green `ci`, secrets and host in the repository settings, README Deploy) and `dev`; work on feature branches merged into `dev` with clean rollbacks. Remote: GitHub `barslg/sourcebound`; CI on GitHub Actions, replayed locally by `make ci-local` before every push.
- Secrets only in `.env` (gitignored): `MODEL_API_KEY` (server fallback, local/test only), `MODEL_KEY_SECRET` (encrypts the per-user keys), `SESSION_SECRET_KEY`, Google OAuth client id/secret, `DATABASE_URL`. Per-user Meta keys live encrypted in `user_model_keys` (migration 0025). Map: `.claude/knowledge/credentials-map.md`.

## Decisions and why

- Hackathon rules drive the shape: every runtime model is a Meta model (so retrieval is code + Postgres FTS + Spark web_search);
  repo public, created ≥ 26 Oct, only event work; the demo video opens with "what existed before / what was built this week";
  a LICENSE file (MIT); README lists third-party libraries and their licences and names which Meta model does what.
- Not one line from the author's earlier learning-management system or from employer projects; their *shape* is reused as principles (`docs/PRINCIPLES.md`).
- Layer map with calls only downward: routes → controllers → services → providers (Protocol + adapter) → clients (the only external I/O);
  models/ the only SQL; schemas/ pure DTOs; config.py the only env reader; redact.py for logs and errors.
- RBAC: permission catalogue declared in code and synced to the DB at startup; a new permission is granted to no existing non-admin role;
  roles = permissions + CRUD per type + scope by tags. SSO sign-in creates an account with no rights; superadmins come from
  `SUPERADMIN_EMAILS`; invite links (UUID, optional seat count) on a course or course group make the person a student of those courses;
  users can be deleted (schema cascades) or banned (no re-login).
- Writes that take time are operations: 202 + operation id, job on Procrastinate, progress by SSE, audit, notification.
- Generation is a run of steps persisted from day one; a 4xx/5xx stops the run with a marker and it resumes from the last good step;
  a pre-generation estimate (blocks, calls, tokens, cost, time) is a forecast only, never a limit.
- Code owns structure; the model fills plain text inside strict JSON schemas (Meta strict subset). Answers are checked by code.
- Planning in two layers (owner 2026-11-02): the outline writes the scenario (learner, case, annotated topics with sections,
  questions for the author); the architect plans one module over it. Writers are chained by handoffs ("previously"), the example
  reads the teach text, exercises read both and target the topic's misconception; the reader's evidence is verified verbatim by
  code; no figure block is planned; the continuity agent is retired. Agents: outline, architect, researcher (+ web_search),
  block_teach, block_example, block_exercise, reader, saboteur, translator, tutor, librarian.
- Muse Code was the implementer until 2026-11-02 (owner: «Не используем Muse Code. Оно уже своё сделало»); since then Claude Code writes, verifies and commits all code; the Meta API is called only for course runs the owner asks for.
- Two kits, one truth: `AGENTS.md` + `.agents/` (generic agent rules and the Muse Code memory, kept for any tool) and `CLAUDE.md` + `.claude/` (lobby, floors by path, skills `stand`/`course-run`/`verify-slice`, knowledge incl. the session digests and the credentials map). Both stay out of git; `README.md`, `docs/` (minus the planning papers), `prompts/` and `CHANGELOG.md` are tracked since 2026-11-04. Kit in English; owner replies in Russian.
- Block types (2026-11-03): ordering and diagram added, multi-select choice, markdown readings, sources page, glossary, review deck; `figure` is never planned. Each author pays with their own Meta key (account page); learners come by a student link.

## Not built

Day-1 backend harness is landed and hardened (packaging, compose, `src/app` skeleton, migration 0001, LLM layer, live smoke, session-automatic transport guard; tests isolated on `muse_test`, ledger writes autonomous and committed, timeouts go to background, per-unit registry prices). T02 is landed: RBAC catalogue/principal/require/`ctx`, provider-generic OIDC with backend sessions, user ban/delete, invites, operations with Procrastinate plus SSE (migration 0002, routes under `/api/v1`). Landed in T03: `runs/`, `course/`, `agents/` packages, prompt files under `prompts/`, sources with FTS retrieval, and plan runs. T05/T05b landed: block type packages, methodology pipelines, and the real generation pipeline (submit_generate -> compiled DAG -> worker -> done, verified citations, reader holds, code-planted spot-the-error). Landed since: the Vue frontend (author and learner UIs with route registry, grammar, states, reachability, contract and permission gates), CI on GitHub Actions (`make ci-local` replays it), identity and design system, two-level planning, translation, per-user keys, nine block types. Still ahead: the demo course build and read-through (Python basics, 2026-11-03/04), the video, the submission, the API host behind `sourcebound.io`.

## Unconfirmed

- Final product name (judges must not read it as "AI for education chatbot").
- Which host serves the API image behind `sourcebound.io`: the deploy workflow is ready and skips itself until `DEPLOY_HOST` is set. The Google client already carries `https://sourcebound.io` and its callback (owner, 2026-11-04).
- What the 3-minute video must show beyond: source → plan edited by the author → generation with progress → typed blocks with
  source addresses → learner check graded by code → "find the AI error" → teacher report.
