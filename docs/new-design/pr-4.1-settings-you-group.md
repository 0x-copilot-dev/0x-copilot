# PR 4.1 — Settings expansion · "You" group (Profile · Appearance · Shortcuts · Notifications)

> **Status:** Draft (PRD + Spec + Architecture)
> **Plan reference:** Wave 4, PR 4.1 in [`/Users/parthpahwa/.claude/plans/fetch-this-design-file-resilient-pumpkin.md`](../../../.claude/plans/fetch-this-design-file-resilient-pumpkin.md)
> **Owner:** frontend (4 settings sections + 1 hook + density / reduce-motion CSS hooks) · backend (1 table + 2 routes) · backend-facade (2 proxy routes) · api-types (2 types)
> **Size:** **M.** One persistence row per user, two endpoints, four section components, ~80 LOC of CSS hooks. Three of the four sections are presentation over a single `user_preferences` JSONB row; Profile is a sidecar table on `users`.
> **Depends on:** PR 0.1 foundations (✅ accent swatches + ThemeProvider already shipped) · PR 1.6 workspace defaults (no direct dep) · PR 2.2 keymap registry (provides the action list rendered by Shortcuts)
> **Reads alongside:** [`pr-1-2-per-chat-connector-scope.md`](pr-1-2-per-chat-connector-scope.md) (audit-on-write pattern), [`pr-1.6-workspace-defaults-conversation-lifecycle.md`](pr-1.6-workspace-defaults-conversation-lifecycle.md) (the user-vs-workspace fallback chain), [`apps/frontend/CLAUDE.md`](../../apps/frontend/CLAUDE.md), [`packages/design-system/CLAUDE.md`](../../packages/design-system/CLAUDE.md)
> **Sibling docs (Wave 4):** [`pr-4.2-settings-workspace-group.md`](pr-4.2-settings-workspace-group.md) · [`pr-4.3-settings-ai-and-data.md`](pr-4.3-settings-ai-and-data.md) · [`pr-4.4-mcp-overlay-test-connection.md`](pr-4.4-mcp-overlay-test-connection.md) · [`pr-4.5-usage-overlay-share-popover.md`](pr-4.5-usage-overlay-share-popover.md)

---

## 0 · TL;DR

Four sections, one row of persistence, zero new event types.

| Section       | Backend                                                            | Frontend                                                                                         |
| ------------- | ------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------ |
| Profile       | `user_profiles` sidecar on `users` (backend); `PUT /v1/me/profile` | Form bound to existing identity; avatar = URL only in v1                                         |
| Appearance    | `user_preferences.preferences_json.appearance` (backend)           | Reuses `ThemeProvider` + 8-swatch `ACCENT_SCHEMES` already in `@enterprise-search/design-system` |
| Shortcuts     | `user_preferences.preferences_json.shortcuts`                      | Renders the keymap registry from PR 2.2 with override slots                                      |
| Notifications | `user_preferences.preferences_json.notifications`                  | 4-event × 3-channel matrix; senders ship later                                                   |

The persistence model is deliberately one JSONB row per user (`user_preferences`) plus one sidecar table (`user_profiles`). Senders for notifications and avatar uploads are out of scope (see §1.3). Density and reduce-motion are CSS-attribute hooks on `<html>` (`data-density`, `data-reduce-motion`) — no new design-system primitive.

LoC estimate: backend ≈ 240 (1 migration + service + 2 routes + 4 audit actions + tests) · backend-facade ≈ 60 · api-types ≈ 30 · frontend ≈ 520 (4 sections + 1 hook + density CSS + reduce-motion CSS) · design-system ≈ 0 (existing primitives suffice).

---

## 1 · PRD

### 1.1 Problem

The Atlas design doc (Settings → "You" group) requires the surface to host four user-personal panels:

| Panel         | Design intent                                                                                                   | Today                                                                                                              |
| ------------- | --------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| Profile       | avatar, name, email (verified badge), title, timezone, locale, working hours                                    | Name + email come from the session; nothing else exists                                                            |
| Appearance    | theme (system / light / dark), accent (8 swatches), density (comfortable / compact), reduce-motion toggle       | `ThemeProvider` has scheme + accent in localStorage; **no density, no reduce-motion**, no cross-device persistence |
| Shortcuts     | editable keymap with category groups (navigation / composer / approvals)                                        | PR 2.2 ships the global keymap registry; there's nowhere to view or override it                                    |
| Notifications | 4-channel × 4-event matrix (email / Slack / desktop × mention / approval needed / run finished / weekly digest) | Nothing                                                                                                            |

Without this PR the user-personal Settings rail in [`SettingsScreen.tsx`](../../apps/frontend/src/features/settings/SettingsScreen.tsx) renders only `general`, `account`, `capabilities`, `connectors`, `skills`, `claude-code` — and the design doc's six "You" knobs have no home.

A second, smaller problem: today the theme/accent live exclusively in `localStorage` (`ThemeProvider` lines 56-84). A user logging in on a second device sees defaults until they re-set them. The cross-device persistence the design implies — "I set my theme once, it follows me" — needs a server row.

### 1.2 Goals

