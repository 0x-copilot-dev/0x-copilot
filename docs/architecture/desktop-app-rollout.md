# Desktop App Rollout Plan

Status: proposal · Owner: TBD · Last updated: 2026-05-14

This document is the **execution plan** for the desktop architecture defined in [desktop-app.md](desktop-app.md). The architecture spec is the _what_; this is the _how_. Read the architecture spec first — it carries the decisions table, repo layout, and phased delivery summary that this document operationalizes.

Scope of this doc:

- Concrete changes required in `apps/frontend` before any desktop code is written
- The three substitution ports (Transport, Router, KeyValueStore) with code-level signatures and migration paths
- Expanded risk-fix plans (patch budget mechanics, webview pool design, UX divergence plan, OIDC mini-spec outline)
- Per-phase Definition of Done

Out of scope (covered elsewhere): the artifact URI model (architecture §4), workbench customization rules (§7), build/signing/distribution (§11), rebase strategy (§12).

## 1. Guiding principles for execution

These trace back to architecture §2; restated here as they appear in _the order tasks are sequenced_, not the order ideas are explained.

| #   | Principle                           | Execution implication                                                                                                                                                                                          |
| --- | ----------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| E1  | **No flag day**                     | The web app's behavior must be byte-identical before and after Phase 0. Every step is a small, reversible PR that ships green.                                                                                 |
| E2  | **Ports before substrates**         | All three abstraction ports land inside `apps/frontend` first, with web-only implementations. No desktop code is written until the web app rides on the ports.                                                 |
| E3  | **Substrate-agnostic surface**      | `packages/chat-surface` must not contain a single `fetch`, `EventSource`, `window.*`, `document.*`, or `localStorage` reference. Enforced by ESLint, not convention.                                           |
| E4  | **Single source of truth for URIs** | The chat artifact URI schemes (`chat://`, `run://`, …) are parsed and built in one place — `packages/chat-surface/src/uri/`. The web app, the extension, and the deep-link handler all import the same parser. |
| E5  | **Webview is untrusted**            | Every RPC into the extension is Zod-validated. The webview never sees a bearer. No exceptions for "debug" or "telemetry" calls.                                                                                |
| E6  | **Architecture spec is canon**      | When this rollout plan and the architecture spec disagree, the architecture spec wins. PRs that diverge from architecture decisions get rejected; the spec gets amended first.                                 |

## 2. Starting state — what we actually have today

Based on a focused audit of `apps/frontend` on 2026-05-14:

| Concern           | Current shape                                                                                                                                                                                                                  | Substrate-portable?                                                 | Required change                                                 |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------- | --------------------------------------------------------------- |
| HTTP client       | [apps/frontend/src/api/http.ts:95-129](../../apps/frontend/src/api/http.ts) — centralized, pluggable bearer via `configureAuthBearerProvider()` ([AuthContext.tsx:167](../../apps/frontend/src/features/auth/AuthContext.tsx)) | Yes (already abstracted)                                            | Wrap behind `Transport.request()`                               |
| SSE               | [agentApi.ts:587-630](../../apps/frontend/src/api/agentApi.ts) `_streamSseEvents` uses `fetch` stream reader (not `EventSource`) so auth headers work                                                                          | Yes (critical — `EventSource` would have blocked desktop)           | Wrap behind `Transport.subscribeRunStream()`                    |
| Auth              | [AuthContext.tsx:51](../../apps/frontend/src/features/auth/AuthContext.tsx) reads bearer from `localStorage["enterprise.auth.bearer"]`                                                                                         | Partially — context is clean, storage is browser-specific           | Move storage behind `KeyValueStore`; AuthContext stays web-only |
| Routing           | [App.tsx:222-281](../../apps/frontend/src/app/App.tsx) hand-rolled hash routing via `window.history.pushState` + `popstate`/`hashchange`                                                                                       | No — webviews can't drive top-level URL state                       | Move behind `Router` port                                       |
| Layout            | `Sidebar`, `ChatScreen`, `DetailsPanelHost`, `Topbar` are prop-driven; `ChatScreen` owns conversation/run state ([ChatScreen.tsx:178-230](../../apps/frontend/src/features/chat/ChatScreen.tsx))                               | Yes — already decoupled                                             | Lift into `packages/chat-surface` unchanged                     |
| State management  | Local `useState` in `ChatScreen`; no Redux/Zustand                                                                                                                                                                             | Yes                                                                 | No change                                                       |
| Browser API leaks | `window.location.hostname` (MFA rpId) at [App.tsx:91](../../apps/frontend/src/app/App.tsx); `document.visibilityState` in `useConversationConnectors.ts`; `tinykeys` on `window` in `keymap.ts`                                | No                                                                  | Inject config / `PresenceSignal` / scoped DOM root              |
| Build output      | Single Vite SPA bundle; no code-split by route ([vite.config.ts](../../apps/frontend/vite.config.ts))                                                                                                                          | Yes for web; desktop needs an embeddable bundle from `chat-surface` | Add a `chat-surface` build target separate from the SPA         |

