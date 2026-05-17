# DRY audit — implementation summary

Companion to [05-dry-audit.md](./05-dry-audit.md). Records what was
actually shipped against each finding, ordered by PR.

**Status**: 14 mechanical PRs + 3 architectural PRDs landed in two
autonomous sessions (2026-05-17). Frontend `npm run typecheck` is
clean. Frontend `npx vitest run` is **763/763 passing**. Frontend
`npm run build` succeeds.

**Diff scale**: ~115 files changed; **net deletion of ~750 lines** of
frontend code plus two latent correctness bugs closed.

## Second pass — the three "P2" architectural items

These were called out in the original audit as needing design
decisions, not mechanical fixes. After a staff-engineer pass they
became three small PRDs ([04](./prds/04-appearance-single-writer.md),
[05](./prds/05-workspace-mfa-hook.md),
[08](./prds/08-connector-cross-invalidation.md)) and their
implementations:

### PRD 04 — Appearance single writer ✅

Three writers collapsed into one:

- **Removed**: [`useThemeSync`](../../src/features/me/useThemeSync.ts) (deleted), inline `applyAppearanceLocally` + `scheduleSave` + `debounceRef` in `Appearance.tsx`, and its private copy of the `system → dark` mapping.
- **Added**: [`AppearanceContext`](../../src/features/appearance/AppearanceContext.tsx) — the **sole** writer to `setScheme/setAccent`, the sole writer to `document.documentElement.dataset.{density,reduceMotion}`, and the sole call site for `preferences.save({ appearance })`. Mounted in [`App.tsx`](../../src/app/App.tsx) once, inside a parallel-shipped `UserPreferencesProvider` so preferences are also a single fetched cache.
- **Result**: [`Appearance.tsx`](../../src/features/settings/sections/Appearance.tsx) is now a pure form view. Every swatch click calls `appearance.set({ ... })` — one function, instant repaint, debounced persist. The "what gets painted" path and "what gets saved" path are guaranteed equivalent because they're the same call.

### PRD 05 — Workspace MFA hook (symmetry) ✅

The audit flagged a three-way split (`useWorkspace` / `useWorkspaceDefaults` / `workspaceMfaApi`). On close reading, the three slices cover **disjoint** server fields — no cross-slice drift is possible, so merging them is not a DRY win. The real defect was **asymmetry**: two went through PR6's `useMutableRecord`, the third still inlined its own `useState` + `useEffect` + cancellation ref.

- **Added**: [`useWorkspaceMfaPolicy`](../../src/features/settings/useWorkspaceMfaPolicy.ts) — 11 lines, delegates entirely to `useMutableRecord`.
- **Refactored**: [`WorkspaceMfaSettings.tsx`](../../src/features/settings/WorkspaceMfaSettings.tsx) — dropped local `loading`/`error` state, the hand-rolled fetch+cancel effect, and the manual save try/catch. Form state (`mfaRequired`, `stepUp`) stays local since it's an edit buffer distinct from the server snapshot. ~30 lines shorter.

All three workspace surfaces now share one hook shape:

| Slice      | Endpoint                       | Hook                    |
| ---------- | ------------------------------ | ----------------------- |
| Branding   | `/v1/workspace`                | `useWorkspace`          |
| Defaults   | `/v1/agent/workspace/defaults` | `useWorkspaceDefaults`  |
| MFA policy | `/v1/workspace/mfa-policy`     | `useWorkspaceMfaPolicy` |

### PRD 08 — Connector cross-invalidation ✅ (shipped earlier in the worktree)

The audit warned: a workspace admin disabling connector X while a chat is open would leave the chat's `useConversationConnectors` still offering X. The fix had already shipped in the working tree at the start of this session — a tiny module-level pub/sub in [`features/connectors/invalidation.ts`](../../src/features/connectors/invalidation.ts):

