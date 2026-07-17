# Desktop App Architecture (macOS + Windows)

Status: **proposal — substrate decided (custom Electron)** · Owner: TBD · Last updated: 2026-05-17

This document specifies the architecture for the Mac and Windows desktop client of the Atlas / 0x-copilot product. It is the **architecture spec** (what we're building and why); the [Desktop App PRD](../plan/desktop/PRD.md) is the execution plan (phases, agents, orchestration). Read this first, then the PRD.

This doc supersedes the prior fork-based draft (dated 2026-05-14). The prior `desktop-app-rollout.md` was replaced by the PRD and removed in Phase 0.

Substrate: **custom Electron** (one `BrowserWindow` mounting `packages/chat-surface`). Validated by Phase S of the PRD on 2026-05-17; see [phase-0.5/S2-decision.md](../plan/desktop/phase-0.5/S2-decision.md) for the full reasoning. Phase 1's `electron-shell` agent builds `apps/desktop/` from scratch against the Phase 4 contract; spike code does not land on main.

Related architecture docs:

- [service-boundaries.md](service-boundaries.md) — package/app ownership; updated in Phase 0 to register `apps/desktop` and `packages/surface-renderers`.
- [runtime-stream-handshake.md](runtime-stream-handshake.md) — SSE `sequence_no` contract; the desktop transport consumes this unchanged.
- [multi-tenant-deployment.md](multi-tenant-deployment.md) — workspace/tenancy semantics surfaced by the desktop's active-workspace gate.

## 1. Goals & non-goals

### Goals

- macOS + Windows builds, signed and notarized, with auto-update on `stable` and IT-managed updates on `enterprise-mdm`.
- One React tree (`packages/chat-surface`) for chat, layout, destinations, and per-SaaS renderers — byte-identical between `apps/frontend` (web) and `apps/desktop` (renderer).
- Production-grade OIDC + OS keychain from day one (no dev-IdP code in shipped binaries).
- Renderer architecture that **scales to 100+ SaaS** without 100+ hand-built renderers — see §4 (three-tier adapter strategy).
- Inline diff approval that matches the Atlas design's PENDING-block UX, regardless of which renderer tier resolved.
- Swimlane timeline scrubbing of agent-applied changes per surface (Time Machine; see §5.5).
- Single source of truth for: routing, keybindings, URI scheme parsing, Transport contract.

### Non-goals

- No fork of VS Code / Code – OSS / Monaco (substrate decision; see §3).
- No iframe of SaaS web apps as the primary surface model. A `<webview>` escape hatch is permitted per renderer for long-tail edge cases (D23 in PRD).
- No filesystem-editing surface in the renderer (the renderer area is for agent artifacts).
- No Linux release. Linux is CI verification only.
- No multi-window per user session; no multi-account per session.
- No desktop-side LLM provider keys. Provider calls remain server-side via `services/ai-backend`.
- No third-party developer extension marketplace. Tier-2 adapters are agent-generated and quality-gated server-side, not authored by humans outside the org.

## 2. Principles → decisions

| Principle                  | What it forces                                                                                                                                                                                                                                                                                                       |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **DRY**                    | One React tree across web + desktop. One Transport contract. One URI scheme module. One keybinding registry. One adapter contract serving tier-1, tier-2, and tier-3.                                                                                                                                                |
| **Substitution**           | `Transport`, `SurfaceHost`, `Router`, `KeyValueStore`, `PresenceSignal`, `SaaSRendererAdapter` are ports. Web and desktop swap implementations; surface code never branches on substrate. Adapters swap behind `SurfaceRegistry.resolveAdapter`.                                                                     |
| **Simple & elegant**       | Thin Electron shell (main + preload + renderer). No fork, no patches, no rebase pipeline, no webview-RPC protocol version. Each port has the smallest API.                                                                                                                                                           |
| **Single source of truth** | Routing lives in `chat-surface` only. Keybindings in one registry. The artifact URI scheme is parsed in one module imported by both ends. The Transport contract is the only way the renderer talks to the backend. The adapter contract is the only way the host talks to any renderer (tier-1, tier-2, or tier-3). |

## 3. Substrate decision (resolved 2026-05-17)

The substrate options were custom Electron, VS Code marketplace extension, and Code – OSS fork. After iterating on the design and applying the four principles to its actual primitives (not by analogy to Cursor / Claude Code), the working recommendation was **custom Electron + `packages/chat-surface` mounted in a single `BrowserWindow`**.

The decision was **empirically validated by Phase S** of the PRD. Both Electron (S1-B) and a VS Code marketplace extension (S1-A) variant were built around the same shared renderer code (`packages/surface-renderers/email`); substrate cost was measured side-by-side (LOC, build complexity, dev experience, visual fidelity, security boundary). Custom Electron won on single source of truth, simple & elegant, and forward-fit for tier-2 dynamic-adapter loading. Full data: [phase-0.5/S2-decision.md](../plan/desktop/phase-0.5/S2-decision.md).

Reasoning summary (full chain in [project-desktop-substrate-direction](../../.claude/projects/-Users-parthpahwa-Documents-work-0x-copilot/memory/project_desktop_substrate_direction.md)):

- **DRY** is a wash — VS Code's claimed reuse (diff editor, tabs, tree, command palette) doesn't apply: our diffs are inline annotations on structured forms (not text-line diffs), tab strips are segment controls (not editor groups), only Chats has a tree, command palette is ~150 LOC.
- **Substitution** favors Electron — fewer mount layers, no webview-RPC contract to version.
- **Single source of truth** favors Electron decisively — fork splits routing/keybindings/lifecycle into VS Code's authority + ours.
- **Simple & elegant** favors Electron decisively — order-of-magnitude less surface area (no code-oss subtree, no patches, no rebase pipeline, no webview-RPC, no Squirrel).
- The Cursor / Claude Code precedent doesn't transplant — their primary artifact is source code (Monaco diff is the right tool); ours is structured business data (inline annotation on rendered forms is the right tool). Surface resemblance, not architectural fit.

What would flip back to fork:

1. A real plan to ship a third-party developer extension marketplace (extension model becomes product value). Not in the Atlas design.
2. Source-code editing becomes a primary artifact (Monaco diff + code intelligence become load-bearing). Not in the Atlas design.

## 4. Renderer strategy — three tiers

The renderer architecture answers "how do we cover 100+ SaaS without building 100+ renderers?" by tiering. All three tiers implement the same `SaaSRendererAdapter` contract (§7); the host doesn't know or care which tier resolved.

```
                        host (TcSurfaceMount)
                        │
                        │ resolveAdapter('email://draft-7')
                        ▼
       ┌────────────────────────────────────────────┐
       │ Tier 1 — hand-built first-party adapters   │
       │ packages/surface-renderers/{email,sf,…}    │
       │ Pixel-perfect, design-system aligned.      │
       └──────────────────┬─────────────────────────┘
                          │ no match
                          ▼
       ┌────────────────────────────────────────────┐
       │ Tier 2 — agent-generated adapters          │
       │ {userData}/adapters/{scheme}-v{n}.js       │
       │ Code-genned from constrained layout        │
       │ templates (form / table / kanban /         │
       │ definition-list). Sandboxed render-only.   │
       └──────────────────┬─────────────────────────┘
                          │ no match, or render error
                          ▼
       ┌────────────────────────────────────────────┐
       │ Tier 3 — GenericStructuredDiff             │
       │ Always works. Renders the MCP tool-call    │
       │ payload as resource id + field diff +      │
       │ "Open in {SaaS}" link.                     │
       └────────────────────────────────────────────┘
```

### 4.1 Tier 1 — hand-built first-party adapters

For the named SaaS in the design: Email, Salesforce (Opportunity), Sheets, Slides. Each is a React component module in `packages/surface-renderers/{scheme}/` that exports a `SaaSRendererAdapter` implementation. Pixel-perfect; tightly aligned with the design system.

Lives in: `packages/surface-renderers/src/{scheme}/`.

### 4.2 Tier 2 — agent-generated adapters

For the long tail (HubSpot, Linear, Monday, Notion, Zendesk, Intercom, Asana, Pipedrive, …). When the user opens an artifact whose scheme has no tier-1 adapter and no working tier-2 adapter on disk, the host:

1. Renders tier-3 immediately (no waiting).
2. Emits `adapter.generation.requested(scheme, sample_state)` to backend.
3. Backend agent picks a layout template (form / table / kanban / definition-list) and generates an adapter source string per §7.
4. Quality gate (§9 in PRD; six checks Q1–Q6) validates the source.
5. Adapter persisted to `{userData}/adapters/{scheme}-v{n}.js`.
6. Host hot-swaps into `SurfaceRegistry`. Next render uses tier-2.

Lives in: persisted to `{app.getPath('userData')}/adapters/`, loaded dynamically. Source-of-truth for the catalog is the local audit log + Phase 7's server-side shared registry.

Sharing across tenants is Phase 7: a successful adapter (zero render errors over N=10 sessions + zero user-reported issues) enters a server-side review queue; on human approval, it propagates to other tenants who haven't opted out.

### 4.3 Tier 3 — `GenericStructuredDiff`

The safety net. A React component in `packages/chat-surface/src/surfaces/GenericStructuredDiff.tsx` that renders any MCP tool-call payload as a structured diff card — resource id, field changes (old → new), reasoning text, "Open in {SaaS}" deep link. The right-rail PENDING DIFF card from the design.

Implements `SaaSRendererAdapter` with `scheme: '*'`; resolved last in `SurfaceRegistry.resolveAdapter`. Always available; covers any SaaS the agent has an MCP tool for.

### 4.4 Why this tiered model

- **Coverage from day one.** Tier-3 handles every SaaS the agent has an MCP tool for. The user never hits "we don't support this".
- **Quality where it matters.** Tier-1 reserved for high-value workflows where the visual treatment is worth the engineering investment.
- **Long-tail amortized.** Tier-2 lets us cover SaaS we don't ship a renderer for without dedicated engineering time per SaaS.
- **One contract, three implementations.** The host code is the same regardless of which tier wins. DRY at the contract level.
- **Provenance auditable.** `metadata.origin ∈ { first-party, agent-generated, community }` is on every adapter; visible in audit logs.

## 5. Component architecture

Three logical layers, top to bottom:

```
┌─────────────────────────────────────────────────────────────────────┐
│ apps/desktop (Electron)                                             │
│  main (Node)                                                        │
│   - window lifecycle, native menus, deep-link handler               │
│   - OIDC authorization-code + PKCE (loopback callback)              │
│   - safeStorage adapter (per-(workspace_id, server) ciphertext)     │
│   - HTTP + SSE pump (bearer attached here, never in renderer)       │
│   - IPC handlers (Zod-validated)                                    │
│   - tier-2 adapter loader (vm module + AST allowlist + sandbox)     │
│   - electron-updater                                                │
│   - crashReporter                                                   │
│  preload                                                            │
│   - contextBridge exposing typed window.bridge channel (no token)   │
│  renderer (Chromium)                                                │
│   - mounts <ChatShell transport={IpcTransport} ... />               │
└─────────────────────────────┬───────────────────────────────────────┘
                              │ chat-surface bundle (same bytes as web)
                              │
┌─────────────────────────────┴───────────────────────────────────────┐
│ packages/                                                           │
│  chat-surface           shell + destinations + ThreadCanvas +       │
│                         composer + palette + ports +                │
│                         SurfaceRegistry + tier-3 GenericStructured  │
│                         Diff + host TcSurfaceMount                  │
│  chat-transport         Transport + WebTransport + IpcTransport     │
│                         + MockTransport (for spike + tests)         │
│  surface-renderers      Tier-1 hand-built: email / salesforce /     │
│                         sheets / slides. Each implements            │
│                         SaaSRendererAdapter.                        │
│  api-types              existing                                    │
│  design-system          existing                                    │
└─────────────────────────────┬───────────────────────────────────────┘
                              │ HTTPS / SSE
                              ▼
                  backend-facade:8200 (unchanged)
                              │
                  backend, ai-backend (unchanged except for
                  Phase 6 generateRenderAdapter capability and
                  Phase 7 adapter-registry module)
```

### 5.1 Why no separate "extensions"

Earlier drafts split the chat into `enterprise-chat-core` (auth + transport) and `enterprise-chat-ui` (custom editors + trees) as bundled VS Code extensions. With the Electron substrate, that split is replaced by main-process modules (`main/auth/`, `main/transport-bridge/`, `main/adapters/`) and renderer-side packages (`chat-surface`, `surface-renderers`). One less indirection layer, one less IPC contract to version, single source of truth for routing.

### 5.2 Per-SaaS renderers as packages, not extensions

`packages/surface-renderers/` is one npm workspace, not N extensions. Each scheme is a folder; each exports a `SaaSRendererAdapter`. They register into `SurfaceRegistry` via a single `registerAll()` call at bootstrap. Build-time tree-shaking handles dead-code elimination.

This is _not_ a public extension API. Third parties don't write tier-1 adapters. The closest analog is the agent code-gen pipeline (tier-2) — that's the supported path for adding SaaS the org hasn't shipped tier-1 for.

### 5.3 Window model

Single `BrowserWindow`. Decision D9 in PRD. Match the Atlas design (one workspace, one window). Multi-window deferred indefinitely.

### 5.4 Surface model

The `ThreadCanvas` component owns a CSS grid:

```
┌──────────────────────────────┬──────────────┐
│ TcTabs  |  TcSurfaceMount    │   TcChat     │
├──────────────────────────────┴──────────────┤
│ TcSwimlanes  (per-surface bead timeline)    │
└─────────────────────────────────────────────┘
```

`TcSurfaceMount` is the host. It:

1. Reads the active artifact URI from the route.
2. Calls `SurfaceRegistry.resolveAdapter(uri)` — returns a tier-1, tier-2, or tier-3 adapter.
3. Calls `transport.request` to fetch current state (MCP or backend API).
4. Calls `transport.subscribeServerSentEvents` to listen for agent proposed diffs.
5. Calls `adapter.renderCurrent(state)` and `adapter.renderDiff(diff)` — wrapped in error boundary + 100 ms timeout.
6. Renders Approve / Reject / Suggest-changes buttons around the adapter output.
7. On approve, calls MCP via transport.
8. On render error, marks the adapter version broken (if tier-2) and falls back to tier-3.

The adapter never knows about transport, MCP, approve, or reject. **Pure render of state to JSX. D28 in PRD.**

### 5.5 Time Machine

The swimlane (`TcSwimlanes`, bottom of `ThreadCanvas`) is the Time Machine surface. Per-surface bead timeline (one lane per active SaaS), playhead, transport controls, keyboard nav, "Snap to now", pinned beads, "Branch from here" and "Restore this state" actions when scrubbed off-now. `TcChat` shows ghost-message previews when the swimlane is scrubbed off-now (subscribes via context).

There is no separate `time-machine://` URI scheme and no separate destination. Decision D25 in PRD.

## 6. Streaming & data flow

Renderer ↔ main IPC carries every privileged operation. Bearer tokens never enter the renderer.

```
renderer (chat-surface)            main process              backend-facade
─────────────────────────          ────────────              ──────────────
transport.request(req)
  → IPC 'transport.request'
                                  attaches bearer from
                                  active workspace's secret
                                  storage; verifies workspace
                                  gate matches session JWT
                                                          ─→ HTTP request
                                                          ←─ response
  ← IPC response

transport.subscribeServerSentEvents
  → IPC 'transport.subscribe...'
                                  opens fetch stream with
                                  bearer; parses SSE frames
                                                          ─→ GET /v1/...
                                                          ←─ text/event-stream
                                  ← IPC 'stream-event' (per event)
handler(event)                    tracks highest sequence_no
                                  on reconnect: resume from
                                  highest sequence_no
                                  → IPC 'stream-event' (cont'd)
```

Reconnect logic lives in main. The renderer assumes the stream is reliable. The `RuntimeEventEnvelope` shape that `runtime_api` emits is what the renderer's handler receives — no schema translation.

## 7. Frozen contracts (subset)

Full signatures in the PRD §3.3. The load-bearing ones:

```ts
// SaaSRendererAdapter — pure render only. No transport, no MCP, no fetch.
export interface SaaSRendererAdapter<TResource = unknown, TDiff = unknown> {
  readonly scheme: string;
  readonly matches: (uri: string) => boolean;
  readonly renderCurrent: (state: TResource) => React.ReactElement;
  readonly renderDiff: (diff: TDiff) => React.ReactElement;
  readonly metadata: {
    readonly origin: "first-party" | "agent-generated" | "community";
    readonly generatedAt?: string;
    readonly generatorModel?: string;
    readonly schemaVersion: number;
  };
}

// SurfaceRegistry — resolves URI → adapter. Tier-3 has scheme='*' (matched last).
export function registerAdapter(adapter: SaaSRendererAdapter): void;
export function resolveAdapter(uri: string): SaaSRendererAdapter | null;
export function unregisterAdapter(scheme: string, version?: number): void;
export function markBroken(
  scheme: string,
  version: number,
  reason: string,
): void;

// Transport (existing, on-disk; used unchanged by IpcTransport).
export interface Transport {
  request<TRes>(req: TypedRequest): Promise<TRes>;
  subscribeServerSentEvents(opts: SseSubscribeOptions): SseSubscription;
  getSession(): Session;
  capabilities(): TransportCapabilities;
}
```

## 8. Auth & secret handling

Decision D24 in PRD; reasoning in PRD §6.7.

**Per-`(workspace_id, server)` secret storage.** Tokens stored at `{userData}/secrets/{workspace_id}/{server_kind}/{server_id}.bin`, each encrypted with Electron `safeStorage`. Main exposes a scoped API: `getSecret(workspace_id, server_kind, server_id)` / `setSecret(...)` / `deleteSecret(...)` / `deleteWorkspaceSecrets(workspace_id)`. No global accessor; no cross-workspace lookup.

**Active-workspace gate.** Every IPC method that ends in a secret-storage call carries the active `workspace_id` from the renderer; main rejects mismatches against the session JWT's `workspace_id` claim. Prevents a compromised renderer from requesting workspace B's secrets while the session is bound to workspace A.

**OIDC.** Authorization code + PKCE via the OS browser → loopback `127.0.0.1:{port}/cb`. Tokens stored per-workspace in `safeStorage`. Access tokens short-lived (15 min); refresh tokens rotated on every use. On 401: refresh once; on refresh failure, re-launch the flow.

**`safeStorage` availability.** On Linux without a Secret Service provider (gnome-keyring / kwallet), `safeStorage.isEncryptionAvailable()` returns false and the app refuses to start in production builds. CI / dev fallback uses an unencrypted store with a loud warning banner.

## 9. Telemetry, crash, logging

- **Telemetry**: custom event pipeline scoped to product events (app launch, chat opened, run started, run completed, adapter lifecycle events). Never includes chat content. Opt-out at install + in Settings. Default depends on channel: `stable` defaults on with first-launch consent dialog; `enterprise-mdm` defaults off and is MDM-controlled.
- **Crash reporting**: Electron `crashReporter` posts native dumps to our endpoint with minimal context (version, OS, arch). Never includes user content. Renderer JS errors opt-in only.
- **Logging**: structured logs to platform-standard locations (`~/Library/Logs/Enterprise/<version>/` on macOS, `%APPDATA%\Enterprise\logs\` on Windows). "Reveal Logs" command in the palette. "Send Diagnostics" command bundles logs for support tickets.
- **Audit log (local SQLite)**: every adapter lifecycle event (install, hot-swap, broken-mark, regen-success, harvest-to-review) — for tier-2 forensics.

## 10. Distribution

- **Build matrix**: macOS x86_64 + arm64 (Universal lipo), Windows x86_64 (Windows arm64 built, not yet shipped), Linux x86_64 (CI verification only).
- **Signing**: Apple Developer ID Application + notarization (notarytool); Windows EV cert on hardware token. Signing material never on PR/dev runners.
- **Channels**: `stable` (electron-updater enabled) and `enterprise-mdm` (auto-update disabled via build flag; IT pushes updates via MDM).
- **Update server (Phase 8)**: stateless, reads manifests from object storage; force-upgrade flag (`minSupportedVersion`); graceful degradation on unreachable.

## 11. Risks

- **R1** Tier-1 coverage of SaaS long-tail fields (custom SF objects, conditional Sheets formulas). Mitigation: tier-2 + tier-3 are the safety nets; `<webview>` escape hatch for genuine outliers.
- **R2** Tier-2 generated UI security surface (dynamic code loading). Mitigation: AST allowlist, sandbox with no privileged objects (D29), render-with-timeout + error boundary, tier-3 fallback. Adapter purity (D28) is the most important — adapters have nothing privileged to call. Phase 6 includes a focused security review before merge.
- **R3** Tier-2 community-shared adapters trust model (Phase 7). Mitigation: server-side review pipeline; tenant-level opt-out; AST allowlist enforced on download; `metadata.origin === 'community'` visible in audit logs.
- **R4** OIDC provider identity (first-party vs vendor). Resolve before Phase 5.
- **R5** `safeStorage` on Linux requires a Secret Service provider. App refuses to run if absent; confirm acceptable for Linux dev/CI.
- **R6** Update server hosting. Phase 8 assumes object-store-backed; confirm ownership.

## 12. Glossary

- **Tier-1 adapter** — hand-built first-party `SaaSRendererAdapter` for a named SaaS (Email, Salesforce, Sheets, Slides).
- **Tier-2 adapter** — agent-generated `SaaSRendererAdapter` for a long-tail SaaS, persisted to `{userData}/adapters/`, sandboxed render-only.
- **Tier-3** — `GenericStructuredDiff`, the always-available fallback that renders any MCP tool-call payload generically.
- **Host (TcSurfaceMount)** — the chat-surface component that resolves the adapter for a URI, fetches state via Transport, renders the adapter's output, and surrounds it with Approve / Reject / Suggest-changes controls.
- **Pure-render discipline** — adapters have no transport, no MCP, no fetch, no `window`. All actions live in the host. The structural guarantee that makes tier-2 sandboxing tractable.
- **Time Machine** — the swimlane / scrubber inside `ThreadCanvas`. Not a separate destination or URI.
- **Active-workspace gate** — main-process check that the renderer's requested `workspace_id` matches the session JWT's claim, before any secret-storage operation.
- **Workbench / Code – OSS / VS Code fork** — substrate options rejected pending Phase S spike outcome.