**Headline:** the frontend is in better shape than the architecture spec assumed. The API layer and AuthContext are already substitution-friendly. The work is concentrated in three ports and lifting the chat tree into a package.

## 3. The substitution seams

The plan rests on introducing exactly three abstraction ports inside `apps/frontend` **before any desktop code is written**. Once these ports exist and the web app rides them, the desktop work is a parallel `WebviewTransport` + `WebviewRouter` + `WebviewKeyValueStore` implementation — no further changes to the chat surface.

### 3.1 Transport port

Owner: `packages/chat-transport`. Architecture spec §6 defines the interface authoritatively; this section names the migration steps.

```ts
// packages/chat-transport/src/transport.ts
export interface Transport {
  request<TRes>(req: TypedRequest): Promise<TRes>;
  subscribeRunStream(
    runId: string,
    afterSequence: number | undefined,
    handler: (event: RuntimeEventEnvelope) => void,
    signal: AbortSignal,
  ): Promise<void>;
  getSession(): Promise<Session | null>;
  reauthenticate(): Promise<Session>;
  capabilities(): TransportCapabilities;
}
```

Migration steps inside `apps/frontend`:

1. Add `packages/chat-transport` with `Transport` interface + `WebTransport` impl that internally calls today's `http.ts` helpers.
2. Introduce a single `TransportProvider` React context in `apps/frontend/src/app/`.
3. Convert `agentApi.ts` exports into methods on a class that _takes_ a `Transport` (it does not import `httpGet` etc. directly).
4. Replace direct `httpGet/Post/...` callsites in feature code with `useTransport().request(...)`.
5. Delete the standalone bearer-provider plumbing from `AuthContext` once `WebTransport` owns it.

**Done when:** `apps/frontend/src/features/` contains zero references to `httpGet`, `httpPost`, `_streamSseEvents`, `EventSource`, or raw `fetch`. CI lint rule enforces this from then on.

### 3.2 Router port

Owner: `packages/chat-surface` (interface) + `apps/frontend` (web impl) + future `enterprise-chat-core` (webview impl).

```ts
// packages/chat-surface/src/routing/router.ts
export type ArtifactRoute =
  | { kind: "chat"; conversationId: string }
  | { kind: "conversation"; conversationId: string }
  | { kind: "run"; runId: string }
  | { kind: "subagent"; runId: string; subagentId: string }
  | { kind: "tool-result"; runId: string; stepId: string }
  | { kind: "mcp"; serverId: string }
  | { kind: "mcp-tool"; serverId: string; toolName: string }
  | { kind: "skill"; skillId: string }
  | { kind: "workspace"; workspaceId: string }
  | { kind: "settings"; section?: string };

export interface Router {
  current(): ArtifactRoute | null;
  navigate(route: ArtifactRoute, opts?: { replace?: boolean }): void;
  subscribe(handler: (route: ArtifactRoute | null) => void): () => void;
}
```

Two implementations:

- `HashRouter` (web) — wraps today's `routeFromLocation` + `pathForRoute` + `applyAppRoute` from `App.tsx`. Translates `ArtifactRoute` ↔ URL on the fly so deep links survive page reloads.
- `ExtensionRouter` (desktop) — proxies to the VS Code workbench. `navigate({kind: 'chat', conversationId})` becomes `vscode.commands.executeCommand('vscode.open', Uri.parse('chat://<id>'))`. `current()` returns the active editor's URI parsed via the shared `parseArtifactUri`.

Migration steps inside `apps/frontend`:

1. Add `Router` interface to `packages/chat-surface/src/routing/`.
2. Implement `HashRouter` in `apps/frontend/src/app/HashRouter.ts` using today's URL machinery; no behavior change.
3. Replace `App.tsx`'s direct `window.history` + `popstate` plumbing with `useRouter()`.
4. Settings + share routes (`/settings#<section>`, `/share/<token>`) stay web-only; the `Router` interface's `ArtifactRoute` type covers what desktop needs. Web's `HashRouter` accepts a wider `WebRoute = ArtifactRoute | WebOnlyRoute` superset.

**Done when:** `apps/frontend/src/features/` contains zero references to `window.history`, `window.location`, `popstate`, `hashchange`. `App.tsx` is the only file allowed to talk to those, via the `HashRouter` implementation.

### 3.3 KeyValueStore port

