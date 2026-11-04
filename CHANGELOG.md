# Changelog

Format — [Keep a Changelog](https://keepachangelog.com/). Versioning — [SemVer](https://semver.org/).

## [Unreleased]

### Added

- Public-repository hygiene: `SECURITY.md` (private vulnerability reporting),
  `.github/CODEOWNERS`, weekly Dependabot updates for uv, npm and GitHub
  Actions, and a read-only workflow token in CI. (2026-11-04)

- Deploy from `main`: `.github/workflows/deploy.yml` runs after a green `ci`.
  One host runs the whole site from `docker-compose.yml`, which
  gains `web` (nginx with the static build of `frontend/`, built on the deploy
  runner and shipped as `dist/`, `/api/*` proxied to
  `api`, SSE unbuffered) on a loopback `WEB_PORT`, binds `api` to the loopback
  and keeps uploaded sources in the named volume `sources`; the deploy
  workflow writes the production `.env` (Google OAuth client, session and
  key secrets, superadmins, `OIDC_REDIRECT_URL` under `sourcebound.io`) from
  the repository secrets, ships the tracked tree over SSH, runs compose and
  checks `/api/v1/health` on the host and through the site; it skips itself
  until `DEPLOY_HOST` is set. The gate `check_deploy_env` keeps the names it
  writes equal to the settings catalogue. The Cloudflare Worker path of the
  same night is withdrawn: the domain's A record points at the host.
  (2026-11-04)

### Changed

- Lecture-first build replaces the source-grounded chain as the only path the UI offers. The author fills a brief once (levels, context, must cover, outcomes, languages, optional spend cap) and presses Build: `blueprint:course` (one Muse Wonder call and one corrector pass), `lectures:course` (every topic in parallel, each a Wonder call and a corrector pass), `blockplan:course` (code only: the paragraphs become passages of a generated "Lectures" source, the lecture is the topic's reading, exercise types are chosen from the paragraphs), then exercise writers fed from their lecture with code remarks and one corrector pass, then a translation run per extra language. New agents `blueprint`, `lecture`, `corrector` on Wonder at medium effort with no output limit (`UNBOUNDED`); input ordered stable-first for prompt caching; no Reader, saboteur, architect or researcher, no re-asks, holds or rewrite waves; the cap is optional. The retired chain's code stays for stored courses. Measured: Wonder medium reasons about four times less than Spark (1.9k against 8.3k tokens on the heaviest call); a Wonder call is about $0.12 with cache. README, manifest, video script, the pipeline floor, the lobby, the skills and the finish list were rewritten for it. (2026-11-04)

- `README.md`, `docs/` (the manifest, principles and third-party table),
  `prompts/` and this changelog are tracked; the planning papers and the
  agent kits stay local. CI no longer stubs the prompts from the fixtures.
  The README's limits section states what ran live on the stand through
  2026-11-03. (2026-11-04)

- Block types `ordering` (the steps of a procedure, served shuffled with
  opaque ids, graded by position with partial credit; every step a
  quoted run from a taught passage) and `diagram` (an example block whose
  nodes and edges the writer names and the browser draws with mermaid at
  the strict security level; a label not quoted from a cited passage holds
  the block). A choice with several correct options is answered with
  checkboxes. (2026-11-03)
- The learner's reading renders the writers' markdown (markdown-it, HTML
  disabled): paragraphs, bold terms, inline and fenced code, lists, tables.
  Passage markers become numbered footnote buttons (the source's number in
  first-cited order); the first mention of each topic term opens its
  definition in the companion panel. A numbered Sources page and a
  code-built Glossary page per course, for the author's preview and the
  enrolled learner; a review deck of the topic's terms at the end of a
  lesson. (2026-11-03)
- Per-user Meta API keys: an account page (`/account`) where a signed-in
  person adds, replaces or removes their key; stored encrypted with
  `MODEL_KEY_SECRET` (migration 0025 `user_model_keys`), never logged or
  returned; every model-calling operation runs on the actor's key, the
  tutor on the course owner's; without a key the paid actions are
  disabled with the reason. Local and test environments keep the server
  key as a fallback for the mock model. (2026-11-03)
- A one-click student link on the publish page (an invite scoped to the
  course), beside the Invites page. (2026-11-03)
- A url source keeps its address in `storage_path`; empty rows are
  repaired on re-crawl. (2026-11-03)

### Fixed

- Planning no longer stops on model slips code can repair: the outline's
  module and topic ids are renumbered by code and a module the reply
  restated verbatim is dropped; a lesson that restates its sibling
  lessons or the module test loses the copies; a role follows its topic
  kind; a module plan the checks still reject is asked again with the
  failed lines as a fix list (two re-asks) before the run stops; the stop
  text no longer calls a rejected outline an empty plan. A plan whose
  lesson exercises are all one type, whose module test is shorter than
  its lessons, or whose test repeats a type while another is unused is
  rejected. (2026-11-03)

### Changed

- Course generation rebuilt around a scenario: the outline (Wonder)
  writes the learner, the one case the course builds and every topic as
  an annotated line with its terms, what it prepares, what it requires
  and its sections (every section in exactly one topic or declared
  uncovered), plus the questions only the author can settle, which
  block generation until answered; the architect (Spark) then plans one
  module at a time over that list, keeping the scenario's topics, with
  a misconception per topic and an input/action/result spec per example.
  Writers are chained: a topic's teach block reads the handoffs the
  earlier topics' teach blocks wrote ("previously"), the example reads
  its topic's teach text, exercises read both; every generator returns a
  `handoff` and declares assigned passages it left out. One
  `block_exercise` agent replaces check/practice/test; the continuity
  agent is gone (the reader judges continuity against the handoffs);
  figures are no longer planned. The reader's evidence must exist
  verbatim in the block or a passage, or the item is unknown. The
  researcher names gaps and the executor answers each with a real
  `web_search` call, so no candidate URL is model-invented. System
  prompts travel as Responses `instructions` with 24h cache retention.
  Migration 0024 adds `topics.scenario`.

- T04N Plan page split into two pages with one job each: Plan
  (`/courses/:id/plan`) is the outline as a document — inline-editable
  module/topic titles, topics opening inline to their blocks, row More
  menus (rename, move, add topic below, delete confirmed by name), every
  edit autosaved through `PATCH /plan` with a save-status line and the
  409 conflict dialog, open questions in one card only while any are
  open, and a next-step card to the new Generate page
  (`/courses/:id/generate`), which owns the estimate line, the Generate
  / Generate-again button, and the current or last generation run with
  Resume / Cancel and the done links to What to fix and Publish.

### Fixed

- T04O/1 empty topics never reach generation: `/plan/validate` already
  reported them and nobody read it — the Plan page now marks the topic
  ("This topic has no blocks yet. Write a brief or delete it."), the
  next-step card and Generate stay disabled with the reason while the
  tree is invalid, and `/plan/submit` and `/generate/submit` refuse
  with 422; a resumed run rebuilds its pending steps against the
  current plan, dropping deleted topics/blocks as `skipped` with zero
  new model calls.
- T04O/2 one active run per course, server-side: every `submit_*` in
  `planruns.py` (plan, generate, translate) answers 409 "A run is
  already going" while a run of that course is queued/running or its
  submit operation has not reached the worker yet (the click-reload-click
  race); the Generate page disables Generate with that reason while the
  server reports an active run, so the regenerate confirm can never open
  mid-run. The no-double-pay plan test now submits sequentially, the way
  the worker leaves operations done.
- T04O/3 rewrite runs carry `kind="rewrite"`: the rewrite path in
  `planruns.py` wrote `kind="generation"`, so a 2-step rewrite showed as
  the course's last generation run; the sibling query over `new_run`
  found four writers (plan, rewrite, full generation, translation) and
  only the rewrite one was wrong. The Generate page's "latest" takes
  only kind generation, "Everything is written" is computed from blocks
  (no drafts left), and the Runs table labels the rewrite kind.
- T04O/4 keep-mine takes the server's version: `PATCH /plan` answers
  the new `row_version` with the tree and the client stores exactly
  that, never `version += 1`; keep-mine reloads-theirs first and sends
  `base_version` = the server's current version, so the author's next
  own edit saves without 409.
- T04O/5 a stopped plan build has a page state: the Plan page shows the
  stop reason with "Build again" (the shared build form, also reused by
  the empty state) next to the open-questions card; Needs now says "The
  plan build stopped: <reason>"; the Generate page refuses without an
  approved source — disabled with "Approve at least one source first"
  and a link, before any confirm. The queued-state unit gates follow the
  shared form component.
- T04O/6 resume waits for answers: both Resume buttons (Generate page,
  run view) stay disabled with "Answer the open questions first" while
  a required question is open, and the API refuses resume with 409
  carrying the reason. The run-view spec mocks an answered validation
  so it keeps testing the stopped run, not the questions.
- T04O/7 low batch: the Generate poll refreshes the course header when
  the run status changes; the plan build keeps the course's audience and
  the facts line hides "For:" when empty; plan source chips show title +
  handle and link to `/sources#passage-<handle>`, which the Sources page
  anchors per cited handle; the row-menu key reads "Rename"; "Next: …"
  was already an i18n key everywhere; the forecast note renders once;
  unknown stop markers map to "Stopped at block <topic> / <ordinal>";
  the duplicate `open-run` testid is unique (the rest are exclusive
  branches); orphan keys removed in all locales (`plan.saved`,
  `plan.conflict`, `plan.treeToggle`, `plan.audience*`, `plan.bloom*`,
  `plan.noBlocks`, top-level `bloom`).
- T04O/8 e2e: the flow stops its generation run mid-flight, resumes it
  from the page, and asserts the run section (named steps, done card
  with fix/publish links); the 1024 ergonomics check pins the build
  card on the seeded empty plan; the axe sweep covers `/generate`.
- T04N follow-up from the e2e run: the open-questions card is titled
  "Before the course can be written" as specified, the topic disclosure
  buttons meet the 32 px target size, and the plan specs assert the
  in-place titles through their input values.
- T04M visual identity verification: grouped form fields (translate
  languages, plan methodology) now render as 14 px-radius cards on the
  paper ground instead of the browser's square fieldset frame; the
  public-tree gate no longer trips on the task registry.
- T06b learner and teacher API review: feedback never carries a key
  value (only the habit line; find-the-error shows the true sentence of
  the exercise just answered); matching serves ids on both columns and
  grades `{left_id: right_id}` by displayed ids (`mapping_by_index` from
  a learner answers 422); a run's no-draft gate counts only the blocks of
  its own DAG; an author's accept is pinned to the block version
  (migration 0019) so a replayed reader hold cannot re-hold it; a spot
  exercise that cannot be planted is held with a reason; invites need
  ownership of every named course, `GET /invites` and revoke are scoped
  to the caller's courses, group tags are stored per owner
  (`<owner id>:<tag>`); the tutor drops fragments under 6 words / 40
  characters or off clause punctuation and unknown handles (no 400 after
  a paid call), threshold 0.025 with a passage-free hint when the
  materials do not answer; the language a learner was served is stored
  per block (migration 0019) and grades a POST without `lang`; chips of
  a withdrawn source binding are not rendered; a rewrite marks the course
  `generated`; a double-negation translation no longer passes the
  planted-negation check.

- T05c generation correctness, measured from the database: every
  block read/write is scoped by course (two courses sharing plan ids
  b1..b4 no longer cross-write verdicts or citations); prompts see a
  frozen plan skeleton (ids, titles, roles, briefs, handles — never
  status, holds, or versions), so a second identical submit_generate
  makes 0 paid calls; one seeded picker chooses the spot-the-error
  sentence + kind from content passages only (reuse notices are
  flagged non-content at ingest, migration 0015), the saboteur
  receives exactly that pick, and the key is built from it; the
  reader judges the full stored block (learner content + answer key
  + planted index) and re-reads reworked blocks before the run can
  end (no block ends `draft`); rework failures mark the step failed
  and stop the run resumable instead of stranding it; rewrite_block /
  rewrite_held_batch regenerate only the named blocks with
  variant = version count and the hold reason in the prompt; stored
  content is per block type, validated against its type schema
  before persisting (invalid replies are held, never collapsed to
  {text, body}); no UUID in any prompt or learner render (public
  handles only); spot grading credits kind/passage/fix only behind
  the right sentence (whitespace/case normalised, fully_correct
  seeded); number/date mutations protect every reference shape
  (Article 5(1), Articles 4, 5, 2024/1689, paragraph/avsnitt/a Ukrainian clause word);
  submit_plan/submit_generate refuse in code (422, plain sentence)
  with 0 approved passages or an empty plan, before any model call;
  a reply rejected by the output checks is never stored or replayed
  (the architect contract runs inside the provider check, and the
  replay path evicts + re-asks); stop markers are plain sentences
  (exception class and schema path stay in run_steps); every closed
  vocabulary (block types, roles, mutation kinds) is an enum in the
  emitting agent's schema, derived from the registries and checked
  at startup for all agents, nested; malformed id paths answer 404
  through one UUID dependency on every id-taking route; language
  ties detect as unknown. Deleted expand_merrill and teacher_report
  (no production callers; teacher report returns with the T06
  attempts surface).


- T07b translation is real through the product: POST
  /courses/{id}/translate submits a `submit_translate` operation, the
  worker drives a translation run (handlers also registered for resume)
  and finalises per-block ok/held rows; learner reads go through
  resolve_learner_block with a localised fallback notice (none in the
  source language); errors render via error_message in the request
  language, citation chips use format_citation_address, the sign-in
  surface carries signin_privacy_line, and PATCH /sources/{id}/language
  stores the passage language override. Translator output is
  language-detected per block (echoed source is held as "reply not in
  target language" and never enters translation memory); the planted
  error is re-derived by kind on the translated planted/true pair or
  held; numbers compare by normalised value as a multiset and glossary
  terms by word boundary, so nb formats no longer false-hold; a bad
  reply holds one block, held blocks re-ask with variant+1, and
  translation memory is course-scoped and written only for ok blocks.
  Translated exercises grade like the source: answer keys are rebuilt
  by segment id / item index and stored per translation block
  (migration 0014, with the course_translations.run_id foreign key and
  runs.kind actually stored; 0013/0014 have migration tests). The
  translator prompt no longer hard-codes course facts, reading `text`
  is translated, [P2] markers are protected explicitly, and
  detect_language is decided by function words, not one æøå/Cyrillic
  word. Live acceptance over the T05b demo course lives in
  tests/test_t07_live.py; probe/mutant tests in tests/test_t07b_*.py.
- T05b generation pipeline is real end to end: `submit_generate` compiles
  the generation DAG from the stored plan, the worker drives it with the
  generation handlers (resume included), generator steps load upstream
  block texts into prompt and digest, citations are verified verbatim
  against stored passages and language-checked before acceptance, reader
  holds land on blocks with reasons, rework regenerates only held blocks,
  spot-the-error is code-planted with the answer key only in
  `block_versions.answer_key`, grading requires the exact correct value,
  and the reader schema is a fixed six-item rubric object replayed on the
  logged live call (`tests/fixtures/t05_reader_live.json`); parser_spec
  is derived and stored on first URL intake and reused on re-crawls; the
  LLM ledger and reply store strip NUL bytes and stop markers carry only
  step + exception class; the compiler inserts a saboteur step before
  spot_the_error generation (non-numeric kinds require its rewrite).

### Added

- T07 full multilinguality (tests first, red-to-green in
  tests/test_t07_translation.py, tests/test_t07_variants.py, live smoke in
  tests/test_t07_live.py): segment pipeline in src/app/translation/
  (code-extracted stable-id segments per block type; structure, markers,
  numbers, citations, and answer keys never reach the model); translator
  agent (Spark low, prompts/translator/system.md, TRANSLATOR_SCHEMA,
  per-block executor steps so the reply store skips unchanged blocks);
  glossary-first term-table pass with per-segment use checks; code
  re-assembly validated against each block schema (placeholders kept,
  list/option counts equal, numbers unchanged); spot_the_error post-check
  (exactly one planted sentence of the same kind, code re-derived,
  otherwise held with reason; planted rows excluded from translation
  memory); course_translations per (version, language) with per-block
  ok/held/failed plus learner resolve with source-language fallback and a
  visible notice (migration 0013); backend i18n (en/nb/uk error messages,
  one citation-address formatter, trilingual sign-in privacy line);
  passage language detection by script plus en/nb/uk stopword lists with
  a stored per-passage override feeding the FTS config; live demo topics
  en -> nb and en -> uk with parity, glossary-use, invariant, and
  second-run-zero assertions.
- T05 generation pipeline for one methodology end to end (tests first,
  red-to-green in tests/test_t05_pipeline.py, tests/test_t05_resume.py,
  live smoke in tests/test_t05_live.py): Merrill prescription in code with
  Bloom/data branching plus Backward Design and Kolb registered for the
  compiler; seven block packages under src/app/blocks/<type>/ (strict
  schema, code grading, learner render contract, sentinel fixture; every
  fixture through every learner serializer leaks nothing); agents with
  prompts/<agent>/system.md and replay tests (block generators per role on
  Spark medium/high, Wonder-high reader with one call per topic and
  six-item quoted verdicts, Wonder-medium saboteur for non-numeric kinds
  only, continuity reader per module; system + rubric, approved source,
  frozen plan + dossier, skeleton prefix order with
  agent:block_type:course_id cache keys); Merrill compiler plus executor
  handlers (wave A teach/example, wave B reading wave A text, reader per
  topic, continuity, rework of named blocks only; claim+commit, one
  driver lock, stop marker; resume and second identical runs pay nothing
  twice; generate_json only in the executor); find-the-error planter with
  reader-holds-first policy, closed mutation list, code-owned number/date
  swaps, seeded fully-correct answers, partial-credit code grading,
  wrong/true/passage/habit feedback, and per-kind teacher counters;
  course-language guard with per-language glossaries. Live demo course
  "AI literacy at work: the EU AI Act" from the stored EUR-Lex excerpt
  (tests/test_t05_live.py, Spark blocks + Wonder reader, verified
  citations per block, single planted sentence, fault-injection resume
  with zero new paid calls, zero spend on rerun; mock twin in
  tests/test_t05_demo.py proves the same path spends nothing).
- T02c scope mutant killer (tests/test_t02c_revoke_scope.py): a course-a
  holder of role.assign + invite.create revoking an invite.create role
  held only in course-b is refused by the SCOPE check in revoke_role;
  the in-scope revoke still works. The signed-out invite callback keeps
  its 303 to /courses/{tag}; frontend lane: invite tokens are 64 hex
  characters, not 36.
- T03c author API (tests first in tests/test_t03c_author.py, 13 red-to-green):
  GET /courses library rows with profile/plan/question flags; GET
  course plan (same shape as PATCH, per-block source_handles); GET estimate
  forecast; GET course sources with approval/progress plus POST source
  reject (pending bindings held); POST run cancel (cooperative stop marker,
  no new step); GET held blocks with passages and batch quote plus block
  rewrite (202), accept, brief patch, and held batch rewrite (202); POST
  course publish (immutable snapshot, 422 while a block is held). Contract
  fixes: PATCH plan takes base_version with 409 on mismatch; upload accepts
  licence and as_of; run reads carry course_id, ledger cost_so_far, and
  per-topic held/checking status. Every route has ctx + catalogue code with
  scope answering 404; answer keys never serialized.

### Fixed

- Public-tree gate: the committed redaction probe in tests/test_t02b_extra.py
  built its fake Google OAuth secret at runtime so no literal secret-shaped
  marker remains in the tree (same runtime string, same redact coverage).
- T02b security close-out (probed red first on the real database, green after):
  account takeover by email refused (identity keyed only by provider/sub);
  superadmin login requires a verified email with the subject pinned on first
  match; role assignment enforces anti-escalation (codes and tags subset of the
  actor's, never oneself, no global grants by scoped actors); invites always
  grant student (client role choice removed); login states bound to the
  pre-login session with a 10-minute TTL and relative next_url only; scope
  checks in every user/invite/role/operation service with per-grant scope tags;
  invite tokens stored as sha256 only (shown once, audit carries the invite
  id); principals load fresh from the database on every call (no cache, worker
  included) with banned/legacy refusal; compose worker loads
  app.tasks.jobs.app with job tasks registered plus a one-shot worker test;
  bans persist by email and identity digest across deletion, heirs are vetted,
  legacy strips identities/roles/personal fields, audit and operation actors
  are plain uuids, operations cancel instead of moving, bans revoke invite
  uses; second grants union scope tags; sessions carry an absolute expiry,
  unsafe methods check Origin, cookies are __Host-/Secure outside local, and
  invite accept is POST-only; harness creates the procrastinate schema, OIDC
  tests skip per-test, and production commit/rollback is exercised; the auth
  callback redirects to the validated frontend path; /me carries roles with
  scope tags with a user-role revoke route; permission catalogue,
  notifications, and scoped invite listing routes added; OIDC accepts both
  Google issuer forms with azp and ±60 s leeway; malformed UUIDs answer 404;
  redact masks Google client secrets; the session secret signs cookies; CRUD verbs and scope
  tags validate; the administrator role cannot be emptied; echo submits take
  an idempotency key; SSE re-checks session and ban with a per-user stream
  cap. Denied-actor tests guard every route; all five review mutants verified
  caught.
- T02c second-pass review close-out (probed red first on the real database,
  green after, in `tests/test_t02c_probes.py`): the worker no longer imports
  the jobs module (the circular import broke fresh deploys, the migrate step,
  and the echo endpoint in a fresh process); compose and the schema bootstrap
  load `app.tasks.worker.app` with a CLI one-shot worker test on a fresh
  database plus an echo test through the production session path; scope is
  enforced per permission from the grant carrying it (a target with no tags
  needs a global actor; revoke_role carries the assign check; invite creation
  needs containment, not overlap; operation.view reads in-scope owners only;
  the heir must hold course.edit rights in every inherited scope; user and
  invite listings use the permission's own scope); the administrator role is
  immutable except by a superadmin and can never lose codes or be deleted;
  signed-out invite accepts 303 to sign-in with the invite id stashed
  server-side (never the raw token in a row, URL, or header) and the callback
  applies it then 303s to the course page; invites revoke by id; `Origin:
  null` is refused; only boolean `email_verified is True` verifies; migration
  `0007` downgrades past deleted authors and stale pending state (migration
  `0011` renames the stash to `pending_invite_id`); bans give invite seats
  back via `used_count`. Note for the frontend lane: invite tokens are 64 hex
  characters, not 36. Mutants verified caught: dropped ban scope check,
  dropped revoke scope check, administrator role shrink.
- T03d second-pass verification close-out (probed red first on the real
  database, green after, in `tests/test_t03d_probes.py` plus
  `tests/test_migration_0012.py`): a check-rejected reply is paid for
  but never stored, and re-claimed steps ask again under
  `variant=attempt-1`; fetches resolve once and connect to the checked
  IP with the Host preserved (robots too), and multicast/reserved join
  the SSRF guard; stranded `running` runs resume when no driver holds
  the run lock; the production path stores FTS order as `fts` and model
  ranks as `model` with hard negatives; unknown architect handles hold
  their blocks with a reason and the run completes; plan citations
  verify against stored passage text and re-crawls reuse the stored
  parser spec (whose container close is depth-tracked, not descendant
  matched); the production-caller inventory traces real AST call paths;
  identical-bytes pending bindings stay invisible to retrieval and the
  architect; re-claim commits and both citation offsets are covered
  independently; resume refreshes the estimate and snapshots the plan;
  the worker checks each operation's own code; the production provider
  factory shares one ledger engine and closes it; prompt versions hash
  the system file; courseless runs scope by operation owner; internal
  URLs are refused outright and non-http(s) schemes answer 422.
  Frontend-lane findings: rewrite operations enqueue like plan/generate
  (409 until T05, never orphaned); one move shifts siblings with bulk
  ordinals still accepted; `/sources/fetch` queues only
  robots-disallowed/policy cases with a scoped human queue
  (`GET /api/v1/human-queue`, `POST /human-queue/{id}/resolve`);
  per-source ingest status (`queued/parsing/ready/failed`, migration
  `0012` with a downgrade round-trip test) with transcription as an
  operation. Test hygiene: `truncate_all` also clears course tables so
  consecutive runs stay green. Mutants verified caught: dropped binding
  approval, offsets `or`→`and`, always-store reply, dropped retry
  variant, model-rank-as-fts, unpinned fetch/robots, descendant
  container close, worker `operation.run` for every kind.

### Added

- T03 course model, sources, retrieval, and first agents (migration `0004` with a downgrade round-trip test): courses (RESTRICT owner, draft/published, scope tags, row version), deferrable-ordinal modules with entry/exit knowledge, lesson/module_test topics with plan digests, blocks with plan node ids plus versioned content/answer-key rows, immutable course versions, code-declared methodologies synced at startup, course profiles with typed constraint rows, sha256-immutable sources with parser specs and supersedes, addressed passages with a trigger-maintained tsvector (english/norwegian/simple) plus GIN and pg_trgm indexes, citations, retrieval pairs, web candidates, the human queue, run DAG/estimate/stop-marker columns, per-run step keys with input digests, and per-actor operation idempotency.
- Course tree contract (`course/tree.py`): one Pydantic model plus strict JSON schemas for researcher/architect/librarian; code-checked invariants (source ref per block, topic-without-answer briefs, module purposes, methodology prescription, estimate size); `prompts/<agent>/system.md` for researcher, architect, and librarian, versioned by hash, validated at startup, with a stored-input replay test.
- Library (`services/library.py`): sha256 uploads on disk, code chunking (page/paragraph, heading path, timestamps), FTS retrieval Protocol with the `fts` adapter (AND with OR fallback) and a `reranker` stub, no-redirect URL intake with robots-to-human-queue, web candidates pending approval, voice-transcribe registry pricing ($0.18/audio hour).
- Resumable runs (`runs/executor.py`, the only `generate_json` call site): claim-and-commit before the call, code-checked outputs, verbatim citation checks that hold blocks, `run:<id>` advisory driver lock, stop markers, resume from the last good step; `submit_plan`/`submit_generate` operations with per-actor idempotency, plan edit/validate routes, generation refused while required questions are open; planted-500 resume test proving no done step is repaid and an identical second run pays zero; `live` plan test on a synthetic source (skipped without key).
- FTS retrieval tries AND semantics first and falls back to OR when no passage holds every query lexeme, so multi-word queries reach the model instead of returning empty.

- T02 identity, authorisation, and operations backbone: permission catalogue in `rbac/catalogue.py` (section plus action codes, synced at startup, administrator rewritten to all codes, new codes granted to nobody); frozen `Principal` with an in-process cache and global version key; `require()` with the one 403 message and scope 404s; one `ctx` dependency per route (session cookie, CSRF on unsafe methods, principal, audit actor) with audit rows in the same transaction.
- Provider-generic OIDC (`auth/oidc.py`) with a Google adapter (PKCE, one-time state, pinned issuer/audience, nonce, optional `hd` allow-list): first login creates a user with no roles, later logins refresh name/avatar, banned and legacy accounts are refused, superadmin comes from `SUPERADMIN_EMAILS`; backend session cookie (HttpOnly, SameSite=Lax, Secure from config) with rolling expiry, logout, and CSRF endpoint; no tokens in the browser.
- User administration: ban (`banned_at` plus reason, sign-in refused, sessions revoked) and delete (personal-data cascades, heir ownership transfer or `legacy=true` holder that keeps materials but cannot sign in); migration `0002` with a downgrade round-trip test.
- Invite links on a course or course group (UUID token, seat count, expiry, revocation): signed-in accepts grant the scoped student role, signed-out accepts stash the invite and apply it after sign-in, seats enforced atomically under concurrency.
- Long writes as operations: `submit_echo` validates, authorises, persists, commits, enqueues a Procrastinate job, and answers 202; the worker re-authorises the stored actor, advances progress, writes audit, persists a notification, and emits Postgres NOTIFY; per-user SSE `GET /api/v1/events` (session closed before streaming, 20 s ping that never cancels the subscription, unsubscribe in `finally`).
- Routes under `/api/v1` (auth, users, roles, invites, operations, events) with routes-parse / controllers-orchestrate / services-use-cases layering and errors through the one table; `PyJWT[crypto]` dependency for id-token verification; nine new settings in `.env.example`.
- T01b LLM-layer additions: per-step registry (`STEPS` with required `effort` and `max_output_tokens` per `docs/BRIEF.md` §3, sent as `reasoning.effort`/`max_output_tokens`, part of the reply-store hash, validated at startup); `tokens_reasoning` on `llm_calls` billed at the output rate (migration `0003`); background calls write a pending row holding the response id at create time and `resume_pending` polls that id without re-posting; `variant` scopes the request hash for explicit new drafts; `subject_user_id` on `llm_calls` with wipe-on-delete (request/response cleared, cost kept) and no `llm_replies` rows for learner-subject calls; usage-details parsing for object/dict/list shapes; config guards refusing the default DB URL, an empty session key, and `LLM_DEV_MODE=1` outside `local`/`test`; every `SecretStr` from config fed to redaction; `prompt_cache_key` helper (`agent:block_type:course_id`) plus a startup check rejecting dates, uuids, and run ids in prompt files.

### Fixed

- T03b review close-out (migrations `0005`–`0006`, probes in `tests/test_t03b_review.py`): sources are course-scoped (`course_sources` with per-course approval; retrieval and the architect only see approved passages of their own course); SSRF guard refuses private/loopback/link-local/ULA/multicast targets on fetch and robots (fail closed); `plan_node_id` unique per parent instead of per database so consecutive plans no longer collide; the run driver holds a session-level advisory lock for the whole drive and resume accepts stopped runs only; the worker creates-and-drives plan runs (estimate refresh plus immutable snapshot), resume enqueues a `resume_run` operation, and block generation answers 409 until its pipeline lands; plan writes persist block versions with `current_version_id`/`run_step_id` plus `plan` citation rows (passage RESTRICT) in the same transaction, unknown handles fail the step with a marker instead of a `KeyError`; PDF uploads are extracted with page addresses (binary sniffed even when mislabelled), images store with no passages, audio queues a transcription job through the provider and the `llm_calls` ledger, anything else answers 415, and passage language is detected per passage (en/nb/uk heuristics plus override); FTS queries once per distinct passage language with OR-union plus a pg_trgm fallback for `simple`; `course_language`/source languages ship in every agent input with a language rule in every system prompt; step digests carry prompt, upstream outputs, model, effort, and `max_output_tokens` with `<agent>:<plan_node_id>` keys; every handler exception stops the run with a marker; run reads/resume and source fetch/approve answer 404 outside scope; plan/submit and generate/submit require literal permission codes; block types come from a per-kind registry validated at plan time with answer-containment checked against the answer key; the architect receives the whole approved source (≤200k chars) in the cached prefix sorted by `(source_id, numeric address)`; the image copies `prompts/` with an absolute `PROMPT_ROOT`; `retrieval_pairs` store the FTS ranking plus top-k hard negatives; candidate approval fetches and ingests before approving and never re-approves on sha conflict; through-line persisted on the course; re-planning restores author-edited blocks; language CHECK is BCP-47; validate re-runs the tree contract.
- Tests run against an isolated `muse_test` database (created and migrated by session fixtures, `TEST_DATABASE_URL` override) instead of wiping the dev database; Alembic honours an explicitly configured `sqlalchemy.url`; the downgrade test asserts only `alembic_version` remains after `downgrade base`.
- The LLM call log row and reply now commit in a short autonomous session right after the API returns, so a failed step keeps its paid calls and `total_spend_usd` never under-reports; a repeated call still hits `llm_replies` without a new API call.
- Timeouts and dropped connections escalate to `background: true` instead of re-POSTing a possibly paid request; background polls retry on their own budget and their completed retrieves are charged spend; `Retry-After` parses HTTP-date form.
- Strict-subset validator: `null` allowed as a scalar (nullable `anyOf` branches), list-valued `type` rejected as `SchemaViolation`, sibling keys on `anyOf` nodes rejected, `required` compared as a list (duplicates fail).
- Registry prices per unit: `muse-image-1.0` at $0.01/image, `web_search` at $2.50/1,000 searches, contributor cached rate marked unconfirmed.
- Reply-store key is the exact `responses.create` kwargs (model, input, format, background, `prompt_cache_key`) with stable key ordering; model choice is per call (checked with `is_allowed`); `prompt_cache_key` is sent.
- Transport guard is session-automatic (patches `AsyncHTTPTransport` for every test) with a session-level spend total; missing `MODEL_API_KEY` is a 500 configuration error, not a 401.
- Packaging/compose: dropped `readme` from `pyproject` (unblocks the Docker build), pinned `openai>=3.42,<4`, worker command order fixed, `PsycopgConnector(conninfo=...)`, Procrastinate schema applied in the migrate step; `make check` excludes `live`, `make check-live` runs everything.
- T03a gate fixes: web fetch moved from `services/library.py` into `clients/web.py` (settings timeouts, no redirects, SSRF guard refusing non-public targets after DNS resolution) called through `providers/web.py`, so the service never imports httpx; `check_public_tree.py` skips its own rule files (`.gitleaks.toml`, itself, `tests/gates/` fixtures) by explicit allow-list; new catalogue codes `course.plan`, `course.generate`, `source.upload` required in the T03 controllers and plan services; the live plan test uses the URL-taking `create_session_factory`; the live cache test shares one ≥2k-token prefix across two questions under one `prompt_cache_key` (measured 1605 cached tokens on the second call).

### Added

- Day-1 backend harness: `pyproject.toml` (hatchling, uv, Python 3.12), `uv.lock`, `Makefile` (`check`: ruff, format check, mypy strict, pytest), `.env.example`.
- Compose stack: `postgres:16` on host port 5434, multi-stage `Dockerfile` (Python 3.12-slim, `uv sync --frozen`, non-root uid 10001), one-shot `migrate` (`alembic upgrade head`), `api` gated on it, Procrastinate `worker`.
- `src/app` skeleton per `docs/PRINCIPLES.md`: app factory with `/api/v1/health` and startup LLM-schema validation, settings-only `config.py`, `redact.py`, one error hierarchy with the exception→HTTP table, one-`AsyncSession`-per-request `db.py`, redacting structlog setup, empty layer packages, Procrastinate app in `tasks/worker.py`.
- Migration `0001`: users, roles, permissions, role_permissions, user_roles, invites, runs, run_steps, llm_calls, llm_replies, with a working downgrade and a round-trip test.
- LLM layer per `docs/HARNESS.md` §3: model registry with dated prices (Meta adapter over the `openai` SDK Responses API against `https://api.meta.ai/v1`), strict-subset schema validator, call log with registry cost, content-addressed reply store, status-based retry policy, hash-versioned prompt registry, `LLMProvider` Protocol with caching Meta adapter.
- `live` smoke test (skipped without `MODEL_API_KEY`): strict-JSON generation, absent-answer grounding verdict, call log plus reply-store cache-hit assertions.
- `tests/transport_guard.py`: allowlisted host, registry-model, and per-run spend-ceiling guard used by network tests.

- Base repository skeleton: `src/`, `docs/`, `tmp/`, `Dockerfile`, `docker-compose.yml`.
- Assistant kit: lobby, rules, skills and a project MCP file (local, uncommitted).
- Git hygiene: `.gitignore`, `.gitattributes`, `.github/pull_request_template.md`.
- Muse canon: `AGENTS.md` (`muse init` seed), `.agents/skills/`, `.agents/memory/` (committed).
- Maximal AI kit: skills `bootstrap`, `feature`, `bugfix`, `review`, `release`, `docs`; structure map and defaults distillate in `.agents/memory/`.
- `using-the-kit.md` manual and `current-state.md` snapshot in `.agents/memory/`.

### Changed

- AI kit (`AGENTS.md`, `.agents/`) switched fully to English.
- All remaining prose translated to English: README, manifest, changelog, templates, comments.
- Language policy recorded: English for everything in the project; Russian only in owner chat.

### Removed

- `.mcp.json`: Muse has no project MCP file; MCP lives in user settings.
- A duplicate local assistant kit removed; single Muse stack kept.
