# Sourcebound project manifest

Rewritten 2026-11-04 (evening) for the lecture-first product. The earlier manifest is kept locally in the local kit's `knowledge/manifest-2026-11-04-before-lecture-first.md`.

## What it is

**Sourcebound** generates a whole course from one short brief and teaches people to catch the AI when it is wrong. The author fills a brief once (levels, context, what must be covered, outcomes, languages) and presses Build. Meta models write a through-line case and a topic outline, one full lecture per topic, and typed exercises cut from each lecture; code checks what a learner can see (code compiles, cloze blanks are short, options share one shape, matching has enough pairs, steps are one kind), a corrector model fixes what code flags, and answers are graded by code, never by a model. Learners come by a student link; the author reads a report. Entry for the Meta Global AI Developer Hackathon 2026 (Cerebral Valley, Indie track, theme Build for Your World), solo, owner Oleksandr Lytvynov. Live at https://sourcebound.io.

## How it runs

Author path: sign in with Google -> add a Meta key on `/account` -> create a course -> fill the brief -> Build -> publish -> student link -> learner answers -> report.

Build is one operation (`services/build.py`) that derives its next stage from the database:

1. Plan run: `blueprint:course` (one Wonder call + one corrector pass), `lectures:course` (every topic in parallel, each one Wonder call + one corrector pass), `blockplan:course` (code only: paragraphs become the passages of a generated "Lectures" source, the lecture is the topic's teach reading, exercise types are chosen from the paragraphs, a module test has one block per lesson).
2. Generation run: exercise writers only, each fed its own lecture; code remarks, one corrector pass, block accepted.
3. Translation: one run per extra language in the brief.

No Reader, saboteur, architect or researcher; no re-asks, holds or rewrite waves. The spend cap is optional (empty = none). Details and file map: the local kit's `rules/pipeline.md` (local) and the README.

## Stack

- Backend: Python 3.12, FastAPI, SQLAlchemy 2 async + asyncpg, Alembic, PostgreSQL 16, Procrastinate (jobs on the same Postgres), structlog; uv, ruff, mypy strict, pytest. Layers call only downward: routes -> controllers -> services -> providers -> clients (the only external I/O).
- LLM: Meta Model API through the `openai` SDK (Responses API): system prompt as `instructions`, strict `json_schema` output, `prompt_cache_key` with 24 h retention, `reasoning.effort` per step. Model ids, effort, output limits and prices live only in `src/app/llm/registry.py`. Blueprint, lecture and corrector are on Muse Wonder at effort medium with no output limit; the tutor and the translator are on Muse Spark.
- Frontend: Vue 3 + TypeScript + Vite + PrimeVue.
- Auth: Google OIDC, backend session cookie, CSRF on unsafe methods; RBAC catalogue in code; invite links and public courses for learners.
- Delivery: one Docker image; compose with postgres, one-shot migrate, api, worker, web (nginx). One host behind `sourcebound.io`, deployed by GitHub Actions from `main` after a green `ci`; skips itself until `DEPLOY_HOST` is set.
- Secrets only in `.env` (gitignored): names `MODEL_API_KEY` (local fallback), `MODEL_KEY_SECRET`, `SESSION_SECRET_KEY`, Google OAuth id and secret, `DATABASE_URL`. Per-user Meta keys are encrypted rows in `user_model_keys`.

## Decisions and why

- **Hackathon rules (2026-10-26):** every runtime model is a Meta model; repo public and created on or after 2026-10-26; MIT licence; README lists third-party libraries and says which Meta model does what; the demo video opens with what existed before and what was built this week.
- **Code owns structure, the model fills plain text inside strict JSON schemas (2026-10-31).** Ids, order, block types, grading and answer keys are code's.
- **Layered backend and permission-gated routes (2026-10-31).** Every route carries a permission code and a scope check; gates enforce it.
- **Each author pays with their own Meta key (2026-11-03).** Learners cost nothing; the tutor bills the course owner with per-learner and per-site daily limits.
- **Runs are persisted steps (2026-10-31).** A stop keeps finished steps; a resume reuses stored outputs by input digest and never pays twice.
- **Muse Code retired (2026-11-02).** Code is written and verified directly; the Meta API is called only for course runs the owner asks for.
- **Lecture-first replaces the source-grounded chain (2026-11-04, evening).** Measured on the Python course built by the old chain: the case story was invented (0 matches in 473 passages), 5 of 6 invented numbers came from the architect's plan, and the Reader ($5.87) passed 49 bad blocks. Every agent trusted the one before it and a model judged at the end. The new chain has one writer per stage and one corrector pass, and the lecture is the teaching text itself.
- **Wonder at medium effort, no output limit (2026-11-04).** Measured: Wonder medium reasons about 4x shorter than Spark (1.9k vs 8.3k reasoning tokens on the heaviest call); a Wonder call costs about $0.12 with cache; forecast for a Python course about $36 against about $90 if the old chain ran on Wonder. Typed input is ordered stable-first so one topic's calls read the lecture from the prompt cache.
- **Stated cost of lecture-first:** the lecture's facts are judged by the corrector model, not by code or by approved sources. "Every claim has an address in a source" holds only for the exercises' keys, through the generated Lectures source. Sources become an optional hint after the deadline.
- **Brief once, no questions (2026-11-04):** the autopilot never asks the author anything after Build; the cap is optional.
- **English only in the repo;** the product has en, nb and uk locales.

## Not built

- Deleting the retired chain's code (architect, reader, saboteur, researcher, rewrite waves); it stays for stored courses and is unreachable from the UI.
- Sources as an optional hint to the blueprint; web search for lectures.
- Parallel exercise writing in the run driver (lectures are parallel; exercises walk the DAG one step at a time).
- Vector retrieval (Postgres full-text only), image generation, fine-tuned reranker.
- A measured end-to-end cost of a full lecture-first course (the figure above is a forecast).

## History

The product began as a source-grounded course studio: approved sources, an outline and an architect planning every module, researcher and web search, block writers, a reader model that held unsupported blocks, a saboteur planting errors, rewrite waves. It built a 151-block course for about $29 and a Python course audited on 2026-11-04; the audit found the failures above and the chain was replaced the same evening. Its design papers are in the local kit's `knowledge/`.