Owner: `packages/chat-surface` (interface) + `apps/frontend` (web impl) + future `enterprise-chat-core` (extension-backed impl, never used for tokens — tokens live in SecretStorage and only the extension sees them).

```ts
// packages/chat-surface/src/storage/key-value-store.ts
export interface KeyValueStore {
  get(key: string): Promise<string | null>;
  set(key: string, value: string | null): Promise<void>;
  keys(prefix?: string): Promise<string[]>;
}
```

Two implementations:

- `LocalStorageKeyValueStore` (web) — wraps `window.localStorage`. Used by `useLocalStorageState`, `useDiscoverablePref`, etc.
- `ExtensionGlobalStateStore` (desktop) — RPC to `enterprise-chat-core` which writes to the extension's `Memento`. Bearer tokens **never** touch this store on desktop — they live in `SecretStorage` and only the extension sees them.

Migration steps inside `apps/frontend`:

1. Add `KeyValueStore` interface and `LocalStorageKeyValueStore` impl.
2. Refactor `useLocalStorageState` hook to take `KeyValueStore` via context.
3. Convert direct `localStorage.getItem("enterprise.auth.bearer")` calls in `AuthContext.tsx` to use the store _on web only_. Desktop will replace `AuthContext` entirely with an extension-backed session source — see §5.4.

**Done when:** `apps/frontend/src/` has zero direct `localStorage.*` references except inside `LocalStorageKeyValueStore`.

### 3.4 What the surface package never imports

ESLint rule applied to `packages/chat-surface/src/`:

```
no-restricted-globals: ['error',
  'window', 'document', 'localStorage', 'sessionStorage',
  'history', 'navigator', 'location', 'fetch', 'EventSource', 'XMLHttpRequest'
]
```

Violations fail CI. The rule is the contract that lets the same React tree run in two substrates.

## 4. Phase 0 refactor — file-by-file plan

Goal: extract the chat surface into `packages/chat-surface`, the transport into `packages/chat-transport`, introduce the three ports, ship a green web app.

### 4.1 New packages

```
packages/chat-transport/        NEW — see §3.1
  src/transport.ts              interface
  src/types.ts                  TypedRequest, Session, RuntimeEventEnvelope re-export
  src/web/WebTransport.ts       wraps today's http.ts + _streamSseEvents
  src/web/sse.ts                fetch-based SSE reader (moved from agentApi.ts)
  vitest.config.ts
  tsconfig.json
  package.json

packages/chat-surface/          NEW — see §3.2, §3.3
  src/index.ts                  public surface
  src/routing/router.ts         Router interface
  src/storage/key-value-store.ts   KeyValueStore interface
  src/uri/schemes.ts            URI parser/builder (single source of truth)
  src/providers/
    TransportProvider.tsx
    RouterProvider.tsx
    KeyValueStoreProvider.tsx
    SessionProvider.tsx
  src/shell/
    ChatShell.tsx               new top-level entry (replaces ChatScreen as parent)
    ChatLayout.tsx
  src/messages/                 moved from apps/frontend/src/features/chat/components/
  src/composer/                 moved
  src/sidebar/                  moved (the Sidebar component, not the App-level shell)
  src/details/                  moved (DetailsPanelHost + run inspector)
  src/inspector/                stub; expanded in Phase 3
  vite.config.ts                builds an embeddable IIFE bundle for the extension
  tsconfig.json
  package.json
```

### 4.2 File moves and edits in `apps/frontend`

| Current path                                           | Action                                                                                 | Target                                                                                                     |
| ------------------------------------------------------ | -------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `src/api/http.ts`                                      | Move + simplify                                                                        | `packages/chat-transport/src/web/WebTransport.ts` (the bearer plumbing, correlation headers, retry policy) |
| `src/api/agentApi.ts`                                  | Move method bodies                                                                     | `packages/chat-transport/src/web/WebTransport.ts`                                                          |
| `src/api/_streamSseEvents`                             | Move                                                                                   | `packages/chat-transport/src/web/sse.ts`                                                                   |
| `src/features/chat/ChatScreen.tsx`                     | Move + rename                                                                          | `packages/chat-surface/src/shell/ChatShell.tsx`                                                            |
| `src/features/chat/components/sidebar/`                | Move                                                                                   | `packages/chat-surface/src/sidebar/`                                                                       |
| `src/features/chat/components/thread/`                 | Move                                                                                   | `packages/chat-surface/src/messages/`                                                                      |
| `src/features/chat/components/composer/`               | Move                                                                                   | `packages/chat-surface/src/composer/`                                                                      |
| `src/features/chat/components/DetailsPanelHost.tsx`    | Move                                                                                   | `packages/chat-surface/src/details/`                                                                       |
| `src/features/keymap.ts`                               | Refactor — accept root element instead of binding to `window`, then move               | `packages/chat-surface/src/keymap/`                                                                        |
| `src/features/auth/AuthContext.tsx`                    | Stays web-only; refactored to use `KeyValueStore`                                      | unchanged path                                                                                             |
| `src/app/App.tsx`                                      | Refactored to mount `<ChatShell/>` from chat-surface inside the existing routing shell | unchanged path                                                                                             |
| `src/app/HashRouter.ts`                                | NEW — implements `Router` for web                                                      | new file                                                                                                   |
| `src/features/connectors/useConversationConnectors.ts` | Refactor `document.visibilityState` usage behind `PresenceSignal` interface            | unchanged path                                                                                             |