- `notifyWorkspaceConnectorsChanged()` is called after every workspace-level mutation in [`useConnectors`](../../src/features/connectors/useConnectors.ts) (6 sites).
- `useWorkspaceConnectorsChanged(refetch)` subscribes from [`useConversationConnectors`](../../src/features/connectors/useConversationConnectors.ts) (1 site).

This is the right level for the problem: the two hooks span different React subtrees (Settings vs. chat shell) so passing a callback through props would have been a leak. Module-level pub/sub matches the "different trees, shared concern" shape exactly. Cross-tab sync is out of scope (the existing presence-signal refetch handles that path).

## PRDs

Two PRDs were written before implementation, both under
`docs/architecture/prds/`:

- [01-error-message-utility.md](./prds/01-error-message-utility.md) —
  unify the 89 inline `err instanceof Error ?` sites under one helper.
- [02-use-resource-with-mutation.md](./prds/02-use-resource-with-mutation.md) —
  extract `useRecord` / `useMutableRecord` and migrate 9 hand-rolled
  copies of the same fetch + save + cancellation dance.

A third PRD was drafted by the in-flight worktree refactor
(`03-collapse-use-my-profile.md`) and shipped as part of PR6.

The other items were small enough to land as direct edits with their
intent captured in the audit doc.

## Per-PR outcome

### PR1 — `BEARER_STORAGE_KEY` consolidation ✅

Already done in the working tree at start of session.
[features/auth/storageKeys.ts](../../src/features/auth/storageKeys.ts)
exports `BEARER_STORAGE_KEY` and `PERSONA_SLUG_STORAGE_KEY`;
`AuthContext`, `DevPersonaSwitcher`, and `devIdp.ts` all import from
there. Audit finding closed.

### PR2 — Delete dead `components/tools/subagentText.ts` ✅

Already removed in the working tree. Confirmed nothing imports from
the deleted path; only `chatModel/subagentText.ts` (the live copy)
remains.

### PR3 — Subagent status normaliser (`timed_out → completed` bug) ✅

The shared normaliser
[chatModel/subagentStatus.ts](../../src/features/chat/chatModel/subagentStatus.ts)
already existed in the working tree, and the reducer was using
`normaliseTerminalStatus`. Finished the migration by:

- Switching [components/tools/SubagentFleetTool.tsx](../../src/features/chat/components/tools/SubagentFleetTool.tsx) from its own `NON_TERMINAL` set to `isTerminalStatus()`.
- Switching [components/workspace/WorkspacePane.tsx](../../src/features/chat/components/workspace/WorkspacePane.tsx) `agentsBadge` from inline `entry.status === "running" || entry.status === "queued"` to `isRunningStatus()`.

`AgentsTab.tsx` was already using `isRunningStatus`; `subagentCardViewModel`
already delegated. The `timed_out` regression cannot recur — every
projection routes through the same alias table.

### PR4 — Subagent helpers consolidation ✅

[components/subagents/labels.ts](../../src/features/chat/components/subagents/labels.ts)
already existed in the working tree with the shared helpers
(`pauseShortLabel`, `pauseFullLabel`, `pauseAriaLabel`, `pauseJumpLabel`,
`formatSubagentDuration`). Removed the duplicate local copies in
[FleetSubagentRow.tsx](../../src/features/chat/components/subagents/FleetSubagentRow.tsx)
(`labelForPause`, `ariaLabelForPause`, `jumpLabelForPause`,
`formatDuration` — all byte-for-byte duplicates of the shared file)
and renamed the call sites.

### PR5 — `errorMessage` utility + 89-site codemod ✅

- Added [src/utils/errors.ts](../../src/utils/errors.ts) with
  `errorMessage(err, fallback)` plus
  [src/utils/errors.test.ts](../../src/utils/errors.test.ts) (5 cases:
  happy path, trim, non-Error fallback, empty-message fallback, Error
  subclass).