1. **Profile** — Sarah edits her display name, title, timezone, locale and working-hours band; the row persists in backend `users` + a sidecar `user_profiles` row. Email shows a verified badge sourced from `users.email_verified_at` (existing).
2. **Appearance** — Sarah picks theme (`system | light | dark`), accent (one of 8 swatches), density (`comfortable | compact`), and reduce-motion (`auto | always | off`). The change applies live (existing `ThemeProvider`); on save it persists to a server row so it follows her across devices. The localStorage fast path stays as a paint-flicker-avoidance cache.
3. **Shortcuts** — Sarah sees the keymap registry (PR 2.2) grouped by category (Navigation / Composer / Approvals), with a per-binding override slot. She presses a chord into a "Record" field; the override merges into her stored preference map. A Reset button clears overrides.
4. **Notifications** — Sarah toggles per-event-type × per-channel cells in a 4×3 matrix. Persists. Senders are wired in a follow-up; this PR ships **storage + UI** so the matrix is a real preference the senders can read when they ship.
5. **One round-trip per page open.** Settings → "You" loads `GET /v1/me/profile` + `GET /v1/me/preferences` in parallel and never re-fetches per section switch (sections are tabs over the same hydrated state).
6. **Never blocking the chat surface.** Streaming, runs, MCP, audit chain, retention — none of it changes.

### 1.3 Non-goals

- **Avatar upload.** v1 stores `avatar_url` (string) only; the file-upload pipeline lives in a separate PR. The form shows the URL with a tooltip "Drag-drop coming soon."
- **Notification senders.** v1 stores the matrix; the email/Slack/desktop dispatchers are handled separately (referenced in `services/ai-backend/src/runtime_api/app.py` `InboxAndEmailNotificationDispatcher` stub). The matrix is **read-by-senders-when-they-ship**.
- **Working-hours enforcement.** v1 displays + stores; downstream behavior (snoozing notifications outside hours) ships with the senders.
- **Custom themes / user-defined accent colors.** 8 fixed swatches per the design doc. A custom-color picker is explicitly cut.
- **Per-section deep-linking** (e.g. `/settings#profile`). PR 4.3 wires hash routing — until then, the section state is local; section changes don't update the URL. **PR 4.1 ships behind PR 4.3 for hash routing.**
- **Two-pane Settings layout overhaul.** Layout primitives stay as-is; we add four sections and the existing rail renders them.
- **Real-time keymap conflict detection.** Override storage works; surfacing a conflict warning is polish for a follow-up.

### 1.4 Success criteria

- ✅ `GET /v1/me/profile` returns `{display_name, title, timezone, locale, working_hours, avatar_url, email, email_verified_at, updated_at}` in <40 ms p99 against the local stack.
- ✅ `PUT /v1/me/profile` accepts a partial body (RFC 7396 merge-patch); only fields supplied get written; one row per write into `identity_audit_events` with `action='user.profile.update'`.
- ✅ `GET /v1/me/preferences` returns `{appearance, shortcuts, notifications, updated_at}` (deployment defaults materialised when the row is absent so the FE never sees `null`).
- ✅ `PUT /v1/me/preferences` accepts the same partial-merge semantics; one audit row per write.
- ✅ Theme/accent change applies live (existing) AND survives a server-side reload on a fresh device (after `PUT`, refresh on a second browser inherits the persisted accent).
- ✅ Density toggle adds `data-density="compact"` to `<html>`; spacing tokens shrink ~20% per the design doc; chat bubbles tighten; nothing breaks.
- ✅ Reduce-motion toggle (`always`) adds `data-reduce-motion="true"` to `<html>`; `prefers-reduced-motion: reduce` MQ matches; the planning indicator's pulse stops, draft tab's `streaming-cursor` blinks at 0.01ms cycle (effectively static).
- ✅ Shortcuts panel renders the keymap registry from PR 2.2 grouped by category. Pressing a chord into a "Record" field merges into the stored override map; chord parsing reuses `tinykeys` shape (already a transitive dep).
- ✅ Notifications matrix is a 4-row × 3-column toggle grid. Defaults are `mention=email,desktop`, `approval=email,desktop`, `run_finished=desktop`, `weekly_digest=email`. The matrix persists and is queryable by future senders.
- ✅ Streaming handshake byte-identical pre/post merge. `make test` green; ai-backend pytest suite green; frontend typecheck + build green.

### 1.5 User stories

| #    | Persona      | Story                                                                                                                                                                   |
| ---- | ------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| US-1 | Sarah        | I open Settings → Profile, set my title to "Marketing Ops", timezone to America/Los_Angeles. Save. Reload. Title still there.                                           |
| US-2 | Sarah        | I switch theme from System to Dark, accent from gold to atlas-orange. The page recolors immediately. I open the app on my laptop later — it's still atlas-orange.       |
| US-3 | Sarah        | Density → compact. The chat list tightens. The composer's footer hint loses one line of vertical padding. Nothing breaks.                                               |
| US-4 | Sarah (a11y) | Reduce-motion → Always. The "thinking…" indicator stops pulsing. Citation chip hover transitions go to instant.                                                         |
| US-5 | Sarah        | I rebind ⌘K (chat search) to ⌘P. Save. Press ⌘K — focuses sidebar search no longer; ⌘P does. I hit Reset — defaults restored.                                           |
| US-6 | Sarah        | I uncheck "Email · Run finished" because too noisy; check "Desktop · Approval needed" because critical. Save. The matrix persists; senders that arrive later honour it. |
| US-7 | New device   | First login on a phone: theme/accent inherit my server-stored preferences, not OS defaults.                                                                             |

