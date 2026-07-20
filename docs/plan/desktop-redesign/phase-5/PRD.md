# Phase 5 — Settings (solo, profile-gated)

**Branch:** `feat/desktop-redesign` · **Worktree:** `/Users/parthpahwa/Documents/work/enterprise-search-redesign`
**Plan:** [PLAN.md](../PLAN.md) §8 Phase 5 (5A–5E), §5 Target IA, §9 sequencing.
**Design source of truth:** [DESIGN-SPEC.md](../design-reference/DESIGN-SPEC.md) §4 (Settings), §5 (Modal/flow patterns), §0 (tokens/dims), §6 (shortcuts), §9 (decisions overlay).
**Template:** [\_TEMPLATE.md](../_TEMPLATE.md) — this PRD follows all 12 sections in order.

> **Reading note for reviewers.** Several things the plan/prompt framed as "NEW" already exist in this worktree and are cited below with real paths. The single most important correction: **Local models (Ollama) is not net-new** — a full implementation already ships in `apps/frontend` and both backends (see §1 and the _flagged gaps_). Phase 5 is overwhelmingly a **consolidate + move-behind-ports + restyle-to-spec** effort, not a from-scratch build.

---

## 1. Context & problem

Desktop today boots `apps/desktop/renderer/bootstrap.tsx` → `ChatShell` with a single `<DesktopPlaceholder/>` child (`apps/desktop/renderer/DesktopPlaceholder.tsx`) and **no Settings surface at all** — `ChatShell`'s `onOpenSettings` prop is never passed on desktop (`grep onOpenSettings apps/desktop` → 0 hits). Meanwhile the web app owns a **1,390-line monolith** (`apps/frontend/src/features/settings/SettingsScreen.tsx`) that hard-codes a team-shaped IA (Account / **Workspace** / AI & data / Notifications) with admin sections (Members, Billing, Audit log, Workspace MFA) that must never appear in `single_user_desktop`. The section components it composes are real and mostly good (`sections/Profile.tsx`, `Appearance.tsx`, `Shortcuts.tsx`, `ProviderKeys.tsx`, `LocalModels.tsx`, `ModelAndBehavior.tsx`, `PrivacyAndData.tsx`, `Notifications.tsx`, `ToolUsePolicyPanel.tsx`), but they call `fetch`-backed API modules directly (`apps/frontend/src/api/{providerKeysApi,localModelsApi,meApi}.ts`), which **cannot live in `chat-surface`** (framework-agnostic invariant, PLAN.md §3).

This phase delivers the **solo Settings surface** per DESIGN-SPEC §4/§5: a 216px-nav settings module owned by `packages/chat-surface/src/settings` (the SSOT already seeded with `ProfilePage.tsx`, `NotificationsPage.tsx`, `QuietHoursEditor.tsx`, `WebhookSecurityPage.tsx`), each section its own component, all data access routed through the `Transport`/`SecretStorage`/`KeyValueStore` ports, profile-gated via the Phase-0D `DeploymentProfile` port. It builds on **Phase 0** (design-system v2 tokens 0B, `DeploymentProfile` port 0D, `chat-surface` module homes + ESLint boundary guard 0E) and **Phase 2** (rail foot Settings entry 2C, profile-gated `destinations.ts` 2B). It is the realization of the "Settings redo" (MEMORY: _project_settings_redo_).

Why now: Phases 0–2 give us tokens, the profile port, and a rail-foot Settings slot; Phase 5 fills the slot with a real, spec-faithful, framework-agnostic surface so the DoD line "Settings solo surface with BYOK + local models + approval policy; team features gated off" (PLAN.md §11) can be met before the Phase-6 live smoke.

---

## 2. Goals / Non-goals

### Goals

- A **`SettingsSurface`** module in `packages/chat-surface/src/settings` (nav SSOT), mounted on desktop via the rail-foot `onOpenSettings` slot, replacing `DesktopPlaceholder` for the settings route.
- Nav groups exactly per DESIGN-SPEC §4: **Account** (Profile · Appearance · Shortcuts), **Models & keys** (Provider keys `BYOK` · Local models · Model & behavior), **Data & privacy** (Privacy & retention), **Notifications**, **Advanced** collapsible (Key storage & app lock · Developer tokens). Each section its own component.
- **Profile-gate** Workspace / Members / Billing / Audit behind `deployment_profile === "team"`; solo footer copy from §4.
- **BYOK Provider keys** consolidated from `apps/frontend/src/features/settings/sections/ProviderKeys.tsx`, moved behind `Transport`+`SecretStorage`, with the **Add-a-provider-key** modal flow (3 StepDots, DESIGN-SPEC §5).
- **Local models** consolidated from the existing `sections/LocalModels.tsx` and restyled to spec (installed list with jade chip / "default local" chip / GPU-CPU placement; **Download-a-local-model** modal flow with progress + "use as default local").
- **Model & behavior**: default model select (Cloud · Local optgroups), reasoning depth, web access, **approval policy** (read / write / on-chain-spend-destructive), **spend guardrail** (monthly cap + pause-at-cap).
- **Data & privacy**: memory review/toggle, export, delete-all (danger), retention, "Open Activity" jump.
- **Notifications**: single consolidated per-event × channel grid (desktop / sound / email) + quiet hours.
- **Advanced**: Keychain/Touch-ID app-lock controls; developer (CLI) tokens.
- `savebar` dirty-state pattern (Discard / Save) + toast for one-shot actions; `Modal` + `StepDots` reusable flow chrome; all controls token-grounded to DESIGN-SPEC §0.
- `apps/frontend` stays **behaviorally identical** (its `SettingsScreen.tsx` keeps rendering through re-export shims where sections move; web typecheck + tests green).

### Non-goals (explicitly deferred)

- **Command palette entries** for settings ("Add a provider key", "Model & behavior", "Appearance") — **Phase 6A** (`⌘K`).
- **Keyboard-shortcut execution** of `⌘,` Settings / `⌘⇧M` local picker — registered in **Phase 6B**; Phase 5 only renders the read-only Shortcuts grid.
- **Dead-code removal** of `SettingsScreen.tsx` on the **web** side and `DesktopPlaceholder.tsx` — **Phase 6C** (`Remove dead code`). Phase 5 stops _mounting_ them on desktop but does not delete the web monolith.
- **New backend endpoints** beyond what already exists. Spend cap, app-lock, and default-model persistence reuse existing facade routes where present; where a route is missing it is a **stub with a flagged gap** (see §11), not new backend work in this phase.
- **Live-run BYOK/local-model exercise** end-to-end — that's the **Phase 6D** live smoke.
- Team admin surfaces (Members/Billing/Audit/Workspace-MFA) **content** — Phase 5 only _gates_ them off for solo; their team-profile behavior is unchanged.

---

## 3. User stories

Roles: **Solo user** (primary — `single_user_desktop`), **Team admin** (only where profile-gated), **Developer/maintainer** (DX/architecture).