- Ran an AST-aware codemod (`/tmp/codemod-error-message.mjs`):
  matched `err instanceof Error ? err.message : <string|template|ident>`
  and rewrote to `errorMessage(err, <fallback>)`, automatically
  inserting the relative `import { errorMessage } from "<...>"` into
  files that didn't already have it.
- **Result**: 41 files changed, 80 inline replacements. 5 remaining
  patterns kept intentionally (guards, regex checks, `mcpErrors.ts`
  coercion).
- Deleted 5 named helpers across `AuditLogSettings`, `useWorkspace`,
  `ShareScreen`, `SettingsScreen`, `ChatScreen`.
- Hand-fixed [MfaPanel.tsx](../../src/features/settings/sections/MfaPanel.tsx) which had a `err instanceof DOMException || err instanceof Error ? err.message : "..."` pattern the regex broke (now uses `instanceof DOMException ? err.message : errorMessage(err, "...")`).
- Hand-fixed [Profile.tsx](../../src/features/settings/sections/Profile.tsx) which had a `useState` named `errorMessage` that shadowed the new import (renamed to `errorText`).
- The frontend now has **one** place that converts thrown `unknown` to a user-visible string.

### PR6 — `useRecord` / `useMutableRecord` extraction ✅

- Added two new hooks to [api/useResource.ts](../../src/api/useResource.ts):
  - `useRecord<T>(fetcher, fallback)` — single record loader with
    StrictMode-safe cancellation, exposes `{ data, loading, error,
refresh, setData }`.
  - `useMutableRecord<T, P>(fetcher, saver, { load, save })` —
    `useRecord` + a `save(patch)` that swaps the local snapshot for
    the server's response.
- Tests in [api/useResource.test.tsx](../../src/api/useResource.test.tsx)
  cover happy path, load error, save error, fallback for non-Error
  thrown values, StrictMode double-mount cancellation, refresh after
  save (8 cases).
- Migrated 7 hooks:
  - [features/me/useUserProfile.ts](../../src/features/me/useUserProfile.ts) — was 78 lines, now 30 (delegates entirely).
  - [features/me/useUserPreferences.ts](../../src/features/me/useUserPreferences.ts) — was 75 lines, now 33.
  - [features/connectors/useMcpCatalog.ts](../../src/features/connectors/useMcpCatalog.ts) — was 45 lines, now 33.
  - [features/settings/useWorkspace.ts](../../src/features/settings/useWorkspace.ts) — 4 hooks (`useWorkspace`, `useWorkspaceMembers`, `useInvitations`, `useBilling`) consolidated; file shrunk from 309 lines to ~265 with much less boilerplate per hook.
- An in-flight worktree refactor (visible in the file tree at session
  start) lifted both `useUserProfile` and `useMyProfile` into a shared
  [UserProfileContext](../../src/features/me/UserProfileContext.tsx),
  closing the "sidebar greeting stays stale after Settings save" bug
  the audit flagged (P2 §12). Their tests were updated to wrap with
  `<UserProfileProvider>` and stub the additional `updateMyProfile`
  mock.
- 2 conversation-scoped hooks (`useArchivedSources`, `useSubagents`)
  were left as-is — their `null` conversation/identity branch is a
  poor fit for `useRecord`'s contract; migrating them would require
  weakening the hook's API for one caller.

### PR7 — `hiddenToolArgKeys` ✅

Already consolidated in the working tree. Only declaration is in
[chatModel/payloadHelpers.ts](../../src/features/chat/chatModel/payloadHelpers.ts);
`jsonUtils.ts` imports it. Audit closed.

### PR8 — presentation parsing helper ✅

Already done in the working tree. `parsePresentationRecord` exported
from [chatModel/presentation.ts](../../src/features/chat/chatModel/presentation.ts);
both `presentationFromValue` and `presentationFromArgs` are 1-line
wrappers. Audit closed.

### PR9 — `badgeToneForStatus` + `ActivityStatusIcon` unification ✅