---

## 2 · Spec

### 2.1 Wire — `/v1/me/profile`

**Read** `GET /v1/me/profile`

```jsonc
{
  "user_id": "usr_…",
  "email": "sarah.chen@acme.com",
  "email_verified_at": "2026-01-12T09:01:24Z",
  "display_name": "Sarah Chen",
  "title": "Marketing Ops",
  "timezone": "America/Los_Angeles",
  "locale": "en-US",
  "working_hours": {
    "tz": "America/Los_Angeles",
    "start": "09:00",
    "end": "18:00",
    "days": [1, 2, 3, 4, 5],
  },
  "avatar_url": "https://cdn.acme.com/u/sarah.png",
  "updated_at": "2026-05-05T16:01:14.220Z",
}
```

**Write** `PUT /v1/me/profile` (caller is implicitly the session user)

RFC 7396 merge-patch semantics — omit a field to leave it untouched, send `null` to clear (`title: null` clears). The same shape PR 1.2/1.6 already use. We do **not** re-implement merge-patch — Pydantic v2 `model_dump(exclude_unset=True)` is the existing convention.

### 2.2 Wire — `/v1/me/preferences`

**Read** `GET /v1/me/preferences`

```jsonc
{
  "appearance": {
    "theme": "dark", // 'system' | 'light' | 'dark' | 'slate'
    "accent": "atlas-orange", // one of ACCENT_SCHEMES
    "density": "comfortable", // 'comfortable' | 'compact'
    "reduce_motion": "auto", // 'auto' | 'always' | 'off'
  },
  "shortcuts": {
    "overrides": {
      "chat.search": "$mod+p", // tinykeys chord syntax
      "chat.toggle.sidebar": "$mod+\\",
    },
  },
  "notifications": {
    "matrix": {
      "mention": { "email": true, "slack": false, "desktop": true },
      "approval_needed": { "email": true, "slack": false, "desktop": true },
      "run_finished": { "email": false, "slack": false, "desktop": true },
      "weekly_digest": { "email": true, "slack": false, "desktop": false },
    },
  },
  "updated_at": "2026-05-05T16:01:14.220Z",
}
```

When no row exists, the response materialises deployment defaults (a static map in `backend_app/preferences/defaults.py`) so the FE always sees a complete shape — same materialisation pattern PR 1.6 uses for `workspace_defaults`.

**Write** `PUT /v1/me/preferences` — RFC 7396 merge-patch; the per-key merge depth is 2 (`appearance.theme = 'dark'` updates only `appearance.theme`; `notifications.matrix.mention.email = false` updates only that cell). Server-side validation rejects unknown keys — schema is finite per Pydantic v2 strict-mode.

### 2.3 Persistence

**Two migrations** in `services/backend/migrations/`. Numbered after the in-flight ones (PR 4.2 lands `0019_invitations.sql`; this PR is `0020_user_profiles_preferences.sql` if 4.1 lands second; the actual number is decided at PR-merge time — coordinate with the wave 4 owner).

```sql
-- 0020_user_profiles_preferences.sql

-- One row per user. Sidecar to users — keeps cross-device, non-identity-critical
-- profile data out of the identity table so identity migrations stay simple.
CREATE TABLE IF NOT EXISTS user_profiles (
    user_id          TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    org_id           TEXT NOT NULL,                              -- denormalised for RLS
    title            TEXT,
    timezone         TEXT,                                        -- IANA tz database id; validated at write
    locale           TEXT,                                        -- BCP-47; validated at write
    working_hours    JSONB,                                       -- { tz, start, end, days[] }
    avatar_url       TEXT,                                        -- v1: URL only
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE user_profiles ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON user_profiles
    USING (org_id = current_setting('app.current_org', true));

-- One row per user. Single JSONB blob keyed by client-area: appearance,
-- shortcuts, notifications. Server validates the shape but does not query
-- substructure (the senders / FE do). Single row keeps the read O(1).
CREATE TABLE IF NOT EXISTS user_preferences (
    user_id          TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    org_id           TEXT NOT NULL,
    preferences      JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE user_preferences ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON user_preferences
    USING (org_id = current_setting('app.current_org', true));
```

**Why two tables, not one wider `users` row:**

- `users` (migration 0004) is identity-critical (UNIQUE email, SCIM-reconciled). Adding presentation columns invites schema churn we don't want on a hot identity table.
- `user_profiles` is denormalised (`org_id` carried) for RLS uniformity with `agent_conversations` and friends. Foreign key to `users.user_id` cascades on delete.
- `user_preferences` is intentionally one JSONB column. The shape is small (<2 KB), opinion-only, and ~10x more likely to evolve (notification event types, shortcut categories) than identity columns. JSONB is the right shape; we accept the cost of server-side validation in Pydantic.

