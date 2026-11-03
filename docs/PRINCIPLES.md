# Principles (shape distilled from an earlier internal admin project of the author; no code is reused)

## Layers
- Calls only downward: `routes` (parse + validate, no logic) → `controllers` (orchestration, error mapping) → `services` (use cases) →
  `providers` (a `typing.Protocol` per external capability + adapters) → `clients` (the only external I/O, raw dicts).
  `models/` is the only place with SQL; `schemas/` are pure DTOs; `helpers/` pure functions; `tasks/` thin job wrappers.
- Exactly one module does the I/O of each external system (Meta Model API, Google OIDC, object store).
- `config.py` is the only env reader; every tunable lives in the settings catalogue; no literals in logic.
- Secret boundary: only the client knows the key; `redact.py` masks secrets in logs and errors; comparisons by digest.
- Errors: one exception hierarchy, wrapped at the layer that caught them; one table maps exceptions to HTTP codes; httpx never leaks past `clients/`.

## Authorisation (one module)
- Permissions are a declared catalogue in code (`code, category, label, description`); `section.*` codes gate UI sections, the rest gate actions;
  synced to a DB table at startup; unknown codes rejected on save.
- Role = permission codes + CRUD matrix per record type + scope. A new permission is granted to no existing non-admin role; the administrator
  role is rewritten to all codes on every start. `has(code) = is_superadmin or code in permissions`.
- Anti-escalation: nobody grants what they lack, nor edits their own role. An object outside scope answers 404.
- Per route: one `ctx` dependency (session cookie, CSRF on unsafe methods, principal, audit actor) and an explicit `require(principal, code)`
  in controllers/services; the worker re-authorises. `/me` returns permissions; the UI only hides what is unavailable; the backend is the authority.
- Identity: an account is created at first SSO login with NO permissions, whoever the person is (security default); rights are granted
  afterwards by a superadmin. Superadmin identities come from configuration (`SUPERADMIN_EMAILS`), never from "first user wins".
  Invite links (UUID, optional seat count) on a course or a course group give the student role scoped to those courses — scope by tags,
  as that project scopes its records. A user can be deleted or banned; a banned identity cannot sign in again. Deleting a user removes the
  objects they created through foreign-key cascades declared in the schema (personal data, answers, progress); when the person owns courses or materials,
  deletion asks for an heir (ownership moves to them) or turns the account into a legacy holder: a duplicate that keeps the materials
  but can never sign in. Provider-generic OIDC (Google first).

## Writes, jobs, events
- A write that takes time is an operation: validate → authorise → persist operation → commit → enqueue → `202 {operation_id}`; the worker
  re-authorises, writes audit, emits a notification. Sets of records are sent whole with a base version for conflict detection.
- One `AsyncSession` per request: commit on success, rollback on any exception, `expire_on_commit=False`, pooled engine with pre-ping.
  Jobs get a fresh engine per task. Queues by purpose. Bounded fan-out with a semaphore.
- SSE for progress: the DB session is closed before streaming; ping every 20 s; unsubscribe in `finally`; the browser keeps one EventSource.
- HTTP clients: explicit connect/read timeouts from settings; retry only idempotent reads (≤ 2); never retry writes; never follow redirects.
- Read models use stale-while-revalidate; background refreshes are silent and replace data only when its signature changed.
- API: one versioned prefix, fields only added, times in UTC ISO 8601.

## Frontend (Vue 3 + PrimeVue)
- One API wrapper module with endpoint constants; components never call `fetch` or build URLs. A failure shows the backend's message.
- No tokens in browser storage: auth is the backend's session cookie. No `eval`, no HTML from string concatenation, no inline styles or scripts, no CDN assets.
- No fake data, no buttons that do nothing. Colours only through tokens. Every mutation shows a loading state and a success/failure message, then refreshes the affected list.
- Tables: search + pagination + row actions; filters and sort in the URL. Forms: typed fields with an example hint; optional fields never required;
  leaving with unsent edits asks. Delete confirms by naming the object and saying it is irreversible.
- States for every screen: loading (202 → "Queued" → toast + bell), empty (muted line), error (backend message), denied (route guard to the first
  allowed section; controls hidden or disabled with the reason). Dark and light both readable; monospace for names and values.
- User-facing text in English source, ready for i18n; UI locales en/nb/uk; no emoji, no Unicode arrows in copy.

## Tests and delivery
- No mocks: tests run against the real local Postgres and, for the LLM layer, a transport guard that limits real calls to the configured
  dev model and a spend ceiling per run. Every test is written first and shown red. UI checked by headless Playwright on a production build.
- One image, migration as a one-shot job, services start on healthcheck. Lint + typecheck from day one.
- A rule that diverges from the code is a bug of the rule: docs are updated in the same change.