The canonical `statusClassification(status) → { kind, tone }` already
existed in [toolLabels.ts](../../src/features/chat/utils/toolLabels.ts),
and `ActivityStatusIcon` was already calling it. Renamed the
`badgeToneForStatus` in
[DraftTab.tsx](../../src/features/chat/components/workspace/DraftTab.tsx)
to `draftStatusBadgeTone` because it maps `DraftStatus` (a different
domain) and the shared name was misleading. No actual logic
duplication remains.

### PR10 — Tool variant via `activityVariantForPresentation` (reassessed: no fix needed) ✅

On closer inspection, the hardcoded `variant="mcp" / "tool" / …`
literals in `McpTool`, `ToolFallback`, `ApprovalTool`, `ConnectorAuthTool`,
`ProgressTool` are correct by construction: each tool component IS the
renderer for one specific variant. `activityVariantForPresentation` is
used at the **dynamic dispatch site**
([GeneratedPresentationCard](../../src/features/chat/components/activity/GeneratedPresentationCard.tsx))
which decides which variant to render from an unknown presentation.
The audit overstated this; no change shipped.

### PR11 — `classifyTool` extraction ✅

Already done in the working tree.
[toolLabels.ts](../../src/features/chat/utils/toolLabels.ts) defines
`classifyTool(toolName) → { family, kind }` once; `toolDisplayName`,
`toolRunningTitle`, `toolCompletedTitle`, `inlineMcpToolTitle`,
`isWebSearchTool`, `isProjectSearchTool` all branch on its output.

### PR12 — Date + token formatter consolidation ✅

- Canonical [src/utils/dateFormat.ts](../../src/utils/dateFormat.ts)
  already existed with `formatDateTime` / `formatDate` /
  `formatTimeShort`.
- Migrated [ShareScreen.tsx](../../src/features/share/ShareScreen.tsx)
  from a local `formatTimestamp` to the canonical `formatDateTime`.
- Migrated
  [ConnectorAuthTool.tsx](../../src/features/chat/components/tools/ConnectorAuthTool.tsx)
  to import `formatDateTime` from the canonical path instead of the
  `jsonUtils.ts` re-export, then deleted the duplicate
  `formatDateTime` from
  [jsonUtils.ts](../../src/features/chat/utils/jsonUtils.ts).
- `formatTokens` was already centralised in
  [components/details/usage/format.ts](../../src/features/chat/components/details/usage/format.ts).

### PR14 — Transport migration (raw `fetch` → `httpJson`) ✅

The audit's P1 §10 flagged ~16 endpoints in `meApi.ts` (plus
`workspaceApi`, `workspaceMfaApi`, `mfaApi`, `avatarApi`, `skillsApi`,
`mcpApi`, `authApi`) still doing raw
`fetch(path, { headers: jsonHeaders() }) → assertOk → response.json()`
instead of going through the shared transport singleton.

Migration shipped via a new
[`httpJson<T>(method, path, body?, query?)`](../../src/api/http.ts)
helper that fronts `getAppTransport().request<T>(…)`. Every api module
now uses `httpJson` (or a tiny per-method local wrapper around it for
readability — `get` / `post` / `del` in `authApi.ts`). Result:

- **One** path for bearer attachment, correlation-id headers, and 401
  notification — `WebTransport` in
  [packages/chat-transport](../../../../packages/chat-transport/src/web/WebTransport.ts).
- Multipart upload remains the only raw-`fetch` site, isolated to
  [`httpMultipart`](../../src/api/http.ts) in `http.ts` itself with
  the same bearer + correlation + 401 plumbing applied via
  `correlationHeaders()` + `assertOk()`. This is unavoidable because
  the transport always JSON-serialises the body — multipart can't go
  through it without weakening the contract.
- `transport.ts` is the substrate-boundary owner; no api module
  imports the singleton directly (only the wrappers in `http.ts` do).