**Why not one `user_settings` JSONB blob covering profile too:** `display_name`, `title`, `timezone`, `locale` are queryable in admin tooling (member directory, working-hours-aware notifications). Keeping them as columns means index-friendly queries; the JSONB blob holds opinion data only.

### 2.4 Service path

```
backend-facade  /v1/me/profile        →  backend  /internal/v1/me/profile
backend-facade  /v1/me/preferences    →  backend  /internal/v1/me/preferences
```

Backend handlers live in `services/backend/src/backend_app/routes/me_profile.py` (new) and `me_preferences.py` (new). The pattern mirrors `routes/me.py` (existing — returns the session principal). Caller identity is the existing `x-enterprise-user-id` header; **never** caller-supplied identity for the write target. The path is `/v1/me/*` — no `/v1/users/{id}` admin form (admin updates ride a separate flow in PR 4.2 Members).

`backend-facade` proxy: 4 routes (GET + PUT × 2). Pattern is `forward_json_to_backend()` with `Authorization` header preservation, identical to `me_routes.py` lines 27-44.

### 2.5 Audit

One row per privileged write into `identity_audit_events` (existing append-only chain):

| Action                    | Metadata                                                                                                     |
| ------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `user.profile.update`     | `{ before, after, diff_keys }` — fields touched only; full PII fields are stored in the row but not the diff |
| `user.preferences.update` | `{ before_keys, after_keys, diff_paths }` — paths through the JSONB tree; e.g. `appearance.accent`           |

**Why `before/after` for profile but not preferences:** profile fields are short and forensically meaningful (timezone change as a phishing signal); preferences are bulky JSON with no security implication, so we record the path-set diff to keep the chain compact. This is the same compaction pattern `mcp_audit_events` uses for scope changes (commit `e07d10b`).

### 2.6 Permissions

| Caller                                        | Read self                                     | Read other                                          | Write self                              | Write other |
| --------------------------------------------- | --------------------------------------------- | --------------------------------------------------- | --------------------------------------- | ----------- |
| Session user (`x-enterprise-user-id` matches) | ✅                                            | ❌ (404)                                            | ✅                                      | ❌ (403)    |
| Workspace admin                               | ✅                                            | ✅ (read-only directory; ships with PR 4.2 members) | ❌ (✅ via PR 4.2 admin override route) | —           |
| Service-to-service                            | ✅ via existing `RuntimeServiceAuthenticator` | ✅                                                  | ❌                                      | ❌          |

This PR ships the **self** path. PR 4.2's Members panel adds the admin read for the directory, with no write — admin-as-user impersonation is explicitly cut.

### 2.7 Error semantics

| Condition                                            | Status | Code                    |
| ---------------------------------------------------- | ------ | ----------------------- |
| `GET /me/profile` for an authenticated session       | 200    | —                       |
| `PUT /me/profile` with bad timezone (not in IANA db) | 422    | `invalid_timezone`      |
| `PUT /me/profile` with bad locale (not BCP-47)       | 422    | `invalid_locale`        |
| `PUT /me/profile` with `working_hours.start > end`   | 422    | `invalid_working_hours` |
| `PUT /me/preferences` with unknown top-level key     | 422    | `invalid_request`       |
| `PUT /me/preferences` with unknown shortcut id       | 422    | `unknown_shortcut`      |
| `PUT /me/preferences` with malformed chord           | 422    | `invalid_chord`         |
| `PUT /me/preferences` with unknown event type        | 422    | `unknown_event`         |

Timezone validation: Python 3.13 ships `zoneinfo.available_timezones()`. Locale validation: `babel.Locale.parse()` — `babel` is already in the runtime tree (via `services/ai-backend/requirements.txt` for date formatting in markdown). If `babel` is not present in `services/backend`'s tree, validation falls back to a regex (BCP-47 is a small grammar) — no new dep.

### 2.8 Frontend contract (`@enterprise-search/api-types`)

```ts
// packages/api-types/src/index.ts

export interface UserProfile {
  user_id: string;
  email: string;
  email_verified_at: string | null;
  display_name: string | null;
  title: string | null;
  timezone: string | null; // IANA; e.g. 'America/Los_Angeles'
  locale: string | null; // BCP-47; e.g. 'en-US'
  working_hours: {
    tz: string;
    start: string; // 'HH:MM'
    end: string;
    days: number[]; // 0=Sun..6=Sat
  } | null;
  avatar_url: string | null;
  updated_at: string;
}

export type AccentScheme =
  | "atlas-orange"
  | "gold"
  | "amber"
  | "red"
  | "lime"
  | "teal"
  | "blue"
  | "violet";
export type ThemeScheme = "system" | "light" | "dark" | "slate";
export type Density = "comfortable" | "compact";
export type ReduceMotion = "auto" | "always" | "off";

export interface UserPreferences {
  appearance: {
    theme: ThemeScheme;
    accent: AccentScheme;
    density: Density;
    reduce_motion: ReduceMotion;
  };
  shortcuts: { overrides: Record<string, string> };
  notifications: {
    matrix: Record<NotificationEvent, Record<NotificationChannel, boolean>>;
  };
  updated_at: string;
}

export type NotificationEvent =
  | "mention"
  | "approval_needed"
  | "run_finished"
  | "weekly_digest";
export type NotificationChannel = "email" | "slack" | "desktop";

export type UpdateUserProfileRequest = Partial<
  Omit<UserProfile, "user_id" | "email" | "email_verified_at" | "updated_at">
>;
export type UpdateUserPreferencesRequest = DeepPartial<UserPreferences>;
```

