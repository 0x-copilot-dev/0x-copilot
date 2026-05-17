# Desktop App PRD — Atlas Workspace on Custom Electron

Status: **draft for review** · Owner: TBD · Last updated: 2026-05-17

Supersedes [docs/architecture/desktop-app.md](../../architecture/desktop-app.md) and replaces [docs/architecture/desktop-app-rollout.md](../../architecture/desktop-app-rollout.md) once approved. Phase 0 of this plan rewrites desktop-app.md and deletes the rollout doc.

This is the **master plan**. Every subagent reads it top to bottom before producing their own sub-PRD. Cross-cutting decisions live here so they have one source of truth.

---

## 0. TL;DR

The substrate decision (custom Electron vs VS Code extension vs Code – OSS fork) is **deferred to an empirical spike** that runs before any other work. See **§5 Phase S** for the spike protocol. The rest of this PRD is written assuming the working recommendation — custom Electron + `packages/chat-surface` mounted in a single `BrowserWindow` — and will be revised in place if the spike outcome differs.

This PRD describes the **full 1.0 product**, not an MVP cut. All three renderer tiers, full auth, signing/notarization, auto-update, telemetry, crash reporting, and the agent-generated-adapter pipeline with server-side registry all ship as part of 1.0. Phases are an _engineering_ sequence (what to build in what order so each phase builds on a working base), not a release cut.

**Renderer strategy — three tiers** (see §3.4):

1. **Tier 1 — hand-built first-party adapters** for the named SaaS in the design: Email, Salesforce, Sheets, Slides. Each implements the frozen `SaaSRendererAdapter` contract.
2. **Tier 2 — agent-generated adapters** for the long tail (HubSpot, Linear, Monday, Notion, Zendesk, …). Code-genned from constrained layout templates, AST-scanned, sandboxed render-only, persisted per tenant. Eventually promoted to a shared server-side registry on quality criteria.
3. **Tier 3 — `GenericStructuredDiff`** fallback that renders any MCP tool-call payload as the right-rail PENDING-DIFF card from the design. Always available as the safety net when tier-1 / tier-2 don't match or throw.

**Load-bearing architectural rule** (D28): adapters are **pure render functions of state**. They have no transport, no MCP client, no `fetch`, no `window`. Every action — fetch current state, compute diff, apply, approve, reject, suggest-changes — lives in the host (`TcSurfaceMount` inside chat-surface). This makes tier-2 sandboxing trivial (no privileged objects to leak), keeps tier-1 / tier-2 / tier-3 swappable from the host's perspective, and concentrates audit-relevant code (security, retention, idempotency) in one place.

**Phase order (engineering sequence)**: Phase S (spike + decision) → Phases 0–3 (foundation, shell, chat-surface, destinations) → Phase 4 (adapter contract + tier-1 renderers + tier-3 fallback) → Phase 5 (auth + working-product smoke) → Phase 6 (tier-2 codegen pipeline + sandbox) → Phase 7 (tier-2 sharing + server-side registry + review pipeline) → Phase 8 (hardening: signing, updater, crash, telemetry).

Parallel subagents within each phase, integration branch per phase, orchestrator (Claude) merges and resolves conflicts.

## 1. Goals & non-goals

### Goals

- macOS and Windows desktop builds, signed and notarized, with auto-update
- One React tree for chat, layout, destinations, and per-SaaS renderers — byte-identical between `apps/frontend` (web) and `apps/desktop` (renderer)
- Production-grade OIDC + OS keychain from day one (no dev-IdP in shipped binaries)
- Per-SaaS renderers driven by `MCP` tool calls; diff approval inline on the renderer; swimlane timeline scrubbing per surface
- Single source of truth for: routing (hash routes inside `chat-surface`), keybindings (one registry in `chat-surface`), URI scheme parsing (one module), Transport contract

### Non-goals

- No fork of VS Code / Code – OSS / Monaco
- No third-party extension marketplace (first-party renderers only — see D7)
- No iframe embedding of SaaS web apps as the primary surface model (a `WebviewView` escape-hatch is permitted per renderer)
- No filesystem editing surface in MVP (the renderer area is for agent artifacts only)
- No Linux release (Linux is CI verification only)
- No multi-window or multi-account per session in MVP
- No desktop-side LLM provider keys; provider calls remain server-side via `services/ai-backend`

## 2. Principles → outcomes

| Principle                  | What it forces in this plan                                                                                                                                                                                                                           |
| -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **DRY**                    | One React tree across web + desktop. One Transport interface. One URI scheme module. One keybinding registry. Per-SaaS renderers are plain React components, not framework-flavored extensions.                                                       |
| **Substitution**           | `Transport`, `SurfaceHost`, `Router`, `KeyValueStore`, `PresenceSignal` are ports. Web and desktop swap implementations; the surface code never branches on substrate.                                                                                |
| **Simple & elegant**       | Thin Electron shell (main + preload + renderer). No fork, no patches, no rebase pipeline, no webview-RPC protocol version. Each port has the smallest possible API.                                                                                   |
| **Single source of truth** | Routing lives in `chat-surface` only (not in the workbench _and_ us). Keybindings live in one place. The artifact URI scheme is parsed in one module imported by both ends. The Transport contract is the only way the renderer talks to the backend. |

## 3. Architecture

### 3.1 Component map

```
┌─────────────────────────────────────────────────────────────────┐
│ apps/desktop                                  Electron app      │
│ ┌─────────────────────┐ ┌─────────────────────────────────────┐ │
│ │ main (Node)         │ │ renderer (Chromium)                 │ │
│ │ - window lifecycle  │ │ ┌─────────────────────────────────┐ │ │
│ │ - OIDC + safeStorage│ │ │ <ChatShell                      │ │ │
│ │ - HTTP+SSE pump     │ │ │   transport={IpcTransport}      │ │ │
│ │ - IPC handlers      │ │ │   router={HashRouter}           │ │ │
│ │ - electron-updater  │ │ │   storage={IpcKeyValueStore}    │ │ │
│ └──────────▲──────────┘ │ │   surfaceHost={null /* MVP */}  │ │ │
│            │ IPC        │ │ />                              │ │ │
│            │ (Zod)      │ │ │                               │ │ │
│            ▼            │ └─┴───────────────────────────────┘ │ │
│ ┌─────────────────────┐ │                                     │ │
│ │ preload             │ └─────────────────────────────────────┘ │
│ │ contextBridge       │                                         │
│ └─────────────────────┘                                         │
└─────────────────────────────────────────────────────────────────┘
                           ▲
                           │ chat-surface bundle (same bytes as web)
                           │
┌─────────────────────────────────────────────────────────────────┐
│ packages/                                                       │
│   chat-surface         shell + destinations + ThreadCanvas      │
│                        + composer + palette + ports             │
│   chat-transport       Transport interface + WebTransport       │
│                        + IpcTransport                           │
│   surface-renderers    Email / Sheet / Salesforce / Slide       │
│                        renderers registered into chat-surface   │
│   api-types            existing                                 │
│   design-system        existing                                 │
└─────────────────────────────────────────────────────────────────┘
                           │ HTTPS / SSE
                           ▼
                  backend-facade:8200 (unchanged)
                           │
                  backend, ai-backend (unchanged)
```

### 3.2 Target directory layout

