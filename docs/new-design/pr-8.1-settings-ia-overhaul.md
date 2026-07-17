# PR 8.1 — Settings IA overhaul (rail + top chrome + Profile restructure)

## Context

The current Settings page diverges from the Atlas design bundle in three ways that matter for a non-engineer audience:

1. **Rail bloat.** 17 items across YOU / WORKSPACE / AI & DATA. Includes placeholders (`Capabilities`, `Claude Code`), duplicates (`General` overlaps Appearance, `Account` overlaps Profile's sign-in concerns), no icons, no counts.
2. **No top chrome.** "Back to chat", brand mark, and `<h1>Settings</h1>` are stacked into the rail header. The design uses a real top bar with `< Back to Atlas`, a workspace-scoped crumb (`Settings · Northwind workspace`), a `Manage MCP servers` quick-link, and a user pill.
3. **Profile is a flat field list.** No card grouping, paste-URL avatar, engineer-internal copy ("Cross-device fields. They follow you to every browser…"), no Bio, no inline Sign-in & security, no SSO/re-auth affordance.

This PR re-organises the IA to match the design while folding in features that are unique to our deploy (`API keys`, `Skills`, `Audit log`).

## Confirmed user decisions (this conversation)

| #   | Decision                                                                                              |
| --- | ----------------------------------------------------------------------------------------------------- |
| 1   | Split `API keys` if workspace-issued exists later; v1 ships personal-only under **ACCOUNT**.          |
| 2   | `Skills` lives under **AI & DATA** (it has `org` scope today, so it's not purely a per-user concern). |
| 3   | `Audit log` is always visible in the rail with an `Admin` badge; non-admin sees the read-only state.  |
| 4   | **Kill** the `Claude Code` placeholder section.                                                       |
| 5   | **Kill** the `Capabilities` placeholder section (redundant with Connectors).                          |

## Goals

- 10 rail items in 4 groups; icon + label + optional badge per item.
- Real top chrome with workspace context.
- Profile restructured into **Identity** + **Sign-in & security** cards.
- Drop dead inline sections (`GeneralSettings`, `AccountSettings`, `PlaceholderSettings`).
- No regression in admin gating, no regression in OAuth deep-links.

## Non-goals (deferred)

- **Bio field**. Requires `UserProfile` contract change + a backend column on the user-profile-owning service. Phase 2.
- **Real avatar upload**. URL paste stays in v1; the input gets a live preview and proper labelling. Pipeline for upload is its own PR.
- **MFA toggle**. Workspace-controlled today via login MFA enforcement; user-toggleable opt-in is out of scope. The Sign-in & security card surfaces sessions + sign-out only.
- **Personal vs. workspace API keys split**. Stays single-pane; revisit if/when admin-issued tokens land.
- **Hash routing changes**. The existing `useSettingsSection()` already serves all the slugs we keep.

## Information architecture

### Rail (4 groups, 10 items)

```
ACCOUNT                                ← per-user, "you"
  Profile          [user]
  Appearance       [sun]               ← absorbs General; gains Locale ("Region & language")
  Shortcuts        [⌘]
  API keys         [key]               ← personal tokens

WORKSPACE                              ← admin / shared
  Workspace        [building]   Admin
  Members & roles  [users]      N
  Billing & usage  [card]
  Audit log        [doc]        Admin

AI & DATA
  Model & behavior [spark]
  Connectors       [link]       N
  Skills           [book]       N
  Privacy & data   [shield]

NOTIFICATIONS
  Notifications    [bell]
```

Slugs we keep, unchanged: `profile`, `appearance`, `shortcuts`, `api-keys`, `notifications`, `workspace`, `members`, `billing`, `audit-log`, `model-and-behavior`, `connectors`, `skills`, `privacy-data`.

Slugs we delete: `general`, `account`, `capabilities`, `claude-code`. `SettingsSection` union loses these four and the `useSettingsSection` parser falls through to the new default (`profile`).

Counts wire up to data we already fetch:

- `Members & roles N` ← `useWorkspaceMembers(identity).members.length`
- `Connectors N` ← `connectors.servers.length` (filter to enabled? — keep total for parity with design's `8`)
- `Skills N` ← `skills.skills.length`

`Admin` badge on `Workspace` and `Audit log` reads from the same `auth.identity?.permission_scopes?.includes('admin:users')` check `SettingsScreen` already computes.

### Top chrome (new)

Above the rail + content shell, full-width:

```
[< Back to Atlas]   [Settings · <workspace.display_name>]   [⌘ Manage MCP servers]   [avatar email]
```

- **Back to Atlas** — calls existing `onBackToChat`.
- **Crumb** — workspace `display_name` from `useWorkspace(identity)`. Placeholder text "Settings" while loading; falls back to "Settings" on error (the rail still works).
- **Manage MCP servers** — triggers a hash navigation to `#connectors` (the section header already has a "Browse catalog" CTA that opens `McpOverlay`). Keeps the chrome calm — overlay open is still a one-click in-section step.
- **User pill** — circle with the user's first initial + the email. Uses `auth.identity` for the email; falls back gracefully when missing.

The brand block + `Back to chat` button currently inside `.settings-nav` move into the top chrome and are removed from the rail. Rail starts directly with the first group label.

### Profile section (two cards)

**Card 1 · Identity**

- Header: `Profile` / `How you appear across Atlas. Visible to your workspace.`
- Avatar row: 64px circle preview (initial fallback) + Upload-photo button (disabled, hint "Upload coming soon — paste a URL below for now") + Remove button + URL input below.
- Display name (existing field, controlled).
- Email row: `<code>` value + verification badge + "Linked via SSO. **Re-authenticate**" inline link when `email_verified_at` is set; the link calls `auth.logout()` so the user re-flows through the login screen.
- Job title (existing `title` field, relabelled).
- Time zone (existing `timezone` field; hint: "IANA tz id — e.g. America/Los_Angeles. Used for scheduling and digests.").
- Save changes button (existing flow).

**Card 2 · Sign-in & security**

- "Active sessions" — inlined `<AccountSessionsPanel />` (existing component, no changes).
- "Sign out everywhere" — danger button at the foot of the card; calls `auth.logout()`.

Locale moves out: it's now in **Appearance → Region & language** since it controls display formatting (date, number, lists), not identity.

### Appearance section additions

A second card at the bottom:

**Card 2 · Region & language**

- Locale (existing `locale` field on `UserProfile`; written via `useUserProfile().save({ locale })`).
- Hint: `BCP-47 tag — e.g. en-US, fr-FR. Affects date and number formatting.`

The Appearance section keeps its preferences hook for theme/accent/density/reduce-motion (which writes via `updateMyPreferences`) and the new card uses the user-profile hook (which writes via `updateMyProfile`). Two hooks coexist; both already pass-through identity headers via the shared HTTP client.

## Files touched

**Frontend**

- [apps/frontend/src/features/settings/SettingsScreen.tsx](apps/frontend/src/features/settings/SettingsScreen.tsx) — rail rewrite, top chrome, drop dead sections.
- [apps/frontend/src/features/settings/sections/Profile.tsx](apps/frontend/src/features/settings/sections/Profile.tsx) — two-card restructure, drop locale, add SSO re-auth link, inline sessions + sign-out.
- [apps/frontend/src/features/settings/sections/Appearance.tsx](apps/frontend/src/features/settings/sections/Appearance.tsx) — new Region & language card threading `useUserProfile` for locale only.
- [apps/frontend/src/features/settings/useSettingsSection.ts](apps/frontend/src/features/settings/useSettingsSection.ts) — drop `general`, `account`, `capabilities`, `claude-code` from the parsed union.
- [apps/frontend/src/styles.css](apps/frontend/src/styles.css) — top-chrome layout, rail-row icon + badge styles. The `.settings-shell` grid switches to `grid-template-rows: auto 1fr` to host the chrome above the existing two-column body.

**Caller**

- [apps/frontend/src/app/App.tsx](apps/frontend/src/app/App.tsx) — no signature change; `SettingsScreen` keeps the same props.

**Sub-components used as-is**

- [AccountSessionsPanel](apps/frontend/src/features/settings/AccountSessionsPanel.tsx) — inlined into the new Sign-in & security card.

**Tests touched**

- `Profile.test.tsx` (if present) — update field assertions for the new structure.
- `useSettingsSection.test.ts` — adjust the parser tests for dropped slugs.

## Service-boundary check

This PR is frontend-only. No new endpoints. No new contract fields. `useUserProfile`, `useWorkspace`, `useWorkspaceMembers` already exist; we are only re-arranging which section consumes them.

## Verification

```bash
npm run typecheck --workspace @0x-copilot/api-types
npm run typecheck --workspace @0x-copilot/frontend
npm run build --workspace @0x-copilot/frontend
```

Manual walk in `make dev`:

1. Open Settings — top chrome shows `Settings · <workspace>`, rail has 4 groups / 10 items / icons / badges.
2. `Members & roles` shows the actual count badge; `Connectors` and `Skills` reflect their counts.
3. Toggle to a non-admin user (or unset `admin:users` scope) — `Workspace` and `Audit log` rows still render with `Admin` badge but content is read-only / blocked.
4. Open Profile — two cards (Identity + Sign-in & security). Active sessions render, Sign-out-everywhere works, Re-authenticate link bounces to login.
5. Open Appearance — theme / accent / density / motion still work; Locale field below in Region & language saves to the profile endpoint.
6. Direct-link `/settings#connectors` — lands on Connectors with `Browse catalog` available.
7. Direct-link `/settings#general` — falls through cleanly to the default (Profile), no broken render.
8. Click `Manage MCP servers` in the top chrome — navigates to `#connectors`.

## Phase 2 (next PRs, not this one)

- **Bio field**: `UserProfile.bio` in api-types + backend column + textarea in Identity card.
- **Avatar upload**: drag-drop + S3-style upload pipeline; URL paste becomes a fallback.
- **Workspace API keys**: split rail row into `Personal` / `Workspace` (admin-only) once admin-issued tokens land.
- **MFA opt-in toggle**: when a per-user MFA enrolment flow exists.