| ID          | Story                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **US-5.1**  | _As a Solo user, I want a Settings surface I can open from the rail foot, so that I can configure the app without leaving the desktop shell._ **Given** desktop booted in `single_user_desktop`, **When** I click the rail-foot gear, **Then** the Settings surface mounts full-height (topbar suppressed, DESIGN-SPEC §1) with the 216px nav showing groups Account / Models & keys / Data & privacy / Notifications / Advanced and the solo footer "Solo desktop mode. Workspace, members & billing appear only when 0xCopilot is deployed for a team." **And** Workspace / Members / Billing / Audit nav items are **absent**. |
| **US-5.2**  | _As a Solo user, I want my Profile, Appearance, and Shortcuts under one Account group, so that personal settings are found first._ **Given** Settings open, **When** I select Appearance, **Then** I see Theme tiles (Dark / Light / System "Match macOS"), Accent swatches (sky/jade/ember/violet), Density (Comfortable/Compact/Spacious), Reduce-motion toggle; **When** I pick an accent, **Then** `[data-accent]` updates live and persists via preferences.                                                                                                                                                                 |
| **US-5.3**  | _As a Solo user, I want to add a provider API key through a guided flow, so that BYOK is obvious and safe._ **Given** Provider keys with an empty provider, **When** I click "Add key", **Then** a 3-step modal opens (enter key `sk-…` → "Validating with {provider}…" → choose default model → Add); **When** validation fails, **Then** the step shows an inline error and no key is stored; **When** it succeeds, **Then** the connected list shows logo + name + model chip + masked `key_hint` + Rotate / Remove, and the plaintext key is never re-displayed.                                                              |
| **US-5.4**  | _As a Solo user, I want to download and manage local models, so that I can run fully offline._ **Given** Local models with Ollama running, **When** I open "Get another model → Download", **Then** the flow picks a model (name·param, size·note) → progress bar (%) → "Ready to run locally" + "Use as default local model" toggle → Finish; **Given** Ollama not running, **Then** I see the install/re-check setup steps, not a broken list; **Given** a download interrupts, **Then** the row shows an ember error with a retry affordance.                                                                                  |
| **US-5.5**  | _As a Solo user, I want a default model and behavior controls, so that I steer the agent globally._ **Given** Model & behavior, **When** I open the Default model select, **Then** it groups **Cloud · your keys** and **Local · your machine** optgroups; **And** I can set Reasoning depth (Auto/Quick/Standard/Deep), Web access (toggle), Approval policy (Read-only: Auto-approve/Ask first; Write: Require/Ask/Auto/Block; On-chain/spend/destructive: Require/Block), and a Monthly API cap ($) with Pause-runs-at-cap toggle; **And** a note clarifies "_which_ tools = per-connector on the Connectors page".            |
| **US-5.6**  | _As a Solo user, I want privacy, retention, and memory controls in one place, so that I own my data._ **Given** Privacy & retention, **When** I set "Keep run history for" to 30 days, **Then** the value persists; **When** I click "Review N memories →", **Then** I reach the memory list; **When** I click "Export everything", **Then** an export is queued to `~/copilot/export` with a toast; **When** I click "Delete all history", **Then** a typed-confirmation danger dialog gates the destructive action.                                                                                                             |
| **US-5.7**  | _As a Solo user, I want one notifications surface, so that I stop juggling three overlapping panels._ **Given** Notifications, **Then** I see a per-event × channel grid (**desktop / sound / email**) for: Approval requested, Run finished, Run paused / needs input, Connector error, Spend threshold, Product updates; **And** a Quiet hours toggle with the copy "approval requests always break through"; **When** I toggle a cell, **Then** it persists and no separate legacy panel is shown.                                                                                                                             |
| **US-5.8**  | _As a Solo user, I want key-storage and app-lock controls, so that my keys and history are protected on-device._ **Given** Advanced → Key storage & app lock, **Then** I see the note "keys in macOS Keychain, encrypted at rest", an "Encrypt local run history" toggle, a "Require Touch ID to open" toggle, and "Lock after" (5 min / 15 min / 1 hour / Never). **Given** the platform can't provide Touch ID, **Then** that control is disabled with an explanatory hint rather than hidden abruptly.                                                                                                                         |
| **US-5.9**  | _As a Solo user, I want developer (CLI) tokens, so that the `copilot` CLI can authenticate locally._ **Given** Advanced → Developer tokens, **When** I click "Create a token", **Then** the token is shown once with "shown once, then keychain"; the list shows name / masked / last-used / Revoke.                                                                                                                                                                                                                                                                                                                              |
| **US-5.10** | _As a Team admin (team profile), I want Workspace / Members / Billing to appear, so that team administration still works._ **Given** `deployment_profile === "team"`, **When** I open Settings, **Then** the nav adds Workspace / Members / Billing (and Audit) groups and hides the solo footer; **Given** `single_user_desktop`, **Then** those items never render even if a deep-link slug targets them (unknown-slug → default section).                                                                                                                                                                                      |
| **US-5.11** | _As a Solo user, I want a clear dirty/save model, so that I never lose or accidentally apply edits._ **Given** a section with unsaved edits, **Then** a `savebar` shows "Unsaved changes · Discard / Save"; **When** I navigate away with unsaved edits, **Then** I'm warned (or edits are discarded per section contract); one-shot actions (export, rotate) confirm via toast, not the savebar.                                                                                                                                                                                                                                 |
| **US-5.12** | _As a Developer/maintainer, I want settings sections framework-agnostic in `chat-surface`, so that web and desktop share one copy._ **Given** the ESLint substrate rule (Phase 0E), **When** I build a settings section, **Then** it uses `Transport`/`SecretStorage`/`KeyValueStore` ports (no bare `fetch`/`window`/`localStorage`), and `apps/frontend` consumes the same component through a re-export shim with zero web behavior change.                                                                                                                                                                                    |
| **US-5.13** | _As a Solo user, on a fresh install with no keys/models, I want honest empty states, so that I know what to do next._ **Given** no provider keys, **Then** Provider keys shows every provider as an "Add key" row (not a blank card); **Given** no local models with Ollama running, **Then** Local models shows "No local models yet. Download one above."; **Given** a load error (facade unreachable), **Then** the section shows a role="alert" error + Retry, not a spinner forever.                                                                                                                                         |

---

## 4. Functional requirements

### A. Settings shell & IA (US-5.1, US-5.10, US-5.11)

- **FR-5.1** The Settings surface MUST render full-height with the shell topbar suppressed (DESIGN-SPEC §1: "Suppressed on Run and Settings").
- **FR-5.2** The nav MUST be 216px wide and render groups+items exactly: Account (Profile/Appearance/Shortcuts), Models & keys (Provider keys tag "BYOK"/Local models/Model & behavior), Data & privacy (Privacy & retention), Notifications, Advanced-collapsible (Key storage & app lock/Developer tokens).
- **FR-5.3** When `deployment_profile === "single_user_desktop"`, the nav MUST NOT render Workspace / Members / Billing / Audit items and MUST render the solo footer copy from DESIGN-SPEC §4.
- **FR-5.4** When `deployment_profile === "team"`, the nav MUST add Workspace / Members / Billing / Audit groups and MUST NOT render the solo footer.
- **FR-5.5** The active section MUST be derivable from and reflected to a nav-slug source of truth (one canonical slug union owned by `chat-surface/src/settings`), and an unknown/gated slug MUST resolve to the default section (`profile`) rather than error.
- **FR-5.6** Content max-width MUST be 620px; `.set-card`, `.set-note`, `.frow`, `.krow`, `.savebar` chrome MUST match DESIGN-SPEC §4 structure.
- **FR-5.7** A dirty section MUST surface a `savebar` ("Unsaved changes" Discard/Save); a completed one-shot action MUST surface a toast; the two MUST NOT be conflated.

### B. Account group (US-5.2)

- **FR-5.8** Profile MUST provide Display name, Working hours (9:00–18:00 / Anytime / Custom…), Time zone, and Cloud-sync toggle **off by default** with the "runs fully local; nothing leaves this device" copy.
- **FR-5.9** Appearance MUST provide Theme tiles (Dark / Light / System "Match macOS" — exactly 3; `slate` is legacy, round-tripped not surfaced, §5.5), Accent swatches (sky/jade/ember/violet — the reconciled 4-accent set per §5.5, **not** the current 9-entry `ACCENT_SCHEMES`), Density (Comfortable/Compact/Spacious), Reduce-motion toggle; each change MUST apply live via `[data-accent]`/`[data-density]`/`[data-reduce-motion]` and persist.
- **FR-5.9a** Because `UserProfileAccent` and `UserProfileDensity` do not yet carry the spec values (§5.5), Appearance MUST persist `spacious`/the four-accent set through the reconciled `api-types` contract **iff** Phase 0B/0C + the profile facade route accept it; otherwise it MUST fall back to a `KeyValueStore`-local pref that still sets the `[data-density]`/`[data-accent]` attribute live, and MUST record a flagged gap (§11) — it MUST NOT present an option that silently fails to persist.
- **FR-5.10** Shortcuts MUST render the read-only shortcut set from DESIGN-SPEC §6 (New run `⌘N`, palette `⌘K`, Approve `⌘↵`, Reject `⌘⌫`, Pause `⌘.`, Rewind `⌘←`, Step `⌘→`, Live `⌘L`, Mode `⌘M`, Local picker `⌘⇧M`, Settings `⌘,`, Search activity `⌘⇧F`).

### C. Models & keys (US-5.3, US-5.4, US-5.5, US-5.13)