```
apps/desktop/                                    NEW
├── package.json
├── tsconfig.json
├── electron-builder.yml
├── main/
│   ├── index.ts                                 app entry (createWindow, app lifecycle)
│   ├── window.ts                                BrowserWindow create/restore
│   ├── ipc/
│   │   ├── handlers.ts                          register all ipcMain handlers
│   │   └── schemas.ts                           Zod schemas for the IPC channel
│   ├── auth/
│   │   ├── oidc-client.ts                       PKCE flow
│   │   └── loopback-server.ts                   ephemeral http://127.0.0.1:{port}/cb
│   ├── secret-storage.ts                        Electron safeStorage adapter
│   ├── transport-bridge.ts                      fetch + SSE pump; bearer injection
│   ├── deep-links.ts                            enterprise:// protocol handler
│   ├── updater.ts                               electron-updater wiring
│   └── crash-reporter.ts                        crashReporter.start(...)
├── preload/
│   └── bridge.ts                                contextBridge.exposeInMainWorld
├── renderer/
│   ├── index.html
│   ├── bootstrap.tsx                            ReactDOM.createRoot + <ChatShell />
│   └── ipc-transport-factory.ts                 builds IpcTransport from window.bridge
└── build/
    ├── mac/
    │   ├── entitlements.plist
    │   ├── Info.plist.template
    │   └── notarize.ts
    ├── windows/
    │   ├── wix.xml
    │   └── sign.ps1
    └── linux/                                   CI verification only

packages/chat-surface/                           EXISTING (grows)
├── src/
│   ├── ports/                                   NEW — port interfaces only
│   │   ├── Transport.ts                         re-export from chat-transport
│   │   ├── SurfaceHost.ts                       mount/unmount/pause/snapshot (DEFINED, not consumed in MVP)
│   │   ├── Router.ts                            navigate/back/subscribe
│   │   ├── KeyValueStore.ts                     existing — formalize
│   │   └── PresenceSignal.ts                    existing — formalize
│   ├── shell/                                   NEW
│   │   ├── ChatShell.tsx
│   │   ├── AppRail.tsx                          52 px destinations rail
│   │   ├── ContextPanel.tsx                     224 px per-destination filter list
│   │   └── Topbar.tsx
│   ├── routing/                                 EXISTING — extend
│   │   ├── HashRouter.ts                        Router port impl
│   │   ├── uri/
│   │   │   ├── schemes.ts                       constants
│   │   │   └── parser.ts                        parseArtifactUri / buildArtifactUri
│   │   └── route-table.ts                       destination → component map
│   ├── destinations/                            NEW
│   │   ├── chats/
│   │   │   ├── ChatsDestination.tsx
│   │   │   ├── ChatsSidebar.tsx                 project → thread tree
│   │   │   └── (thread canvas mounted from thread-canvas/)
│   │   ├── home/HomeDestination.tsx
│   │   ├── inbox/InboxDestination.tsx
│   │   ├── todos/TodosDestination.tsx
│   │   ├── projects/ProjectsDestination.tsx
│   │   ├── library/LibraryDestination.tsx
│   │   ├── agents/AgentsDestination.tsx
│   │   ├── tools/ToolsDestination.tsx
│   │   ├── connectors/ConnectorsDestination.tsx
│   │   ├── team/TeamDestination.tsx
│   │   └── memory/MemoryDestination.tsx
│   ├── thread-canvas/                           NEW
│   │   ├── ThreadCanvas.tsx                     CSS grid: [tabs+surface | TcChat] [TcSwimlanes]
│   │   ├── TcTabs.tsx
│   │   ├── TcSurfaceMount.tsx                   reads SurfaceRegistry, renders component for URI
│   │   ├── TcSwimlanes.tsx                      per-surface bead timeline + scrubber
│   │   ├── TcChat.tsx                           message list + Activity/Approvals tabs
│   │   └── TcInlineDiff.tsx                     inline annotation with Approve/Reject
│   ├── composer/                                NEW
│   │   ├── Composer.tsx
│   │   ├── ToolPicker.tsx
│   │   ├── ModelPicker.tsx
│   │   └── MentionPopover.tsx
│   ├── palette/                                 NEW
│   │   └── CommandPalette.tsx
│   ├── surfaces/                                NEW
│   │   ├── SurfaceRegistry.ts                   register(scheme, component); resolve(uri)
│   │   └── types.ts                             SurfaceRendererProps
│   ├── messages/                                EXISTING
│   ├── citations/                               EXISTING
│   ├── presence/                                EXISTING
│   ├── providers/                               EXISTING
│   ├── storage/                                 EXISTING
│   └── index.ts                                 public surface

packages/chat-transport/                         EXISTING (grows)
├── src/
│   ├── transport.ts                             EXISTING — Transport interface
│   ├── types.ts                                 EXISTING
│   ├── web/                                     EXISTING — WebTransport
│   └── ipc/                                     NEW
│       ├── IpcTransport.ts                      renderer-side impl
│       ├── rpc-protocol.ts                      shared Zod schemas
│       └── window-bridge.ts                     typed accessor for window.bridge

packages/surface-renderers/                      NEW
├── package.json
├── tsconfig.json
└── src/
    ├── index.ts                                 registerAll() — called once at bootstrap
    ├── email/
    │   ├── EmailRenderer.tsx                    matches email://
    │   ├── EmailDiffOverlay.tsx                 the "PENDING · DRAFTED FROM …" UX
    │   └── index.ts                             registerSurface('email', EmailRenderer)
    ├── sheet/
    │   ├── SheetRenderer.tsx                    matches sheet-row://
    │   ├── SheetCellDiff.tsx
    │   └── index.ts
    ├── salesforce/
    │   ├── OpportunityRenderer.tsx              matches sf-opp://
    │   ├── SfFieldDiff.tsx
    │   └── index.ts
    └── slide/
        ├── SlideRenderer.tsx                    matches slide://
        ├── SlideDiff.tsx
        └── index.ts
```

### 3.3 Key port signatures (the contracts subagents work against)

These are the load-bearing interfaces. Phase 0 freezes them before any other agent starts. After Phase 0 merges, they change only by explicit orchestrator decision with a migration note.

```ts
// packages/chat-transport/src/transport.ts (EXISTING — verbatim)
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

// packages/chat-surface/src/ports/SurfaceHost.ts (NEW)
export interface SurfaceHost {
  mountSurface(args: {
    id: string;
    uri: string;
    rect: DOMRect;
  }): Promise<SurfaceHandle>;
  unmountSurface(id: string): Promise<void>;
  pauseSurface(id: string): Promise<void>;
  resumeSurface(id: string): Promise<void>;
  snapshotSurface(id: string, t: number): Promise<Blob>;
  onSurfaceEvent(handler: (event: SurfaceEvent) => void): () => void;
}

// packages/chat-surface/src/ports/Router.ts (NEW — formalized from existing)
export interface Router {
  current(): Route;
  navigate(route: Route): void;
  back(): void;
  subscribe(handler: (route: Route) => void): () => void;
}
export interface Route {
  destination: Destination;
  view?: string;
  id?: string;
}

// packages/chat-surface/src/surfaces/SaaSRendererAdapter.ts (NEW — FROZEN in Phase 4)
//
// PURE RENDER ONLY (D28). Adapters MUST NOT:
//   - import or call Transport, MCP, fetch, XMLHttpRequest, EventSource
//   - access window, document, localStorage, history, navigator
//   - emit side effects of any kind
//
// All actions live in the host (TcSurfaceMount inside chat-surface):
//   - The host fetches current state via MCP.
//   - The host subscribes to the agent's run stream for proposed diffs.
//   - The host renders the Approve / Reject / Suggest-changes buttons
//     AROUND the adapter's output (the adapter does not render them).
//   - The host calls MCP on approve; queues regen on reject-with-feedback.
//
// Same contract for tier-1 (hand-built), tier-2 (agent-generated), and
// tier-3 (the generic fallback — implements this same interface).
export interface SaaSRendererAdapter<TResource = unknown, TDiff = unknown> {
  readonly scheme: string; // 'email', 'sf-opp', 'hubspot-deal', …
  readonly matches: (uri: string) => boolean;

  readonly renderCurrent: (state: TResource) => React.ReactElement;
  readonly renderDiff: (diff: TDiff) => React.ReactElement;

  readonly metadata: {
    readonly origin: "first-party" | "agent-generated" | "community";
    readonly generatedAt?: string; // ISO; tier-2 only
    readonly generatorModel?: string; // tier-2 only
    readonly schemaVersion: number; // bumps when the contract changes
  };
}

// packages/chat-surface/src/surfaces/SurfaceRegistry.ts (NEW — FROZEN in Phase 4)
//
// Resolution order: exact scheme match → tier-3 fallback (scheme === '*').
// Multiple tier-2 versions for the same scheme: highest non-broken version wins.
export function registerAdapter(adapter: SaaSRendererAdapter): void;
export function resolveAdapter(uri: string): SaaSRendererAdapter | null;
export function unregisterAdapter(scheme: string, version?: number): void; // tier-2 hot-swap
export function markBroken(
  scheme: string,
  version: number,
  reason: string,
): void;
export function clearRegistry(): void; // tests only

// apps/desktop/main/ipc/schemas.ts (NEW)
export const IpcRequest = z.discriminatedUnion("method", [
  z.object({
    id: z.string(),
    method: z.literal("transport.request"),
    params: TypedRequestSchema,
  }),
  z.object({
    id: z.string(),
    method: z.literal("transport.subscribeRunStream"),
    params: z.object({
      runId: z.string(),
      afterSequence: z.number().optional(),
    }),
  }),
  z.object({
    id: z.string(),
    method: z.literal("transport.cancelSubscription"),
    params: z.object({ subscriptionId: z.string() }),
  }),
  z.object({
    id: z.string(),
    method: z.literal("transport.getSession"),
    params: z.object({}),
  }),
  z.object({
    id: z.string(),
    method: z.literal("transport.reauthenticate"),
    params: z.object({}),
  }),
  z.object({
    id: z.string(),
    method: z.literal("storage.get"),
    params: z.object({ key: z.string() }),
  }),
  z.object({
    id: z.string(),
    method: z.literal("storage.set"),
    params: z.object({ key: z.string(), value: z.unknown() }),
  }),
  z.object({
    id: z.string(),
    method: z.literal("storage.delete"),
    params: z.object({ key: z.string() }),
  }),
]);
```

### 3.4 Adapter strategy — three tiers

Every renderable artifact resolves to one `SaaSRendererAdapter`. The agent's MCP tool calls produce structured payloads `(resource, proposed_diff)`; the host (`TcSurfaceMount`) calls `SurfaceRegistry.resolveAdapter(uri)` and renders.

```
                              host (TcSurfaceMount)
                              │
                              │ uri = 'email://draft-7'
                              ▼
            ┌─────────────────────────────────────────────┐
            │ SurfaceRegistry.resolveAdapter(uri)         │
            └────────┬────────────────────┬───────────────┘
                     │                    │
            scheme match? ── yes ─────┐  │ no
                     │                │  │
                     ▼                ▼  ▼
       ┌──────────────────────┐  ┌──────────────────────┐
       │ Tier 1 — first-party │  │ Tier 2 — generated   │
       │ packages/surface-    │  │ {userData}/adapters/ │
       │ renderers/email/...  │  │ {scheme}-v{n}.js     │
       │ Hand-built. Pixel-   │  │ Code-genned by agent │
       │ perfect. Design-     │  │ from templates.      │
       │ system aligned.      │  │ Sandboxed (D29).     │
       └──────────┬───────────┘  └──────────┬───────────┘
                  │                         │ render throws or absent
                  │                         ▼
                  │             ┌─────────────────────────┐
                  │             │ Tier 3 — fallback       │
                  └────────────►│ GenericStructuredDiff   │
                  no match      │ Renders MCP payload as  │
                                │ resource id + field     │
                                │ diff + 'Open in {SaaS}' │
                                │ link. Always works.     │
                                └─────────────────────────┘
```

Each tier implements the same `SaaSRendererAdapter` contract (§3.3). The host doesn't know or care which tier resolved — same `renderCurrent` / `renderDiff` call shape.

**Pure-render discipline (D28) — load-bearing.** The adapter contract has no side-effect-capable methods. Adapters cannot fetch, apply, approve, or reject. The host owns:

- Fetching current state via MCP (`transport.request` + `subscribeServerSentEvents`)
- Receiving proposed diffs from the agent's run stream
- Rendering the Approve / Reject / Suggest-changes buttons _around_ the adapter's output
- Calling MCP to apply on approve
- Triggering agent regeneration on reject-with-feedback or render failure

This split lets us sandbox tier-2 trivially (no privileged objects to leak — see D29), keep all three tiers swappable from the host's perspective, and concentrate audit-relevant code (security, retention, idempotency) in one place.

**Tier-2 lifecycle** (full detail in §9.5):