`DeepPartial` is already a project utility (used by PR 1.2 for connector scope updates). One re-use, no new dep.

### 2.9 Frontend wiring

| Concern                 | Reuse                                                                                                  | Add                                                                                          |
| ----------------------- | ------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------- |
| Section host            | `SettingsScreen` left rail + section switch                                                            | Four section components + four `{id, label}` rail entries                                    |
| Theme + accent          | `ThemeProvider`, `useTheme`, `ACCENT_SCHEMES` (8 swatches in design-system)                            | One `<AccentPicker>` (40 LOC) calling `setAccent`                                            |
| Density attribute       | `<html>` root                                                                                          | One root effect: `document.documentElement.dataset.density = preferences.appearance.density` |
| Reduce-motion attribute | `<html>` root                                                                                          | Same pattern: `document.documentElement.dataset.reduceMotion = …`                            |
| Shortcuts               | PR 2.2 keymap registry export `getKeymapRegistry(): KeymapAction[]`                                    | `<ShortcutsTable>` rendering grouped registry; `<ChordRecorder>` using `tinykeys` parser     |
| Notifications matrix    | Existing `Switch` from `@enterprise-search/design-system`                                              | `<NotificationsMatrix>` (12 toggles in a CSS grid)                                           |
| Save / dirty state      | Existing `useDebouncedSave` (or hand-rolled 300ms debounce, ~12 LOC)                                   | `usePreferencesDraft` hook holding `{server, draft, isDirty, save}`                          |
| Hydration               | `useQuery`-style — one `useUserProfile()` + `useUserPreferences()` in `apps/frontend/src/features/me/` | Two ~30 LOC hooks                                                                            |

`tinykeys` (~3 KB gzipped, 1 dep, MIT) is the de-facto chord parser the JS ecosystem agrees on; it's already a transitive dep of `assistant-ui` (verify before merge with `npm ls tinykeys`). If absent, we use a 30-line chord parser rather than pulling a new top-level dep.

### 2.10 Density CSS (≈ 30 LOC)

```css
/* packages/design-system/src/styles.css — add after the accent block */
:root[data-density="compact"] {
  --space-row-gap: 0.375rem; /* was 0.5rem  */
  --space-card-pad: 0.625rem; /* was 0.75rem */
  --space-section: 1rem; /* was 1.25rem */
  --line-height-body: 1.45; /* was 1.55    */
}

/* Reduce-motion: explicit override beats the OS query so the user's choice wins */
:root[data-reduce-motion="always"] *,
:root[data-reduce-motion="always"] *::before,
:root[data-reduce-motion="always"] *::after {
  animation-duration: 0.01ms !important;
  animation-iteration-count: 1 !important;
  transition-duration: 0.01ms !important;
  scroll-behavior: auto !important;
}

/* Auto: defer to the OS preference (this is what we always do today by default;
   the explicit attribute makes the precedence chain readable) */
@media (prefers-reduced-motion: reduce) {
  :root[data-reduce-motion="auto"] *,
  :root[data-reduce-motion="auto"] *::before,
  :root[data-reduce-motion="auto"] *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}
```

Spacing tokens that sites already consume (`--space-row-gap`, `--space-card-pad`, `--space-section`) are re-used; we only define the **compact override**. Components that style with `padding: 0.5rem` (hard-coded) are unaffected — they ignore the toggle. We accept that small surface as v1; future polish migrates the remaining components to tokens.

---

## 3 · Architecture

### 3.1 Where this lives in the system

```
   ┌────────────────┐                            ┌──────────────────────┐
   │ apps/frontend  │  GET/PUT /v1/me/profile    │  backend-facade      │
   │  Settings →    │ ────────────────────────►  │  /v1/me/* proxy      │
   │  You group     │  GET/PUT /v1/me/preferences│  (no business logic) │
   │                │ ◄────────────────────────  └──────┬───────────────┘
   │  ThemeProvider │                                   │ /internal/v1/me/*
   │  (existing)    │                                   ▼
   │                │                            ┌──────────────────────┐
   │  data-density  │                            │  backend             │
   │  data-reduce-  │                            │  MeProfileService    │
   │  motion attrs  │                            │  MePreferencesService│
   └────────────────┘                            └──────┬───────────────┘
                                                        │
                                                        ▼
                                               ┌─────────────────────────┐
                                               │  user_profiles    (NEW) │
                                               │  user_preferences (NEW) │
                                               │  identity_audit_events  │
                                               └─────────────────────────┘
```

Nothing in `services/ai-backend/` is touched. The user-personal Settings panels read directly from `services/backend` — same pattern `/v1/me` already uses for the session principal.

### 3.2 Streaming impact — explicitly **none**