- **FR-5.11** Provider keys MUST list connected providers (logo + name + model chip + masked `key_hint` + Rotate/Remove) and empty providers as "Add key" rows; supported providers MUST include Anthropic, OpenAI, OpenRouter, Google AI (and, per DESIGN-SPEC §4, Groq + xAI — see flagged gap) plus the "Any OpenAI-compatible endpoint works too" affordance.
- **FR-5.12** The Add-provider-key flow MUST be a modal with 3 StepDots (enter key → validate with spinner "Validating with {provider}…" → choose default model → Add), MUST store the plaintext key exactly once (PUT body), and MUST never re-display plaintext (reads carry only `key_hint`).
- **FR-5.13** Provider keys MUST show the note "Keys are encrypted at rest in your local vault and never sent to a 0xCopilot server." _(Amended 2026-07-20: the original "macOS Keychain" wording was factually wrong — keys are TokenVault-encrypted in the local database; OS-keychain protection is the separate opt-in shipped in #124. UI copy must never claim keychain storage for provider keys.)_
- **FR-5.14** Local models MUST show three states: Ollama-not-running setup steps; Ollama-running installed list (jade chip logo, name·param, "default local" chip, size, Run/Delete) + "Get another model → Download".
- **FR-5.15** The Download-a-local-model flow MUST be a modal (pick model → progress bar % with size/speed/ETA → "Ready to run locally" + "Use as default local model" toggle → Finish) and MUST surface an ember error on interruption/not-found without wedging the surface.
- **FR-5.16** Model & behavior MUST provide: Default model select with **Cloud · your keys** / **Local · your machine** optgroups; Reasoning depth (Auto/Quick/Standard/Deep); Web access toggle.
- **FR-5.17** Model & behavior MUST provide an Approval policy block: Read-only (Auto-approve / Ask first); Write (Require approval / Ask first / Auto-approve / Block); On-chain, spend & destructive (Require approval / Block); with the note that _which_ tools is per-connector on the Connectors page.
- **FR-5.18** Model & behavior MUST provide a Spend guardrail: Monthly API cap ($ input, "across all provider keys") + Pause-runs-at-cap toggle.

### D. Data & privacy (US-5.6)

- **FR-5.19** Privacy & retention MUST provide: "Keep run history for" (Forever/90/30/7 days); "Open Activity" button; Memory toggle + "Review N memories →"; "Export everything" (→ `~/copilot/export`); "Delete all history" (danger, typed-confirmation).
- **FR-5.20** Delete-all MUST require typed confirmation and MUST NOT execute an irreversible delete without it (matches the existing PrivacyAndData typed-confirmation contract).

### E. Notifications (US-5.7)

- **FR-5.21** Notifications MUST render a single per-event × channel grid (desktop/sound/email) for: Approval requested, Run finished, Run paused / needs input, Connector error, Spend threshold, Product updates. The shipped `NotificationDefaults.destinations_enabled` contract is per-_destination_ on/off with **no channel or event axis** (§5.5); PR-5.8 MUST therefore either widen `settings.ts` + the facade enum to an event×channel model, or persist the grid as `KeyValueStore`-local prefs with a flagged gap (§11). It MUST NOT relabel the existing per-destination toggles as if they were the event×channel grid.
- **FR-5.22** Notifications MUST provide a Quiet hours toggle with "approval requests always break through" copy, and MUST be the _only_ notifications surface (no legacy `NotificationsV2Panel`/`NotificationDefaultsPanel` shown alongside on desktop).

### F. Advanced (US-5.8, US-5.9)

- **FR-5.23** Key storage & app lock MUST provide: the Keychain note; "Encrypt local run history" toggle; "Require Touch ID to open" toggle (disabled-with-hint when unsupported); "Lock after" (5 min / 15 min / 1 hour / Never).
- **FR-5.24** Developer tokens MUST list local CLI tokens (name / masked / last-used / Revoke) and a "Create a token" affordance that shows the token once ("shown once, then keychain").
- **FR-5.25** The Advanced group MUST be collapsible.

### G. Architecture / boundaries (US-5.12)

- **FR-5.26** All Phase-5 settings section components that land in `packages/chat-surface/src/settings` MUST access data only through ports (`Transport`, `SecretStorage`, `KeyValueStore`, `NotificationPort`) — no bare `fetch`/`window`/`document`/`localStorage` — enforced by the Phase-0E ESLint substrate rule.
- **FR-5.27** `apps/frontend` MUST consume moved sections via re-export shims (from `@0x-copilot/chat-surface`), keeping `SettingsScreen.tsx` behavior identical and web tests green.
- **FR-5.28** The Settings surface MUST be wired on desktop through `ChatShell`'s `onOpenSettings` slot (`packages/chat-surface/src/shell/ChatShell.tsx`) and MUST NOT import `apps/frontend/src/**`.

**FR → story → test map** is in §8 (each FR has ≥1 named test).

---

## 5. Architecture & system design

### 5.1 Single source of truth

- **Settings module SSOT → `packages/chat-surface/src/settings`.** It already owns `ProfilePage.tsx`, `NotificationsPage.tsx`, `QuietHoursEditor.tsx`, `WebhookSecurityPage.tsx` and re-exports via `packages/chat-surface/src/settings/index.ts`. Phase 5 promotes it to the **canonical settings home**: a new `SettingsSurface` (nav + content router) plus one component per section. The **nav slug union + default** (today duplicated in `apps/frontend/src/features/settings/sections.ts` and the `SettingsSection` type in `SettingsScreen.tsx:69`) becomes a single `settingsNav.ts` in the module, **profile-gated**.
- **Consolidated / removed duplication:**
  - `SettingsScreen.tsx` (1,390 lines) stops being the desktop settings composer; on desktop it is replaced by `SettingsSurface`. It remains the web composer until Phase 6C, re-exporting moved sections so there is one component implementation, not two.
  - The three overlapping notification surfaces (`sections/Notifications.tsx`, `sections/NotificationsV2Panel.tsx`, `NotificationDefaultsPanel.tsx`) collapse to one `chat-surface/src/settings/NotificationsPage.tsx` (DESIGN-SPEC §4: "replacing the three overlapping notification panels"). **Caveat (see §5.5):** the current `NotificationsPage` is a per-_destination_ on/off grid over `NotificationDefaults.destinations_enabled` with My/Workspace tabs — it does **not** yet carry the spec's event×channel (desktop/sound/email) model. Phase 5 **reworks** it, not merely reuses it; PR-5.8 owns the data-model change and the flagged contract gap.
  - Approval policy: the existing `sections/ToolUsePolicyPanel.tsx` (read/write/destructive × auto/ask/require/block, via `/v1/me/policies/tool-use`) is the **canonical approval-policy control**, moved into the Model & behavior section — not re-implemented.
- **Design tokens SSOT → `packages/design-system`.** All settings chrome (`.set-card`/`.frow`/`.krow`/`.savebar`/`.seg`/`.swatch`/`.theme-tile`/`.ctog`/`.csel`/`.bar`) MUST resolve to design-system v2 tokens (DESIGN-SPEC §0). No second CSS system; the current ad-hoc `apps/frontend/src/styles.css` `.settings-*` classes and `settings/workspace.css` are token-reconciled, not forked.

### 5.2 Boundaries & ports (respect PLAN.md §3 / CLAUDE.md)

- No `apps/*` imports another app's `src/`. Desktop mounts `SettingsSurface` from `@0x-copilot/chat-surface`, never from `apps/frontend`.
- `chat-surface` stays **framework-agnostic**. Ports used by Phase 5:
  - **`Transport`** (`packages/chat-surface/src/ports/Transport.ts`, typed `request()`/`TypedRequest`/`HttpMethod` re-exported from `@0x-copilot/chat-transport`) — all facade `/v1/*` reads/writes (provider keys, local models, workspace defaults, retention, memory, notifications, tool-use policy, CLI tokens). This **replaces** the `fetch`-backed `apps/frontend/src/api/{providerKeysApi,localModelsApi,meApi,workspaceApi,memoryApi}.ts` calls, which are web-only and must not move into `chat-surface`. A thin `settings/data/*` layer in the module builds `TypedRequest`s and calls the injected `Transport`.
  - **`SecretStorage`** (`packages/chat-surface/src/providers/SecretStorageProvider.tsx` → `storage/secret-storage`) — the app-lock / keychain surface reads capability + never handles plaintext keys itself (BYOK plaintext still goes only in the PUT body to the facade, per `ProviderKeys.tsx` invariant).
  - **`KeyValueStore`** (`ports/KeyValueStore.ts`) — UI-local prefs (Advanced-group collapsed state, last-open section) that must not be server round-trips.
  - **`NotificationPort`** (`ports/NotificationPort.ts`) — used only to _describe/preview_ channels; actual delivery config persists via `Transport`.
  - **`DeploymentProfile` port (Phase 0D)** — the gate for §5.1 nav gating. **Dependency:** the client-side port is introduced by Phase 0D; the backend concept already exists (`packages/service-contracts/src/copilot_service_contracts/deployment_profile.py`, `services/backend-facade/src/backend_facade/deployment_profile.py`, `services/backend/src/backend_app/desktop_app.py`). If 0D lands the port as a React context/hook, Phase 5 consumes it; **if 0D is not yet merged, PR-5.1 is blocked** (see §10).

### 5.3 Data flow & key types

- **Read path:** `SettingsSurface` → section component → `settings/data/<x>.ts` builds `TypedRequest` → injected `Transport.request()` → facade `/v1/*` → typed response from `@0x-copilot/api-types`.
- **Named contracts (real, verified in `packages/api-types/src`):**
  - Per-domain files: `providerKeys.ts` (`ProviderKeyProvider` = `openai|anthropic|google|openrouter` — **4 values, no Groq/xAI**, see drift §5.5 / gap #5; `ProviderKeySummary.key_hint`, `ListProviderKeysResponse`, `PutProviderKeyRequest`); `localModels.ts` (`LocalModelsStatus.ollama_running`, `LocalModelSummary.{size_bytes,quantization,parameter_size,run_placement}`, `LocalModelRunPlacement`, `LocalModelPullEvent`); `settings.ts` (`NotificationDefaults.destinations_enabled: PerDestinationToggle` — **per-destination on/off, NO channel axis**; `NotificationQuietHoursBlob`, `UpdateNotificationDefaultsRequest`, `WorkspaceNotificationDefaults`).
  - Defined directly in `packages/api-types/src/index.ts` (not per-domain files; verified by line): `ToolUsePolicyResponse` (`index.ts:3173`), `UpdateWorkspaceDefaultsRequest` (`:588`) / `WorkspaceBehaviorOverrides` (`:637`), `RetentionEffectiveResponse` (`:672`), `AppearancePreferences` (`:2867`) with `UserProfileTheme` (`:2850` = `system|light|dark|slate`), `UserProfileAccent` (`:2853` = `sky|atlas-orange|gold|amber|red|lime|teal|blue|violet`), `UserProfileDensity` (`:2864` = `comfortable|compact`), `UserProfileReduceMotion` (`:2865` = `auto|always|off`), `UpdateUserProfileRequest` (`:2917`), `ApiKeySummary` (`:3216`) / `CreateApiKeyResponse` (`:3237`) (developer tokens).
- **These enums do not yet match DESIGN-SPEC §0/§4 — see the contract-drift table §5.5.** Every Phase-5 section that persists one of them either (a) consumes the reconciled contract landed by Phase 0B/0C, or (b) surfaces the extra option as a `KeyValueStore`-local pref with a flagged gap — never silently drops or fakes it.
- **Nav model:** `SettingsNavItem = { id: SettingsSectionSlug; label; icon; tag?; group; profileGate?: "team" }`; `SettingsSectionSlug` is the SSOT union.
- **Save model:** section holds a `draft` + `dirty` boolean → `savebar` → `Transport` write → toast/rollback. Reuse the debounced-persist pattern already in `ModelAndBehavior.tsx` (`SAVE_DEBOUNCE_MS = 300`).

### 5.4 Reuse vs new

| Component / module                                                      | Disposition                                                                                                                    | Path                                                                                                          |
| ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------- |
| `SettingsSurface` (nav + content router + savebar host)                 | **New**                                                                                                                        | `packages/chat-surface/src/settings/SettingsSurface.tsx`                                                      |
| Settings nav SSOT (slug union + profile gate)                           | **New** (replaces the two duplicated lists)                                                                                    | `packages/chat-surface/src/settings/settingsNav.ts`                                                           |
| `Modal` + `StepDots` flow chrome                                        | **New** (generalize `apps/frontend/src/features/settings/Modal.tsx`)                                                           | `packages/chat-surface/src/settings/Modal.tsx`                                                                |
| Settings chrome CSS (`.set-card`/`.frow`/`.krow`/`.savebar`/…) → tokens | **New (token-grounded)**                                                                                                       | `packages/design-system/src/styles.css` (+ `chat-surface` consumes)                                           |
| `Transport`-backed data layer for settings                              | **New**                                                                                                                        | `packages/chat-surface/src/settings/data/*.ts`                                                                |
| Profile page                                                            | **Reuse**                                                                                                                      | `packages/chat-surface/src/settings/ProfilePage.tsx`                                                          |
| Notifications page (consolidated)                                       | **Rework** (per-destination toggle → per-event×channel grid; drop My/Workspace tabs on solo) — _not a drop-in reuse, see §5.5_ | `packages/chat-surface/src/settings/NotificationsPage.tsx`                                                    |
| Quiet hours editor                                                      | **Reuse**                                                                                                                      | `packages/chat-surface/src/settings/QuietHoursEditor.tsx`                                                     |
| Appearance                                                              | **Move** (behind ports)                                                                                                        | `apps/frontend/.../sections/Appearance.tsx` → `packages/chat-surface/src/settings/AppearancePage.tsx`         |
| Shortcuts (read-only grid)                                              | **Move**                                                                                                                       | `apps/frontend/.../sections/Shortcuts.tsx` → `packages/chat-surface/src/settings/ShortcutsPage.tsx`           |
| Provider keys (BYOK)                                                    | **Move** (fetch→Transport)                                                                                                     | `apps/frontend/.../sections/ProviderKeys.tsx` → `packages/chat-surface/src/settings/ProviderKeysPage.tsx`     |
| Local models                                                            | **Move** (fetch→Transport, restyle)                                                                                            | `apps/frontend/.../sections/LocalModels.tsx` → `packages/chat-surface/src/settings/LocalModelsPage.tsx`       |
| Approval policy control                                                 | **Move + relocate** into Model & behavior                                                                                      | `apps/frontend/.../sections/ToolUsePolicyPanel.tsx` → `packages/chat-surface/src/settings/ApprovalPolicy.tsx` |
| Model & behavior (default model/depth/web/spend)                        | **New around reused policy**                                                                                                   | `packages/chat-surface/src/settings/ModelAndBehaviorPage.tsx`                                                 |
| Privacy & retention                                                     | **Move**                                                                                                                       | `apps/frontend/.../sections/PrivacyAndData.tsx` → `packages/chat-surface/src/settings/PrivacyPage.tsx`        |
| Key storage & app lock                                                  | **New**                                                                                                                        | `packages/chat-surface/src/settings/AppLockPage.tsx`                                                          |
| Developer tokens                                                        | **Move (Personal tab only)**                                                                                                   | `apps/frontend/.../sections/ApiKeys.tsx` → `packages/chat-surface/src/settings/DeveloperTokensPage.tsx`       |
| Workspace/Members/Billing/Audit                                         | **Reuse, gate off solo**                                                                                                       | stay in `apps/frontend/.../settings/*` (team-profile only)                                                    |
| Web shim re-exports                                                     | **New (thin)**                                                                                                                 | `apps/frontend/src/features/settings/sections/*` re-export from `@0x-copilot/chat-surface`                    |
| Desktop mount                                                           | **Modify**                                                                                                                     | `apps/desktop/renderer/bootstrap.tsx` (`onOpenSettings` → `SettingsSurface`)                                  |

### 5.5 Known contract drift (spec vs. shipped `api-types`) — reconcile in-PR or flag, never fake

Read from the actual sources; each row is a place where DESIGN-SPEC demands a value the shipped contract does not yet carry. Resolution rule: **widen the contract only if the facade/backend accepts the value in the same PR; otherwise render the extra option as a `KeyValueStore`-local pref (client-only) with a flagged gap (§11) — no silent no-op that looks live.**

| Concept                 | DESIGN-SPEC demands                                                                                                                                                 | Shipped contract (verified)                                                                                                                                                                                                               | Resolution in Phase 5                                                                                                                                                                                                                                                                                                                                                                  |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Accent set**          | sky · jade · ember · violet (single-accent discipline, §0)                                                                                                          | `UserProfileAccent` (`index.ts:2853`) = sky · atlas-orange · gold · amber · red · lime · teal · blue · violet (9, v1); `ACCENT_SCHEMES` (`design-system/src/index.tsx:45`) mirrors the same 9                                             | **Blocked on Phase 0B/0C** reconciling `ACCENT_SCHEMES` **+** `UserProfileAccent` to the 4-accent set. PR-5.3 consumes the reconciled list; if 0B/0C has not narrowed it, `AppearancePage` renders only the four spec swatches (sky/jade/ember/violet) from `ACCENT_SCHEMES` and flags the remainder. **Do NOT claim `ACCENT_SCHEMES` already = sky/jade/ember/violet — it does not.** |
| **Density options**     | Comfortable · Compact · **Spacious** (`[data-density=spacious]` token exists, §0)                                                                                   | `UserProfileDensity` (`index.ts:2864`) = comfortable · compact (**no `spacious`**)                                                                                                                                                        | PR-5.3 widens the union + `UpdateUserProfileRequest` **iff** the profile facade route accepts it; else `spacious` persists as a `KeyValueStore`-local pref that still sets `[data-density=spacious]` live, flagged (§11). FR-5.9 acceptance splits accordingly.                                                                                                                        |
| **Theme tiles**         | Dark · Light · System (3 tiles, §4)                                                                                                                                 | `UserProfileTheme` (`index.ts:2850`) = system · light · dark · **slate** (4)                                                                                                                                                              | Appearance surfaces exactly the 3 spec tiles; `slate` is a legacy value — round-tripped if already set, no tile added.                                                                                                                                                                                                                                                                 |
| **Notifications model** | per-**event** {Approval requested, Run finished, Run paused, Connector error, Spend threshold, Product updates} × per-**channel** {desktop, sound, email} grid (§4) | `NotificationDefaults.destinations_enabled: PerDestinationToggle` (`settings.ts`) = flat per-**destination** on/off (chats/runs/approvals/inbox/…); **no channel axis, no event axis, plus My/Workspace tabs** in `NotificationsPage.tsx` | **Not a drop-in reuse.** `NotificationsPage` is reworked to the event×channel grid (§5.1, PR-5.8). The channel dimension has no backend contract; PR-5.8 either lands a widened `settings.ts` contract + facade enum **or** persists event×channel as `KeyValueStore`-local prefs, flagged (§11, risk N).                                                                              |
| **Provider set**        | Anthropic · OpenAI · OpenRouter · Google AI · **Groq** · **xAI** (§4)                                                                                               | `ProviderKeyProvider` = openai · anthropic · google · openrouter (4)                                                                                                                                                                      | PR-5.4 adds Groq/xAI to the union + facade **iff** the backend accepts them; else render as the "Any OpenAI-compatible endpoint" custom row, flagged (§11).                                                                                                                                                                                                                            |

---

## 6. Affected files / component inventory

### Create

- `packages/chat-surface/src/settings/SettingsSurface.tsx` — nav + content router + savebar/toast host.
- `packages/chat-surface/src/settings/settingsNav.ts` — slug union SSOT + profile-gated nav model + default section.
- `packages/chat-surface/src/settings/Modal.tsx` — `Modal` + `StepDots` flow chrome (DESIGN-SPEC §5).
- `packages/chat-surface/src/settings/AppearancePage.tsx`, `ShortcutsPage.tsx`, `ProviderKeysPage.tsx`, `LocalModelsPage.tsx`, `ModelAndBehaviorPage.tsx`, `ApprovalPolicy.tsx`, `PrivacyPage.tsx`, `AppLockPage.tsx`, `DeveloperTokensPage.tsx`.
- `packages/chat-surface/src/settings/AddProviderKeyModal.tsx`, `DownloadLocalModelModal.tsx` (flow modals, DESIGN-SPEC §5).
- `packages/chat-surface/src/settings/data/{providerKeys,localModels,workspaceDefaults,retention,memory,notifications,toolUsePolicy,developerTokens}.ts` — `Transport`-backed data layer.
- Test files colocated: `*.test.tsx` for `SettingsSurface`, `settingsNav`, each new page, and both flow modals (vitest).
- `apps/desktop/SMOKE.md` — add the Settings smoke steps (append; file exists per CLAUDE.md).

### Modify

- `packages/chat-surface/src/settings/index.ts` — export `SettingsSurface`, nav, modals, moved pages.
- `packages/chat-surface/src/shell/ChatShell.tsx` — ensure `onOpenSettings` renders `SettingsSurface` region full-height (topbar suppressed) on desktop; no `apps/frontend` import.
- `apps/desktop/renderer/bootstrap.tsx` — pass `onOpenSettings`, mount `SettingsSurface` in place of `DesktopPlaceholder` for the settings route; wire ports (`SecretStorageProvider` real store).
- `packages/design-system/src/styles.css` — add token-grounded settings chrome classes.
- `apps/frontend/src/features/settings/sections/{Appearance,Shortcuts,ProviderKeys,LocalModels,ToolUsePolicyPanel,PrivacyAndData}.tsx` — become thin re-exports from `@0x-copilot/chat-surface` (web behavior identical).
- `apps/frontend/src/features/settings/SettingsScreen.tsx` — nav `railSections` gated by profile so web team build is unchanged but solo profile hides admin (defensive; primary gate is the desktop `SettingsSurface`).

### Delete

- **None in Phase 5.** `DesktopPlaceholder.tsx`, the web `SettingsScreen.tsx` monolith, and the duplicated `sections.ts` slug list are removed in **Phase 6C** after the desktop surface is proven. (Flagged so no dead code is _left by_ Phase 5 without a scheduled owner.)

---

## 7. PR / commit breakdown

Ordered; each ≤ ~1000 LOC, independently reviewable, leaves web typecheck + tests green. **PR-5.1 and PR-5.2 are prerequisites for all section PRs; PR-5.9 wires everything last.** The two **L** PRs (PR-5.5 local models + download flow; PR-5.6 model & behavior + relocated policy + spend) are the LOC risk: if either exceeds ~1000 LOC in review, split along the seam noted in its scope (5.5 → page-move / download-modal; 5.6 → default-model+depth+web / relocated `ApprovalPolicy` / spend-guardrail) into `PR-5.5a/5.5b`, `PR-5.6a/5.6b/5.6c` — same deps, same acceptance rows apply to the union.

| PR         | Title                                            | Scope                                                                                                                                                                                                                                                                                                                                  | Files                                                                                                                                   | Upstream deps                                                          | Acceptance                                                                                                                                                                                                                                                                                                                            | Size |
| ---------- | ------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---- |
| **PR-5.1** | Settings shell + nav SSOT + profile gate         | `SettingsSurface` (nav 216px, content router, savebar/toast host, Advanced-collapsible), `settingsNav.ts` (slug union + profile gate + default), consume `DeploymentProfile` port; solo footer.                                                                                                                                        | `chat-surface/src/settings/{SettingsSurface,settingsNav}.tsx/ts` + tests; `index.ts`                                                    | Phase 0D (profile port), 0E (module homes/ESLint), 2C (rail-foot slot) | Nav renders solo groups only when `single_user_desktop`; team adds Workspace/Members/Billing; unknown slug → `profile`; footer copy exact; topbar suppressed.                                                                                                                                                                         | M    |
| **PR-5.2** | Settings design primitives (tokens)              | `Modal`+`StepDots`; `.set-card`/`.set-note`/`.frow`/`.krow`/`.savebar`/`.seg`/`.swatch`/`.theme-tile`/`.ctog`/`.csel`/`.bar` grounded to design-system v2 tokens.                                                                                                                                                                      | `chat-surface/src/settings/Modal.tsx`+test; `design-system/src/styles.css`                                                              | 0B (v2 tokens)                                                         | Chrome matches DESIGN-SPEC §0 dims/hex in light+dark; `Modal` 500px, focus-trapped, ESC-close; StepDots reflect step.                                                                                                                                                                                                                 | S    |
| **PR-5.3** | Account group (Profile · Appearance · Shortcuts) | Move Appearance+Shortcuts behind ports; reuse `ProfilePage`; wire into surface. Reconcile accent set to 4 + handle `spacious` per §5.5. Web shims.                                                                                                                                                                                     | `chat-surface/src/settings/{AppearancePage,ShortcutsPage}.tsx`+tests; `apps/frontend/.../sections/{Appearance,Shortcuts}.tsx`→shim      | PR-5.1, PR-5.2, Phase 0B/0C (accent reconcile)                         | Theme tiles = 3 (slate legacy); accent swatches = sky/jade/ember/violet only; density incl. Spacious applies live (persist via reconciled contract **or** `KeyValueStore` fallback + flagged gap per FR-5.9a); reduce-motion applies live+persist; Shortcuts grid = §6 set; web Appearance/Shortcuts unchanged (existing tests pass). | M    |
| **PR-5.4** | Provider keys (BYOK) + Add-key flow              | Move `ProviderKeys` behind `Transport`; `AddProviderKeyModal` (3 StepDots, validate spinner, choose default model); keychain note; empty "Add key" rows.                                                                                                                                                                               | `chat-surface/src/settings/{ProviderKeysPage,AddProviderKeyModal}.tsx`+tests; `data/providerKeys.ts`; web shim                          | PR-5.1, PR-5.2                                                         | Add/rotate/remove via Transport; plaintext only in PUT; masked hint on reads; `ProviderKeys.test.tsx` parity ported.                                                                                                                                                                                                                  | M    |
| **PR-5.5** | Local models + Download flow                     | Move `LocalModels` behind `Transport`; restyle to spec (jade chip / default-local chip / GPU-CPU); `DownloadLocalModelModal` (progress %, size/speed/ETA, "use as default local"); Ollama-not-running setup + interrupt error.                                                                                                         | `chat-surface/src/settings/{LocalModelsPage,DownloadLocalModelModal}.tsx`+tests; `data/localModels.ts`; web shim                        | PR-5.1, PR-5.2                                                         | Three states render; SSE pull streams progress; interrupt→ember; `LocalModels.test.tsx` parity ported.                                                                                                                                                                                                                                | L    |
| **PR-5.6** | Model & behavior                                 | Default model select (Cloud/Local optgroups) + Reasoning depth + Web access; relocate `ToolUsePolicyPanel`→`ApprovalPolicy` (read/write/danger); Spend guardrail (monthly cap + pause).                                                                                                                                                | `chat-surface/src/settings/{ModelAndBehaviorPage,ApprovalPolicy}.tsx`+tests; `data/{workspaceDefaults,toolUsePolicy}.ts`; web shim      | PR-5.1, PR-5.2, PR-5.4, PR-5.5 (optgroup sources)                      | Optgroups sourced from provider keys + local models; policy persists via `/v1/me/policies/tool-use`; spend-cap stub flagged if no route.                                                                                                                                                                                              | L    |
| **PR-5.7** | Data & privacy                                   | Move `PrivacyAndData`; retention select; memory toggle + "Review N →"; export→toast; delete-all typed-confirm; "Open Activity" jump.                                                                                                                                                                                                   | `chat-surface/src/settings/PrivacyPage.tsx`+test; `data/{retention,memory}.ts`; web shim                                                | PR-5.1, PR-5.2                                                         | Retention persists; delete-all gated by typed confirm; memory link routes; `PrivacyAndData.test.tsx` parity ported.                                                                                                                                                                                                                   | M    |
| **PR-5.8** | Notifications (rework to event×channel)          | Rework `NotificationsPage` from per-destination on/off (`destinations_enabled`) to per-event×channel grid (desktop/sound/email) + quiet hours via `QuietHoursEditor`; drop My/Workspace tabs on solo; remove legacy panels from desktop composition. Widen `settings.ts` + facade enum **or** `KeyValueStore`-local fallback per §5.5. | `chat-surface/src/settings/NotificationsPage.tsx` (data-model rework), `data/notifications.ts`+tests                                    | PR-5.1, PR-5.2                                                         | Six events × three channels persist (via widened contract or flagged local fallback — no relabeled destination toggles); quiet-hours break-through copy; single surface only; existing `NotificationsPage.test.tsx` updated for the new model.                                                                                        | M    |
| **PR-5.9** | Advanced + gating + desktop wire                 | `AppLockPage` (keychain note, encrypt-history, Touch-ID, lock-after) + `DeveloperTokensPage` (personal tab of `ApiKeys`); confirm team gating; wire `onOpenSettings`→`SettingsSurface` in `bootstrap.tsx` (replace `DesktopPlaceholder` for settings).                                                                                 | `chat-surface/src/settings/{AppLockPage,DeveloperTokensPage}.tsx`+tests; `apps/desktop/renderer/bootstrap.tsx`; `apps/desktop/SMOKE.md` | PR-5.1..5.8                                                            | Touch-ID disabled-with-hint when unsupported; token shown once; desktop rail-foot gear opens Settings; live boot renders surface (smoke).                                                                                                                                                                                             | M    |

---

## 8. Testing plan

Runner rules (CLAUDE.md): TS via `npm run test --workspace @0x-copilot/chat-surface` (and `--workspace @0x-copilot/frontend` for shims) — **vitest**; Python via the owning service `.venv` **pytest**; live smoke per `apps/desktop/SMOKE.md`.

### Unit (vitest — `packages/chat-surface`)

- `settings/settingsNav.test.ts` — **FR-5.3/5.4/5.5**: given `single_user_desktop`, nav excludes `workspace|members|billing|audit`; given `team`, includes them; unknown slug resolves to `profile`; solo footer present only for solo. _(assert on the returned nav array + resolver, not the DOM.)_
- `settings/SettingsSurface.test.tsx` — **FR-5.1/5.2/5.6/5.7/5.25**: renders 216px nav groups in order; content max-width 620; Advanced group toggles collapsed/expanded; a dirty section shows `savebar` with Discard/Save; a one-shot action fires a toast not the savebar; topbar-suppressed contract asserted via the region role.
- `settings/Modal.test.tsx` — **FR-5.12/5.15**: 500px modal, focus-trapped, ESC closes, StepDots advance 1→2→3, backdrop click behavior.
- `settings/AppearancePage.test.tsx` — **FR-5.9/5.9a**: exactly 3 theme tiles render (slate not surfaced); accent swatches render only sky/jade/ember/violet (assert the 9-entry `ACCENT_SCHEMES` is filtered to 4); selecting accent sets `[data-accent]`; density incl. Spacious sets `[data-density=spacious]` live; reduce-motion sets `[data-reduce-motion]`; when the profile contract rejects `spacious`/an accent, the value persists via the `KeyValueStore` fallback (mock) and still sets the attribute — no silent drop.
- `settings/ShortcutsPage.test.tsx` — **FR-5.10**: renders all 12 §6 chords read-only.
- `settings/ProviderKeysPage.test.tsx` — **FR-5.11/5.13**: empty providers render "Add key"; saved provider shows `key_hint`+Rotate/Remove; no plaintext reveal affordance; keychain note present. _(port assertions from existing `ProviderKeys.test.tsx`.)_
- `settings/AddProviderKeyModal.test.tsx` — **FR-5.12**: happy path stores once via mocked `Transport`; validation-failure step shows `role="alert"` and does not PUT.
- `settings/LocalModelsPage.test.tsx` — **FR-5.14**: Ollama-not-running→setup steps; running+empty→"No local models yet"; running+list→jade chip/default-local chip/placement label. _(port from `LocalModels.test.tsx`.)_
- `settings/DownloadLocalModelModal.test.tsx` — **FR-5.15**: streamed `LocalModelPullEvent`s drive the bar; interrupt→ember error; "use as default local" toggle in the ready step.
- `settings/ModelAndBehaviorPage.test.tsx` — **FR-5.16/5.18**: default-model select has Cloud+Local optgroups sourced from mocked keys/models; reasoning-depth 4 options; web toggle persists; monthly cap + pause persist. _(extend existing `ModelAndBehavior.test.tsx`.)_
- `settings/ApprovalPolicy.test.tsx` — **FR-5.17**: read/write/danger axes with the correct mode option sets; persists via mocked `/v1/me/policies/tool-use`.
- `settings/PrivacyPage.test.tsx` — **FR-5.19/5.20**: retention select persists; delete-all blocked until typed-confirm matches; export fires toast; "Review N memories" link routes. _(port from `PrivacyAndData.test.tsx`.)_
- `settings/NotificationsPage.test.tsx` — **FR-5.21/5.22**: six events × three channels persist; quiet-hours break-through copy; single surface. _(extend existing `NotificationsPage.test.tsx`.)_
- `settings/AppLockPage.test.tsx` — **FR-5.23**: keychain note; encrypt/Touch-ID/lock-after controls; Touch-ID disabled-with-hint when capability absent (mock `SecretStorage`/capabilities).
- `settings/DeveloperTokensPage.test.tsx` — **FR-5.24**: create shows token once; list shows masked/last-used/Revoke.
- `settings/data/*.test.ts` — **FR-5.26**: each data module builds the expected `TypedRequest` (method/path/body) and calls the injected `Transport` — proving no bare `fetch`.

### Integration (vitest, mocked transport)

- `settings/SettingsSurface.integration.test.tsx` — navigate nav → each section mounts and reads via one mocked `Transport`; profile switch (`single_user_desktop`↔`team`) re-renders nav; deep-link to a gated slug under solo falls back to `profile`. **FR-5.3/5.4/5.5/5.28**.
- **Web regression (vitest — `@0x-copilot/frontend`)**: existing settings tests MUST pass **unchanged** after each move (shims preserve behavior) — verified paths: `apps/frontend/src/features/settings/sections/{ProviderKeys,LocalModels,ModelAndBehavior,PrivacyAndData}.test.tsx`, `apps/frontend/src/features/settings/__tests__/SettingsGateway.test.tsx` (note: under `__tests__/`, not the section dir), `apps/frontend/src/features/settings/useWorkspaceDefaults.test.tsx`, `apps/frontend/src/features/settings/AuditLogSettings.test.tsx`. **FR-5.27**.
- ESLint substrate guard (Phase 0E rule) run over `chat-surface/src/settings/**` MUST report zero bare `fetch`/`window`/`localStorage`. **FR-5.26**.

### Python (pytest — owning services)

- Local-models routes remain green (no new backend, but Transport wiring exercises them): `cd services/ai-backend && .venv/bin/python -m pytest tests/unit/runtime_api/test_local_models_routes.py tests/unit/runtime_api/test_local_models_service.py`.
- Facade settings/local-models routes: `cd services/backend-facade && .venv/bin/python -m pytest` targeting `settings_routes.py` / `local_models_routes.py` (regression only). Deployment-profile gating: `services/backend/tests/test_deployment_profile.py`, `test_desktop_app.py` stay green.

### E2E / live desktop smoke (`apps/desktop/SMOKE.md`, staged runtime)

Per CLAUDE.md desktop recipe (`node tools/desktop-runtime/stage.mjs …` then `COPILOT_RUNTIME_DIR=… npm run dev --workspace @0x-copilot/desktop`):

1. Boot desktop → sign in → rail-foot **gear** opens Settings full-height, topbar suppressed, solo footer visible, no Workspace/Members/Billing. **FR-5.1/5.3**
2. Appearance → switch accent sky→jade→ember→violet; toggle density/reduce-motion → applies live. **FR-5.9**
3. Provider keys → Add key flow → paste a real key → validating spinner → choose default model → Add → list shows masked hint. **FR-5.11/5.12** _(unit fakes have hidden real breakage before — MEMORY project_virtuals_launch_effort; validate live.)_
4. Local models → if Ollama running, Download flow with a small GGUF → progress → "use as default local". **FR-5.14/5.15**
5. Model & behavior → default model select shows the just-added cloud key + local model in the two optgroups; set approval policy + spend cap. **FR-5.16/5.17/5.18**
6. Privacy → set retention 30d; Export → toast; Delete-all → typed-confirm dialog appears (do **not** confirm). **FR-5.19/5.20**
7. Notifications → toggle a cell + quiet hours. **FR-5.21/5.22**
8. Advanced → Touch-ID toggle disabled-with-hint if unsupported; create a dev token (shown once). **FR-5.23/5.24**

### Regression guard

Web `apps/frontend` settings behavior identical: run `npm run typecheck --workspace @0x-copilot/frontend` + the settings vitest suite before/after each shim PR; diff-review that each web `sections/*.tsx` shim only re-exports (no prop or DOM change). **FR-5.27**.

**FR→test coverage:** every FR-5.n above is named in at least one case (FR-5.1→SettingsSurface+smoke; 5.2→Modal/design primitives visual+Modal.test; 5.3/5.4/5.5→settingsNav+integration; 5.6/5.7→SettingsSurface.test; 5.8→ProfilePage.test (reused) + smoke; 5.9/5.9a→AppearancePage.test+smoke; 5.10→ShortcutsPage.test; 5.11–5.13→ProviderKeys+AddKey; 5.14/5.15→LocalModels+Download; 5.16–5.18→ModelAndBehavior+ApprovalPolicy; 5.19/5.20→PrivacyPage; 5.21/5.22→NotificationsPage; 5.23/5.24→AppLock+DeveloperTokens; 5.25→SettingsSurface.test; 5.26→data/\*.test+ESLint; 5.27→web regression suite; 5.28→SettingsSurface.integration).

---

## 9. UI/UX acceptance checklist

Grounded in DESIGN-SPEC §0/§4/§5. Single-accent discipline: **sky `#5fb2ec` is the only accent**; jade `#57c785` = success/default-local, ember `#f0764f` = destructive, amber `#e8b45e` = warning; connector/model logos neutralized to `--panel3`/`--tx2` monochrome.

**Layout / dims**

- [ ] Settings `.set` = **216px nav + content**; content max-width **620px**; modal **500px**; base font **13px**, line-height **1.5**.
- [ ] Nav groups + items exactly per §4; group headings non-clickable; Provider keys shows the mono **"BYOK"** tag; Advanced group is **collapsible**.
- [ ] Radii `--r 8 / --r-lg 12 / --r-sm 6`; spacing `--pad 13 / --gap 10`; hairlines `--line/.06`, `--line2/.10`, `--line3/.18`.
- [ ] Topbar **suppressed** on Settings (full-height surface).

**States (default / hover / active / focus-visible / loading / empty / error)**

- [ ] Nav item: default `--mut`; hover `--panel2`; **active = `--panel2` bg + 2px accent left bar** (mirror rail active spec §1).
- [ ] `.frow` control default/hover/active; `.seg`/`.swatch`/`.theme-tile` active = accent ring; `.ctog` on = accent.
- [ ] focus-visible ring = **`2px solid var(--accent)` offset 2** on every interactive control, nav item, swatch, modal close.
- [ ] Loading: provider-keys/local-models/model-behavior show a skeleton or "Loading…" card (reuse existing `Card` loading copy), never a bare blank.
- [ ] Empty: no provider keys → all "Add key" rows; no local models (Ollama up) → "No local models yet. Download one above."; no dev tokens → empty-state copy.
- [ ] Error: `role="alert"` + Retry on facade-unreachable (providers, local-runtime, workspace defaults); download interrupt → ember error text.
- [ ] Streaming: local-model **download progress** bar (`.bar`) with `%`, size, speed, ETA; validating-key spinner "Validating with {provider}…".
- [ ] Danger: Delete-all uses ember + typed-confirmation dialog; Remove key/model uses ember `Button variant="danger"`.

**Flows (DESIGN-SPEC §5)**

- [ ] Add provider key: `.scrim`+`.modal`, head (logo+title+mono subtitle+×), **3 StepDots**, foot actions; validate → default-model → Add.
- [ ] Download local model: pick → progress → "Ready to run locally" + "Use as default local model" toggle → Finish.

**a11y**

- [ ] Nav is a `tablist`/`tab` (or list+`aria-current`) with roving focus; content region labelled; Advanced disclosure has `aria-expanded`.
- [ ] Approval-policy pill groups use `role="radiogroup"`/`role="radio"` + `aria-checked` (as in current `ModelAndBehavior.tsx`/`ToolUsePolicyPanel.tsx`).
- [ ] Modals: `role="dialog"` + `aria-modal`, focus trap, ESC close, return focus to trigger.
- [ ] Errors `role="alert"`; toasts `role="status"`.
- [ ] `prefers-reduced-motion` / `[data-reduce-motion=1]` zeroes modal + progress transitions.
- [ ] Contrast: text on `--panel*` meets AA in light+dark; disabled Touch-ID control has a visible, readable hint (not color-only).

**Theming / density / single-accent**

- [ ] Correct in **light + dark**; `[data-density=compact|spacious]` spacing verified (Spacious via widened contract or `KeyValueStore` fallback, §5.5/FR-5.9a); Appearance theme tiles (exactly 3) reflect current theme; accent swatch row shows only sky/jade/ember/violet.
- [ ] Zero stray decorative color: provider/model logos monochrome; only sky/jade/ember/amber per semantic rules.

**Reuse noted**

- [ ] `Card`/`Field`/`Button`/`Switch`/`Select`/`TextInput`/`Badge` from `@0x-copilot/design-system`; accent swatches driven by `ACCENT_SCHEMES` (`design-system/src/index.tsx:45`) — **which currently holds the 9-entry v1 palette (sky/atlas-orange/gold/amber/red/lime/teal/blue/violet), NOT sky/jade/ember/violet**; the swatch row MUST render only the reconciled 4-accent single-accent set (§5.5), consuming Phase 0B/0C's narrowed `ACCENT_SCHEMES` or filtering to the four spec ids until then. `ProfilePage`/`QuietHoursEditor` reused as-is from `chat-surface/src/settings`; `NotificationsPage` **reworked** (§5.5), not reused unchanged.

---

## 10. Dependencies & sequencing

**Upstream (blocked by):**

- **Phase 0D** — `DeploymentProfile` client port (React context/hook). **Hard blocker for PR-5.1** (nav gating). Backend concept exists; the _client_ port must land first.
- **Phase 0B** — design-system v2 tokens (`styles.css`). Blocks PR-5.2 chrome.
- **Phase 0B/0C** — narrowing `ACCENT_SCHEMES` + `UserProfileAccent` to the single-accent set (sky/jade/ember/violet) and neutralizing decorative color. **Soft-blocks PR-5.3** (Appearance): if not landed, PR-5.3 filters swatches to the four spec ids and flags (§5.5, gap #10).
- **Phase 0E** — `chat-surface` settings module home + ESLint substrate guard. Blocks all moves.
- **Phase 2B** — profile-gated `destinations.ts` (Settings is rail-foot, not a destination, but shares the profile model).
- **Phase 2C** — rail-foot Settings entry / `onOpenSettings` wired on desktop. Blocks PR-5.9 mount.

**Internal order (DAG):** PR-5.1 → PR-5.2 → {PR-5.3, PR-5.4, PR-5.5, PR-5.7, PR-5.8} (parallelizable) → PR-5.6 (needs 5.4+5.5 for optgroup sources) → PR-5.9 (needs all).

**Downstream (blocks):**

- **Phase 6A** command palette entries ("Add a provider key", "Model & behavior", "Appearance", "Open Settings") consume the nav SSOT from PR-5.1.
- **Phase 6B** shortcut execution (`⌘,`, `⌘⇧M`) targets the surface from PR-5.9.
- **Phase 6C** deletes `DesktopPlaceholder.tsx` + web `SettingsScreen.tsx` monolith once PR-5.9 is proven.
- **Phase 6D** live smoke includes the settings/BYOK/local-model path (this PRD §8 step 3–5).

---

## 11. Risks & mitigations

| Risk                                                                                                                                                                                                                   | Severity | Mitigation                                                                                                                                                                                                                                                                                               |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`DeploymentProfile` client port (0D) not merged** → PR-5.1 blocked                                                                                                                                                   | High     | Confirm 0D landed before starting; if not, PR-5.1 defines a minimal `useDeploymentProfile()` shim defaulting to `single_user_desktop` on desktop (desktop composition root always is), flagged for 0D reconciliation.                                                                                    |
| **Moving `fetch`-backed sections into `chat-surface` regresses web**                                                                                                                                                   | High     | Incremental one-section-per-PR; `apps/frontend` re-export shims; run web settings vitest + typecheck each PR; ESLint boundary guard.                                                                                                                                                                     |
| **Local models double-implementation** (already exists in `apps/frontend` + both backends)                                                                                                                             | Med      | Treat as a **move+restyle**, not new; port `LocalModels.test.tsx`; keep `services/ai-backend` local-model routes untouched. Flagged in gaps.                                                                                                                                                             |
| **Spend cap / app-lock / default-model-persist may lack a facade route**                                                                                                                                               | Med      | Ship UI + typed `TypedRequest`; if the route 404s, render a stubbed "coming soon"/disabled state and file a flagged gap — no silent no-op that looks live.                                                                                                                                               |
| **Provider set drift** (spec wants Groq+xAI; `ProviderKeyProvider` has only 4)                                                                                                                                         | Low      | Add Groq/xAI to the `api-types` union + facade in the same PR-5.4 only if the backend accepts them; else render as "Any OpenAI-compatible endpoint" custom row and flag.                                                                                                                                 |
| **Notification model mismatch** — shipped `NotificationDefaults.destinations_enabled` is per-_destination_ on/off with **no event and no channel axis**; spec wants 6 events × 3 channels (desktop/sound/email) (§5.5) | High     | Treat PR-5.8 as a **data-model rework, not "extend the event set"**: land a widened `settings.ts` + facade enum for event×channel, or persist the grid as `KeyValueStore`-local prefs and flag; never relabel destination toggles as the event×channel grid (FR-5.21).                                   |
| **Appearance contract drift** — `UserProfileAccent` (9 v1 accents) ≠ spec 4-accent set; `UserProfileDensity` lacks `spacious`; `ACCENT_SCHEMES` still the 9-entry v1 palette (§5.5)                                    | Med      | PR-5.3 depends on Phase 0B/0C narrowing `ACCENT_SCHEMES` + `UserProfileAccent`; filter swatches to sky/jade/ember/violet until then; widen density union iff the profile route accepts `spacious`, else `KeyValueStore` fallback that still sets `[data-density=spacious]` live + flagged gap (FR-5.9a). |
| **Framework-agnostic invariant broken by ports gaps** (e.g. Touch-ID needs native APIs)                                                                                                                                | Med      | Route native capability through `SecretStorage`/capabilities port; UI reads capability booleans, never calls native APIs directly.                                                                                                                                                                       |
| **Rollback**                                                                                                                                                                                                           | —        | Each PR is behind the desktop mount; until PR-5.9 flips `onOpenSettings`, desktop still shows `DesktopPlaceholder` (no user-visible settings), so 5.1–5.8 are safe to land dark. PR-5.9 is the single reversible switch.                                                                                 |

---

## 12. Definition of done

- [ ] All **FR-5.1 … FR-5.28** met and mapped to passing tests (§8).
- [ ] `npm run test --workspace @0x-copilot/chat-surface` green (new settings unit + integration).
- [ ] `npm run test --workspace @0x-copilot/frontend` + `npm run typecheck --workspace @0x-copilot/frontend` green — **web settings behaviorally identical** (shims only).
- [ ] `cd services/ai-backend && .venv/bin/python -m pytest tests/unit/runtime_api/test_local_models_*` and facade/backend deployment-profile tests green.
- [ ] Live desktop smoke (§8 steps 1–8) passes on the staged runtime, incl. a **live** Add-key + Download-model exercise.
- [ ] UI/UX checklist (§9) passed in light + dark, compact/comfortable/spacious, reduce-motion.
- [ ] Nav gating verified: solo hides Workspace/Members/Billing/Audit; team shows them; unknown slug → `profile`.
- [ ] ESLint substrate guard: zero bare `fetch`/`window`/`localStorage` in `chat-surface/src/settings/**`.
- [ ] Desktop rail-foot gear opens `SettingsSurface`; `DesktopPlaceholder` no longer shown for the settings route (its deletion scheduled in Phase 6C, tracked).
- [ ] `apps/desktop/SMOKE.md` updated with the settings path; no dead code introduced by this phase without a Phase-6C owner.
- [ ] Every §5.5 contract drift is resolved by a real widen (contract + facade, with a Python test) **or** a `KeyValueStore`-local fallback that persists and is recorded as a flagged gap (§11) — no option in the UI silently fails to persist, and no destination toggle is relabeled as an event×channel cell.

---

### Appendix — flagged discrepancies between spec/plan and actual code (read, not assumed)

1. **Local models is NOT net-new.** The plan/prompt said "expect none" for Ollama, but a full implementation exists: `apps/frontend/src/features/settings/sections/LocalModels.tsx` (+`LocalModels.test.tsx`), `apps/frontend/src/api/localModelsApi.ts`, `packages/api-types/src/localModels.ts`, facade `services/backend-facade/src/backend_facade/local_models_routes.py`, and ai-backend `tests/unit/runtime_api/test_local_models_{routes,service}.py`. Phase 5 = move+restyle, not build.
2. **`chat-surface/src/settings` is already partly the SSOT** — `ProfilePage.tsx`, `NotificationsPage.tsx`, `QuietHoursEditor.tsx`, `WebhookSecurityPage.tsx` already live there. Phase 5 extends the home rather than creating it.
3. **Approval policy already exists** as `sections/ToolUsePolicyPanel.tsx` (read/write/destructive × auto/ask/require/block via `/v1/me/policies/tool-use`), embedded in `ModelAndBehavior.tsx`. Spec's "read/write/on-chain-spend-destructive" maps onto it; only labels/relocation change.
4. **Model & behavior spec ≠ current knobs.** Current `ModelAndBehavior.tsx` exposes system-prompt/temperature/citation-density/refusal/reasoning-effort (workspace-defaults). Spec wants **Default model select (Cloud/Local optgroups)**, **Reasoning depth Auto/Quick/Standard/Deep**, **Web access toggle**, and **Spend cap** — these are net-new controls; the existing ones may be retained as advanced or dropped on solo (decide in PR-5.6).
5. **Provider set gap.** `ProviderKeyProvider` (`packages/api-types/src/providerKeys.ts`) = `openai|anthropic|google|openrouter`. DESIGN-SPEC §4 additionally lists **Groq** and **xAI**. Needs a backend union+facade change or the "OpenAI-compatible endpoint" custom row.
6. **Notification event-set differs.** Existing enum (`mention/approval_needed/run_finished/weekly_digest`) vs spec's six (Approval requested / Run finished / Run paused / Connector error / Spend threshold / Product updates) and channels desktop/sound/email. Reconcile in PR-5.8 (backend enum may need widening).
7. **No `DesktopPlaceholder` settings path today.** `apps/desktop/renderer/bootstrap.tsx` never passes `onOpenSettings`; desktop has zero settings UI. PR-5.9 is the first time settings is reachable on desktop.
8. **`DeploymentProfile` client port not found** in `packages/chat-surface` / `apps/desktop` / `apps/frontend` (only the backend `deployment_profile.py` modules + `service-contracts` constants exist). Phase 0D must deliver the client port; hard dependency for PR-5.1.
9. **App-lock / Touch-ID / "Cloud sync" / "Working hours" / spend-cap** have no evident frontend or facade surface — treat as net-new UI over (possibly) missing routes; stub + flag rather than fake-live.
10. **Appearance accent contract drift.** `UserProfileAccent` (`packages/api-types/src/index.ts:2853`) and `ACCENT_SCHEMES` (`packages/design-system/src/index.tsx:45`) both hold the **9-entry v1 palette** (sky/atlas-orange/gold/amber/red/lime/teal/blue/violet), not the spec's single-accent set (sky/jade/ember/violet, DESIGN-SPEC §0). Earlier PRD drafts asserted `ACCENT_SCHEMES` already = sky/jade/ember/violet — **that is false**; the narrowing is Phase 0B/0C work and a hard input to PR-5.3 (§5.5).
11. **Density contract drift.** `UserProfileDensity` (`index.ts:2864`) = `comfortable|compact` only; the spec + `[data-density=spacious]` token demand a third **Spacious** option. Persisting it needs a union+route widen or a `KeyValueStore`-local fallback (FR-5.9a, §5.5).
12. **Notifications shape drift (biggest).** `NotificationsPage.tsx` + `NotificationDefaults.destinations_enabled: PerDestinationToggle` (`settings.ts`) is a per-_destination_ on/off grid (chats/runs/approvals/inbox/…) with My/Workspace tabs — there is **no channel (desktop/sound/email) axis and no event axis** anywhere in the shipped contract. The spec's event×channel grid is a **rework of the data model**, not a reuse; owned by PR-5.8 (§5.5, risk row). `SettingsGateway.test.tsx` lives under `apps/frontend/src/features/settings/__tests__/`, not the section dir — earlier drafts mis-pathed it.