1. User opens an artifact whose scheme has no tier-1 match and no working tier-2 adapter.
2. Host immediately renders tier-3 (no waiting).
3. Host emits `adapter.generation.requested(scheme, sample_state)` to backend.
4. Backend agent codegen tool picks a layout template (form / table / kanban / definition-list), generates the adapter source, runs the quality bar (Q1–Q6), persists to `{userData}/adapters/{scheme}-v{n}.js`.
5. Host hot-swaps the new adapter into the registry; next render uses tier-2.
6. On any subsequent render error: mark broken, fall back to tier-3, queue regeneration.

## 4. Decision register

**Pending Phase S outcome:** D1, D7, D8, D11, D13, D14 all assume custom Electron. If the spike concludes that a VS Code extension or Code – OSS fork is the better fit, these flip and the spec is amended in place.

| #   | Decision                      | Choice                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | Rationale                                                                                                                                                                                                                                                              | Rejected                                                                                                                                                                                                                                                                           |
| --- | ----------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| D1  | Substrate                     | Custom Electron app                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | Single source of truth + simple-elegant (see §3 reasoning); design isn't editor-shaped                                                                                                                                                                                 | Fork Code – OSS (loses on single-source + simple-elegant; analogy to Cursor doesn't transplant)                                                                                                                                                                                    |
| D2  | Cross-platform structure      | Single `apps/desktop`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | One codebase; OS specifics in `build/{mac,windows,linux}/`                                                                                                                                                                                                             | Separate `apps/mac` + `apps/windows`                                                                                                                                                                                                                                               |
| D3  | React UI sharing              | `packages/chat-surface`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | Same React tree mounts in browser + Electron renderer                                                                                                                                                                                                                  | Duplicate UI; framework-specific UIs                                                                                                                                                                                                                                               |
| D4  | Transport                     | `Transport` port; `WebTransport` (existing) + `IpcTransport` (new)                                                                                                                                                                                                                                                                                                                                                                                                                                        | Substrate substitution                                                                                                                                                                                                                                                 | Direct `fetch` everywhere                                                                                                                                                                                                                                                          |
| D5  | Token storage                 | Electron `safeStorage` → OS keychain                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | Production-grade from day one                                                                                                                                                                                                                                          | localStorage; plain disk                                                                                                                                                                                                                                                           |
| D6  | Auth flow                     | OIDC authorization code + PKCE; loopback `127.0.0.1:{port}` in main process                                                                                                                                                                                                                                                                                                                                                                                                                               | Real auth from day one                                                                                                                                                                                                                                                 | Dev-IdP minting baked into binary                                                                                                                                                                                                                                                  |
| D7  | Per-SaaS renderers            | Plain React components in `packages/surface-renderers`, registered via build-time call to `registerSurface(scheme, Component)`                                                                                                                                                                                                                                                                                                                                                                            | Smallest surface; one source of truth; can evolve to public API later                                                                                                                                                                                                  | VS Code extension API (overkill; we're not third-party); ad-hoc `if (scheme === 'email')` (no DRY)                                                                                                                                                                                 |
| D8  | Artifact identity             | URI scheme: `chat://`, `email://{draft_id}`, `sheet-row://{sf_id}/{row}`, `sf-opp://{org}/{id}`, `slide://{deck}/{n}`, `run://`, `subagent://`, `tool-result://`                                                                                                                                                                                                                                                                                                                                          | Deep links work; URIs are stable identity; registry keys on scheme                                                                                                                                                                                                     | Component mounting only without URIs (no deep links, no shareability)                                                                                                                                                                                                              |
| D9  | Window model                  | Single `BrowserWindow`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | Match the design (one workspace, one window)                                                                                                                                                                                                                           | Multi-window                                                                                                                                                                                                                                                                       |
| D10 | Workspace                     | 1:1 with backend workspace                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | No new abstraction                                                                                                                                                                                                                                                     | VS Code-style "workspace folder"                                                                                                                                                                                                                                                   |
| D11 | Update                        | `electron-updater` on `stable`; disabled on `enterprise-mdm` via build flag                                                                                                                                                                                                                                                                                                                                                                                                                               | Standard Electron pattern                                                                                                                                                                                                                                              | Squirrel (no reason to bring it without the fork)                                                                                                                                                                                                                                  |
| D12 | Code signing                  | Apple Developer ID + notarytool; Windows EV cert on hardware token                                                                                                                                                                                                                                                                                                                                                                                                                                        | Unavoidable                                                                                                                                                                                                                                                            | Self-signed                                                                                                                                                                                                                                                                        |
| D13 | Diff UX                       | Inline annotation React component (`TcInlineDiff` + per-renderer overlays) anchored to a region; structured (not text-diff editor)                                                                                                                                                                                                                                                                                                                                                                        | Matches the Atlas design; each renderer owns its diff vocabulary                                                                                                                                                                                                       | Monaco diff editor (wrong shape — our diffs are field-level / region-level, not text-line-level)                                                                                                                                                                                   |
| D14 | Swimlane scrubbing            | Renderer pauses live state and renders cached snapshot at time `t`; main process is not involved                                                                                                                                                                                                                                                                                                                                                                                                          | Renderer owns its own snapshot semantics; nothing third-party to capture                                                                                                                                                                                               | Main-process WebContents pause/snapshot (we don't host third-party WebContents)                                                                                                                                                                                                    |
| D15 | Telemetry                     | Custom event pipeline — disabled by default in MVP; opt-in dialog later                                                                                                                                                                                                                                                                                                                                                                                                                                   | No Microsoft telemetry to strip                                                                                                                                                                                                                                        | Inherit anything                                                                                                                                                                                                                                                                   |
| D16 | Crash reporting               | Electron `crashReporter` → our endpoint; renderer JS errors opt-in only                                                                                                                                                                                                                                                                                                                                                                                                                                   | Standard; respects privacy                                                                                                                                                                                                                                             | Third-party (Sentry/Bugsnag) — possible later                                                                                                                                                                                                                                      |
| D17 | Multi-account                 | Single account per window in MVP                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | Match the design; defer complexity                                                                                                                                                                                                                                     | Multi-account in one window                                                                                                                                                                                                                                                        |
| D18 | Comments                      | Default to none (per [CLAUDE.md](../../../CLAUDE.md)); add only when the WHY is non-obvious                                                                                                                                                                                                                                                                                                                                                                                                               | Project standard                                                                                                                                                                                                                                                       | Liberal comments                                                                                                                                                                                                                                                                   |
| D19 | Docs locations                | Architecture in `docs/architecture/`; this plan in `docs/plan/desktop/`; per-component sub-PRDs in `docs/plan/desktop/phase-{n}/{slug}.md`; no per-package READMEs unless user-facing                                                                                                                                                                                                                                                                                                                     | One home per kind                                                                                                                                                                                                                                                      | Scattered docs                                                                                                                                                                                                                                                                     |
| D20 | Branch / merge                | git worktrees per agent; integration branch `desktop/phase-{n}` between agent branches and `main`; orchestrator resolves conflicts                                                                                                                                                                                                                                                                                                                                                                        | Main always green; conflict resolution batched per phase                                                                                                                                                                                                               | Direct-to-main per agent                                                                                                                                                                                                                                                           |
| D21 | Agent flow                    | Spec-first, then continue: each agent writes its sub-PRD then implements in one run                                                                                                                                                                                                                                                                                                                                                                                                                       | Faster end-to-end; orchestrator reviews PRD + diff together at merge                                                                                                                                                                                                   | Pause-for-review at sub-PRD; doubles agent count                                                                                                                                                                                                                                   |
| D22 | Old specs                     | Phase 0 rewrites `desktop-app.md` to reflect this PRD; `desktop-app-rollout.md` is deleted                                                                                                                                                                                                                                                                                                                                                                                                                | This PRD replaces both                                                                                                                                                                                                                                                 | Archive (keeps confusing alternate spec visible)                                                                                                                                                                                                                                   |
| D23 | Renderer escape hatch         | Each renderer may, as a last resort, embed a `<webview>` tag opened against the SaaS URL with `partition="persist:{tenant}-{saas}"` for unsupported workflows                                                                                                                                                                                                                                                                                                                                             | Pragmatic fallback when API coverage gaps appear                                                                                                                                                                                                                       | No escape hatch (forces users out to OS browser); always-embed (defeats the whole renderer model)                                                                                                                                                                                  |
| D24 | Secret storage scoping        | One ciphertext file per `(workspace_id, server)` under `{userData}/secrets/{workspace_id}/{server_kind}/{server_id}.bin`, protected by Electron `safeStorage`. Main-process API is scoped: `get(workspace_id, server_kind, server_id)` / `set(...)` / `deleteWorkspace(workspace_id)`. No global accessor. Active-workspace gate: requests from renderer carry the active `workspace_id` and main rejects mismatches against the session JWT's workspace claim.                                           | First-principles reasoning in §6.8 below                                                                                                                                                                                                                               | Single keychain entry per server (single point of failure: one decrypt operation reveals all workspaces' tokens at once); single global secrets file (worst — no compartmentalization at all)                                                                                      |
| D25 | Time Machine surface          | Time Machine _is_ the `ThreadCanvas` swimlane in advanced mode — not a separate destination, not a separate URI scheme. `TcSwimlanes` owns scrub + branch-from-here + restore-this-state + pinned beads + snap-to-now; `TcChat` shows ghost messages when the swimlane is scrubbed off-now.                                                                                                                                                                                                               | DRY — the swimlane and the time-machine surface are the same primitive; the prototype's separate `Time Machine.html` was an iteration artifact, not a production split.                                                                                                | Separate `time-machine://` URI scheme (two truths for the same data); separate destination (forces users to switch context away from the thread they're inspecting)                                                                                                                |
| D26 | Renderer-to-renderer coupling | None at the client. Cross-renderer effects (sheet edit → slide refresh) flow through the backend run stream as separate `RuntimeEventEnvelope` events; the renderers observe the same stream and react independently.                                                                                                                                                                                                                                                                                     | DRY (one event source); single source of truth (backend is authoritative); avoids client-side bus that would double the event model.                                                                                                                                   | Client-side event bus (two event models to keep in sync); direct renderer-to-renderer imports (couples renderers, defeats the registry)                                                                                                                                            |
| D27 | Renderer architecture         | Three-tier adapter model: tier-1 hand-built first-party, tier-2 agent-generated, tier-3 `GenericStructuredDiff` fallback (§3.4). All three implement the same `SaaSRendererAdapter` contract.                                                                                                                                                                                                                                                                                                             | Solves the 100+ SaaS scaling problem at the architecture level; tier-3 gives 100% coverage from day one; tier-1 reserved for high-value workflows; tier-2 amortizes long-tail cost via agent code-gen from constrained templates.                                      | One renderer per SaaS (doesn't scale to 100+); only generic diff (loses rich preview for Email and other high-value cases); only iframe-the-SaaS (brittle, security-fraught, breaks on SaaS redesigns)                                                                             |
| D28 | Adapter purity                | Adapters are pure render-only — no transport, no MCP client, no fetch, no `window`. Approve / Reject / Suggest-changes buttons live in the host (`TcSurfaceMount`). Host owns all I/O and side-effects: fetch current state, compute diff, apply diff, queue regen.                                                                                                                                                                                                                                       | Trivial sandbox for tier-2 (nothing privileged to leak — D29 reduces to "wrap a function and time it"). DRY (action handling in one place across all SaaS, not duplicated per renderer). Pure functions are testable, deterministic, substrate-neutral, hot-swappable. | Adapter-as-controller (each renderer handles its own actions): N× the code, N× the sandbox burden, N× the bug surface. Action handlers inside generated tier-2 code would defeat sandboxing entirely.                                                                              |
| D29 | Tier-2 sandbox                | Tier-2 adapters loaded via dynamic `import()` of `{userData}/adapters/{scheme}-v{n}.js`. No privileged globals in scope (no transport, no fetch, no IPC, no `window` / `document` / `localStorage`). Render wrapped in error boundary + 100 ms wall-clock timeout. AST-scanned at install against an import allowlist (`react`, `@enterprise-search/design-system` primitives, a small set of pure utilities). On throw or timeout: roll back to tier-3, mark adapter version broken, queue regeneration. | Pure-render contract (D28) makes a privileged-object-free sandbox sufficient. Defense in depth via timeout + error boundary + static analysis. Adapter literally has nothing to act with.                                                                              | Iframe-per-adapter (high overhead at scale); Web Worker (can't return JSX); full process isolation (overkill for pure render); allowlist disabled (lets in `fetch`, `eval`, etc.)                                                                                                  |
| D30 | Phase order for renderers     | Phase 4 ships: the `SaaSRendererAdapter` contract; tier-3 `GenericStructuredDiff`; all four named tier-1 renderers (Email, Salesforce, Sheets, Slides). Phase 6 ships: tier-2 codegen pipeline + sandbox + quality gates. Phase 7 ships: tier-2 server-side registry + sharing + review pipeline. The contract is frozen in Phase 4 and does not change in Phase 6 or 7 — only its consumers grow.                                                                                                        | Lock the contract once, with all named tier-1 cases proving it accommodates real renderers. Tier-2 is a strictly additive consumer of the same contract. Tier-3 is the safety net throughout. Engineering sequence lets each phase build on a verified base.           | Ship contract + 1 renderer + tier-3 first, defer tier-1 fill-in (user explicitly chose full product, not MVP); ship tier-2 before tier-1 (no production reference for the contract); ship all three tiers in one phase (too much novel work in one batch, hard to triage failures) |

## 5. Workstreams

Each phase below names its parallel agents. Inside a phase, agents run concurrently. Between phases, orchestrator merges the integration branch to main before launching the next phase.

**Phase S runs before Phase 0** and gates all subsequent work.

### Phase S — Substrate spike (sub-phases S0 → S1 → S2)

Goal: pick the substrate empirically. Build the **same minimal surface** (Email composer with inline diff approval, driven by a deterministic mocked MCP `Gmail.draft.create` event stream) in two host substrates. Compare on substrate LOC, build complexity, dev experience, and visual fidelity.

The renderer code is shared and substrate-independent — that's the point. If the spike's renderer can't be mounted unchanged in both substrates, our port design is wrong and we redesign it before continuing.

**Sub-phase S0 — Spike prep (1 sequential agent)**

| Agent | Slug         | Scope                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| ----- | ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| S0    | `spike-prep` | (1) Scaffold `packages/surface-renderers/` (package.json, tsconfig.json, src/index.ts). (2) Add `packages/surface-renderers/src/email/`: `EmailRenderer.tsx` (the composer-shaped renderer matching the screenshot — To/Cc/Subject/body, PENDING block highlight, Send/Schedule footer, Auto-saved label), `EmailDiffOverlay.tsx` (the floating STREAMING/Approve-&-send card), `index.ts` (registers `email://`). (3) Add `packages/chat-surface/src/thread-canvas/TcInlineDiff.tsx` (generic inline-annotation primitive used by the email renderer). (4) Add `packages/chat-surface/src/surfaces/{SurfaceRegistry.ts,types.ts}` and `packages/chat-surface/src/routing/uri/{schemes.ts,parser.ts}`. (5) Add `packages/chat-transport/src/mock/{MockTransport.ts,email-fixture.ts}` — deterministic streaming of a fake `Gmail.draft.create` event sequence over ~3 seconds, then one PENDING DIFF event. Implement against the existing `Transport` interface (`subscribeServerSentEvents`, `request`, `getSession`, `capabilities`). (6) Update root `package.json` workspaces array and `tsconfig.base.json` path mappings to register `@enterprise-search/surface-renderers`. (7) Render tests for `EmailRenderer`, `EmailDiffOverlay`, `TcInlineDiff`, `parser`. (8) Write the sub-PRD at `docs/plan/desktop/phase-0.5/S0-spike-prep.md`. Branch: `spike/0.5-prep`. |

**Sub-phase S1 — Variant builds (2 parallel agents)**

Each variant agent depends on S0 merged. Each builds its substrate-specific shell, mounts `EmailRenderer`, wires `MockTransport`, and writes a short README with `npm` scripts to launch and stop.

| Agent | Slug             | Scope                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ----- | ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| S1-A  | `spike-vscode`   | `apps/vscode-spike/`: marketplace-style VS Code extension (NOT a fork — runs against vanilla VS Code or VS Code Insiders the developer has installed). `package.json` with extension manifest (`engines.vscode`, `activationEvents`, `contributes.customEditors` for `email://`). `src/extension.ts` registers a `CustomEditorProvider` that opens `email://draft-1` and mounts a webview. `src/webview-bootstrap.tsx` mounts `<EmailRenderer>` from `@enterprise-search/surface-renderers` wired through a webview-side `Transport` adapter that proxies to the extension host via `postMessage`. `src/host-rpc.ts` extension-side: receives RPCs, delegates to `MockTransport`, fans SSE events back over `postMessage`. `README.md` with `npm run spike:vscode:dev` (uses `vsce package` + opens in current VS Code with `--extensionDevelopmentPath`). No fork. |
| S1-B  | `spike-electron` | `apps/electron-spike/`: minimal Electron app. `package.json` with electron + electron-builder dev deps. `main/index.ts` creates a `BrowserWindow` loading `renderer/index.html`. `preload/bridge.ts` exposes a typed `window.bridge` IPC channel. `renderer/bootstrap.tsx` mounts `<EmailRenderer>` from `@enterprise-search/surface-renderers` wired through a renderer-side `Transport` adapter that proxies to main via `window.bridge`. `main/transport-bridge.ts` receives RPCs, delegates to `MockTransport`, fans SSE events back over IPC. `README.md` with `npm run spike:electron:dev`.                                                                                                                                                                                                                                                                   |

**Sub-phase S2 — Decision (orchestrator, sequential)**

After both variants land, orchestrator:

1. Launches both, screen-records the user flow (open → see PENDING block stream in → STREAMING card appears → Approve → state updates).
2. Counts substrate LOC for each variant (everything outside the shared renderer + transport + chat-surface diff component).
3. Measures cold launch time and resident memory at idle.
4. Captures qualitative notes: how clean is the seam, what did the substrate fight us on, what came for free.
5. Writes the decision report at `docs/plan/desktop/phase-0.5/S2-decision.md` with: side-by-side metrics, recording links, recommendation, and which of D1/D7/D8/D11/D13/D14 are confirmed or flipped.
6. **Surfaces the report to the user.** User picks. Orchestrator does not pick unilaterally — this is the whole reason we're spiking.

Spike exit: User commits to substrate; PRD §4 decisions update in place; the losing variant's `apps/{slug}/` is deleted (history kept in git).

### Phase 0 — Foundation & ports (sequential, single agent)

Goal: freeze the contracts every other agent works against, and replace the old specs.

| Agent | Slug         | Scope                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| ----- | ------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 0A    | `foundation` | (1) Rewrite `docs/architecture/desktop-app.md` to reflect this PRD. (2) Delete `docs/architecture/desktop-app-rollout.md`. (3) Update `docs/architecture/service-boundaries.md` to add `apps/desktop` and `packages/surface-renderers`. (4) Define ports in `packages/chat-surface/src/ports/` (`Transport.ts` re-export, `SurfaceHost.ts`, `Router.ts`, formalize `KeyValueStore.ts` and `PresenceSignal.ts`). (5) Define `packages/chat-surface/src/routing/uri/{schemes.ts,parser.ts}`. (6) Define `packages/chat-surface/src/surfaces/{SurfaceRegistry.ts,types.ts}`. (7) Scaffold empty `apps/desktop/` and `packages/surface-renderers/` package.json + tsconfig.json + tsconfig path mappings. (8) ESLint rule: `chat-surface` cannot import `window`/`document`/`fetch`/`localStorage` bare. |

Exit criteria: typecheck passes across all packages; the new specs review cleanly; ports compile; ESLint rule blocks a deliberately-bad sample.

### Phase 1 — Shell & substrate (4 parallel agents)

Depends on: Phase 0 merged.

| Agent | Slug                | Scope                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| ----- | ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1A    | `electron-shell`    | `apps/desktop/main/index.ts`, `window.ts`, `crash-reporter.ts`, `deep-links.ts` (protocol registration only — no routing logic), build config (`electron-builder.yml`, `package.json` scripts), bare-bones `preload/bridge.ts` exposing a typed `bridge.ipc` channel placeholder, `renderer/index.html` + `renderer/bootstrap.tsx` mounting a `<ChatShell />` stub. Smoke test: `npm run desktop:dev` opens a window with the chat-surface shell rendered. |
| 1B    | `chat-shell-layout` | `packages/chat-surface/src/shell/{ChatShell,AppRail,ContextPanel,Topbar}.tsx`. Stubs only for destination content (renders the destination name). No per-destination logic yet. CSS grid: 52 px rail + 224 px context + 1fr main + 380 px right rail (toggleable).                                                                                                                                                                                         |
| 1C    | `ipc-transport`     | `packages/chat-transport/src/ipc/{IpcTransport.ts,rpc-protocol.ts,window-bridge.ts}` + `apps/desktop/main/ipc/{handlers.ts,schemas.ts}` + `apps/desktop/main/transport-bridge.ts` (HTTP+SSE pump). Bearer-token plumbing stub (real OIDC arrives in Phase 5).                                                                                                                                                                                              |
| 1D    | `routing-palette`   | `packages/chat-surface/src/routing/{HashRouter.ts,route-table.ts}` (Router port impl), `packages/chat-surface/src/palette/CommandPalette.tsx` (~150 LOC, Cmd+K). Wire `ChatShell` to listen on the `Router` port.                                                                                                                                                                                                                                          |

Exit criteria: `apps/desktop` launches a window showing the empty shell with rail + context + main + right rail; Cmd+K opens the palette; `IpcTransport.request` round-trips through `transport-bridge` and back; tests for routing and palette pass.

### Phase 2 — Chat surface depth (5 parallel agents)

Depends on: Phase 1 merged.

| Agent | Slug               | Scope                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| ----- | ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| 2A    | `chats-sidebar`    | `packages/chat-surface/src/destinations/chats/ChatsSidebar.tsx`: project → thread tree (collapsible carets, search, fullscreen toggle, active highlighting). Reads thread list via `Transport.request`.                                                                                                                                                                                                                                                                                           |
| 2B    | `thread-canvas`    | `packages/chat-surface/src/thread-canvas/{ThreadCanvas,TcTabs,TcSurfaceMount}.tsx`. CSS grid: `[tabs + SurfaceMount                                                                                                                                                                                                                                                                                                                                                                               | TcChat] [TcSwimlanes]`. `TcSurfaceMount`reads`SurfaceRegistry.resolve(uri)`; for Phase 2 mounts a placeholder when the registry returns null. |
| 2C    | `tc-swimlanes`     | `packages/chat-surface/src/thread-canvas/TcSwimlanes.tsx`: per-surface bead timeline, playhead, transport controls, keyboard ← / → / Esc, "Snap to now", pinned beads, "Branch from here" and "Restore this state" actions when scrubbed off-now. **This component is the Time Machine surface (D25)** — no separate file, no separate URI. Beads sourced from the run-events stream; branch/restore call backend endpoints over `Transport.request`.                                             |
| 2D    | `tc-chat-composer` | `packages/chat-surface/src/thread-canvas/TcChat.tsx`, `packages/chat-surface/src/composer/{Composer,ToolPicker,ModelPicker,MentionPopover}.tsx`. Mode-aware (Studio / Auto / Focus): Studio shows TcChat with messages; Focus collapses to Activity / Approvals tabs. **TcChat also shows ghost-message previews when TcSwimlanes is scrubbed off-now** — the previewed messages render in a muted treatment with a "viewing 11:43:36" indicator. Subscribes to swimlane scrub state via context. |
| 2E    | `tc-inline-diff`   | `packages/chat-surface/src/thread-canvas/TcInlineDiff.tsx`: the generic inline-annotation primitive that each per-SaaS renderer composes (Pending / Streaming / Accepted / Rejected states; Approve / Reject buttons; provenance pill).                                                                                                                                                                                                                                                           |

Exit criteria: ThreadCanvas renders for the chats destination with a placeholder surface; Cmd+K can open a thread; composer sends a request and streams the response into TcChat; the swimlane scrubs against a mocked event stream; TcInlineDiff renders all states in Storybook (or equivalent fixture).

### Phase 3 — Destinations (4 parallel agents, runs alongside Phase 2)

Depends on: Phase 1 merged. **Does not depend on Phase 2** — destinations are leaf pages.

| Agent | Slug                           | Scope                                                                                                                                             |
| ----- | ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| 3A    | `dest-home-inbox-todos`        | `packages/chat-surface/src/destinations/{home,inbox,todos}/*.tsx`. Reads list payloads via `Transport.request`; renders cards + filters + counts. |
| 3B    | `dest-projects-library`        | `packages/chat-surface/src/destinations/{projects,library}/*.tsx`.                                                                                |
| 3C    | `dest-agents-tools-connectors` | `packages/chat-surface/src/destinations/{agents,tools,connectors}/*.tsx`.                                                                         |
| 3D    | `dest-team-memory`             | `packages/chat-surface/src/destinations/{team,memory}/*.tsx`.                                                                                     |

Exit criteria: every destination renders without errors; navigating between destinations via Cmd+K and AppRail clicks works; context panel per destination shows filters and counts.

### Phase 4 — Adapter contract + tier-3 + tier-1 renderers (two sub-rounds, 5 parallel agents total)

Depends on: Phase 2 merged (needs `ThreadCanvas.TcSurfaceMount`).

Phase 4 has two sub-rounds because all other agents depend on the contract that 4A freezes.

**Sub-round 4-a (1 sequential agent, blocks 4-b)**

| Agent | Slug               | Scope                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| ----- | ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 4A    | `adapter-contract` | Freeze `packages/chat-surface/src/surfaces/{SaaSRendererAdapter.ts,SurfaceRegistry.ts}` per §3.3 (PURE RENDER ONLY — D28). Rewrite the existing spike-prep registry from `{component → component}` to `{scheme → adapter}` with hot-swap (`unregisterAdapter`, `markBroken`, version disambiguation). Implement the host-side glue in `packages/chat-surface/src/thread-canvas/TcSurfaceMount.tsx`: resolve the adapter for the URI, call `renderCurrent` / `renderDiff`, surround the adapter's output with the host-owned Approve / Reject / Suggest-changes controls (D28), wrap the call in an error boundary + 100 ms render timeout, fall back to tier-3 on throw/timeout (D29). Tests: hot-swap, miss → tier-3 fallback, render-with-timeout, error boundary, version disambiguation. Also: extend the `chat-surface` ESLint rule with a new layer for `packages/surface-renderers/src/**` that bans `Transport`, `fetch`, `XMLHttpRequest`, `EventSource`, `window`, `document`, `localStorage`, `sessionStorage`, `import()` of non-allowlisted modules — enforces D28/D29 at lint time for tier-1 (tier-2's enforcement is a separate AST scanner in Phase 6). |

**Sub-round 4-b (5 parallel agents, after 4A merges)**

| Agent | Slug                 | Scope                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| ----- | -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 4B    | `tier3-generic-diff` | `packages/chat-surface/src/surfaces/GenericStructuredDiff.tsx`: tier-3 adapter (`scheme: '*'`, resolved last in `resolveAdapter`). Renders any MCP tool-call payload as a structured diff card — resource id, field changes (old → new), reasoning text, "Open in {SaaS}" deep link. This is the right-rail PENDING DIFF card from the design. Tests: missing fields, deeply nested payloads, very large payloads (truncation strategy), unknown SaaS, no `proposed` payload (renders current state only).                                           |
| 4C    | `tier1-email`        | Rewrite the spike-prep `EmailRenderer` to conform to the new adapter contract (pure render only — strip any state hooks that were action-handling; pull approve/reject up into the host's `TcSurfaceMount` wrapper). Full screenshot fidelity: composer chrome (To/Cc/Subject/body), PENDING block highlight, provenance pill, streaming cursor, Send/Schedule footer, "Auto-saved · 2s ago" indicator. Tests: render-current shape, render-diff shape, accessibility (semantic HTML for the composer; tabbable Approve/Reject in the host wrapper). |
| 4D    | `tier1-salesforce`   | `packages/surface-renderers/src/salesforce/{OpportunityRenderer,OpportunityDiff}.tsx` + `index.ts` (registers `sf-opp://`). Pure render of Opportunity fields (Account, Stage, Close Date, ARR, Owner, custom fields). Diff renderer overlays field-level pending changes with provenance. Tests: matches contract; renders unknown custom fields gracefully (falls through to a generic field row).                                                                                                                                                 |
| 4E    | `tier1-sheets`       | `packages/surface-renderers/src/sheet/{SheetRenderer,SheetDiff}.tsx` + `index.ts` (registers `sheet-row://`). Pure render of a sheet region (header row + data rows). Diff renderer highlights changed cells. Formula chrome (`D5 =SUM(D2:D4) * RENEWAL_UPLIFT`-style) is read-only in this phase. Tests: contract conformance; very wide sheets (column virtualization).                                                                                                                                                                            |
| 4F    | `tier1-slides`       | `packages/surface-renderers/src/slide/{SlideRenderer,SlideDiff}.tsx` + `index.ts` (registers `slide://`). Pure render of a slide preview (title + bullets + thumbnail). Diff renderer overlays the changed slide region with a before/after toggle. Tests: contract conformance; missing thumbnail.                                                                                                                                                                                                                                                  |

Each 4-b agent: imports `TcInlineDiff` and the design-system primitives from `chat-surface`; implements `SaaSRendererAdapter`; no transport / no fetch / no actions (D28); ESLint rule from 4A enforces.

**Phase 4 exit criteria** (all must hold):

- Opening a thread whose canvas URI matches any registered tier-1 scheme renders the tier-1 adapter.
- Opening a thread whose URI matches no tier-1 scheme renders tier-3 `GenericStructuredDiff`.
- Throwing inside any adapter's `renderDiff` triggers the host's error boundary, falls back to tier-3, logs the failure, and does not crash the surrounding chat.
- Tier-1 renderers cannot import or reference `Transport`, `fetch`, `window`, etc. — verified by ESLint rule from 4A and by negative test cases that deliberately try to import banned primitives.
- Hot-swap: `unregisterAdapter('email')` followed by `registerAdapter(newEmail)` swaps the renderer on the next render of an `email://` URI without remounting the host.

### Phase 5 — Auth, integration & smoke (sequential)

Depends on: Phase 4 merged.

| Agent | Slug               | Scope                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ----- | ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 5A    | `auth-integration` | `apps/desktop/main/auth/{oidc-client,loopback-server}.ts` + `secret-storage.ts` (per D24 scoping: per-`(workspace_id, server)` ciphertext files with active-workspace gate; see §6.7). Replace the bearer stub from Phase 1 with the real OIDC flow. End-to-end smoke: launch → log in via system browser → see chats → open thread → send → stream response → approve a diff in a tier-1 renderer → see swimlane bead update → open a thread with an unknown SaaS scheme → see tier-3 render the same payload. Document the smoke test in `apps/desktop/SMOKE.md`. |

Exit criteria: a fresh install on macOS and Windows can log in and complete the full smoke including both a tier-1 approve and a tier-3 render.

### Phase 6 — Tier-2 agent code-gen pipeline (4 parallel agents)

Depends on: Phase 5 merged (we want a verified working tier-1+tier-3 product before adding the codegen complexity).

The agent generates `SaaSRendererAdapter` implementations from constrained templates, persisted per tenant, sandboxed, with the §9.5 quality bar enforced. This phase ships local-only tier-2 (no server-side registry yet — that's Phase 7).

| Agent | Slug                    | Scope                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| ----- | ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 6A    | `tier2-sandbox`         | `apps/desktop/main/adapters/{loader,sandbox,ast-allowlist}.ts` + `packages/chat-surface/src/surfaces/Tier2Loader.tsx`. Dynamic `import()` of `{userData}/adapters/{scheme}-v{n}.js` via `vm` module in main (no privileged globals exposed); AST scan with the import allowlist (`react`, `@enterprise-search/design-system` primitives, a documented pure-utility set); render wrapper with 100 ms timeout + error boundary (already present from 4A, extended for tier-2). Tests: deliberately bad adapters (calls `fetch`, imports `child_process`, infinite loop, throws on every render) are all caught and rolled back to tier-3 without affecting the host. |
| 6B    | `tier2-codegen-backend` | `services/ai-backend/agent_runtime/capabilities/` — new MCP-style capability `generateRenderAdapter(scheme, sample_state, layout_template)`. Layout templates as constants: `form`, `table`, `kanban`, `definition-list`. Given a SaaS scheme + a sample state from the live MCP tool + a template choice, produces an adapter source string. Capability emits to the run stream so the desktop can persist the result. Tests: each template produces a syntactically valid adapter that passes the AST allowlist; round-trip (codegen → AST scan → smoke render) succeeds for each template.                                                                      |
| 6C    | `tier2-lifecycle`       | `apps/desktop/main/adapters/{registry-host,quality-gate,lifecycle-events}.ts`. Orchestrates the full pipeline: detect missing adapter → request generation → run smoke render (Q4) → install on success → hot-swap into the chat-surface registry → on render error (Q6) mark broken + queue regen. Bounded retry budget (3 attempts, ~5 s each); on overflow continue regen in background with tier-3 visible. Persists adapter lifecycle events to a local SQLite audit log. Tests: full happy-path round trip in <5 s; broken adapter triggers exactly one regen; retry budget exhaustion surfaces tier-3 immediately.                                          |
| 6D    | `tier2-quality-gate`    | `apps/desktop/main/adapters/quality-gate/{schema,allowlist,smoke-render,error-boundary,broken-mark}.ts`. Implementation of Q1–Q6 from §9.5: schema validation at load (Zod-checked adapter shape), static analysis (the AST allowlist scanner with explicit deny list), smoke render before activation (synthetic minimal state + diff), render-with-timeout + error-boundary instrumentation, render-error invalidation. Each Q has a unit test that proves it catches its category.                                                                                                                                                                              |

Exit criteria: opening a thread whose canvas URI has no tier-1 match triggers tier-2 generation; on success the user sees a tier-2 render; deliberately-bad generated adapters are caught at every quality gate without escaping; the audit log has an entry for every adapter lifecycle event.

### Phase 7 — Tier-2 sharing & server-side registry (3 parallel agents)

Depends on: Phase 6 merged.

| Agent | Slug                     | Scope                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| ----- | ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 7A    | `tier2-registry-backend` | `services/backend/` — new `adapter_registry` module: storage for shared adapters (S3-style object store with metadata in Postgres), version management, review queue, promote/demote APIs. Backend audit events for every promotion. Multi-tenant: an adapter is private to its origin tenant until promoted; once promoted, available to all tenants who haven't opted out.                                                                                                                                                   |
| 7B    | `tier2-client-sharing`   | `apps/desktop/main/adapters/{harvest,download,opt-out}.ts`. Harvest: when a local tier-2 adapter has met the success criteria (zero render errors over N=10 sessions + zero user-reported issues), submit it to the server-side review queue with anonymized usage metadata. Download: on app start, fetch the shared registry's allowlisted adapters and install them locally (subject to the same quality gate as locally generated ones). Opt-out: tenant-level setting to disable shared adapters (always use local-only). |
| 7C    | `tier2-review-pipeline`  | `apps/frontend/src/admin/adapter-review/*` (new) + corresponding `backend-facade` endpoints. Admin UI for the review queue: view candidate adapter source, side-by-side with template + sample state, run smoke render, approve / reject / request-changes. Required: human reviewer can never see tenant-private data; review uses synthetic samples only.                                                                                                                                                                    |

Exit criteria: a locally-generated adapter on Tenant A meets the success criteria → enters review queue → admin approves → propagates to Tenant B's app on next start → Tenant B opens the same SaaS and sees the shared adapter (verified by `metadata.origin === 'community'`). Opt-out path verified.

### Phase 8 — Hardening (4 parallel agents)

Depends on: Phase 7 merged.

| Agent | Slug              | Scope                                                                                                                                                                                      |
| ----- | ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 8A    | `signing-mac`     | `apps/desktop/build/mac/*`: entitlements, notarization, hardened runtime. CI workflow that signs + notarizes on tag.                                                                       |
| 8B    | `signing-windows` | `apps/desktop/build/windows/*`: WiX MSI, EV cert signing, Authenticode timestamp. CI workflow.                                                                                             |
| 8C    | `updater`         | `apps/desktop/main/updater.ts` + update-server manifest format + force-upgrade flag (`minSupportedVersion`).                                                                               |
| 8D    | `telemetry-crash` | `apps/desktop/main/crash-reporter.ts` (stubbed in Phase 1; wire to endpoint) + custom-events pipeline + first-launch consent dialog. Telemetry redactor lint rule from §6.7 wired into CI. |

Exit criteria: signed installers download and install; auto-update succeeds end-to-end including a tier-2 adapter version bump; crash dump round-trip verified; telemetry opt-out honored at the keychain layer.

## 6. Coding standards

These are **non-negotiable** for every subagent. Phase 0 codifies them in lint rules where possible.

### 6.1 Comments

Per [CLAUDE.md](../../../CLAUDE.md): **default to no comments**. Add one only when the _why_ is non-obvious — a hidden constraint, a subtle invariant, a workaround for a specific upstream bug, or behavior that would surprise a reader. Do not explain _what_ the code does — names handle that. Do not reference the current task or PR ("added for the Y flow", "handles the case from issue #123") — those rot.

If a comment is necessary, **one short line** at most. No multi-paragraph docstrings, no block comments. JSDoc only on exported port interfaces, and only at the type level (parameters and constraints) — never as narration.

### 6.2 File and module layout

- One component per `.tsx` file; component name matches filename (PascalCase).
- Hooks are `useFoo.ts` (camelCase, `use` prefix). Utilities are `kebab-case.ts`. Constants are `UPPER_SNAKE_CASE` inside their file.
- Tests are colocated: `Foo.tsx` and `Foo.test.tsx` next to each other.
- Barrel exports (`index.ts`) only at package boundary; internal modules import the specific file.
- No default exports for components or hooks. Named exports only.

### 6.3 TypeScript

- `"strict": true` everywhere. No `any` (use `unknown` and narrow). No non-null assertions (`!`) — narrow with type guards.
- Branded types for IDs (`type ThreadId = string & { readonly __brand: 'ThreadId' }`). New brands added in `packages/api-types`.
- `readonly` on all interface fields by default; mutable only when ownership requires it.
- Prefer `type` over `interface` unless you need declaration merging.

### 6.4 React

- Functional components + hooks only; no class components.
- Lift state only when two siblings need it. Otherwise local `useState`.
- No `useEffect` for derived state — compute inline.
- Event handlers prefixed `on` in props (`onApprove`, `onReject`); internal callers use `handle` prefix.

### 6.5 Substrate-port discipline

- `packages/chat-surface/src/` MUST NOT reference bare browser primitives (`window.*`, `document.*`, `fetch`, `localStorage`, `EventSource`). Use ports.
- The package's own web reference impls (e.g. `LocalStorageKeyValueStore`) intentionally use `globalThis.X` member access — that prefix marks "I know this is a substrate touchpoint." Anything beyond that exception goes through a port.
- `packages/surface-renderers/src/` MUST NOT import from `apps/*` or from `chat-surface/src/shell` (only from `chat-surface/src/{ports,surfaces,thread-canvas}`).
- `apps/desktop` MAY import from `packages/*` but never from `apps/frontend`.

### 6.6 Imports

Order, separated by blank lines:

1. Built-ins (`node:*`)
2. External packages (alphabetized)
3. `@enterprise-search/*` (alphabetized)
4. Relative imports (parent dirs before children)

Type-only imports use `import type`. Never `import * as X`.

### 6.7 Security primitives (load-bearing — read before Phase 5)

The secret-storage decision (D24) is reasoned from first principles. Any agent touching auth or storage must hold to these:

**Principles applied:**

- **Least privilege.** A token granting access to workspace A's Salesforce must not be retrievable when the user is acting in workspace B. The smallest unit of "retrievability" is `(workspace_id, server)`.
- **Compartmentalization (blast radius).** If process memory is compromised after a `safeStorage.decryptString` call, only the _currently decrypted_ secret should be exposed. Bundling multiple workspaces' tokens into one ciphertext blob means one decrypt op exposes all of them — unacceptable.
- **Revocability.** Removing a workspace must delete every secret bound to it, with no orphans. Per-`(workspace_id, server)` files make deletion a directory tree walk; a single shared blob requires careful map-key surgery and is more error-prone.
- **Defense in depth.** OS-managed encryption (`safeStorage` → macOS Keychain item / Windows DPAPI) is the second layer; per-entry scoping is the first.

**Implementation:**

- Path: `{app.getPath('userData')}/secrets/{workspace_id}/{server_kind}/{server_id}.bin` where `server_kind ∈ {backend, mcp, saas}`. File contents = `safeStorage.encryptString(JSON.stringify(payload))`.
- API in `apps/desktop/main/secret-storage.ts`:
  ```ts
  export function getSecret(
    workspace_id: WorkspaceId,
    server_kind: ServerKind,
    server_id: string,
  ): Promise<unknown | null>;
  export function setSecret(
    workspace_id: WorkspaceId,
    server_kind: ServerKind,
    server_id: string,
    payload: unknown,
  ): Promise<void>;
  export function deleteSecret(
    workspace_id: WorkspaceId,
    server_kind: ServerKind,
    server_id: string,
  ): Promise<void>;
  export function deleteWorkspaceSecrets(
    workspace_id: WorkspaceId,
  ): Promise<void>;
  ```
  Deliberately no `listAll()`, no `getByServer(server_id)` cross-workspace lookup.
- **Active-workspace gate.** Every IPC method that ends in a secret-storage call receives the active `workspace_id` from the renderer and main rejects any mismatch against the current session JWT's `workspace_id` claim. This prevents a compromised renderer from requesting workspace B's secrets while the session is bound to workspace A.
- **No secrets in renderer.** Renderer never sees plaintext tokens. The transport bridge in main attaches the bearer when making the outbound HTTP call; renderer only sees opaque session handles (e.g. "session for workspace acme").
- **No secrets in logs.** Logging primitives in `apps/desktop/main/` redact known token-shaped fields (`*token*`, `*secret*`, `*Authorization*`) at write time. Enforced in Phase 6 with a lint rule.
- **`safeStorage` availability.** On Linux without a Secret Service provider, `safeStorage.isEncryptionAvailable()` returns false and the app refuses to start in production builds (CI/dev fallback uses an unencrypted store with a loud warning banner).

**Verification before Phase 5 sign-off:**

1. Decryption of `wsp_acme/saas/salesforce.bin` while the session claim says `wsp_globex` is rejected by the active-workspace gate and produces an audit-log entry.
2. `deleteWorkspaceSecrets(wsp_acme)` removes the entire `secrets/wsp_acme/` directory and nothing else.
3. Process memory dump after a `getSecret` call contains the decrypted token for at most one entry, not all of them.
4. Windows Credential Manager / macOS Keychain inspector shows one master entry (the `safeStorage` key), not N token entries.

### 6.8 Tests

- Vitest for TS, pytest for Python (per existing services).
- One assertion-style per test file (`expect`).
- Renderer/component tests use React Testing Library; query by role, then text, then test-id. Never by class.
- No snapshot tests for components (they rot). Snapshots only for serializable contract output (e.g. URI parser).
- Integration tests for the IPC channel: a `__bridge.test.ts` round-trips every method declared in `rpc-protocol.ts`.

## 7. Sub-PRD template

Every subagent's first deliverable is `docs/plan/desktop/phase-{n}/{slug}.md` using this template. The agent writes it, commits it, then proceeds to implementation in the same run.

````markdown
# Phase {n}.{x}: {component-name}

## Vision

Remember Think from a architectural design perspective. Assume you are a staff engineer, knwo you all sytem desing prencipals -> DRY, subsituation, simple & elgant code is best code, only one source of truth etc.

## Status

- Status: in-progress
- Agent slug: {slug}
- Branch: desktop/phase-{n}-{slug}
- Worktree: {path}
- Created: {date}

## Scope

**In scope** (files this agent owns):

- path/to/file.ts
- path/to/other.tsx

**Out of scope** (do NOT touch):

- any file outside the list above

## Functional requirements

- [ ] FR-1: {observable behavior}
- [ ] FR-2: ...

## Non-functional requirements

- Performance: {e.g. swimlane scrub re-renders ≤16 ms at 60 fps with 1000 beads}
- Accessibility: {e.g. all interactive elements keyboard-reachable; focus visible; ARIA roles correct}
- Test coverage: {e.g. unit tests for parser; component tests for all interaction states}

## Interfaces consumed

List every port / type imported, with the file path. If the agent finds an interface that doesn't yet exist, **stop and flag to the orchestrator** rather than inventing one.

- `Transport` from `@enterprise-search/chat-transport`
- `Router` from `@enterprise-search/chat-surface/ports/Router`

## Interfaces produced

New exported types / functions / components, with their signatures.

```ts
export function registerSurface(
  scheme: string,
  c: React.ComponentType<SurfaceRendererProps>,
): void;
```

## Open questions

If non-empty at write time, the agent **still proceeds to implementation** with its best guess (per D21 — spec-first-then-continue), but flags each question here so the orchestrator can adjudicate at merge time.

## Done criteria

- [ ] All FRs met
- [ ] `npm run typecheck --workspace @enterprise-search/{pkg}` passes
- [ ] `npm test --workspace @enterprise-search/{pkg}` passes
- [ ] `npm run lint` passes (project root)
- [ ] No imports outside scope
- [ ] No bare browser primitives (chat-surface) / no node:\* primitives (renderer)
- [ ] No new third-party dependency without a one-line justification here

## Notes for orchestrator review

Anything non-obvious about the implementation that warrants a reviewer's attention.
````

## 8. Orchestration protocol

### 8.1 Spawning

Each agent is launched via the `Agent` tool with `isolation: "worktree"`. The tool returns the worktree path and branch name on completion. Branch naming: `desktop/phase-{n}-{slug}`. The orchestrator never lets an agent see another agent's worktree.

Each agent prompt is **self-contained** and includes:

- The phase + slug
- A direct link to this PRD with instructions to read it before doing anything
- The exact scope (files in / files out)
- The sub-PRD template, with placeholders filled in for that agent
- A reminder of the coding standards (§6) and the substitution discipline
- The list of port files to read first (Phase 0 outputs)

### 8.2 Branch model

```
main
└── desktop/phase-{n}   ← integration branch (orchestrator-owned)
    ├── desktop/phase-{n}-{slug-A}   ← agent A
    ├── desktop/phase-{n}-{slug-B}
    ├── desktop/phase-{n}-{slug-C}
    └── desktop/phase-{n}-{slug-D}
```

- Agent branches off `main` (the current tip when the phase starts).
- Agent commits with conventional messages: `feat(desktop): {what}`, `chore(plan): add phase-{n} sub-PRD`. Subject ≤ 70 chars. Body explains the _why_.
- Agent does NOT push, does NOT merge, does NOT rebase. Only commits to its own branch in its own worktree.

### 8.3 Merge sequence (per phase)

Orchestrator performs, after all agents in the phase have completed:

1. Create `desktop/phase-{n}` branch from current `main`.
2. For each agent branch, in a deterministic order (alphabetical by slug):
   - `git merge --no-ff desktop/phase-{n}-{slug}` into `desktop/phase-{n}`
   - Resolve any conflicts (see §8.4)
   - Run `npm run typecheck` (root) and the affected workspaces' tests
   - If green, continue; if red, fix in `desktop/phase-{n}` and commit the fix
3. After all agent branches are merged:
   - Re-run typecheck + tests for the whole monorepo
   - Run integration smoke (Phase 1+)
4. Fast-forward `main` to `desktop/phase-{n}` if clean.
5. Delete `desktop/phase-{n}` and all agent branches after the merge to `main`.

### 8.4 Conflict resolution rules

Conflicts at the agent boundary should be **rare** because the phase design assigns disjoint file sets. If they happen:

- **Same file, different lines** → orchestrator auto-applies both, then re-runs typecheck.
- **Same line, semantic disagreement** → orchestrator reads both sub-PRDs, picks the one that better fits the PRD's intent, and records the rejection in a `RESOLVED-CONFLICTS.md` note inside the phase folder.
- **Interface drift** (one agent assumed a port shape, another defined it differently) → orchestrator restores the Phase 0 contract; the offending agent's work is rebased onto it. If the agent invented a port that doesn't exist, the orchestrator pauses and asks the user before proceeding.

Hard rule: orchestrator NEVER discards an agent's work to "make conflicts go away". Either the work fits the PRD (then keep it) or it doesn't (then surface to the user). No silent deletions.

### 8.5 Failure handling

- An agent that exits with errors (typecheck / lint / test failures inside its own worktree) **is not merged**. The orchestrator preserves the branch + worktree for inspection and surfaces the failure to the user.
- An agent that exceeds the scope (touches files outside its sub-PRD's "in scope" list) **is not merged**. Same preservation.
- An agent that produces no commits → its worktree is auto-cleaned by the tool; no action.

### 8.6 Communication back to the user

After each phase merge:

- Orchestrator posts a one-paragraph summary: phase done, what shipped, any conflicts resolved, any open questions, what Phase {n+1} will start.
- Orchestrator does not auto-launch the next phase. User says "go" first.

## 9. Risks & open questions

- **R1 Inspector gap** — design has no dedicated subagent/citations/run-timeline panel; backend emits these. We're not building one in this plan. Flag to designer.
- **R2 Tier-1 SaaS coverage** — tier-1 renderers may not cover the long-tail of fields for their named SaaS (e.g. custom SF objects, complex SF page layouts, conditional Sheets formulas). D23 escape hatch (`<webview>`) is the safety valve. Decide a per-SaaS coverage SLO before Phase 4-b agents start.
- **R3 OIDC provider identity** — first-party (extends `services/backend`) vs vendor (WorkOS/Auth0/Okta). Affects token shape and federation. Resolve before Phase 5.
- **R4 Update-server endpoint** — needs a host. Phase 8 assumes object-store-backed manifests; confirm ownership.
- **R5 Telemetry consent UX** — privacy/legal sign-off needed before Phase 8D. First-launch dialog wording is gated on that.
- **R6 `safeStorage` on Linux** — requires a Secret Service provider (gnome-keyring / kwallet). The app refuses to run if absent (D24); confirm this is acceptable for Linux developer workstations and CI.
- **R7 Tier-2 generated UI security surface** — dynamic code loading is an attack vector. Mitigations codified in §9.5: AST allowlist (Q2), sandbox with no privileged objects (D29), render-with-timeout + error boundary (Q3), tier-3 fallback (Q6). Tier-2 ships in Phase 6 with its own focused security review _before_ the phase merges. The pure-render contract (D28) is the most important mitigation — adapters have nothing privileged to call.
- **R8 Tier-2 community-shared adapters trust model** — Phase 7 introduces adapters authored on Tenant A and rendered on Tenant B. A malicious tenant submitting a deliberately-broken adapter is a threat. Mitigations: server-side review pipeline (7C), tenant-level opt-out (7B), AST allowlist still enforced on download (6D), `metadata.origin === 'community'` is visible in audit logs. Confirm with security review.
- **R9 Tier-2 codegen quality regression** — the agent's adapter quality may degrade as model versions change. Mitigations: pinned `generatorModel` in adapter metadata; A/B comparisons in the success-criteria metric; manual review of promotion candidates. Phase 7 should include a regression watch on the registry.

Resolved (was Q1–Q3 in an earlier draft):

- **Time Machine** → D25 (mode inside Chats via `TcSwimlanes`; not a separate destination or URI).
- **Secret storage scoping** → D24 (per-`(workspace_id, server)` ciphertext files, active-workspace gate, see §6.7).
- **Cross-renderer effects** → D26 (backend run stream only; no client-side event bus).
- **Renderer scaling to 100+ SaaS** → D27 / §3.4 (three-tier adapter strategy: tier-1 hand-built for high-value SaaS, tier-2 agent-generated for the long tail, tier-3 generic fallback for everything).

## 9.5 Generated UI policies (tier-2)

Governs the agent-generated adapter pipeline (Phase 6 builds it, Phase 7 adds sharing). Locked in advance so Phase 4 / 5 work is forward-compatible.

### 9.5.1 Quality bar

| #   | Mechanism                            | What it catches                                                                                                                                                        | When it runs         | Phase                     |
| --- | ------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------- | ------------------------- |
| Q1  | Schema validation at load            | Generated module exports the wrong shape                                                                                                                               | Every load           | 6D                        |
| Q2  | Static-analysis allowlist            | Generated code reaches outside the sandbox (no `fetch`, `XMLHttpRequest`, `import` of non-allowlisted modules, `eval`, `new Function`, direct DOM access beyond React) | Pre-install AST scan | 6D                        |
| Q3  | Render-with-timeout + error boundary | Runtime bugs, infinite loops, memory bombs                                                                                                                             | Every render         | 4A (host) + 6A (extended) |
| Q4  | Smoke render before activation       | Works on agent's first attempt, breaks on real data                                                                                                                    | Pre-activation       | 6D                        |
| Q5  | Constrained templates                | Visual inconsistency, generation failures                                                                                                                              | Generation time      | 6B                        |
| Q6  | Render-error invalidation            | Schema drift, edge cases at scale                                                                                                                                      | Every live render    | 6C                        |

All six enforced from Phase 6 onward. Visual regression (full screenshot diffing) is post-Phase 7 — deferred to the server-side review pipeline's roadmap.

### 9.5.2 Security model

- Tier-2 adapters loaded via dynamic `import()` of `{userData}/adapters/{scheme}-v{n}.js` from disk only. Never loaded from the network at render time (downloads are pre-fetched via 7B, AST-scanned, persisted, _then_ loaded).
- **No privileged objects in adapter scope.** Adapter `import` graph is restricted to: `react`, `@enterprise-search/design-system` primitives, a documented allowlist of pure utility functions. Anything else fails the AST allowlist (Q2). The adapter has no way to call `Transport`, MCP, `fetch`, IPC bridge, `window`, `document`, `localStorage`, `child_process`, `fs`, etc. — they simply do not exist in its scope.
- **Render-with-timeout.** 100 ms wall-clock per `renderCurrent` / `renderDiff` call. Timeout → error boundary → tier-3 fallback → mark adapter version broken → queue regeneration.
- **No persistence from adapters.** Adapters cannot write to disk, read from disk, start workers, open network connections, call `eval` / `Function`. Enforced by the sandbox primitives (D29) + the AST allowlist (Q2).
- **Sandbox process model.** Tier-2 adapter loading uses Node's `vm` module in the renderer's preload to expose only the allowlisted module graph. Renderer-process isolation is sufficient given pure-render discipline (D28); we don't need per-adapter `BrowserView` overhead.
- **Audit trail.** Every adapter lifecycle event (install, hot-swap, broken-mark, regen-success, harvest-to-review) writes to the local SQLite audit log; on Phase 7 promotion, also written to the backend audit table.

### 9.5.3 Sharing model

- **Phase 6 (local-only).** Each tenant's `userData/adapters/` is private. No sharing.
- **Phase 7 (server-side registry).** Locally-generated adapters meeting the success criteria are submitted to a server-side review queue. After human approval, they propagate to other tenants who have not opted out.
- **"Successful" criteria.** Zero render errors over N=10 sessions + zero user-reported issues. The metric runs in 6C; Phase 7 consumes it.
- **Review pipeline (7C).** Human reviewers see candidate adapter source side-by-side with the layout template and a synthetic sample state. Reviewers never see tenant-private real data — only synthetic samples. Approve / reject / request-changes.
- **Tenant opt-out (7B).** A tenant can disable shared adapters entirely — they fall back to local-only generation + tier-3.
- **Provenance.** Every adapter carries `metadata.origin ∈ { first-party, agent-generated, community }`. Surfaced in logs and an admin view; not surfaced to end users (per D27 trust UX answer).

### 9.5.4 Versioning & failure handling

- **Generation retry budget.** Agent retries within 3 attempts, ~5 s each. On budget exhaustion: user sees tier-3 immediately; regeneration continues in background; on eventual success, the new version hot-swaps in for the next render.
- **Schema drift.** A previously-working adapter that starts throwing is marked broken on the offending version, falls back to tier-3, and queues regeneration. The broken version stays on disk for diff/audit until regen succeeds.
- **Version resolution.** Multiple adapter versions for the same scheme may exist on disk; `resolveAdapter` returns the highest non-broken version. `markBroken(scheme, version, reason)` is the only way to demote.
- **Capability changes.** When the host's design-system or contract `schemaVersion` changes, existing tier-2 adapters that depend on the removed surface are auto-marked broken at load (Q1 catches the schema mismatch); regeneration is queued.
- **Concurrent generation.** If two users in the same tenant open the same unknown scheme simultaneously, only one regeneration request is issued (deduplicated by `scheme` + `tenant_id`); both see tier-3 until the single regeneration completes.

## 10. What ships when

Honest sequencing for the full 1.0 product. Each "session" is "orchestrator spawns N agents in parallel, waits, reviews, merges to integration branch, runs typecheck/tests, fast-forwards main".

- **Phase S** — 3 sub-rounds (1 agent for prep, 2 parallel for variants, 1 decision review). Substrate locked.
- **Phase 0** — 1 sequential agent. Spec rewrites, ports frozen, ESLint rules, package scaffolding.
- **Phase 1** — 4 parallel. App launches; empty shell on chosen substrate.
- **Phase 2** — 5 parallel. Chat surface depth: sidebar, ThreadCanvas, swimlanes, TcChat + composer, inline diff primitive.
- **Phase 3** — 4 parallel, alongside Phase 2 (independent file sets). All 11 destinations.
- **Phase 4** — 1 sequential (contract freeze) + 5 parallel (tier-3 + 4 tier-1 renderers). Adapter contract, tier-3 GenericStructuredDiff, tier-1 Email / Salesforce / Sheets / Slides.
- **Phase 5** — 1 sequential. Real OIDC + active-workspace gate + smoke test verifying both tier-1 and tier-3 paths.
- **Phase 6** — 4 parallel. Tier-2 codegen pipeline + sandbox + quality gate + lifecycle. Includes a focused security review _before_ the phase merges to main.
- **Phase 7** — 3 parallel. Tier-2 sharing: server-side registry, client harvest/download/opt-out, admin review pipeline.
- **Phase 8** — 4 parallel. Signing (mac + windows), updater, telemetry + crash. Releasable.

No promised dates. The order is the contract; the calendar adapts to it.

---

## Appendix A — What's already on disk

Snapshot at 2026-05-17 (verify before relying):

- `packages/chat-surface/src/` — `messages/`, `citations/`, `presence/`, `providers/`, `storage/`, `routing/` (partial), `shell/`, `index.ts`. After spike-prep merges: also `surfaces/`, `thread-canvas/TcInlineDiff.tsx`, `routing/uri/`.
- `packages/chat-transport/src/` — `transport.ts`, `types.ts`, `web/`, `index.ts`. After spike-prep merges: also `mock/`. No `ipc/` yet.
- `packages/surface-renderers/` — scaffolded in spike-prep (Email renderer + tests). Tier-1 renderers added in Phase 4. Tier-2 adapters land at runtime via Phase 6.
- `apps/desktop/` — does not exist yet (Phase 1).
- `apps/frontend/` — existing; consumes chat-surface + chat-transport via WebTransport.

## Appendix B — Reading order for a new agent

1. This PRD (top to bottom).
2. Its phase row in §5 and the assigned slug's scope.
3. The four [memory files](../../../.claude/projects/-Users-parthpahwa-Documents-work-enterprise-search/memory/) (project-atlas-product-model is the most important).
4. The port files in `packages/chat-surface/src/ports/` (frozen after Phase 0).
5. The Transport contract: `packages/chat-transport/src/transport.ts`.
6. The repo-root [CLAUDE.md](../../../CLAUDE.md) (section "Conventions Worth Knowing") and any path-scoped CLAUDE.md in the agent's scope.
7. Then write the sub-PRD, then implement.