| Subsystem                                       | Touched?                                                                          |
| ----------------------------------------------- | --------------------------------------------------------------------------------- |
| `runtime_events` schema                         | No                                                                                |
| `RuntimeEventEnvelope`                          | No                                                                                |
| SSE handshake                                   | No                                                                                |
| Worker job loop                                 | No                                                                                |
| Capabilities / tools / MCP loaders              | No                                                                                |
| Citation registry, drafts, approvals, subagents | No                                                                                |
| Audit chain                                     | Additive — two new `action` constants on `identity_audit_events` (existing chain) |

The agent runtime is a strict consumer of `workspace_defaults` (PR 1.6). It does **not** consume user preferences for behavior — themes are presentation, working hours are not enforced in v1, shortcut overrides are FE-only, notification matrix is read by senders that don't exist yet. So this PR has zero impact on what the model sees.

### 3.3 Why preferences live in **backend**, not ai-backend

`workspace_defaults` lives in ai-backend because the runtime services (`RunService`, `ConversationService`) read it on every conversation/run create. **User preferences are never consumed by the runtime.** They're presentation (FE) and notification (backend-domain). Identity already lives in backend; preferences sidecar onto identity. Putting them in ai-backend would force a useless cross-service hop for every theme toggle.

This is the same boundary call PR 1.6 §3.3 spelled out, applied in the opposite direction: the row goes wherever its consumers live.

### 3.4 DRY — what we reuse vs. what we add

| Concern                          | Reuse                                                                                      | Add                                                                                                                                       |
| -------------------------------- | ------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------- |
| Identity / RBAC                  | session header path, `x-enterprise-user-id` resolution                                     | —                                                                                                                                         |
| Audit chain                      | `identity_audit_events` writer + chain-signature trigger (migration 0002)                  | Two new `action` constants                                                                                                                |
| Append-only enforcement          | Trigger from `0002_audit_hardening.sql`                                                    | —                                                                                                                                         |
| RFC 7396 merge-patch             | Pydantic v2 `model_dump(exclude_unset=True)` (PR 1.2 / 1.6 pattern)                        | —                                                                                                                                         |
| RLS policy template              | `tenant_isolation USING (org_id = current_setting('app.current_org', true))` (`0008_rls`)  | Two `CREATE POLICY` invocations                                                                                                           |
| Sidecar table pattern            | `users.deleted_at` + sidecar pattern from `0004_identity_foundation.sql`                   | One sidecar (`user_profiles`)                                                                                                             |
| Single-row-per-user JSONB        | Same shape `auth_providers.config` already uses                                            | One JSONB column                                                                                                                          |
| ThemeProvider                    | `packages/design-system/src/index.tsx:61-100`                                              | One `useThemeSync` effect that mirrors `useUserPreferences().appearance` into the provider; localStorage stays as the paint-flicker cache |
| Accent swatches                  | `ACCENT_SCHEMES` (8 already shipped per design-system inventory)                           | One `<AccentPicker>` UI                                                                                                                   |
| Switch / Field / Card primitives | `@enterprise-search/design-system`                                                         | —                                                                                                                                         |
| Chord parsing                    | `tinykeys` (already transitive via `assistant-ui`) **or** 30-line inline parser            | Verify or inline                                                                                                                          |
| Notifications matrix             | `Switch` × 12 in a CSS grid                                                                | One small component                                                                                                                       |
| Density tokens                   | Existing `--space-*` tokens for the comfortable scheme                                     | One `[data-density="compact"]` override block (~10 lines)                                                                                 |
| Reduce-motion CSS                | `prefers-reduced-motion: reduce` MQ in browser                                             | One `[data-reduce-motion]` override block                                                                                                 |
| FE state                         | Existing `useQuery` pattern across the app (or hand-rolled fetch+state — both are present) | Two thin hooks                                                                                                                            |

### 3.5 Pre-built libraries — what we considered, what we use

| Need                | Considered                                | Decision                                                                                                                                                             |
| ------------------- | ----------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Color picker        | `react-color`, `react-colorful`           | **Skip.** The design is a fixed 8-swatch list, not a continuous picker. We render `<button data-accent={scheme}>` × 8.                                               |
| Chord recorder      | `tinykeys`, `mousetrap`, `hotkeys-js`     | **`tinykeys` if already transitive; else inline.** It's the smallest of the three, and PR 2.2's keymap registry already uses its chord syntax (`$mod+k`).            |
| Form state          | `react-hook-form`, `formik`, `final-form` | **Skip.** Four fields per panel, simple validation. Native `onBlur` save + `useState` is cheaper and matches the PR 4.2 / 4.3 pattern.                               |
| Theme persistence   | `react-themes`, `next-themes`             | **Skip.** `ThemeProvider` is in-tree and proven.                                                                                                                     |
| Server state cache  | `@tanstack/react-query`, `swr`            | **Skip.** Two endpoints, no invalidation graph. Two thin `useQuery`-shaped hooks. Adding a cache lib here doesn't pay off until 4.2/4.3/4.5 use it too — they don't. |
| Locale validation   | `babel`, `bcp47-validate`                 | **Reuse `babel.Locale.parse` if `babel` is already on the path; else 1 regex.** `bcp47-validate` is unmaintained.                                                    |
| Timezone validation | `pytz`, `zoneinfo`                        | **Use `zoneinfo`** — Python 3.13 stdlib, `available_timezones()` is the canonical IANA set.                                                                          |
| Avatar upload       | `multer`, `formidable`                    | **Out of scope (v1 is URL only).**                                                                                                                                   |
| Dialog primitive    | `@radix-ui/react-dialog`                  | **Defer to PR 4.4** — wizard needs a real dialog there. The "You" panel doesn't need a modal.                                                                        |