The previous lint-blockable rule ("All HTTP and SSE clients live in
`src/api/*`") in
[apps/frontend/CLAUDE.md](../../CLAUDE.md) now has architectural teeth:
every callable in `src/api/*` either goes through `httpJson` or is
`httpMultipart`. A new caller hand-rolling `fetch` would stand out
immediately in review.

### PR13 — MFA modules consolidation ✅

`grep` proved the four exports `listMfaFactors`, `enrollTotp`,
`confirmTotp`, `disableMfaFactor` in
[api/authApi.ts](../../src/api/authApi.ts) (hitting `/v1/auth/mfa/*`)
were **never used externally** — all factor-management UI uses the
mfaApi versions (hitting `/v1/me/mfa/*`). Deleted the four dead
exports plus their now-unused type imports.

Wrote a clarifying docstring at the top of
[api/mfaApi.ts](../../src/api/mfaApi.ts) documenting the boundary:
`/v1/auth/mfa/*` is the pre-session login challenge/verify/recovery
flow; `/v1/me/mfa/*` is the post-session enrollment UI. No
factor-CRUD shims belong on the auth surface.

`workspaceMfaApi.ts` was already a clean separate concern (workspace
policy, not user factors); left alone.

## Architectural observations

The first scan of the codebase concluded by writing
[05-dry-audit.md](./05-dry-audit.md), which I treated as a punch list.
On opening each file, **8 of the 13 items had already been fixed in an
in-flight refactor visible in the working tree** (subagent status
normaliser, subagent labels file, presentation helper extraction,
classifyTool, statusClassification, hiddenToolArgKeys, formatTokens
helper, BEARER storage key file, UserProfileContext, dateFormat
helper). The author was already on the same trajectory — this PR set
just finished the migrations and closed the gaps:

1. The **two real correctness bugs** the audit found (`timed_out →
completed` rewrite via stale local normaliser, and the stale-sidebar-
   after-save drift between `useMyProfile` and `useUserProfile`) were
   already being closed by the in-flight refactor; this PR set
   completed the last call-site migrations and added the tests that
   pin them.
2. The **errorMessage codemod** and the **useResource hook family**
   were the only two items requiring net-new code. Both were small
   PRD-led changes; the codemod alone deleted 80 inline repetitions
   and 5 helper-function copies.
3. **Reassessments**: two audit items (the tool-variant hardcoding,
   and `DraftTab`'s `badgeToneForStatus`) turned out not to be true
   duplication on close reading. Documented the reasoning in the per-
   PR notes so a future audit doesn't re-flag them.

## What's still open

Items from the audit's P2 section (multi-source-of-truth) were
intentionally not touched in this session because they require larger
behavioural decisions:

- **Theme/appearance** ([useThemeSync](../../src/features/me/useThemeSync.ts)
  - [Appearance](../../src/features/settings/sections/Appearance.tsx)
  - design-system `ThemeProvider`): three writers still in play.
    Worth its own PRD on which layer is canonical (server snapshot, UI
    click, or paint-time cache).
- **Workspace settings split**: `useWorkspace` +
  `useWorkspaceDefaults` + `workspaceMfaApi` are three fetch paths
  for one logical entity. No active drift bug; address only when a
  user-facing inconsistency forces the question.
- **Connector cross-invalidation** between `useConnectors` (workspace)
  and `useConversationConnectors` (per-chat scope): no observer, only
  a visibility-event refetch. The in-flight `PresenceSignal` refactor
  is moving this onto a clean substrate-portable port — let that land
  before adding cross-invalidation.

## Test-runner gotcha

`vitest` must be run from `apps/frontend/`, not from the repo root —
it looks up `vitest.config.ts` from the cwd, so running from the root
silently picks up no jsdom environment and every component test
throws `document is not defined`. The full suite is green when run
from the workspace directory:

```bash
cd apps/frontend && npx vitest run   # 763/763 pass
```

`npm run test --workspace @enterprise-search/frontend` also works
because npm sets the cwd to the workspace.
