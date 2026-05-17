# Frontend DRY / Single-Source-of-Truth Audit

**Scope:** `apps/frontend/src/` — every `.ts`/`.tsx` excluding `.test.*`.
**Concern:** the same logic, constant, or piece of state lives in multiple
places. Drift is already happening (e.g. `BEARER_STORAGE_KEY` declared
twice with the same value), and several patterns are hand-copied across
5+ files instead of going through a shared helper that already exists.

Findings are grouped by severity. Each finding lists the actual file
paths and line numbers so the work is mechanical.

---

## Severity legend

- **P0** — same value declared as a literal in 2+ places. A rename will
  silently break one side. Pure duplication, no design tension.
- **P1** — same algorithm/shape repeated. Bug fixes need to be applied N
  times. A canonical helper either exists and is being bypassed, or
  needs to be extracted.
- **P2** — multiple owners of the same logical state. No drift today,
  but invalidation is missing, so any future write through one owner
  leaves the others stale.

---

## P0 — Duplicated constants

### 1. `BEARER_STORAGE_KEY = "enterprise.auth.bearer"` declared twice

- [features/auth/AuthContext.tsx:52](../../src/features/auth/AuthContext.tsx#L52) — canonical owner; reads/writes/clears.
- [features/chat/components/sidebar/DevPersonaSwitcher.tsx:24](../../src/features/chat/components/sidebar/DevPersonaSwitcher.tsx#L24) — second copy, also writes the bearer to localStorage.

Two files write the same key under two un-linked constants. If anyone
renames the key in `AuthContext`, the dev persona switcher will keep
writing to the old key and the next reload will look "logged out."

**Fix:** export `BEARER_STORAGE_KEY` from `AuthContext.tsx` (or
better — from a new `features/auth/storageKeys.ts`) and import it in
`DevPersonaSwitcher.tsx`. Same applies to `PERSONA_SLUG_STORAGE_KEY`
in [features/auth/devIdp.ts:37](../../src/features/auth/devIdp.ts#L37) —
keep it co-located with the bearer key.

### 2. Two MFA API surfaces for the same domain

Three modules touch MFA, with overlapping endpoints:

- [api/mfaApi.ts](../../src/api/mfaApi.ts) — hits `/v1/me/mfa/factors`, `/v1/me/mfa/factors/totp/{enroll,confirm}`, `/v1/me/mfa/factors/webauthn/*`.
- [api/authApi.ts:136-177](../../src/api/authApi.ts#L136-L177) — hits `/v1/auth/mfa/factors`, `/v1/auth/mfa/factors/totp/{enroll,confirm}`, `/v1/auth/mfa/challenge`, `/v1/auth/mfa/verify`, `/v1/auth/mfa/recovery/consume`.
- [api/workspaceMfaApi.ts](../../src/api/workspaceMfaApi.ts) — `/v1/workspace/mfa-policy`.

The first two are not the same endpoint, but neither comment explains
which one a new caller should pick, and `listMfaFactors()` exists in
both with different shapes. This guarantees a future caller will pick
the wrong one.

**Fix:** delete whichever module is dead (likely `mfaApi.ts` given the
facade routes through `/v1/auth/mfa/*`), or rename so the boundary is
obvious (`authApi` → login-time MFA, `meApi` → enrolled-factor
management). Add a one-line docstring on each that points to the other.

### 3. Status string unions retyped across files

The same union ends up as bare string literals in many places instead of
being centralised:

- Run/message status: `"queued" | "running" | "completed" | "failed" | "cancelled"` appears as `===` comparisons in [features/chat/chatRunState.ts:157-158](../../src/features/chat/chatRunState.ts#L157-L158), [features/chat/chatModel/status.ts:136-166](../../src/features/chat/chatModel/status.ts#L136-L166), [features/chat/chatModel/subagentReducer.ts:26-34,283-290](../../src/features/chat/chatModel/subagentReducer.ts#L26-L34), [features/chat/ChatScreen.tsx:953](../../src/features/chat/ChatScreen.tsx#L953), [features/settings/SettingsScreen.tsx:1168](../../src/features/settings/SettingsScreen.tsx#L1168).
- Approval decision: `"approved" | "rejected" | "cancelled"` in [features/chat/chatModel/approval.ts:12](../../src/features/chat/chatModel/approval.ts#L12), [features/chat/chatModel/status.ts:14-15](../../src/features/chat/chatModel/status.ts#L14-L15), [features/chat/ChatScreen.tsx:2299,2324](../../src/features/chat/ChatScreen.tsx#L2299), [features/chat/chatModel/mcpAuth.ts:112,117](../../src/features/chat/chatModel/mcpAuth.ts#L112).
- Chat phases (`"starting" | "working" | "acting" | "writing" | "reasoning" | "terminal" | "idle" | "waiting_for_permission"`): declared as a TS union in [features/chat/chatRunState.ts:6-13](../../src/features/chat/chatRunState.ts#L6-L13) but compared as bare strings in [features/chat/ChatScreen.tsx:1288-1289,1921](../../src/features/chat/ChatScreen.tsx#L1288-L1289).

**Fix:** Export `const RUN_STATUS = { Queued: "queued", … } as const` from
the canonical declaration site and have call-sites import `RUN_STATUS.Failed`
instead of `"failed"`. TypeScript already prevents typos in the union, but a
constant turns these into find-all-references navigable symbols and prevents
the "is it `cancelled` or `canceled`?" trap (the codebase already has both —
see [ChatScreen.tsx:953](../../src/features/chat/ChatScreen.tsx#L953) using
`"cancelled"`, while [App.tsx:245](../../src/app/App.tsx#L245) uses
`"approved"` and other parts use `"canceled"`).

### 4. `LEGACY_PREFIX` and connector storage prefixes

[features/connectors/useDiscoverablePref.ts:37](../../src/features/connectors/useDiscoverablePref.ts#L37) declares `"enterprise.discoverable."` as the legacy localStorage prefix. The migration logic only lives here, so this one is fine — but if any new code adds another `enterprise.<feature>.*` namespace, follow the same pattern of co-locating the prefix with its migration.

---

## P1 — Duplicated logic

### 5. `err instanceof Error ? err.message : "..."` repeated ~89 times

This is the single biggest source of code repetition in the frontend.
Every component that catches an error from an API hand-rolls the same
unwrap:

```ts
setError(err instanceof Error ? err.message : "Could not load X");
```

It also exists as a named helper **three times**, each with the same body:

- [features/settings/AuditLogSettings.tsx:350-352](../../src/features/settings/AuditLogSettings.tsx#L350-L352) — `toMessage(err, fallback)`
- [features/settings/useWorkspace.ts:301-303](../../src/features/settings/useWorkspace.ts#L301-L303) — `toMessage(err, fallback)`
- [features/share/ShareScreen.tsx:354-356](../../src/features/share/ShareScreen.tsx#L354-L356) — `toMessage(err, fallback)`

And a fourth variant:

- [features/settings/SettingsScreen.tsx:1348-1349](../../src/features/settings/SettingsScreen.tsx#L1348-L1349) — `errorMessage(err, fallback)`
- [features/chat/ChatScreen.tsx:2014](../../src/features/chat/ChatScreen.tsx#L2014) — `errorMessage(err, fallback)`

Inline call sites (non-exhaustive — there are 89 total): see
[ConfirmDialog.tsx:45](../../src/features/connectors/ConfirmDialog.tsx#L45),
[ConnectorCard.tsx:59](../../src/features/connectors/ConnectorCard.tsx#L59),
[useMcpCatalog.ts:33](../../src/features/connectors/useMcpCatalog.ts#L33),
[JsonEditorPanel.tsx:65,100](../../src/features/connectors/JsonEditorPanel.tsx#L100),
[useConversationConnectors.ts:89](../../src/features/connectors/useConversationConnectors.ts#L89),
[ConnectorRow.tsx:61](../../src/features/connectors/ConnectorRow.tsx#L61),
[McpOverlay.tsx:347,367,669](../../src/features/connectors/mcp/McpOverlay.tsx#L347),
[MembersSettings.tsx:206,220,385](../../src/features/settings/MembersSettings.tsx#L206),
[WorkspaceSettings.tsx:92](../../src/features/settings/WorkspaceSettings.tsx#L92),
[useWorkspaceDefaults.ts:57,102](../../src/features/settings/useWorkspaceDefaults.ts#L57),
[AccountSessionsPanel.tsx:35,52](../../src/features/settings/AccountSessionsPanel.tsx#L35),
[WorkspaceMfaSettings.tsx:55,79](../../src/features/settings/WorkspaceMfaSettings.tsx#L55),
[MfaPanel.tsx:62,82,102,117](../../src/features/settings/sections/MfaPanel.tsx#L62), etc.

**Fix:** add `src/utils/errors.ts` with a single canonical
`errorMessage(err: unknown, fallback: string): string`, re-export from
`api/http.ts` so it sits next to `assertOk`. Delete the three local
copies, codemod the 89 inline cases.

### 6. `useResource`-shaped fetch hooks are re-implemented N times

There is a canonical [`useResource`](../../src/api/useResource.ts) that
encapsulates `{ data, loading, error, refresh }` plus identity gating
and cancellation. Two hooks use it correctly
([useConnectors.ts:49](../../src/features/connectors/useConnectors.ts#L49),
[useSkills.ts:33](../../src/features/skills/useSkills.ts#L33)). At
least seven hooks bypass it and inline the same 30-line pattern:

| File                                                                                                                       | Reason it bypasses                                                                                          |
| -------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| [features/connectors/useMcpCatalog.ts:21-44](../../src/features/connectors/useMcpCatalog.ts#L21-L44)                       | Field renamed to `entries`; comment even says it tries to match `useResource` shape but doesn't share code. |
| [features/me/useUserProfile.ts:28-78](../../src/features/me/useUserProfile.ts#L28-L78)                                     | Adds `save()` and `cancelledRef` for StrictMode double-invoke.                                              |
| [features/me/useUserPreferences.ts:25-75](../../src/features/me/useUserPreferences.ts#L25-L75)                             | Identical shape to `useUserProfile`.                                                                        |
| [features/settings/useWorkspace.ts:43-99](../../src/features/settings/useWorkspace.ts#L43-L99) — `useWorkspace`            | Adds `save()` + bespoke error handling.                                                                     |
| [features/settings/useWorkspace.ts:117-175](../../src/features/settings/useWorkspace.ts#L117-L175) — `useWorkspaceMembers` | Adds `removeMember()`, `updateRole()`.                                                                      |
| [features/settings/useWorkspace.ts:190-259](../../src/features/settings/useWorkspace.ts#L190-L259) — `useInvitations`      | Adds `invite()`, `revoke()`.                                                                                |
| [features/settings/useWorkspace.ts:272-295](../../src/features/settings/useWorkspace.ts#L272-L295) — `useBilling`          | Same shape, no mutations.                                                                                   |
| [features/sources/useArchivedSources.ts:36-84](../../src/features/sources/useArchivedSources.ts#L36-L84)                   | Same shape + `restore()`.                                                                                   |
| [features/settings/sections/MfaPanel.tsx:62-117](../../src/features/settings/sections/MfaPanel.tsx#L62-L117)               | Same shape inlined into the component.                                                                      |

The pattern is always:

```ts
const [data, setData] = useState<T | null>(null);
const [loading, setLoading] = useState(true);
const [error, setError] = useState<string | null>(null);
const cancelledRef = useRef(false);

const fetchOnce = useCallback(async () => {
  try {
    const next = await fetchFn(...);
    if (!cancelledRef.current) { setData(next); setError(null); }
  } catch (err) {
    if (!cancelledRef.current) setError(toMessage(err, fallback));
  } finally {
    if (!cancelledRef.current) setLoading(false);
  }
}, [...]);

useEffect(() => {
  cancelledRef.current = false;
  void fetchOnce();
  return () => { cancelledRef.current = true; };
}, [fetchOnce]);
```

**Fix path:**

1. Add `useResourceWithMutation<T, Patch>(fetcher, saver, errorFallback)`
   to `src/api/useResource.ts`. Returns the same `{ data, loading, error,
refresh, save }` envelope as `useUserProfile`.
2. Migrate `useUserProfile`, `useUserPreferences`, the four
   `useWorkspace`/`useWorkspaceMembers`/`useInvitations`/`useBilling`
   hooks, `useArchivedSources`, and the inline `MfaPanel` fetcher.
3. After migration, `useResource` and `useResourceWithMutation` should
   be the only public way to express "load, cache, optionally mutate" in
   the frontend.

### 7. Approval / question / forward-action loop pattern (4×)

[`features/chat/chatModel/approval.ts`](../../src/features/chat/chatModel/approval.ts)
contains four nearly identical loops that walk a message list, find
the tool-call part with a given `approvalId`, mutate it, and re-derive
the message's status. Lines 25-80, 91-123, 195-225, 239-272. Same
inner shape:

```ts
return items.map((item) => {
  if (item.kind !== "message") return item;
  const content = item.content.map((part) => {
    if (!isToolCallPart(part) || part.toolCallId !== approvalId) return part;
    return { ...part, args: jsonArgs(...), result: {...} };
  });
  if (content === item.content) return item;
  return { ...item, content, status: deriveStatus(item, content) };
});
```

**Fix:** extract `mapApprovalPart(items, approvalId, mutate)` so each
of the four callers only supplies the `(part) => updatedPart` lambda
plus the optional status-derivation.

### 8. Date / timestamp formatting (3 implementations of the same options)

The same `toLocaleString` option object lives in three places:

- [features/settings/AuditLogSettings.tsx:327-338](../../src/features/settings/AuditLogSettings.tsx#L327-L338) — `formatTimestamp(iso)`
- [features/settings/AccountSessionsPanel.tsx:146-160](../../src/features/settings/AccountSessionsPanel.tsx#L146-L160) — `_formatTimestamp(iso)` (identical body)
- [features/chat/components/details/ContextPanel.tsx:330](../../src/features/chat/components/details/ContextPanel.tsx#L330) — `_formatTimestamp(iso)` (a fourth copy)
- [features/chat/utils/jsonUtils.ts:135-137](../../src/features/chat/utils/jsonUtils.ts#L135-L137) — `formatDateTime(value)` (no option object — caller-style locale)
- [features/chat/components/tools/ApprovalTool.tsx:565](../../src/features/chat/components/tools/ApprovalTool.tsx#L565) — `formatTimeShort(iso)` (different format, but same domain)
- Inline `new Date(x).toLocaleString()` / `.toLocaleDateString()` in
  [MembersSettings.tsx:253,475,521](../../src/features/settings/MembersSettings.tsx#L253),
  [MfaPanel.tsx:316](../../src/features/settings/sections/MfaPanel.tsx#L316),
  [ApiKeys.tsx:286](../../src/features/settings/sections/ApiKeys.tsx#L286).

`toLocaleString()` arguments are token-formatting decisions that should
match across the product — today they don't.

**Fix:** consolidate into `src/utils/dateFormat.ts` with
`formatDateTime`, `formatDate`, `formatTimeShort`, and have every
component import from there. Delete the local `formatTimestamp`/
`_formatTimestamp` copies.

### 9. Number formatting (`toLocaleString()` for tokens)

`value.toLocaleString() + " tok"` appears in:

- [features/chat/components/details/ContextPanel.tsx:327](../../src/features/chat/components/details/ContextPanel.tsx#L327)
- [features/chat/components/details/usage/UsageConversationView.tsx:294](../../src/features/chat/components/details/usage/UsageConversationView.tsx#L294)
- [features/chat/components/details/usage/UsageWorkspaceChart.tsx:171,191](../../src/features/chat/components/details/usage/UsageWorkspaceChart.tsx#L171)
- [features/chat/components/details/usage/UsageTopUsersTable.tsx:133](../../src/features/chat/components/details/usage/UsageTopUsersTable.tsx#L133)

**Fix:** `formatTokens(n)` already exists at
[ContextPanel.tsx:327](../../src/features/chat/components/details/ContextPanel.tsx#L327) —
hoist it to `features/chat/components/details/usage/format.ts` and
import everywhere.

### 10. Raw `fetch()` in api/\* modules bypassing the transport

The codebase has a deliberate Transport singleton at
[api/transport.ts:19](../../src/api/transport.ts#L19) so that bearer +
401 + correlation headers route through one place. The `httpGet` /
`httpPost` / `httpPatchQuery` helpers in
[api/http.ts:153-225](../../src/api/http.ts#L153-L225) use it. But a
large set of api modules still do raw `fetch()` + `correlationHeaders()`

- `assertOk()`:

* [api/meApi.ts](../../src/api/meApi.ts) — 16+ endpoints, all raw fetch.
* [api/workspaceApi.ts:142,146](../../src/api/workspaceApi.ts#L142)
* [api/workspaceMfaApi.ts:14,24](../../src/api/workspaceMfaApi.ts#L14)
* [api/mfaApi.ts:22,32,44,54,64,76](../../src/api/mfaApi.ts#L22)
* [api/avatarApi.ts:21,31](../../src/api/avatarApi.ts#L21)
* [api/skillsApi.ts:32](../../src/api/skillsApi.ts#L32)
* [api/mcpApi.ts:50,114,138](../../src/api/mcpApi.ts#L50)
* [features/auth/devIdp.ts:63,74](../../src/features/auth/devIdp.ts#L63) — the only fetch outside `api/*`, in violation of the CLAUDE.md rule "All HTTP and SSE clients live in `src/api/*`".

The comment in [http.ts:129-131](../../src/api/http.ts#L129-L131) says
"legacy api modules wrap `assertOkJson` for the JSON happy path;
everything else routes through getAppTransport()" — but right now
"legacy" is the dominant pattern. Until they're migrated, every change
to bearer / 401 / header behaviour has to be applied through two paths.

**Fix:** finish the rollout. Each api module replaces `fetch(path, {
headers: jsonHeaders() }) + assertOk` with `getAppTransport().request()`.
Once `meApi.ts` is converted, the others are mechanical. Move
`devIdp.ts` callers into `api/devIdpApi.ts`.

### 11. Persona-write + bearer-write happens in two places, out of band

- [AuthContext.tsx:181](../../src/features/auth/AuthContext.tsx#L181) writes `BEARER_STORAGE_KEY` when a session is minted.
- [DevPersonaSwitcher.tsx:52-54](../../src/features/chat/components/sidebar/DevPersonaSwitcher.tsx#L52-L54) writes `BEARER_STORAGE_KEY` directly from a sidebar component when a new persona is selected.

The component then calls `window.location.reload()` to let `AuthContext`
pick up the new bearer on the next mount. This works but the persona
switcher is doing auth-state management from a UI component instead of
calling a method on `AuthContext`.

**Fix:** add `AuthContext.switchPersona(slug)` that mints + sets the
bearer in-place (no reload). DevPersonaSwitcher calls that and never
touches `localStorage` directly.

---

## P2 — Multiple sources of truth for the same state

### 12. User profile fetched twice through two hooks

- [features/auth/useMyProfile.ts:28-57](../../src/features/auth/useMyProfile.ts#L28-L57) — lazy single-fetch of `/v1/me/profile`, returns `{ display_name, email }`. Consumed by sidebar `UserCard`, `ThreadBody`, `ThreadWelcome`.
- [features/me/useUserProfile.ts:28-78](../../src/features/me/useUserProfile.ts#L28-L78) — full single-fetch of the **same endpoint**, returns the full `UserProfile` and a `save()`. Consumed by `App`, `SettingsScreen`, settings `Profile`, `Appearance`.

Both hooks subscribe to their own copy. When the user edits their
display name in Settings, the sidebar greeting stays stale until a hard
reload. Same endpoint, two caches, no invalidation between them.

**Fix:** delete `useMyProfile`. Have callers consume the slice they
need from `useUserProfile` (or a memo-selector on top). Single fetch,
single cache, save() refreshes both consumers automatically.

### 13. Theme/appearance has three writers

- [features/me/useUserPreferences.ts:25-75](../../src/features/me/useUserPreferences.ts#L25-L75) owns the server snapshot of `appearance.theme` / `appearance.accent` / density / reduce-motion.
- [features/me/useThemeSync.ts:29-66](../../src/features/me/useThemeSync.ts#L29-L66) reads that snapshot, mirrors it into the design-system `ThemeProvider` via `setScheme`/`setAccent`, and writes `data-density` + `data-reduce-motion` onto `document.documentElement` directly.
- [features/settings/sections/Appearance.tsx:77-117](../../src/features/settings/sections/Appearance.tsx#L77-L117) calls `setScheme`/`setAccent` from the same provider when the user clicks a swatch — this is the third writer to the same theme state.
- The design-system `ThemeProvider` itself caches to its own `appearance` localStorage key.

So a theme change can come from: server snapshot, user click, or
localStorage cache, and the document attributes are written by only
one of the three. This is the underlying reason for the "flash of
wrong theme" issue users see on cold reload.

**Fix:** make `ThemeProvider` the single writer to both `document`
attributes and `localStorage`, and have `useThemeSync` push to the
provider only — no direct `documentElement` writes. The
`Appearance.tsx` UI keeps using `setScheme/setAccent`; the diff is
that those calls go through `useUserPreferences.save()` to persist,
not directly to the provider.

### 14. Workspace settings split across three modules with no shared cache

- [features/settings/useWorkspace.ts](../../src/features/settings/useWorkspace.ts) — owns workspace branding, members, invitations, billing.
- [features/settings/useWorkspaceDefaults.ts](../../src/features/settings/useWorkspaceDefaults.ts) — owns workspace model defaults.
- [api/workspaceMfaApi.ts](../../src/api/workspaceMfaApi.ts) — workspace MFA policy, accessed only from `WorkspaceMfaSettings.tsx` (no hook layer).

These are three independent fetch paths for one logical entity. An
admin toggling MFA policy doesn't invalidate the workspace hook; the
workspace settings tab can show a stale MFA banner.

**Fix:** unify under a single `WorkspaceContext` with sub-selectors,
or at minimum publish a `workspaceInvalidated` event that all three
hooks listen for.

### 15. Connector state has no cross-invalidation

- [features/connectors/useConnectors.ts](../../src/features/connectors/useConnectors.ts) — workspace-installed servers.
- [features/connectors/useConversationConnectors.ts](../../src/features/connectors/useConversationConnectors.ts) — per-conversation enabled subset, re-syncs only on visibility change.
- [features/connectors/projectConnectors.ts](../../src/features/connectors/projectConnectors.ts) — pure projection.

If a workspace admin disables connector X while a chat is open, the
chat keeps offering X in its connector popover until the user switches
chats or the tab regains visibility.

**Fix:** `useConversationConnectors` should observe the workspace
connector list (lift them into a shared context, or have
`useConnectors` expose a subscribe API) instead of relying on
visibility events.

### 16. Bearer storage has three writers

- [AuthContext.tsx:179-181](../../src/features/auth/AuthContext.tsx#L179-L181) — `setBearer()` clears or writes `BEARER_STORAGE_KEY`.
- [AuthContext.tsx:163](../../src/features/auth/AuthContext.tsx#L163) — `bearerRef.current` in-memory copy.
- [api/transport.ts:16](../../src/api/transport.ts#L16) — `_bearerProvider` closure read by every HTTP request.
- [DevPersonaSwitcher.tsx:54](../../src/features/chat/components/sidebar/DevPersonaSwitcher.tsx#L54) — fourth writer to the same localStorage key, out of band.

Two writers (AuthContext + DevPersonaSwitcher) plus two read paths
(ref + provider). The `setAuthBearerProvider` wiring keeps them in
sync, but only because every component plays by the contract. The
DevPersonaSwitcher breaks the contract today (writes localStorage
without going through `setBearer`).

**Fix:** see (11) — collapse to one writer. The transport singleton's
provider becomes the only read path.

---

## P0 — Subagent display: TWO `subagentText.ts` files

The repo has **two files with the same name** containing **different
content**, neither importing from the other:

- [features/chat/chatModel/subagentText.ts](../../src/features/chat/chatModel/subagentText.ts) (93 lines) — model-layer helpers: `subagentKeyForEvent`, `subagentNameForEvent`, `meaningfulSubagentName`, `meaningfulSubagentTitle`, `shortSubagentSummary`, `truncateText`.
- [features/chat/components/tools/subagentText.ts](../../src/features/chat/components/tools/subagentText.ts) (80 lines) — UI-layer helpers: `subagentCardTitle`, `subagentInlineTitle`, `subagentStatusLabel`, `subagentFallbackProgress`, `summarizeSubagentResult`.

`grep` shows the `components/tools/` copy has no importers — it
appears **dead** (the actual subagent cards consume
`subagentCardViewModel.ts`). The shared filename means any future
developer will land on the wrong one in their editor's quick-open.

**Fix:** delete `features/chat/components/tools/subagentText.ts` if
truly unused; otherwise rename one of them (`subagentEvents.ts` for
the chatModel one, `subagentLabels.ts` for the UI one) so they can
never be confused.

---

## P1 — Subagent status normalisation in 3 places

The same `raw → "running" | "completed" | "failed" | "cancelled" | …`
transform exists three times with **incompatible behaviour**:

- [chatModel/subagentReducer.ts:282-291](../../src/features/chat/chatModel/subagentReducer.ts#L282-L291) — `terminalStatus(raw)`: only outputs `"cancelled" | "failed" | "completed"`. Drops `"timed_out"`.
- [components/subagents/subagentCardViewModel.ts:116-134](../../src/features/chat/components/subagents/subagentCardViewModel.ts#L116-L134) — `normaliseStatus(raw, isError)`: outputs `failed | cancelled | timed_out | completed | queued | paused | running`. **Preserves** `"timed_out"`.
- [components/workspace/useSubagentActivities.ts:166-180](../../src/features/chat/components/workspace/useSubagentActivities.ts#L166-L180) — `statusFromArgs(...)`: outputs the same set as `normaliseStatus` but also accepts `"success"`, `"succeeded"`, `"error"`, `"canceled"`, `"timeout"` as aliases.

A "timed_out" subagent will display as **timed out** in the workspace
pane and the card, but the reducer's projected `SubagentEntry.status`
silently rewrites it to `"completed"`. Anything that reads the
projected entry (sidebar counts, fleet aggregates) shows the wrong
terminal state — this is a real, latent bug from the duplication.

**Fix:** export the richest normaliser (`normaliseStatus` + alias
table) from a new `chatModel/subagentStatus.ts`, have the reducer and
both other call sites import it. Add tests pinning every alias.

---

## P1 — Pause-label formatting duplicated 3× + duration duplicated 2×

- [components/subagents/SubagentCard.tsx:172-183](../../src/features/chat/components/subagents/SubagentCard.tsx#L172-L183) — `pauseShortLabel(reason)` → "approval" | "connector" | "answer"
- [components/subagents/SubagentCard.tsx:185-197](../../src/features/chat/components/subagents/SubagentCard.tsx#L185-L197) — `jumpLabelForPause(reason)` → "approval" | "connector auth" | "question"
- [components/subagents/FleetSubagentRow.tsx:181-192](../../src/features/chat/components/subagents/FleetSubagentRow.tsx#L181-L192) — `labelForPause(reason)` → "waiting on approval" | "waiting on connector" | "waiting for answer"
- [components/subagents/FleetSubagentRow.tsx:207-218](../../src/features/chat/components/subagents/FleetSubagentRow.tsx#L207-L218) — `jumpLabelForPause(reason)` — **byte-for-byte identical to the SubagentCard copy**.

Plus `formatDuration(ms)` duplicated:

- [components/subagents/subagentCardViewModel.ts:261-269](../../src/features/chat/components/subagents/subagentCardViewModel.ts#L261-L269) — `durationFromStarted`
- [components/subagents/FleetSubagentRow.tsx:220-227](../../src/features/chat/components/subagents/FleetSubagentRow.tsx#L220-L227) — `formatDuration`

**Fix:** consolidate to `components/subagents/labels.ts` with
`pauseShortLabel`, `pauseFullLabel`, `pauseJumpLabel`,
`formatSubagentDuration`. SubagentCard and FleetSubagentRow import
from there.

## P1 — `isRunningStatus` helper exists but is bypassed

[chatModel/subagentReducer.ts:71](../../src/features/chat/chatModel/subagentReducer.ts#L71)
exports `isRunningStatus(status)` but two consumers reimplement the
check inline:

- [components/workspace/AgentsTab.tsx:96](../../src/features/chat/components/workspace/AgentsTab.tsx#L96) — `entry.status === "running" || entry.status === "queued"`
- [components/workspace/WorkspacePane.tsx:255](../../src/features/chat/components/workspace/WorkspacePane.tsx#L255) — same inline check.
- [components/tools/SubagentFleetTool.tsx:36-40](../../src/features/chat/components/tools/SubagentFleetTool.tsx#L36-L40) — declares its own `NON_TERMINAL = {"queued", "running", "paused"}` set.

The three definitions don't agree — `SubagentFleetTool` counts
`"paused"` as non-terminal; the inline checks don't. A paused
subagent is therefore "running" in the fleet tool but "terminal"
in the AgentsTab counter.

**Fix:** import `isRunningStatus` everywhere. Add `isPausedStatus` if
that branch is also needed. Delete the local `NON_TERMINAL` set.

---

## P1 — Presentation payload parsing duplicated across layers

The "presentation" payload (title, status_label, kind, summary,
group_key, primary_entity, action_label, result_preview, debug_label)
is parsed in two places with overlapping behaviour:

- [chatModel/presentation.ts:80-116](../../src/features/chat/chatModel/presentation.ts#L80-L116) — `presentationFromValue(raw)`: event-level parse from backend payload.
- [components/activity/presentationHelpers.ts:20-41](../../src/features/chat/components/activity/presentationHelpers.ts#L20-L41) — `presentationFromArgs(args)`: parse from tool args object.

Both use the same `asRecord` / `stringValue` / `presentationRows`
primitives but reimplement the field-by-field extraction. If the
backend adds a new presentation field, both need patching.

**Fix:** extract `parsePresentationRecord(record)` to
`chatModel/presentation.ts` and have both helpers call it. Each
public function reduces to a one-line wrapper that picks the right
source object out of its input.

## P1 — `hiddenToolArgKeys` declared twice with the same values

- [chat/utils/jsonUtils.ts:148-165](../../src/features/chat/utils/jsonUtils.ts#L148-L165) — 15 keys.
- [chatModel/payloadHelpers.ts:69-112](../../src/features/chat/chatModel/payloadHelpers.ts#L69-L112) — same 15 keys, plus `hiddenApprovalArgKeys` (+3) and `hiddenSubagentArgKeys` (+6).

Both Set literals enumerate the same strings (`status`, `summary`,
`delta`, `event_type`, `action_id`, `approval_id`, `approval_kind`,
…). Adding a new hidden key requires touching both files; the next
reviewer who only updates one will silently leak the field into the
"visible args" panel.

**Fix:** `jsonUtils.ts` imports `hiddenToolArgKeys` from
`payloadHelpers.ts` (the richer file). Delete the local Set.

## P1 — `badgeToneForStatus` mapping duplicated 2× + a 3rd variant

- [chat/utils/toolLabels.ts:202-230](../../src/features/chat/utils/toolLabels.ts#L202-L230) — canonical: `status → "success" | "warning" | "danger"`.
- [components/workspace/DraftTab.tsx:268-283](../../src/features/chat/components/workspace/DraftTab.tsx#L268-L283) — re-declares `badgeToneForStatus` locally for `DraftStatus`. Same shape, different enum.
- [components/activity/ActivityStatusIcon.tsx:3-26](../../src/features/chat/components/activity/ActivityStatusIcon.tsx#L3-L26) — different output (icon, not tone) but the same status-string switch.

**Fix:** generalise `toolLabels.ts` into a single
`statusClassification(status) → { tone, icon }` table that DraftTab
and ActivityStatusIcon both consume. Delete the local copies.

## P1 — Tool variant hardcoded instead of `activityVariantForPresentation`

[components/activity/presentationHelpers.ts:5-18](../../src/features/chat/components/activity/presentationHelpers.ts#L5-L18)
defines `activityVariantForPresentation()` as the canonical decoder,
but tool components hardcode the result:

- [components/tools/McpTool.tsx:76,86](../../src/features/chat/components/tools/McpTool.tsx#L76) — `variant="mcp"` literal.
- [components/tools/ApprovalTool.tsx:29](../../src/features/chat/components/tools/ApprovalTool.tsx#L29) — passes literal `variant` to ActivityCard.
- Other tool components do the same — `variant="tool"` literal in JSX.

Today the hardcoded values match what the decoder would return — but
the decoder is the supposed source of truth, and the hardcoded
literals will drift the moment the decoder grows a new branch.

**Fix:** every tool component computes its variant via
`activityVariantForPresentation(presentation)` at the top of render,
never as a JSX literal.

## P1 — Tool title classification repeats `isWebSearchTool` / `isProjectSearchTool` checks

[chat/utils/toolLabels.ts:76-167](../../src/features/chat/utils/toolLabels.ts#L76-L167)
contains four functions — `toolDisplayName`, `toolRunningTitle`,
`toolCompletedTitle`, `inlineMcpToolTitle` — and each of them inlines
the same set of pattern checks (`web_search`, project search, `ls`,
…). A new tool family would have to be added in four places.

**Fix:** extract `classifyTool(toolName) → { family, kind }` once,
have each title function branch on the classification.

---

## Citation code — verified clean (gold standard)

The citation system is **the model for what good single-source-of-truth
looks like in this repo** and is worth using as a template when fixing
the items above.

- [packages/chat-surface/src/citations/](../../../../packages/chat-surface/src/citations/) and [packages/chat-surface/src/messages/](../../../../packages/chat-surface/src/messages/) own every citation-related algorithm: `linkReducer`, `registry`, `connectorLabel` (`humanizeConnector`), `sourceFreshness`, `SourceFavicon`, `citationHrefs`, `citationRemarkPlugin`, `markdownLinks`, `streamingCursor`.
- The frontend's `features/chat/components/citations/` is a **thin wrapper layer** — `SourceRow`, `SourcePreview`, `CitationChip`, `OrdinalCitationChip` import the headless component from chat-surface and inject web-specific hooks (registry resolution, preview triggers, debug breadcrumbs).
- [features/chat/chatModel/citationReducer.ts](../../src/features/chat/chatModel/citationReducer.ts) and [features/chat/chatModel/citationLinkReducer.ts](../../src/features/chat/chatModel/citationLinkReducer.ts) are pure reducers that delegate to chat-surface's `applyCitationLinkEvent` / `upsertCitation` — they own no algorithm, only the debug-callback binding.
- Frontend tests under `components/citations/*.test.tsx` test the chat-surface implementation — no local reimplementation.

The only frontend-local citation file is
[chatModel/citedToolSources.ts](../../src/features/chat/chatModel/citedToolSources.ts),
which is **frontend-specific projection logic** (turning tool calls
into Source rows). Appropriately local.

---

## Streaming / SSE — verified clean

The streaming layer is **well-partitioned** with clear single owners:

- **SSE frame parsing**: only [packages/chat-transport/src/web/sse.ts](../../../../packages/chat-transport/src/web/sse.ts) — `runSseStream()` is the single chunk-accumulator + `\n\n`-splitter + `event:`/`data:` parser.
- **Envelope validation**: [api/agentApi.ts:590-614](../../src/api/agentApi.ts#L590-L614) (`streamRunEvents`) and [api/agentApi.ts:645-679](../../src/api/agentApi.ts#L645-L679) (`streamInboxEvents`). Two consumers of one parser — different envelope shapes (`RuntimeEventEnvelope` vs `InboxEventEnvelope`), so the split is intentional. The inline shape-check in `streamInboxEvents` could become a typed predicate, but it's not duplication.
- **Event → state reduction**: only [chatModel/eventReducer.ts:40-195](../../src/features/chat/chatModel/eventReducer.ts#L40-L195) — single `applyRuntimeEvent` dispatcher. Sub-reducers (`citationReducer`, `citationLinkReducer`, `sourcesReducer`, `subagentReducer`, `draftsRegistry`) own disjoint domains and are called from one site each.
- **Sequence_no cursor + reconnect**: only [features/chat/ChatScreen.tsx](../../src/features/chat/ChatScreen.tsx) — `latestSequenceRef` (L203), updated at L472, passed to `startEventStream` (L532), reconnect timer at L529-535. `sseQueryFor()` ([api/agentApi.ts:684-693](../../src/api/agentApi.ts#L684-L693)) constructs `?after_sequence=N` for both streams so runtime and inbox cursors cannot diverge.
- **Heartbeat**: filtered at [eventReducer.ts:44](../../src/features/chat/chatModel/eventReducer.ts#L44) — single drop site.
- **Background streams**: [features/chat/runtime/useBackgroundChatStreams.ts](../../src/features/chat/runtime/useBackgroundChatStreams.ts) — single registry; per-slot sequence cursors so chat-switch resume works.

ChatScreen does **not** inline SSE parsing or event reduction. ShareScreen does not stream. The `chat-surface` package owns no streaming logic.

---

## Other clusters verified clean

- [api/transport.ts](../../src/api/transport.ts) — single `WebTransport` instance, single bearer provider.
- [app/HashRouter.ts](../../src/app/HashRouter.ts) + [app/routes.ts](../../src/app/routes.ts) + [features/settings/sections.ts](../../src/features/settings/sections.ts) — settings sections registry is consolidated post-refactor (deleted `useSettingsSection.ts`).
- [app/keymap.ts](../../src/app/keymap.ts) — single keyboard registry.
- [features/connectors/authStateDisplay.ts](../../src/features/connectors/authStateDisplay.ts) — single mapping table.
- [features/skills/useSkills.ts](../../src/features/skills/useSkills.ts) + [api/skillsApi.ts](../../src/api/skillsApi.ts) — clean single ownership.
- [features/settings/AuditLogSettings.tsx](../../src/features/settings/AuditLogSettings.tsx) + [api/auditApi.ts](../../src/api/auditApi.ts) — clean.
- Subagent display-title derivation (`formatAgentName` + `deriveTaskText` / `deriveFindingText` in [subagentCardViewModel.ts:75-83,186-232](../../src/features/chat/components/subagents/subagentCardViewModel.ts#L75-L83)) — single helper used by both `subagentCardFromArgs` and `subagentCardFromEntry`.
- Subagent activity timeline (`SubagentActivityList`) — single component consumed by both `SubagentCard` and `FleetSubagentRow`.

---

## Suggested order of attack

Ordered by "how much pain per hour of work."

1. **Bearer-key collapse + DevPersonaSwitcher** (P0 #1, P1 #11, P2 #16).
   10 minutes. Eliminates a real drift bug.
2. **Delete or rename one `subagentText.ts`** (P0, subagents section).
   5 minutes. Eliminates the dead-file landmine.
3. **Subagent status normaliser** (subagent P1).
   The "timed_out → completed" bug is the only **active known-incorrect
   behaviour** caught in this audit; should jump the queue.
4. **`errorMessage(err, fallback)`** (P1 #5).
   Drop `src/utils/errors.ts` and codemod ~89 inline + 5 named copies.
   Single biggest line-count win; unblocks (5).
5. **`useResourceWithMutation`** (P1 #6).
   Migrate 8 hooks. ~400 lines deleted; removes every `cancelledRef`
   ritual in the codebase.
6. **Status constant tables** (P0 #3).
   `RUN_STATUS`, `APPROVAL_DECISION`, `CHAT_PHASE` const objects.
   Cheap, prevents the next "is it `cancelled` or `canceled`?" bug.
7. **Subagent helpers consolidation** (subagent P1: pause labels,
   duration, `isRunningStatus`). Cluster of cheap fixes in one PR.
8. **Presentation/tool/badge helpers** (P1: `presentationFromArgs`
   vs `presentationFromValue`, `hiddenToolArgKeys`, `badgeToneForStatus`,
   `activityVariantForPresentation`, `classifyTool`). Each is small;
   ship as one "chatModel/components dedup" PR.
9. **Date and token formatters** (P1 #8, #9). Mechanical.
10. **Delete `useMyProfile`** (P2 #12). Closes the sidebar-stale-greeting
    bug.
11. **Resolve the two MFA API modules** (P0 #2). Likely deletion.
12. **Finish transport migration** (P1 #10). `meApi.ts` and friends
    stop using raw `fetch + assertOk`. Lowest immediate value but it
    is what makes the bearer flow (16) provably correct.
13. **Multi-source state items** (P2 #13, #14, #15). Larger refactors;
    benefit from (6) landing first.

The first five items alone delete ~400 lines, eliminate the two known
correctness bugs the audit surfaced (`BEARER_STORAGE_KEY` drift, the
`timed_out → completed` rewrite), and remove the dominant copy-paste
pressure on every new feature that fetches data or catches an error.