The deciding rule: **adopt a library only when the alternative is more than one screenful of bespoke code and the lib is the de-facto choice.** Five libs offered for needs we don't have, declined; one (`tinykeys`) sized to the actual surface, kept.

### 3.6 Sequence — Sarah changes accent, density, reduce-motion

```
Sarah        FE (Settings → Appearance)         ThemeProvider           backend
 │               │                                    │                     │
 │  open page    │ ─── GET /v1/me/profile          ──►│                     │
 │ ──────────── ►│ ─── GET /v1/me/preferences      ──►│                     │
 │               │ ◄── 200 {appearance, shortcuts, …} │ ◄────────────────── │
 │               │  hydrate ThemeProvider             │                     │
 │               │  applyAttrs(html, density, motion) │                     │
 │               │                                    │                     │
 │  pick orange  │ optimistically setAccent('atlas-orange')                 │
 │               │ ────────────────────────────────► page recolors live     │
 │               │ debounce 300ms                    │                     │
 │               │  PUT /v1/me/preferences { appearance.accent: 'atlas-orange' }
 │               │ ────────────────────────────────────────────────────────►│
 │               │                                    │  validate, write,   │
 │               │                                    │  audit, return      │
 │               │ ◄──────────────────────────────────────────────────────── │
 │               │  isDirty = false                  │                     │
 │               │                                    │                     │
 │  ⌥ phone next morning                                                    │
 │               │ first paint reads localStorage cache (atlas-orange) ─── no flicker
 │               │ then GET /v1/me/preferences confirms                      │
```

The localStorage cache stays — it's the paint-flicker avoidance the design wants. After the server fetch resolves, we update the cache too. If the server says something different (cross-device divergence), the server wins; the cache catches up on next render.

### 3.7 Edge cases

| Case                                                                                                | Behaviour                                                                                                                                                   |
| --------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| User has no row yet                                                                                 | `GET` returns deployment defaults; `PUT` creates the row.                                                                                                   |
| Client sends an accent we don't have                                                                | 422 `invalid_request`. Frontend renders the previous accent + a toast.                                                                                      |
| Two tabs, same user, change accent in both at the same time                                         | Last-write-wins; the late tab sees the early tab's effect on next focus refetch (use `visibilitychange` to refetch on tab focus — already a project idiom). |
| User toggles `reduce_motion = "always"` on a tab where the welcome animation is mid-fly             | Animation snaps to its end frame (`animation-duration: 0.01ms`); no broken layout because animations target keyframe progress, not visibility.              |
| Notifications matrix toggled OFF for `mention.email` while Sarah is in the middle of being notified | Senders read at send-time; the in-flight email goes through.                                                                                                |
| Avatar URL points to a 404                                                                          | The `<img onError>` falls back to initials (existing `<AppIcon>` primitive).                                                                                |
| User has 0 shortcut overrides                                                                       | Registry + empty overrides → render registry as-is.                                                                                                         |
| User overrides a chord that was just renamed in PR 2.2's registry                                   | We render the registry as the source of truth; orphan overrides (override id not in current registry) are ignored, and a debug-only toast invites a Reset.  |
| Working hours with `start == end`                                                                   | 422 `invalid_working_hours` (caller intent ambiguous).                                                                                                      |
| Daylight-saving change in the user's timezone                                                       | We store `tz` and `start/end` as wall-clock; the FE applies the user's `Intl.DateTimeFormat` at render. Server stores strings; no shift logic.              |

### 3.8 Test plan

**Backend (`services/backend/tests/`)**

- `unit/me/test_profile_get_put.py`
  - empty row → deployment defaults shape
  - `PUT` then `GET` round-trips
  - bad timezone / locale / working_hours → 422
  - merge-patch (`title=null` clears; omit leaves alone)
  - one audit row per `PUT`; chain verifier passes
- `unit/me/test_preferences_get_put.py`
  - same set on the preferences endpoint
  - shortcut chord validation
  - unknown notification event → 422
  - deep merge: `notifications.matrix.mention.email = false` updates only that cell
- `integration/test_me_endpoints_rls.py`
  - cross-org caller → 404 (does not leak existence)

**Frontend (`apps/frontend/src/features/me/`)**

- `useUserProfile.test.ts` — optimistic save, rollback on 4xx
- `useUserPreferences.test.ts` — same
- `Appearance.test.tsx` — accent click recolors live; `PUT` fires after debounce; isDirty flips
- `Shortcuts.test.tsx` — chord recorder captures `$mod+k`; reset clears overrides
- `Notifications.test.tsx` — matrix toggle persists; defaults render when row is absent

**Cross-service smoke (`make test`)** — one happy path through facade → backend → DB for both endpoints.

### 3.9 Rollout