### 4.3 PR sequence

Each row is a separately-mergeable PR. Stops are explicit refactor commits; no PR should mix moves and behavior changes.

| #   | PR                                                                                                                                                                                           | Net behavior change      | Verifies                             |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------ | ------------------------------------ |
| 1   | Create `packages/chat-transport` skeleton + `WebTransport` that internally calls today's `http.ts` exports                                                                                   | None                     | Typecheck + existing tests           |
| 2   | Shim `apps/frontend/src/api/http.ts` over a singleton `WebTransport` (api modules and feature callsites unchanged — `httpGet/Post/...` keep their public signatures and delegate internally) | None                     | Existing tests                       |
| 3   | Move `_streamSseEvents` into `chat-transport/src/web/sse.ts` and expose via `Transport.subscribeRunStream`                                                                                   | None                     | SSE smoke test                       |
| 4   | Add ESLint rule banning `fetch`/`EventSource`/raw HTTP in `apps/frontend/src/features/`                                                                                                      | None                     | CI                                   |
| 5   | Create `packages/chat-surface` skeleton + Router interface + `HashRouter` impl in `apps/frontend`                                                                                            | None                     | Routing manual test                  |
| 6   | Move `ChatScreen` → `chat-surface/src/shell/ChatShell.tsx`, keep `App.tsx` mounting it                                                                                                       | None                     | Full manual smoke                    |
| 7   | Move thread/composer/sidebar/details components into `chat-surface`                                                                                                                          | None                     | Full manual smoke                    |
| 8   | Refactor `keymap.ts` to bind to root element parameter; move into `chat-surface`                                                                                                             | None                     | Keyboard shortcuts manual test       |
| 9   | Add `KeyValueStore` interface + `LocalStorageKeyValueStore`; refactor `useLocalStorageState`                                                                                                 | None                     | Persisted prefs manual test          |
| 10  | Refactor `useConversationConnectors` to use `PresenceSignal` interface                                                                                                                       | None                     | Visibility-aware refresh manual test |
| 11  | Add ESLint rule banning all browser globals in `packages/chat-surface/src/`                                                                                                                  | Surfaces hidden coupling | CI                                   |
| 12  | Update [docs/architecture/service-boundaries.md](service-boundaries.md) to list the two new packages                                                                                         | None                     | Doc review                           |

**Total: ~12 small PRs over ~2 weeks of one engineer.** Each PR is independently revertible.

**PR #5 DoD fix (executed 2026-05-15):** [apps/frontend/src/features/settings/useSettingsSection.ts](../../apps/frontend/src/features/settings/useSettingsSection.ts) was the last `popstate` / `hashchange` / `window.history` site in `apps/frontend/src/features/`. Audit showed the `useSettingsSection()` hook had zero production consumers — only its own test exercised it. Deleted the dead hook + test; lifted the still-live `migrateLegacySettingsPath()` into `HashRouter.ts` (URL routing concern, properly belongs there); kept the constants in a new `settings/sections.ts`. Coverage preserved via new [HashRouter.test.ts](../../apps/frontend/src/app/HashRouter.test.ts) (+12 tests). One remaining `window.history.replaceState` in [LoginScreen.tsx:582](../../apps/frontend/src/features/auth/LoginScreen.tsx#L582) is SSO callback URL hygiene, not routing — flagged for a separate auth-flow port (likely with the OIDC mini-spec in §5.4).

**PR #6 scope refinement (executed 2026-05-15):** the earlier draft row called for "Move ChatScreen → chat-surface/src/shell/ChatShell.tsx, keep App.tsx mounting it." Audit showed this creates a broken intermediate state: chat-surface would import sub-components from `apps/frontend/src/features/chat/components/` (package depending on app internals — layering violation) until PR #7 moved them too. Rescoped PR #6 to **establish the chat-surface mount + provider infrastructure** (TransportProvider, RouterProvider, ChatShell wrapper) without physically moving any component. App.tsx now wraps its render tree in `<ChatShell transport={…} router={…}>`. Subsequent PRs migrate components bottom-up (leaf components first, ChatScreen last) — each migration swaps singleton/prop access for `useTransport()` / `useRouter()` hooks inside the new package. This restructures the PR #6/#7 sequence into a bottom-up component migration with no broken intermediate states.

