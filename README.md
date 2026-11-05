# Sourcebound

Sourcebound turns one short brief into a whole course. The author states who the learners are, what they must be able to do and what the course must cover, then presses Build. A chain of agents on Meta models writes the course's through-line case, a lecture for every topic, one picture per topic and typed exercises cut from each lecture. Code checks everything a learner can see (code compiles, cloze blanks are short, options share one shape, matching has enough pairs, ordering keys follow the stated order); a corrector model fixes what code flags; answers are graded by code, never by a model. The tutor answers "not in the materials" instead of guessing. The unit of output is a course (plan, modules, topics, typed blocks), not an answer. Built for the Meta Global AI Developer Hackathon 2026, Indie track, Build for Your World, solo.

- **Live:** https://sourcebound.io (the example course "Python for a shop owner" is open to every signed-in user)
- **Demo video:** https://youtu.be/ugFaPW0U6zM
- **On X:** https://x.com/bars_lg/status/2118147277168787764
- **Hackathon:** https://cerebralvalley.ai/e/meta-ai-developer-hackathon

Every model call is listed on the run page and on the course's agent-chain page with its model, step, tokens, cost and latency.

## What existed before / what was built this week

Nothing existed. This repository was created on 2026-10-31 for the event and every commit in it is event work. The idea comes from the author's earlier learning-management work; no code, text or data from it was reused. `git log` is the provenance record. The first design (approved sources, a reader model judging every block) was built and measured in the first days; the audit on 2026-11-04 showed it trusted every stage and judged too late, and it was replaced by the lecture-first chain below the same evening.

## The demo path

1. Sign in with Google.
2. Add your Meta API key on the account page (stored encrypted, shown only as its last four characters).
3. Create a course and fill the brief once: levels, context, what must be covered, outcomes, languages (and, if you want one, a spend cap; empty means none).
4. Press Build. One press runs the blueprint, every lecture, the exercises and the translations; nothing more is asked.
5. Publish, copy the student link, open it as a learner, answer, and read the report.

## How a course is built

1. **Blueprint:** one Muse Wonder call writes the learner, the one case the course builds, modules and topics with annotations; a corrector pass checks it.
2. **Lectures:** every topic gets its own full lecture (Muse Wonder, all topics in parallel), each followed by one corrector pass.
3. **Block plan (code only):** the lecture's paragraphs become passages of a generated "Lectures" source; the lecture is the topic's reading; exercise types are chosen from the paragraphs (ordering for steps, matching for pairs, cloze for code, else cloze or choice alternating); each module test has one block per lesson.
4. **Pictures:** one simple picture per topic from Muse Image (one object, plain background, no text, no people), all topics at once, shown above the lecture.
5. **Exercises:** a writer produces each exercise from its own lecture; code repairs what it can (ordering steps follow the key, every cloze gap is `___`) and checks the rest (compile, blank length, option shape, matching pairs, ordering kind, language) as remarks; one corrector pass applies them; the block is accepted.
6. **Translation:** one run per extra language of the brief.

No reader, saboteur, architect or researcher, no re-asks, holds or rewrite waves. The lecture's facts are judged by the corrector model, not by code or by approved sources; this is the stated cost of the design. Typed input is ordered stable-first so the calls of one topic read the lecture from the prompt cache.

## Which Meta models do what

Source of truth: `STEPS` in `src/app/llm/registry.py` (model, reasoning effort and output limit per step; all three are part of the reply hash).

| Model | Steps (agent) | What it does |
|---|---|---|
| `muse-wonder-1.0` | `blueprint` (medium, no output limit) | the learner, the through-line case, modules, topics and their annotations, from the brief alone |
| `muse-wonder-1.0` | `lecture` (medium, no output limit) | one full lecture per topic, advancing the case |
| `muse-wonder-1.0` | `corrector` (medium, no output limit) | one pass over each blueprint, lecture and exercise draft against the code's remarks |
| `muse-wonder-1.0` | `block_exercise` (medium, no output limit) | writes the exercise of a block from its lecture; exercise types are chosen by code |
| `muse-spark-1.3` | `translator` (low) | translates one block per call; code keeps markers, numbers and answer keys |
| `muse-spark-1.3` | `tutor` (low) | answers a learner question from retrieved passages only |
| `muse-voice-transcribe-1.0` | audio sources | transcript with timestamps for uploaded audio (`src/app/clients/web.py`) |
| `muse-image-1.0` | `illustrator` | one simple WebP picture per topic, $0.01 each |

Cost, measured from the call ledger:

