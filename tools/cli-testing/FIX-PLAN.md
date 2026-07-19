# 0xCopilot CLI — fix plan (PRDs)

Staff-engineer remediation of the defects found in the live-smoke
([runs/&lt;ts&gt;/REPORT.md](runs)). Each PRD states the confirmed root cause, the
**single-source-of-truth** fix (no bandaids, boundaries honored), and a
**Definition of Done**. Executed in phases; each phase is a branch → PR → merge,
gated on its DoD, with a final full re-run of the harness.

Principle applied throughout: fix the **contract/source**, not the symptom; keep
each concern owned in exactly one place; never widen a service boundary.

---

## Phase A — backend correctness (both are hard 500s)

### PRD-1 (CRITICAL) — conversation create 500s (`record.org_id` on a dict)

- **Root cause:** `runtime_api_store.py:1950` opened the tenant connection with
  `org_id=record.org_id`, but `write_audit_log(record: dict)` takes a **dict** —
  every other line reads `data.get(_Fields.*)` and line 1948 already computes the
  normalized local `org_id`. Attribute access on a dict → `AttributeError` → 500
  on `POST /v1/agent/conversations` → no run/chat can start. The dict contract is
  intentional and identical across the in-memory + file adapters and ~20 call
  sites, so **callers are correct; only line 1950 was wrong.**
