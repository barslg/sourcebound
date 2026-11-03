# Sourcebound

Sourcebound builds courses in which every sentence is bound to a source, grades the learner's answers with code, and teaches people to catch the AI when it is wrong: a reader model holds any block that its source does not support, the tutor answers "not in the materials" instead of guessing, and the "find the AI error" exercise plants a false claim for the learner to find. The unit of output is a course (plan, modules, topics, typed blocks), not an answer. Built for the Meta Global AI Developer Hackathon 2026, Indie track, Build for Your World, solo.

Not a chatbot and not a basic RAG app: the author approves sources and edits the plan, a pipeline of Meta models writes the blocks inside structures that code owns, and code (not a model score) decides what a learner sees.

## What existed before / what was built this week

Nothing existed. This repository was created on 2026-10-31 for the event and every commit in it is event work. The idea comes from the author's earlier learning-management work; no code, text or data from it was reused. `git log` is the provenance record.

## Which Meta models do what

Source of truth: `STEPS` in `src/app/llm/registry.py` (model, reasoning effort and output limit are required per step and are part of the reply hash).

| Model | Steps (agent) | What it does |
|---|---|---|
| `muse-wonder-1.0` | `outline` (high) | writes the scenario of the whole course from the sources' sections: the learner, the one case the course builds, modules, and every topic as an annotated line (what it teaches, the terms it introduces, what it prepares, what it requires, which sections it covers), plus the questions only the author can settle |
| `muse-spark-1.3` | `architect` (high) | plans one module over the scenario: objectives, the misconception the checks target, block skeletons with source refs inside the topic's own sections, and for each example its input, action and result on the case |
| `muse-spark-1.3` | `researcher` (low) | ranks addressed library passages for a query, rephrases it per source language, names the gaps no passage answers |
| `muse-spark-1.3` | `web_search` (low, the `web_search` tool) | one grounded search per gap; code keeps the result list, so every web candidate URL comes from a search, never from the model |
| `muse-spark-1.3` | `block_teach`, `block_example` (medium) | writes the reading, then the worked example (Input, Action, Result on the case) from the teach text; each ends with a handoff the next topic's writer reads as "previously" |
| `muse-spark-1.3` | `block_exercise` (high) | writes a check, practice or module-test item from the taught text only; keys are quoted spans, one distractor is built from the topic's misconception |
| `muse-spark-1.3` | `reader` (high) | reads every block against its passages, the scenario and the handoffs on seven rubric items; every item's evidence must exist verbatim in the block or a passage, or the item is unknown and never a pass |
| `muse-spark-1.3` | `saboteur` (medium) | rewrites a verified claim into a planted error of a named kind (number and date swaps are done by code) |
| `muse-spark-1.3` | `translator` (low) | translates one block per call, code keeps markers, numbers and answer keys |
| `muse-spark-1.3` | `tutor` (low) | answers a learner question from retrieved passages only |
| `muse-voice-transcribe-1.0` | audio sources | transcript with timestamps, passages addressed `t00:01:12-00:01:40` (`src/app/clients/web.py`) |
| `muse-image-1.0` | priced in the registry | registered with a dated price; no step calls it (see Limits) |