| Course | Topics | Blocks | Model calls | Cost |
|---|---|---|---|---|
| Python for a shop owner (live example, with pictures) | 10 lessons + 4 module tests | 40 | 103 | $3.38 |
| First pilot of the same brief (no pictures) | 13 lessons + 4 module tests | 52 | 107 | $4.96 |

Wonder at medium effort reasons about four times less than Spark did on the same heaviest call (1.9k against 8.3k reasoning tokens), so it costs less than twice as much per call while writing more; the prompt cache serves 27 to 62 % of the input once every call of a course shares one cache route.

How the Model API is used (`src/app/clients/meta.py`): the Responses API through the `openai` SDK; the agent's system prompt as `instructions`, the typed task as `input`; every structured call asks for a strict `json_schema` built from the agent's schema; `prompt_cache_key` with 24-hour retention keeps the stable prefix cached across a run that lasts hours; `reasoning.effort` per step from the registry; learner-subject calls carry a hashed `safety_identifier`; a dropped connection or a 5xx never triggers a blind second POST, the call continues as a background response and is polled by its stored id.

## Architecture

```
routes -> controllers -> services -> providers -> clients
```

- `src/app/routes`, `controllers`: HTTP in and out under `/api/v1`, one permission code and one scope check per route.
- `src/app/services`: use cases (courses, build, plan runs, learner, grading, tutor, report, invites, users).
- `src/app/providers`: the LLM provider (reply store, call ledger), retrieval (Postgres full-text search, per-passage language), web fetching.
- `src/app/clients`: the only modules that touch the network: `meta.py`, `replay.py` (recorded replies), `web.py`.
- `src/app/blocks`: block types (reading, diagram, choice, matching, cloze, ordering, scenario_practice, spot_the_error, legacy figure), each with a schema, a code check, a learner render and an answer key that never reaches a learner route. Readings are markdown with HTML disabled; a diagram is drawn by the browser from labelled nodes, every label a quoted run from a cited passage.
- `src/app/pipelines/lecture_first.py`, `src/app/runs`: a plan run and a generation run are stored DAGs of steps (`blueprint:course`, `lectures:course`, `blockplan:course`, one exercise step per block); the same input digest reuses the stored output, a changed digest reruns downstream, a stopped run resumes without paying for finished steps.
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
- Without any key: `scripts/demo_replay.sh` builds a demo course from the recorded ledger in `tests/fixtures/` at cost 0; the ledger was recorded with the earlier chain, so it shows the product's screens, not the lecture-first calls.

## Deploy

One host runs the stack from `docker-compose.yml` with every port on the loopback; the host's own nginx terminates TLS and proxies to the `web` port. `.github/workflows/deploy.yml` runs after every green `ci` on `main` once its repository variables are set (names at the top of that file): it builds the frontend, writes the production `.env` from the repository secrets, ships the tree over SSH, restarts compose and fails unless `/api/v1/health` answers through the site. Until those are set the live site is deployed by hand with the same steps, after a database backup. Every published release builds both images and publishes them as packages of this repository: `ghcr.io/barslg/sourcebound-api` and `ghcr.io/barslg/sourcebound-web` (`.github/workflows/release.yml`).

## Checks

`make check`: ruff, mypy strict, pytest on a dedicated database, and nine mechanical gates (`scripts/run_gates.py`): import layers, public tree without secrets, permission and scope on every route, no symbol only tests call, scoped SQL, frontend calls against the OpenAPI, closed vocabularies equal in code and schemas, prompt contract, deploy env. `make ci-local` replays the CI jobs on a fresh clone. `make check-live` runs the live acceptances (spends money).

## Licence and third parties

MIT, see `LICENSE`. Components, versions and licences: `docs/THIRD_PARTY.md`. Demo sources are public-domain or openly licensed (EUR-Lex excerpt with acknowledgement, NIST excerpt).

## Limits

- The lecture-first chain was written and checked on 2026-11-04. Before that, the first design ran live between 2026-10-31 and 2026-11-03 on the author's stand (1,109 Meta calls for about 76 USD); the tutor is proven on the mock path only, as are audio sources. Its code stays in the tree to replay stored courses and is not offered in the UI.
- A lecture's facts are judged by a model, not by sources. A subject newer than the model (a product released after its training) comes out wrong: the model substitutes a product it knows. A field of exact facts in the brief, and web search for lectures, come after the event.
- Exercise writers run one step at a time in the run driver; lectures are parallel.
- Retrieval for the tutor is Postgres full-text search behind a `RetrievalProvider` protocol. No vector store, no fine-tuned reranker.
- The `figure` block type stays registered only to render old courses and is never planned. Mermaid is heavy for the flows it draws; the scenario exercise's risk tiers are a frontend constant.
