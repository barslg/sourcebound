# Sourcebound

Sourcebound generates whole courses with a team of Meta models and checks them with code. Muse Wonder writes the scenario of the course, Muse Spark plans every module and writes every block: the models explain in their own words, and every specific (a rule, a number, a name, a code sample and its output) must come from a source the author approved: code checks every quote and every answer key verbatim against the passages, and a reader model holds a block whose specifics the sources do not support. Answers are graded by code, and the course teaches people to catch the AI when it is wrong: the tutor answers "not in the materials" instead of guessing, and the "find the AI error" exercise plants a false claim for the learner to find. The unit of output is a course (plan, modules, topics, typed blocks), not an answer. Built for the Meta Global AI Developer Hackathon 2026, Indie track, Build for Your World, solo. Live at https://sourcebound.io.

Generation, not search: the course is written by the models, not retrieved from the web. The author approves sources and edits the plan, a pipeline of Meta models writes the blocks inside structures that code owns, and code (not a model score) decides what a learner sees. Every model call is listed on the run page with its model, step, tokens, cost and latency.

## What existed before / what was built this week

Nothing existed. This repository was created on 2026-10-31 for the event and every commit in it is event work. The idea comes from the author's earlier learning-management work; no code, text or data from it was reused. `git log` is the provenance record.

## Which Meta models do what

Source of truth: `STEPS` in `src/app/llm/registry.py` (model, reasoning effort and output limit per step; all three are part of the reply hash).

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

How the Model API is used (`src/app/clients/meta.py`): the Responses API through the `openai` SDK; the agent's system prompt as `instructions`, the typed task as `input`; every structured call asks for a strict `json_schema` built from the agent's schema; `prompt_cache_key` with 24-hour retention keeps the stable prefix cached across a run that lasts hours; `reasoning.effort` per step from the registry; the researcher's gaps go to the `web_search` tool and code reads `web_search_call.results`; learner-subject calls carry a hashed `safety_identifier`; a dropped connection or a 5xx never triggers a blind second POST, the call continues as a background response and is polled by its stored id.

## Architecture

```
routes -> controllers -> services -> providers -> clients
```

- `src/app/routes`, `controllers`: HTTP in and out under `/api/v1`, one permission code and one scope check per route.
- `src/app/services`: use cases (courses, library, plan runs, learner, grading, tutor, report, invites, users).
- `src/app/providers`: the LLM provider (reply store, call ledger), retrieval (Postgres full-text search, per-passage language), web fetching.
- `src/app/clients`: the only modules that touch the network: `meta.py`, `replay.py` (recorded replies), `web.py`.
- `src/app/blocks`: nine block types (reading, diagram, choice, matching, cloze, ordering, scenario_practice, spot_the_error, legacy figure), each with a schema, a code check, a learner render and an answer key that never reaches a learner route. Readings are markdown with HTML disabled; a diagram is drawn by the browser from labelled nodes, every label a quoted run from a cited passage.
- `src/app/runs`, `pipelines`: a generation run is a stored DAG of steps; the same input digest reuses the stored output, a changed digest reruns downstream, a stopped run resumes without paying for finished steps.
- `src/app/tasks`: Procrastinate worker on the same Postgres; progress reaches the browser by SSE over LISTEN/NOTIFY.
- `src/app/llm`: step registry, prompt versions, the content-addressed reply store and the call log (tokens and cost per call).
- `frontend/`: Vue 3, author and learner views, one API wrapper, no tokens in the browser.

## Run it

Needs Docker, Node 22 and Python 3.12 with `uv`.

```bash
cp .env.example .env               # fill the names you need
(cd frontend && npm ci && npm run build)
docker compose up -d --build       # postgres, migrate, api, worker, web on http://localhost:10600
```

- Google sign-in: an OAuth client of type Web application with redirect URI `http://localhost:10600/api/v1/auth/callback` (equal to `OIDC_REDIRECT_URL`); its id and secret in `.env`, plus `SESSION_SECRET_KEY` and `MODEL_KEY_SECRET` as long random strings.
- First administrator: the Google address in `SUPERADMIN_EMAILS` becomes superadmin on its first sign-in; every other sign-in gets the learner role only, which opens the courses their author marked public.
- Each author runs courses on their own Meta key, added on `/account` and stored encrypted; planning, generation, translation and rewrites are billed to whoever starts them, the tutor to the course owner. `MODEL_API_KEY` in `.env` is a fallback for local and test only.
- Learners arrive by a link or by a public course: the publish page makes a student link in one click (whoever opens it signs in with Google and is enrolled), or marks the course public so every signed-in user can take it. The tutor bills the course owner, at most 20 questions per learner and 300 per site a day.
- Without any key: `scripts/demo_replay.sh` builds the demo course "AI literacy at work: the EU AI Act" from the recorded ledger in `tests/fixtures/` through the real product path, at cost 0.

## Deploy

One host runs the stack from `docker-compose.yml` with every port on the loopback; the host's own nginx terminates TLS and proxies to the `web` port. `.github/workflows/deploy.yml` runs after every green `ci` on `main`: it builds the frontend, writes the production `.env` from the repository secrets, ships the tree over SSH, restarts compose and fails unless `/api/v1/health` answers through the site. Names and variables are listed at the top of that file.

## Checks

`make check`: ruff, mypy strict, pytest on a dedicated database, and nine mechanical gates (`scripts/run_gates.py`): import layers, public tree without secrets, permission and scope on every route, no symbol only tests call, scoped SQL, frontend calls against the OpenAPI, closed vocabularies equal in code and schemas, prompt contract, deploy env. `make ci-local` replays the CI jobs on a fresh clone. `make check-live` runs the live acceptances (spends money).

## Licence and third parties

MIT, see `LICENSE`. Components, versions and licences: `docs/THIRD_PARTY.md`. Demo sources are public-domain or openly licensed (EUR-Lex excerpt with acknowledgement, NIST excerpt).

## Limits

- Live between 2026-10-31 and 2026-11-03 on the author's stand: 1,109 Meta calls for about 76 USD across 7 plan runs, 4 generation runs, 3 rewrites after reader holds and 1 translation run; every agent in the table has run live except the tutor, which is proven on the mock path only, as are audio sources.
- Retrieval is Postgres full-text search behind a `RetrievalProvider` protocol; every ranking is recorded in `retrieval_pairs` for a future reranker on an open-weights Meta model. No vector store, no fine-tuned reranker, no image generation yet.
- The `figure` block type stays registered only to render old courses: a model-written figure invented its numbers in every case measured. The glossary, the term marks and the review deck show the same terms three ways; mermaid is heavy for the flows it draws; the scenario exercise's risk tiers are a frontend constant. Listed for removal or replacement after the event.