How the API is used (verified in `src/app/clients/meta.py`): the Responses API through the `openai` SDK; the agent's system prompt travels as `instructions` and the typed task as `input`; every structured call asks for a strict `json_schema` format built from the agent's schema (`validate_strict_subset` rejects a schema the API would not accept); `prompt_cache_key` plus `prompt_cache_retention: 24h` keep the stable prefix (system prompt, the topic's passages) cached across a run that lasts hours; `reasoning.effort` is set per step from the registry; the researcher's gaps are answered with the `web_search` tool and `include: web_search_call.results`, read by code; learner-subject calls carry a hashed `safety_identifier`; a dropped connection or a 5xx never triggers a blind second POST, the call continues as a background response and is polled by its stored id.

## Architecture

```
routes -> controllers -> services -> providers -> clients
```

- `src/app/routes`, `controllers`: HTTP in and out, `/api/v1`, one permission code and one scope check per route (`scripts/check_routes.py`).
- `src/app/services`: use cases (courses, library, planruns, learner, grading, tutor, report, invites, users).
- `src/app/providers`: the LLM provider (reply store short-circuit, call ledger), retrieval (Postgres full-text search), web fetching.
- `src/app/clients`: the only modules that touch the network: `meta.py` (Model API), `replay.py` (recorded replies), `web.py`.
- `src/app/blocks`: nine block types (reading, diagram, choice, matching, cloze, ordering, scenario_practice, spot_the_error, and the legacy figure that no plan prescribes any more), each with a schema, a code check, a learner render and a fixture whose answer key must never reach a learner route. Readings are markdown rendered with HTML disabled; a diagram is drawn by the browser from labelled nodes and edges, every label a quoted run from a cited passage.
- `src/app/runs`, `pipelines`: a generation run is a stored DAG of steps `<agent>:<plan_node_id>`; the same input digest reuses the stored output, a changed digest reruns downstream, a stopped run resumes without paying for finished steps.
- `src/app/tasks`: Procrastinate worker on the same Postgres. Progress reaches the browser by SSE over Postgres LISTEN/NOTIFY (`services/events.py`).
- `src/app/llm`: step registry, prompt versions, the content-addressed reply store (`llm_replies`) and the call log (`llm_calls`, tokens and cost per call).

## Where a full RAG could stand

Today retrieval is Postgres full-text search with the language chosen per passage (English, Norwegian; Ukrainian falls back to `simple` plus `pg_trgm`), behind the `RetrievalProvider` protocol in `src/app/providers/retrieval.py`. Every ranking is written to `retrieval_pairs` from the first run. The planned next step is a retrieval reranker fine-tuned from those pairs on an open-weights Meta model (Muse Glimmer, or Muse Spark Open served by a third party), swapped in behind the same protocol; `RerankerStub` is the placeholder and keeps FTS order. We did not use a hosted vector database or third-party embeddings: the event rules allow Meta models only, and the owner's instruction was to keep retrieval on our own Postgres and open weights.

## Run it

Needs Docker, and Python 3.12 with `uv` for the scripts.

```bash
cp .env.example .env          # fill the names you need; see the table below
docker compose up -d --build  # postgres (host port 5434), migrate, api (8000), worker
```

- The agents' system prompts are tracked as `prompts/<agent>/system.md` (the prompt version in every call record is their hash); the stubs in `tests/fixtures/agent_prompts` serve the test suite only.
- First administrator: put the Google account's address in `SUPERADMIN_EMAILS`. Sign-in grants no rights by itself; that address, with a verified email, becomes superadmin on its first sign-in and its subject is pinned.
- Google OAuth client, five lines: (1) Google Cloud console, APIs and Services, Credentials, create an OAuth client of type Web application; (2) authorised redirect URI `http://localhost:8000/api/v1/auth/callback` (it must equal `OIDC_REDIRECT_URL`); (3) copy the client id to `GOOGLE_OAUTH_CLIENT_ID` and the secret to `GOOGLE_OAUTH_CLIENT_SECRET` in `.env`; (4) set `SESSION_SECRET_KEY` to a long random string; (5) `docker compose up -d --build` again.
- Each author runs courses on their own Meta key, added on the account page (`/account`): it is stored encrypted with `MODEL_KEY_SECRET` (set it to a long random string), never logged and never returned; planning, generation, translation and rewrites are billed to the person who starts them, the tutor to the course owner. `MODEL_API_KEY` in `.env` is the fallback only in `APP_ENV=local` or `test` (the mock model, the local stand); without any key the paid actions are disabled with the reason, and replay mode (below) needs none.
- Learners arrive by a link: the publish page makes a student link in one click (an invite scoped to the course); whoever opens it, signs in with Google and is enrolled as a student of that course.

## Deploy

The frontend is a static build; the API, the worker and Postgres run from the one Docker image on any host that has HTTPS in front of port 8000.

- A Cloudflare Worker (`frontend/wrangler.jsonc`) serves `frontend/dist` as static assets and proxies `/api/*` to the API through `frontend/worker/index.ts`, so the session cookie and the CSRF token stay on the site's own origin. Workers Builds settings: production branch `main`, root directory `frontend`, build command `npm run build`, deploy command `npx wrangler deploy` (non-production branches: `npx wrangler preview`), variable `API_ORIGIN=https://<api host>` under Settings, Variables and Secrets (`keep_vars` keeps it across deploys); then the custom domain. Without `API_ORIGIN` every `/api` call answers 503 with that sentence.
- The API host runs `docker compose up -d --build` with a production `.env`: `APP_ENV=prod` (compose passes it to the api container), `SESSION_SECRET_KEY`, `MODEL_KEY_SECRET`, `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET`, `OIDC_REDIRECT_URL=https://<site>/api/v1/auth/callback` (the same URI in the Google client), `SUPERADMIN_EMAILS`. Outside `local` the session cookie is `__Host-` prefixed and Secure, startup refuses a missing secret, and the server Meta key is never used: each author adds their own on `/account`.
- Postgres is bound to the host's loopback only (`127.0.0.1:5434`); the API container is the only thing a reverse proxy exposes.

## Other providers for testing

The product and the submission run on Meta models only. For tests and local development, while the Meta key is unusable, `MODEL_PROVIDER=anthropic` with `ANTHROPIC_API_KEY` in `.env` routes the same calls through `src/app/clients/anthropic.py` (Anthropic Messages API, strict JSON by structured output): the `muse-spark-1.3` tier is served by `claude-sonnet-5-5`, `muse-wonder-1.0` by `claude-opus-5-5`; image, search and voice have no equivalent. The call log, reply store and cost carry the real model id and its registry price; calls are synchronous (no background mode). The setting is refused outside `APP_ENV` local or test. Run the live acceptances on it with `MODEL_PROVIDER=anthropic make check-live`; their recorded ledgers must be exported from a Meta run, not from this provider.

## Replay the demo without a model key

`MODEL_REPLAY_LEDGER=<path>[,<path>]` swaps the network client for `src/app/clients/replay.py`: each request is looked up by the same content digest the reply store uses and answered from a recorded ledger (the JSON the live acceptances export: `tests/fixtures/t05_live_ledger.json`, and `tests/fixtures/t07_live_ledger.json` when present). It pays nothing, and a request the ledger never recorded stops the step with `ReplayMissError` and the sentence "no recorded reply for this request". Everything else is the live path: code checks, citations verified by code, the reply store, the call log (cost 0).

```bash
scripts/demo_replay.sh
```

It starts the compose database, recreates the database `muse_demo`, migrates it and builds "AI literacy at work: the EU AI Act" from the recorded runs through the product path (course, approved source, plan run, generation run). Then run the stack against it: `DATABASE_URL=postgresql+asyncpg://muse:muse@db:5432/muse_demo docker compose up -d api worker` (or set `DATABASE_URL` in `.env`), sign in with the `SUPERADMIN_EMAILS` account (superadmins see every course), open the course, and on the Fix tab accept or rewrite the block the reader held, then Publish.

Measured on 2026-11-01 against the committed ledger: course built with 13 blocks all written, 22 citations verified by code, 23 model calls served from the ledger, cost 0. One block (the planted-error exercise of the module test) is held by the reader, which is the point of the demo. The run ends `stopped`, not `done`: the recorded ledger predates the rework step that follows a hold, so that one request has no recorded reply. A re-recording with a working key closes it (see Limits).


## Gates

`make check` runs ruff, ruff format, mypy (strict), `pytest -m "not live"` on a dedicated `muse_test` database, and `make gates`, which runs eight mechanical gates (`scripts/run_gates.py`): `check_layers` (the import layers above, one owner per external system, `generate_json` only in the step executor), `check_public_tree` (no secrets, private paths or personal data in tracked files), `check_routes` (permission code and scope on every route), `check_callers` (a symbol only tests call is a failure), `check_scoped_sql` (statements keyed by a plan id alone are red), `check_contract` (the frontend's calls against the backend OpenAPI), `check_schema_enums` (closed vocabularies equal in code and schemas), `check_prompt_contract`. `make ci-local` runs the CI jobs on a fresh clone of HEAD. `make check-live` runs the live acceptances (needs a key and spends money).

## Licence and third parties

MIT, see `LICENSE`. Components, versions and licences: `docs/THIRD_PARTY.md`. Demo sources are public-domain or openly licensed (EUR-Lex excerpt with acknowledgement, NIST excerpt).

## Honest limits

- Live, on the author's stand between 2026-10-31 and 2026-11-03 (the call log `llm_calls` is the record): 1,109 Meta calls for about 76 USD across 7 plan runs, 4 generation runs (2 done, 2 stopped and resumable), 3 rewrites after reader holds and 1 translation run; every agent in the table above has run live except the tutor. The tutor and audio sources are proven on the mock path only (guarded transport, no network), as are: resume after a planted 500 pays nothing twice, a second identical run pays zero, access scope of every route, answer keys never in learner routes.
- `muse-image-1.0` is priced in the registry and not called by any step. There is no vector store and no fine-tuned reranker yet.
- The `figure` block type stays registered only to render courses that already hold one; no plan prescribes it any more, because a model-written figure invented its numbers in every case measured. The flashcard deck, the glossary page and the term marks show the same terms three ways; mermaid (670 kB, lazy) draws flows a hundred lines of SVG could; the scenario exercise's risk tiers are a frontend constant from the first demo brief. All four are listed for removal or replacement after the event.