- **Fix (SSOT):** use the local `org_id`. One line. (A typed `AuditLogDraft`
  across all three adapters is the deeper SSOT play but is a separate, larger
  refactor — deliberately deferred so a production-down fix isn't gated on it.)
- **Why it shipped:** the one test that hits this path is skip-gated behind
  `TEST_DATABASE_URL`; CI runs the in-memory/file fakes (which use `.get()`), so
  the Postgres-only bug never executed.
- **DoD:** line uses local `org_id`; `POST /v1/agent/conversations` → 200 on the
  Postgres store; an audit row is written with a valid HMAC chain; a run can start;
  no caller/adapter signatures changed.

### PRD-4 (HIGH) — `GET /v1/me/profile` 500s (`UserRecord` `extra_forbidden`)

- **Root cause:** migration `0015_scim.sql:98` added `users.scim_external_id`, but
  `UserRecord` (a strict `extra="forbid"` contract) never modeled it. `get_user`
  does `SELECT *` → `_row_to_user` validates the whole row → `extra_forbidden`. On
  the desktop (all migrations applied) **every `get_user` 500s.**
- **Fix (SSOT):** model the column on `UserRecord` (`scim_external_id: str | None =
None`) so the domain contract reflects the actual table and `SELECT *` reads
  round-trip. SCIM already maintains the column via its own path; `create_user`/
  `update_user` intentionally don't touch it, so the default-`None` field is
  read-only here → no write regression.
- **DoD:** `UserRecord` models `scim_external_id`; `GET /v1/me/profile` → 200 for a
  fresh self-signup user; backend identity/profile unit tests pass; no change to
  the profile API response shape (`UserProfileResponse` unaffected).

---

## Phase B — sign-in (both shipping methods are dead)

### PRD-2 (CRITICAL) — wallet login 404 + `SIWE_ORIGIN` mismatch

- **Root cause (two):** (1) the desktop opens `{facade}/wallet.html` in the system
  browser, but the supervised facade serves no such route — `wallet.html` lives in
  `apps/frontend` and nothing in the packaged stack serves it → 404. (2) even if
  served, SIWE `expected_origin` defaults to `magic_link_base_url`
  (`http://localhost:5173`); the desktop never sets `SIWE_ORIGIN`, so the message
  domain (facade origin) would `domain_mismatch`.
- **Fix (SSOT):** keep `wallet.html` single-sourced in `apps/frontend`; the
  **facade** serves the _built_ dist artifact over http at the same origin that
  answers `/v1/auth/siwe/*` (mandatory: the page fetches SIWE relative-path +
  derives the domain from `window.location`). The supervisor — the one place that
  knows the dynamic facade port — sets `SIWE_ORIGIN = http://127.0.0.1:{facadePort}`
  for the backend child and `FACADE_WEB_DIST_DIR` for the facade. Env-gated, so the
  web/nginx path is unchanged; no page or SIWE-template copy is created; the facade
  serves a filesystem artifact (config), never imports `apps/frontend/src`.
- **DoD:** packaged boot serves `GET /wallet.html` (+ `/assets/*`) 200; backend
  child env has `SIWE_ORIGIN == facade origin`; a full wallet sign-in (nonce →
  sign → verify → loopback) mints a session with no 404 / no `domain_mismatch`;
  web/self-host path unchanged; boundaries intact.

### PRD-3 (HIGH) — Google login `client_secret is missing`

- **Root cause:** client-type mismatch, not a backend bug. The backend already
  chooses `client_secret_post` when a secret is present and `none` (PKCE-only)
  otherwise (`google.py build_google_provider`). The provided credential is a
  **Web** client (requires a secret); the desktop passthrough only forwards
  `GOOGLE_OAUTH_CLIENT_ID`, so the secret-less `none` path is used against a
  confidential client → Google 400.
- **Fix (SSOT — keep policy in the backend):** (1) forward
  `GOOGLE_OAUTH_CLIENT_SECRET` in the desktop `ENV_PASSTHROUGH_ALLOWLIST` so an
  operator running a Web client can supply it (secret comes from the operator's
  local env, never baked in). (2) The correct posture is a **Desktop-app** client
  (loopback + PKCE, no secret), which the existing `none` path already handles.
  (3) Surface an actionable error hint on a secret-missing 400. (4) Docs.
- **DoD:** allowlist forwards the secret when set (and strips it when unset,
  covered by a test); with a Desktop-app client id (no secret) the flow completes;
  with a Web client id + secret it completes; docs state the two supported
  configurations. _Full green live re-test needs a Desktop-app client id from the
  user._

---

## Phase C — connectors + the silent-failure UX gap

### PRD-5 (MEDIUM) — connecting a catalog tool/MCP 500s silently

- **Root cause:** `desktop_start_oauth` catches only `DesktopOAuthError`/
  `ProfileCatalogError`; `McpOAuthError(SETUP_REQUIRED)` from `discover` (no OAuth
  client configured) escapes as a 500. Separately, the catalog hardcodes
  "Available" for connectors that aren't actually connectable.
- **Fix (SSOT):** (A) catch `McpOAuthError` at the coordinator boundary and
  re-raise a stable-coded `DesktopOAuthError("connector_oauth_setup_required")` →
  map to **409** in the route table; the facade passes the detail code through
  unchanged. (B) availability has one source — the backend reconciliation overlay:
  a profile requiring a pre-registered client with none configured resolves to a
  non-available state, mirrored once in `packages/api-types`, rendered honestly by
  the single `CatalogCard`; hosts stop hardcoding "Available".
- **DoD:** start-oauth on an unconfigured connector → 409 `connector_oauth_setup_
required` (never 500), passed through by the facade; the catalog reports the
  honest availability; the card doesn't offer a doomed click; clicking a
  non-connectable connector shows a graceful message (via PRD-6).

### PRD-6 (MEDIUM/UX) — every write/action failure is silent

- **Root cause:** `RunDestination.handleStartGoal` swallows rejections
  (`.catch(() => {})`); the same for connector connect. No app-wide surface routes
  failed mutations to the user, so 500s look like the app doing nothing.
- **Fix (SSOT):** one in-package `NotificationCenterProvider` + `useNotify()` hook
  - one `ToastStack` in `@0x-copilot/chat-surface` (in-app toast is pure React +
    timers → an in-package provider, **not** a per-substrate `NotificationPort`).
    Both hosts mount it once; every action mutation routes rejections through the one
    `notify(...)` API (error toasts sticky w/ Retry, success/info auto-dismiss, a11y
    `aria-live`, reusing the existing `Toast` primitive).
- **DoD:** start-run 500 → dismissible error toast naming the failure + Retry;
  network failure + rejected host `onStartRun` both surface; connector connect
  failure shows a toast; profile save 500 surfaces; exactly one provider + stack
  per host; existing `RunErrorBanner` (stream errors) unaffected; eslint/typecheck
  pass.

---

## Phase D — minor

### PRD-7 (MINOR) — duplicate ⌘K search box + team-worded palette in solo mode

- **Root cause:** the shell topbar's `CommandPaletteTrigger` is a **dead no-op**
  (`ChatShell` never threads an opener), so the desktop `PaletteHost` renders a
  **second**, functional trigger on top → two search boxes. The palette placeholder
  is hardcoded `"Search the team, your work…"` regardless of profile.
- **Fix (SSOT):** thread `onOpenCommandPalette` through `ChatShell` → `Topbar` (the
  one search affordance), delete the desktop's duplicate trigger; make the
  placeholder profile-aware via `useOptionalDeploymentProfile()` (solo →
  "Search your work, or run a command…").
- **DoD:** exactly one search box per destination; the shell trigger opens the
  palette on both hosts; `CommandPaletteTrigger` renders only inside chat-surface;
  solo placeholder omits "the team"; team placeholder unchanged; typecheck/vitest
  pass.

---

## Phase E — verify everything

Re-stage the runtime (`copilot install --force`, re-pip-installs the fixed
services) + rebuild the desktop, then re-run the full harness end-to-end
(sign-in via wallet + the surface suite) and confirm every DoD above is met and no
regressions. Google's green path is confirmed if the user supplies a Desktop-app
client id; otherwise its code + config fix is verified by unit tests + the
improved error.