**PR #2 scope refinement (executed 2026-05-14):** the earlier draft of this row called for replacing every `httpGet/Post/...` callsite with `useTransport().request(...)`. Audit of `apps/frontend/src/` showed feature code never imports `http.ts` directly — only api modules do, and only three non-api files touch http.ts (`AuthContext`, `AuthContext.test.tsx`, `observability/otel.ts`). The minimum-blast-radius change was therefore to shim `http.ts` over a singleton `WebTransport` while keeping its public signatures intact. Net effect: api modules and features stayed untouched in this PR; the substrate boundary is now owned by `WebTransport` in `packages/chat-transport`. The per-callsite rewrite that the earlier draft envisaged becomes natural during PR #6+ when api modules migrate into `packages/chat-surface` and call `Transport` directly (the `http.ts` shim is then deleted as its last consumers leave `apps/frontend`).

### 4.4 Phase 0 Definition of Done

- [ ] `packages/chat-transport` and `packages/chat-surface` published as workspace packages
- [ ] `apps/frontend` ships unchanged user-visible behavior (manual smoke + existing tests green)
- [ ] ESLint rules in §3.4 enforced in CI
- [ ] Zero references in `apps/frontend/src/features/` to: `httpGet`, `httpPost`, `_streamSseEvents`, `EventSource`, raw `fetch`, `localStorage`, `window.history`, `popstate`, `hashchange`
- [ ] `apps/frontend` bundle size delta within ±5% (large negative would indicate the chat-surface bundle isn't being tree-shaken; large positive would indicate duplication)
- [ ] [docs/architecture/service-boundaries.md](service-boundaries.md) updated with `chat-surface` and `chat-transport` ownership

## 5. Risk mitigation plans

Architecture spec §15 lists the risks at the conceptual level. This section operationalizes the top four.

### 5.1 Patch budget mechanics (mitigates R2)

The architecture spec sets the policy: max 10 patches, max 30 lines each, extension-first rule (§7, D7). Execution mechanics:

**Patch series structure (`apps/desktop/patches/`):**

```
series                           ordered list, one filename per line
README.md                        index with one paragraph per patch
0001-rebrand-about.patch
0002-hide-file-open-folder.patch
...
```

**Required header on every patch:**

```
# Subject: <one-line summary>
# Reason: <why this can't be an extension>
# Owner:  <github handle>
# Filed:  <YYYY-MM-DD>
# Review: <YYYY-MM-DD>   (must be ≤ 90 days from Filed)
# Upstream: <link to filed upstream issue/PR asking for the API, if applicable>
```

**CI enforcement:**

- `apps/desktop/scripts/check-patches.sh` runs in CI:
  - Fail if any patch lacks the header
  - Fail if any patch > 30 modified lines (excluding context lines)
  - Fail if total patch count > 10
  - Fail if any patch's `Review` date is in the past — forces re-justification
- Nightly job: `apps/desktop/scripts/rebase-upstream.sh` fetches latest Code – OSS minor tip, applies series, builds, reports red/green to a Slack channel and a dashboard. Failures filed as GitHub issues automatically.

**Quarterly review checklist (live in `apps/desktop/patches/REVIEW.md`):**

1. For each patch: has the upstream API landed that would let us drop it?
2. For each patch: has the patch grown? If yes, why — file an issue.
3. Are any extensions doing work that could push back into patches? (Anti-pattern.)
4. Total drift to upstream tip — how many lines? Trending?

### 5.2 Webview pool design (mitigates R1)

Architecture spec §3 D9 sets the policy: one full webview per chat tab; shared lightweight webview for non-chat viewers. Execution detail:

**Tier definition (lives in `enterprise-chat-core`):**

```ts
// apps/desktop/extensions/enterprise-chat-core/src/webview/pool.ts
export type WebviewTier = "hot" | "warm" | "cold";

export interface PooledWebview {
  panel: vscode.WebviewPanel;
  tier: WebviewTier;
  uri: ArtifactRef;
  lastShownAt: number;
}

export interface WebviewPool {
  acquire(uri: ArtifactRef, tier: WebviewTier): Promise<PooledWebview>;
  release(uri: ArtifactRef): void;
  stats(): { hot: number; warm: number; cold: number; total: number };
}
```

**Tier policy:**

| Tier | Examples                             | `retainContextWhenHidden` | Pool cap                      | Eviction                                                                                            |
| ---- | ------------------------------------ | ------------------------- | ----------------------------- | --------------------------------------------------------------------------------------------------- |
| Hot  | active `chat://`, Run Inspector      | `true`                    | Unbounded (one per open chat) | None — chats are closed by user                                                                     |
| Warm | 3 most recently viewed artifact tabs | `false`                   | 3                             | LRU; destroyed on hide after cap exceeded                                                           |
| Cold | All other artifact tabs              | `false`                   | Unbounded                     | Destroyed on hide; rehydrated from URI on show, with `getState`/`setState` for scroll position only |

**Bundle strategy:** all viewers mount the same `chat-surface` JS bundle from `media/`. The webview's preload script reads its URI and routes to the appropriate viewer component. One bundle, one HMR target, one cache.

**Perf gate (Phase 3 sign-off):**

- Open 10 artifact tabs (mix of `tool-result://`, `mcp://`, `run://`, `subagent://`) + 2 active chats
- Capture process explorer snapshot on a mid-range Mac (16 GB) and Windows (16 GB)
- Budget: ≤ 800 MB resident across the workbench renderer + all webview iframes
- Budget: p95 reopen time of a cold viewer ≤ 300 ms
- If budgets miss: revisit tier definitions before declaring Phase 3 done

**Note on Chromium process model:** the official VS Code webview docs ([code.visualstudio.com/api/extension-guides/webview](https://code.visualstudio.com/api/extension-guides/webview)) describe webviews as "iframe-like" and "resource-heavy," recommending they "be used sparingly." Whether each webview gets its own renderer process depends on Chromium site isolation under the running Electron version — neither guaranteed nor relied upon by this plan. The pool design is motivated by VS Code's own guidance plus measured baseline overhead, not by an assumed 1:1 webview-to-process mapping.

### 5.3 UX divergence plan (mitigates R4)

The user reflex problem: VS Code shipped with File menu, "Open Folder…", terminal, file explorer. Without intervention, our users will hit these affordances and find dead ends. Architecture spec §4 (Q1 answer) and §16 (D8) commit to "no local file access in phase 1, editor area is artifacts only."

**Required actions, by phase:**

| Action                                                                             | Phase | Mechanism                                                                                        |
| ---------------------------------------------------------------------------------- | ----- | ------------------------------------------------------------------------------------------------ |
| Default activity bar = Chats (not Explorer)                                        | 1     | `product.json` + patched workbench layout default                                                |
| Welcome editor = `chat://welcome`                                                  | 1     | `product.json`                                                                                   |
| Hide File menu items: Open File, Open Folder, Open Recent, New Window from Profile | 1     | Patched menu contributions (patch 0002)                                                          |
| Remove "Get Started" walkthrough                                                   | 1     | `product.json` `disabledFeatures`                                                                |
| Remove Explorer view from default sidebar                                          | 1     | `enterprise-chat-ui` overrides default view container order                                      |
| Hide bottom Panel by default; remove Terminal from default contributions           | 2     | `product.json` + view config                                                                     |
| First-launch onboarding screen explaining the product                              | 2     | `enterprise-chat-ui` custom welcome content; one-time dismissal stored in extension global state |
| Status bar persona + workspace items                                               | 2     | `enterprise-chat-ui` status bar contributions                                                    |
| Replace empty-workbench state ("No folder opened")                                 | 1     | Patch 0003 — replaces the empty editor placeholder with a "Start a chat" call to action          |
| Curate command palette: hide irrelevant commands (Git, Tasks, Terminal-specific)   | 4     | `enterprise-chat-ui` command palette `when` clauses + workbench config                           |

**Anti-action:** do not rename "Workbench" or other internal terminology in user-facing UI. Either accept the term, or hide the surface that exposes it. Renaming via patches creates merge-conflict churn at every upstream change.

**Deferred to Phase 5+:** local file access for "connected workspaces." Cursor's pattern, but with a clear consent UX (per-workspace file scope, revocable in settings). Not in MVP.

### 5.4 OIDC mini-spec outline (mitigates R3)

The architecture spec §8 covers the desktop OIDC flow at the conceptual level. Before Phase 2 begins, a dedicated mini-spec must answer the following, owned by whoever takes Q1 from the open-questions list:

1. **IdP choice** — first-party (extends `services/backend`) or vendor (Auth0 / WorkOS / Okta)?
2. **Token shape** — opaque vs. JWT for the access token; claims schema if JWT
3. **Refresh strategy** — rotation on every use (recommended) or sliding window
4. **Device identity** — `device_id` GUID generation, storage, rotation policy, server-side revocation API
5. **Multi-tenant claims** — single session with `workspace_id` claim (recommended, matches architecture §8) or separate sessions per workspace
6. **Logout semantics** — revoke refresh token server-side, local SecretStorage clear, webview storage clear; the full sequence
7. **Refresh stampede prevention** — request queue + single in-flight refresh promise; concrete code shape
8. **Audit hooks** — every mint/refresh/revoke emits an audit event with `device_id`, `client_version`, `os`
9. **MDM flow** — `prompt=none` first attempt to ride pre-existing SSO; fall back to interactive
10. **Device-bound tokens** — deferred to Phase 2+ pending a compliance buyer ask; spec leaves a placeholder

**Required artifacts before Phase 2 codes the flow:**

- `docs/architecture/desktop-auth.md` covering items 1–10 above
- Decision recorded in this rollout plan §7 with date and owner
- Threat model walkthrough with someone on the security side

**Test cases (must pass before Phase 2 sign-off):**

- First launch, no tokens → system browser opens → callback received → tokens persisted
- Second launch with valid tokens → no browser, immediate session
- Access token expired, refresh token valid → silent refresh
- Refresh token expired → re-launch system browser, no error to user
- 401 mid-stream → refresh, resume stream from highest `sequence_no`
- Logout → tokens gone from SecretStorage (verified by inspecting OS keychain)
- Two concurrent requests during refresh → exactly one refresh, both requests retry once
- Workspace switch → no full re-auth, just `POST /v1/me/active-workspace`; subsequent calls carry the new workspace claim

## 6. Per-phase Definition of Done

Architecture spec §14 lists exit criteria narratively. Restated as checklists for execution. A phase ships only when all boxes are checked, signed by phase owner.

### Phase 0 — Refactor for substitution

(Mirrors §4.4 above; restated here so phase gates live in one place.)

- [ ] `packages/chat-transport` + `packages/chat-surface` shipped
- [ ] `apps/frontend` ports refactor complete (§4.2)
- [ ] ESLint rules enforced in CI (§3.4)
- [ ] Web app smoke + existing tests green
- [ ] Bundle size delta within ±5%
- [ ] [docs/architecture/service-boundaries.md](service-boundaries.md) updated

### Phase 1 — Desktop shell

- [ ] `apps/desktop/code-oss/` vendored at pinned Code – OSS minor tag
- [ ] `apps/desktop/product/` complete: `product.json`, icons (Mac iconset + Windows ico), branding strings
- [ ] Initial patch series (≤ 5 patches): rebrand, hide File menu items, replace welcome editor, default activity bar order, replace empty workbench state
- [ ] `apps/desktop/scripts/build.sh` builds unsigned Mac Universal + Windows MSI
- [ ] `apps/desktop/scripts/rebase-upstream.sh` is functional (run manually once, green)
- [ ] CI matrix: Linux build (verification), unsigned Mac build, unsigned Windows build
- [ ] App launches on Mac + Windows, shows branded empty workbench, opens welcome editor
- [ ] No telemetry traffic to Microsoft endpoints (verified by traffic capture)

### Phase 2 — Bundled chat MVP

- [ ] [docs/architecture/desktop-auth.md](desktop-auth.md) authored and reviewed (§5.4)
- [ ] `enterprise-chat-core`: OIDC client, loopback server, `SecretStorage` wiring, transport RPC bridge, `chat://` URI content provider, SSE pump
- [ ] `enterprise-chat-ui`: custom editor for `chat://`, Chats tree view in left sidebar, persona status bar item, webview entrypoint mounting `chat-surface`
- [ ] All OIDC test cases from §5.4 pass on Mac + Windows
- [ ] Webview CSP forbids non-same-origin requests (verified by attempting a `fetch` from inside the webview and confirming it's blocked)
- [ ] Tokens verified in OS keychain (Keychain Access on Mac, Credential Manager on Windows)
- [ ] SSE reconnect on network blip works (manual: airplane mode toggle mid-stream, verify no message loss)
- [ ] End-to-end smoke: launch → log in → list chats → open chat → send message → receive streamed response → reconnect after disconnect

### Phase 3 — Run inspector + artifacts

- [ ] `RunInspector` view in auxiliary bar (tool calls, subagents, citations, approvals)
- [ ] `run://`, `subagent://`, `tool-result://` URI providers + custom editors
- [ ] Click in inspector → opens artifact tab pinned next to chat
- [ ] Approve/reject pending tool call from inspector round-trips correctly
- [ ] WebviewPool implemented per §5.2
- [ ] Perf gate passes per §5.2 (≤ 800 MB resident, p95 reopen ≤ 300 ms)
- [ ] Activity log of a full agent run reproduces the web app's right-panel content

### Phase 4 — MCP / Skill / Workspace surfaces

- [ ] `mcp://`, `mcp-tool://`, `skill://`, `workspace://` URI providers + custom editors + tree views
- [ ] MCP install / auth / uninstall via card editor; OAuth flows route through system browser
- [ ] Workspace switcher in status bar, hands off to backend per §5.4 test case
- [ ] Skill bundles browseable (read-only in MVP)
- [ ] Side-by-side visual review of desktop vs. web for each artifact view; no fidelity regressions

### Phase 5 — Distribution hardening

- [ ] Apple Developer ID Application certificate installed on release runner; notarization workflow green
- [ ] Windows EV cert on hardware token attached to release runner; Authenticode signing green
- [ ] Update server endpoints live: `download`, `update`, `releases` (per architecture §11)
- [ ] Two channels operational: `stable` (auto-update on), `enterprise-mdm` (auto-update off)
- [ ] Force-upgrade flow tested end-to-end with a `minSupportedVersion` bump
- [ ] Crash reporter endpoint live; test crash on Mac + Windows produces a dump
- [ ] Telemetry consent dialog wired; default behavior matches channel policy
- [ ] `apps/desktop/RELEASE.md` runbook published and rehearsed
- [ ] Internal `stable` release distributed and used by at least 5 internal users for a week
- [ ] MDM pilot validated by IT (Jamf for Mac, Intune for Windows)

## 7. Decisions required, with owners

Architecture spec §16 lists the open questions. Restated here with explicit owners and deadlines for execution. Items unowned by phase kickoff block phase entry.

| ID  | Question                                                                | Required by                                    | Owner | Status                                                                     |
| --- | ----------------------------------------------------------------------- | ---------------------------------------------- | ----- | -------------------------------------------------------------------------- |
| Q1  | OIDC provider — first-party or vendor?                                  | Before Phase 2                                 | TBD   | Open                                                                       |
| Q2  | Workspace switch — single session with claim, or per-workspace session? | Before Phase 2 (the auth spec depends on this) | TBD   | Open                                                                       |
| Q3  | Offline mode — read-only cached browse, or "no network, no app"?        | Before Phase 3                                 | TBD   | Open — defaults to "no network, no app" if unanswered                      |
| Q4  | Skill editing — read-only in MVP, or Monaco-edited bundles in Phase 5?  | Before Phase 4                                 | TBD   | Open — defaults to read-only                                               |
| Q5  | Update-success telemetry — collect install/update outcomes?             | Before Phase 5                                 | TBD   | Open                                                                       |
| Q6  | Crash reporter — owned endpoint or vendor (Sentry / Bugsnag)?           | Before Phase 5                                 | TBD   | Open — defaults to owned endpoint                                          |
| Q7  | Third-party extension API surface — eventual yes or no?                 | Pre-MVP design influence                       | TBD   | Open — if yes, bundled extensions should model the public API from day one |

## 8. What this plan deliberately does _not_ do

Things proposed and rejected; recorded here so they don't resurface mid-execution:

- **No "lite" web app to mirror desktop's restrictions.** The web app keeps full routing, browser-only features, and the Settings/Share surfaces. The chat surface package is the shared piece, not the entire app.
- **No early bet on a marketplace extension form factor.** Architecture spec §3 D1; revisit only after Phase 5 ships.
- **No abstraction over the IdP.** A `pluggable provider` adapter is YAGNI until we actually need to swap providers. The OIDC mini-spec picks one.
- **No multi-window product UX in Phase 1–4.** VS Code multi-window works; the _product_ model is one account per window. Phase 5+ if there's a real ask.
- **No code-split route bundles in `chat-surface` until measured.** Measure the bundle. If it's > 1 MB gzipped, split. If not, don't.
- **No shared package for `apps/frontend`'s App-level routing or login screens.** They stay web-only. The chat surface is the substitution boundary, not the entire frontend.
- **No "headless" mode of `chat-surface` for tests.** Tests use the same React tree with mock `Transport`/`Router`/`KeyValueStore` ports. One source of truth, even for test doubles.

## 9. Cross-references

- Architecture spec: [desktop-app.md](desktop-app.md)
- Service boundaries (must be updated in Phase 0): [service-boundaries.md](service-boundaries.md)
- Streaming contract (consumed unchanged): [runtime-stream-handshake.md](runtime-stream-handshake.md)
- Workspace/tenancy semantics (consumed by workspace switcher): [multi-tenant-deployment.md](multi-tenant-deployment.md), [workspace-topology.md](workspace-topology.md)
- Pending sibling spec: `desktop-auth.md` (to be authored before Phase 2 — §5.4)