- **Flag-free.** `GET` materialises defaults when row absent; old clients see `404` only if they hit the new endpoint (none do today).
- **Zero-downtime migration.** `CREATE TABLE IF NOT EXISTS` × 2; `CREATE POLICY` × 2; `CREATE INDEX` × 0 (PK is enough). No rewrite.
- **Backout.** Drop the two tables; the API returns deployment defaults forever. ThemeProvider's localStorage path keeps working untouched.
- **Forward compatibility.** The `preferences` JSONB tolerates additive top-level keys. Future PRs adding (e.g.) `composer.experimental_voice_mode` ship without a migration.

### 3.10 Open questions

1. **Per-workspace overrides for theming.** The design doc cuts custom themes; should an admin be able to lock the workspace's accent (e.g. for branded environments)? **Not in v1.** The accent is a personal preference. If branding becomes a need, an `organization_branding` row is the right home.
2. **Avatar upload pipeline.** Out of scope; tracked under "future polish."
3. **Sender adapters consuming `notifications.matrix`.** The matrix is wire-ready. The senders themselves need a separate PR (S/M, separate from this wave).
4. **Working-hours-aware notifications.** Snooze logic + DND windows — sender concern, not a storage concern.

---

## 4 · Acceptance checklist

- [ ] Migration `0020_user_profiles_preferences.sql` (or whatever number it lands at) applies cleanly forward and rolls back.
- [ ] `MeProfileService.get/put` round-trips with merge-patch semantics; bad timezone/locale/working_hours return 422.
- [ ] `MePreferencesService.get/put` materialises deployment defaults; deep-merge updates a single cell without touching siblings.
- [ ] One audit row per write; chain verifier passes; new actions registered in `IdentityAuditAction` enum.
- [ ] `backend-facade` exposes `GET/PUT /v1/me/profile` and `GET/PUT /v1/me/preferences`. None reach `/internal/v1/*`.
- [ ] `@enterprise-search/api-types` exports `UserProfile`, `UserPreferences`, `UpdateUserProfileRequest`, `UpdateUserPreferencesRequest`, plus the four union types.
- [ ] `useUserProfile()` + `useUserPreferences()` hooks ship with tests.
- [ ] `<Profile />`, `<Appearance />`, `<Shortcuts />`, `<Notifications />` mount in `SettingsScreen` under the "You" group rail.
- [ ] `ThemeProvider` mirrors `preferences.appearance` (accent, theme); density and reduce-motion are reflected as `<html data-density>` / `<html data-reduce-motion>`.
- [ ] CSS overrides for compact + reduce-motion (always) ship in `packages/design-system/src/styles.css`.
- [ ] Streaming handshake byte-identical pre/post merge.
- [ ] No new event types, no new wire variants, no LangGraph harness changes.
- [ ] `npm run typecheck`, `npm run build`, ai-backend pytest, backend pytest all green; `make test` green.

---

## 5 · References

- Design Doc · Settings → "You" group (eight knobs across four panels) — bundle at `/tmp/design-doc/enterprise-search/project/Design Doc.html` lines 540-545.
- [`packages/design-system/src/index.tsx:32-100`](../../packages/design-system/src/index.tsx) — `ThemeProvider`, `ACCENT_SCHEMES`, `useTheme`.
- [`packages/design-system/CLAUDE.md`](../../packages/design-system/CLAUDE.md) — feature workflows stay in `apps/frontend`.
- [`apps/frontend/src/features/settings/SettingsScreen.tsx`](../../apps/frontend/src/features/settings/SettingsScreen.tsx) — section host the new panels mount into.
- [`services/backend/migrations/0004_identity_foundation.sql`](../../services/backend/migrations/0004_identity_foundation.sql) — `users` table the sidecar attaches to.
- [`services/backend/migrations/0002_audit_hardening.sql`](../../services/backend/migrations/0002_audit_hardening.sql) — append-only chain we extend with two new actions.
- [`services/backend/src/backend_app/routes/me.py`](../../services/backend/src/backend_app/routes/me.py) — pattern the new `me_profile` / `me_preferences` routes follow.
- [`services/backend-facade/src/backend_facade/me_routes.py`](../../services/backend-facade/src/backend_facade/me_routes.py) lines 27-44 — proxy template.
- [FastAPI · Body — Updates (PATCH semantics)](https://fastapi.tiangolo.com/tutorial/body-updates/) — merge-patch convention.
- RFC 7396 — JSON Merge Patch.
- [`tinykeys` README](https://github.com/jamiebuilds/tinykeys) — chord syntax and parser used by PR 2.2's keymap registry.
- [`docs/new-design/pr-1-2-per-chat-connector-scope.md`](pr-1-2-per-chat-connector-scope.md) — `DeepPartial`-style merge-patch, audit on write.
- [`docs/new-design/pr-1.6-workspace-defaults-conversation-lifecycle.md`](pr-1.6-workspace-defaults-conversation-lifecycle.md) — defaults-fallback materialisation pattern.
- [`docs/new-design/pr-2.2-sidebar-user-card-keymap.md`](pr-2.2-sidebar-user-card-keymap.md) — keymap registry consumed by the Shortcuts panel.
