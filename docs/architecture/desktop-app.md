# Desktop App Architecture (macOS + Windows)

Status: proposal · Owner: TBD · Last updated: 2026-05-14

This document specifies the architecture for the Mac and Windows desktop builds of the product, built on a Code – OSS fork. The desktop app is a third client of `backend-facade:8200` alongside the existing web app; no new backend services are introduced.

Related docs:

- [service-boundaries.md](service-boundaries.md) — must be updated when `apps/desktop`, `packages/chat-surface`, and `packages/chat-transport` are introduced
- [runtime-stream-handshake.md](runtime-stream-handshake.md) — SSE event/`sequence_no` contract; the desktop SSE bridge consumes this unchanged
- [multi-tenant-deployment.md](multi-tenant-deployment.md) — workspace/tenancy semantics that the desktop workspace switcher exposes

## 1. Goals & non-goals

### Goals

- Native desktop apps for macOS and Windows that share the existing product surface
- Chat is the primary surface; the editor area dual-uses for agent artifact viewing (MCPs, tool definitions, tool results, skills, conversations, subagent transcripts, workspaces)
- Reuse the existing `apps/frontend` React UI — no parallel implementation
- Single source of truth for API contracts (`@enterprise-search/api-types`), design tokens (`@enterprise-search/design-system`), and chat UI components (new `@enterprise-search/chat-surface`)
- Production-grade OIDC auth and OS-keychain secret storage from day one
- Two distribution channels: direct download (auto-update) and MDM-managed (auto-update disabled)
- Rebaseable against upstream Code – OSS without compounding maintenance pain

### Non-goals (explicit)

- No marketplace VS Code extension form factor in this phase; workbench customization is too heavy to share with a vanilla VS Code host. Re-evaluate post-MVP.
- No new backend services. `backend-facade:8200` remains the only contract surface.
- No local file editing as a product feature in phase 1. The editor area is reserved for agent artifacts.
- No Linux desktop in the release matrix (CI builds it for verification only).
- No multi-window product workflows in phase 1. One window per user session.
- No mobile/web parity expansion. The web app stays as-is and inherits the new transport abstraction as a side effect.

## 2. Principles (and the decisions they justify)

| #   | Principle                                              | Practical implication                                                                                                                                                                                                                                     |
| --- | ------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| P1  | **Single source of truth for UI**                      | One React tree for chat, message rendering, and artifact viewers. Lives in `packages/chat-surface`. Both web and desktop mount it. If the same visual exists in two places, one of them is wrong.                                                         |
| P2  | **Substrate-agnostic surface**                         | The React UI never knows whether it's in a browser tab or a webview. All HTTP/SSE/auth/native-bridge concerns go through a `Transport` port. Any direct `fetch` or `window.*` reference in `chat-surface` is a code smell.                                |
| P3  | **Webview is untrusted**                               | The bearer token never leaves the extension main process. RPC validated against Zod schemas at the boundary. Webview-originated requests get the same scrutiny as a public web request.                                                                   |
| P4  | **Patches are debt**                                   | Every patched line of `code-oss/` core is a merge conflict at every rebase. If a feature can be a built-in extension contribution, it must be. A patch is acceptable only when no extension API can deliver the affordance _and_ the patch is ≤ 30 lines. |
| P5  | **Idempotent build, reproducible release**             | Same git SHA + same toolchain versions → byte-identical installer (modulo signing). Any nondeterminism (timestamps, randomized embedded URLs) gets pinned.                                                                                                |
| P6  | **No leaky abstractions across deployable boundaries** | The desktop app is a client of `backend-facade`. It never imports `services/*/src` or another deployable's internals. Cross-component sharing is `packages/*` only.                                                                                       |
| P7  | **Defaults over configuration**                        | The app opens to a working chat. No first-run wizard. Configurable knobs require an obvious good default; if I can't pick one, the knob shouldn't exist yet.                                                                                              |

## 3. Decisions

| #   | Decision point           | Choice                                                                                           | Rationale                                                                                                                             | Rejected alternatives                                                                                                                                          |
| --- | ------------------------ | ------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| D1  | Distribution form        | Forked Code – OSS standalone app                                                                 | Workbench customization (custom URI schemes, default editor replacement, sidebar takeover) is hostile to vanilla VS Code coexistence. | Marketplace extension only (insufficient control); both forms simultaneously (constrains the fork by the extension's coexistence rules). Reconsider after MVP. |
| D2  | Cross-platform structure | Single `apps/desktop`                                                                            | Code – OSS is already cross-platform; OS differences live in `build/` configs.                                                        | Separate `apps/mac` + `apps/windows` (doubles maintenance for no benefit).                                                                                     |
| D3  | React UI sharing         | New `packages/chat-surface`                                                                      | DRY. Single component tree consumed by web and desktop.                                                                               | Duplicate the chat shell into the extension (parallel implementation, drift inevitable).                                                                       |
| D4  | Transport coupling       | `Transport` port + two impls (web `fetch`/`EventSource`, desktop postMessage RPC)                | Substitution principle. Enables future native transports without UI changes.                                                          | Direct `fetch` everywhere with conditional desktop overrides (leaky, untestable).                                                                              |
| D5  | Token storage            | OS keychain via VS Code `SecretStorage`                                                          | Webviews must not hold raw tokens; keychain is the production-grade store.                                                            | localStorage (insufficient; cleartext on disk).                                                                                                                |
| D6  | Auth flow (desktop)      | OIDC authorization-code flow via system browser → loopback callback                              | Production-grade from day one.                                                                                                        | Dev-IdP bearer minting (development-only flow; baking it into a signed binary is a security smell).                                                            |
| D7  | Patches vs extensions    | Extension-first; patches ≤ 30 lines and audited                                                  | Rebase tax.                                                                                                                           | Liberal patches (compounds at every upstream merge).                                                                                                           |
| D8  | Artifact representation  | URI scheme + custom editor + tree provider                                                       | Free reuse of VS Code's tab/split/peek/command-palette/quick-open infrastructure.                                                     | Modal panels or hardcoded sidebar layouts (reinvents primitives VS Code already ships).                                                                        |
| D9  | Webview density          | One full webview per chat tab; one shared lightweight webview for non-chat viewers               | Chromium per-context cost is real once many artifact tabs are open.                                                                   | Per-tab webview universally (memory bloat); single webview for everything (chat state lost when switching tabs).                                               |
| D10 | Workspace concept        | Map 1:1 to existing backend workspace                                                            | Avoid introducing a new abstraction.                                                                                                  | Treat "VS Code workspace" as a separate desktop concept (two truths, sync nightmare).                                                                          |
| D11 | Update mechanism         | Squirrel auto-update on `stable`; disabled on `enterprise-mdm`                                   | Standard VS Code pattern, retargeted to our update server. MDM disables for IT control.                                               | Manual install only (poor UX); third-party updater (more moving parts).                                                                                        |
| D12 | Code signing             | Apple Developer ID Application + notarization; Windows EV cert on hardware token                 | Unavoidable for production desktop distribution.                                                                                      | Self-signed/unsigned (Gatekeeper/SmartScreen will block); shared software cert (key extraction risk).                                                          |
| D13 | Upstream rebase cadence  | Pin to a Code – OSS minor tag; weekly rebase to patch tip during active dev, monthly when stable | Drift between rebases is the worst — small frequent rebases beat large rare ones.                                                     | Pin forever (security tax); track upstream tip (constant breakage).                                                                                            |
| D14 | Telemetry                | Strip Microsoft's; add our own scoped to product events, opt-out at install                      | Customer trust + privacy contract.                                                                                                    | Inherit VS Code's (sends data to Microsoft endpoints; non-starter).                                                                                            |
| D15 | Crash reporting          | Native crash dumps to our endpoint; renderer JS errors opt-in only                               | Native dumps catch Electron/fork bugs; JS error reporting requires user consent.                                                      | Third-party (Sentry/Bugsnag) — possible later; not in MVP.                                                                                                     |
| D16 | Multi-account            | Single account per window; multi-window allowed in phase 5+                                      | Keep MVP simple; matches the existing web app surface.                                                                                | Multi-account-in-one-window (UX complexity not yet warranted).                                                                                                 |

## 4. The artifact model

The load-bearing idea: every product object is a URI with a content provider, custom editor, tree provider, and command set. This makes the editor area an _artifact viewer_ and gives us VS Code's tabs, splits, peek, quick-open, and command palette for free.

| URI scheme    | Example                              | Editor                                        | Tree home             | Editable              |
| ------------- | ------------------------------------ | --------------------------------------------- | --------------------- | --------------------- |
| `chat`        | `chat://{conversation_id}`           | Full webview → `chat-surface` `ChatShell`     | Chats sidebar         | N/A (live surface)    |
| `convo`       | `convo://{conversation_id}`          | Lightweight webview → `TranscriptView`        | Chats sidebar         | No                    |
| `run`         | `run://{run_id}`                     | Lightweight webview → `RunView`               | Runs sidebar          | No                    |
| `subagent`    | `subagent://{run_id}/{subagent_id}`  | Lightweight webview → `SubagentView`          | Run inspector (right) | No                    |
| `tool-result` | `tool-result://{run_id}/{step_id}`   | Lightweight webview → `ToolResultView`        | Run inspector (right) | No                    |
| `mcp`         | `mcp://{server_id}`                  | Lightweight webview → `McpCardView`           | MCPs sidebar          | Limited (auth, scope) |
| `mcp-tool`    | `mcp-tool://{server_id}/{tool_name}` | Lightweight webview → `McpToolView`           | MCPs sidebar (child)  | No                    |
| `skill`       | `skill://{skill_id}`                 | Lightweight webview → `SkillBundleView`       | Skills sidebar        | Phase 5               |
| `workspace`   | `workspace://{workspace_id}`         | Lightweight webview → `WorkspaceSettingsView` | Workspaces sidebar    | Yes                   |

Each scheme is registered by `enterprise-chat-core` (content provider + tree data provider) and `enterprise-chat-ui` (custom editor provider) at activation.

```ts
// packages/chat-surface/src/uri/schemes.ts
export const ARTIFACT_SCHEMES = {
  chat: "chat",
  conversation: "convo",
  run: "run",
  subagent: "subagent",
  toolResult: "tool-result",
  mcp: "mcp",
  mcpTool: "mcp-tool",
  skill: "skill",
  workspace: "workspace",
} as const;

export type ArtifactScheme =
  (typeof ARTIFACT_SCHEMES)[keyof typeof ARTIFACT_SCHEMES];

export function parseArtifactUri(raw: string): ArtifactRef {
  /* … */
}
export function buildArtifactUri(ref: ArtifactRef): string {
  /* … */
}
```

URI parsing and formatting live in `chat-surface` so the web app and desktop produce identical references (e.g. deep links pasted from one surface open correctly in the other).

## 5. Component architecture

Three logical layers, top to bottom:

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Shell — apps/desktop/code-oss (forked Code – OSS)                       │
│  • product.json branding + update URL + default open editor              │
│  • patches/  (audited, ≤ 30 lines each, listed in patches/series)        │
└─────────────────────────────┬────────────────────────────────────────────┘
                              │
┌─────────────────────────────┴────────────────────────────────────────────┐
│  Bundled extensions — apps/desktop/extensions/                           │
│                                                                          │
│  enterprise-chat-core      enterprise-chat-ui                            │
│  • OIDC client             • Custom editors (chat://, run://, …)         │
│  • SecretStorage           • Activity bar / sidebar trees                │
│  • Transport RPC bridge    • Auxiliary bar Run Inspector view            │
│  • SSE pump                • Status bar items                            │
│  • URI content providers   • Command palette commands                    │
│  • Workspace state sync    • Webview entrypoint that mounts chat-surface │
│                                                                          │
│  (no UI)                   (mounts webviews; calls into core via RPC)    │
└─────────────────────────────┬────────────────────────────────────────────┘
                              │
┌─────────────────────────────┴────────────────────────────────────────────┐
│  Shared packages — packages/                                             │
│  • chat-surface (NEW)      React components (chat, viewers, inspector)  │
│  • chat-transport (NEW)    Transport interface + WebTransport +          │
│                            WebviewTransport + RPC protocol               │
│  • api-types (existing)    TS contracts to backend-facade                │
│  • design-system (exist.)  Primitives + tokens                           │
└─────────────────────────────┬────────────────────────────────────────────┘
                              │ HTTPS / SSE
                              ▼
                  backend-facade:8200 (unchanged)
```

**Why two extensions and not one?** The boundary between long-lived security-sensitive code (auth, transport, secret storage) and frequently-changing UI contributions matters. `enterprise-chat-core` gets stricter review and slower release; `enterprise-chat-ui` can iterate fast. They communicate via typed in-process APIs, not via RPC.

## 6. Transport contract

The chat surface speaks to a single port:

```ts
// packages/chat-transport/src/transport.ts
export interface Transport {
  /** RPC-style request over HTTPS. */
  request<TRes>(req: TypedRequest): Promise<TRes>;

  /** Resumable server-sent stream subscription. */
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

export interface TransportCapabilities {
  readonly substrate: "web" | "desktop-webview";
  readonly nativeSecretStorage: boolean;
  readonly fileSystemAccess: boolean; // false in phase 1
  readonly clipboardWrite: boolean;
  readonly openExternal: boolean;
}
```

Two implementations:

- **`WebTransport`** (`packages/chat-transport/src/web/`) — `fetch` + cookie-based session + SSE via `fetch` stream reader (avoids `EventSource`'s header limitation). Used by `apps/frontend`.
- **`WebviewTransport`** (`packages/chat-transport/src/webview/`) — `postMessage` RPC. Used inside desktop webviews. The matching `ExtensionTransportBridge` lives in `enterprise-chat-core` and translates RPCs into real HTTPS/SSE calls with the bearer attached from `SecretStorage`.

RPC protocol (schema-validated both directions):

```ts
// packages/chat-transport/src/webview/rpc-protocol.ts
export const RpcRequest = z.discriminatedUnion("method", [
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
]);

export const RpcResponse = z.union([
  z.object({ id: z.string(), ok: z.literal(true), result: z.unknown() }),
  z.object({ id: z.string(), ok: z.literal(false), error: RpcErrorSchema }),
]);

export const RpcEvent = z.object({
  type: z.literal("stream-event"),
  subscriptionId: z.string(),
  event: RuntimeEventEnvelopeSchema,
});

export const PROTOCOL_VERSION = 1;
```

Every RPC carries an `id`; the extension rejects unknown methods or schema failures with a typed error. SSE events flow as `RpcEvent` messages on the same channel. Protocol mismatches are loud, not silent.

## 7. Workbench customization plan

**Allowed via `product.json`:**

- App name, icon, bundle ID, URL handlers
- Update server URL and channel name
- Default open editor (point at `chat://welcome`)
- Telemetry endpoint (none initially — we don't ship MS telemetry)
- Disabled features list (e.g. interactive playground, walkthroughs)

**Allowed via bundled extensions:**

- Custom URI providers + custom editors (see §4)
- Tree views in activity bar + sidebar
- Auxiliary bar view (Run Inspector)
- Status bar items
- Commands + keybindings
- Welcome content for empty states
- Quick-open contributions (e.g. "Go to chat…", "Go to run…")

**Patches (audited, listed in `apps/desktop/patches/series`):**

- Rebrand any "Visual Studio Code" strings not covered by `product.json`
- Hide File menu items that don't apply (`Open Folder`, `Open Workspace from File`, `New Window from Profile`, …)
- Change default activity bar order to put Chats first
- Remove the upstream "Get Started" walkthrough page
- Override the default welcome editor to `chat://welcome`

**Forbidden patches:**

- Patches to the editor engine (Monaco)
- Patches that change extension API surface area
- Patches that diverge from upstream architecture in any way that makes upstream merges harder

Each patch carries a `# Why:` header explaining the reason; `apps/desktop/patches/README.md` is the index.

## 8. Auth & secret handling

**Desktop OIDC flow:**

1. Extension activates; checks `SecretStorage` for `access_token` + `refresh_token`
2. If access token absent or expired beyond refresh window:
   1. Spin up a single-use loopback HTTP server bound to `127.0.0.1:{ephemeral_port}`
   2. Open system browser to `https://app.acme.com/auth/desktop?client_id=…&redirect_uri=http://127.0.0.1:{port}/cb&code_challenge=…&state=…`
   3. Browser auth completes; redirects back to loopback with `?code=…&state=…`
   4. Extension verifies `state`, exchanges `code` + PKCE verifier for tokens at backend's token endpoint
   5. Tokens stored in `SecretStorage`; loopback server shut down
3. Access token short-lived (15 min); refresh token rotated on every use
4. On 401 from `backend-facade`: refresh once; on refresh failure, re-launch flow

**Logout:** clear `SecretStorage`, close all webviews, return to login view.

**Multi-tenant (workspace switch):** the session JWT carries a `workspace_id` claim. Switching workspace calls `POST /v1/me/active-workspace`; backend issues a new session bound to the new workspace. No full re-auth.

**Web app:** keeps its existing session machinery. Dev-IdP minting stays for `apps/frontend` dev; production web uses real OIDC via cookie-based session at the web origin. No auth code is shared between web and desktop — they're substrate-specific by design.

## 9. Streaming & SSE bridging

Webviews can't open `EventSource` to arbitrary origins under VS Code's CSP, and `EventSource` can't carry custom headers anyway. The flow:

```
webview                       extension (core)              backend-facade
───────                       ───────────────               ──────────────
transport.subscribeRunStream
  → postMessage RPC
                              ExtensionTransportBridge
                                opens fetch stream with Bearer
                                                       ─────→ GET /v1/agent/runs/{id}/stream
                                                              ?after_sequence=N
                              parses each SSE event ←─────  (text/event-stream)
  ←── postMessage 'stream-event'
handler(event)                 tracks highest sequence_no
                              on disconnect: reconnect from
                                highest sequence_no
                              fans out to multiple webview
                                subscribers if applicable
```

Reconnect logic lives in _one place_ — the extension. The React surface assumes the stream is reliable. The same `RuntimeEventEnvelope` shape that `runtime_api` already emits is what the webview's handler receives; no schema translation.

## 10. Repo layout (proposed deltas)

```
apps/
  frontend/                       MODIFIED → uses chat-surface + WebTransport
  desktop/                        NEW
    .gitignore
    package.json
    README.md
    code-oss/                     git subtree of microsoft/vscode at pinned tag
    product/
      product.json
      icons/
      branding/
    patches/
      series
      0001-rebrand-about-dialog.patch
      0002-hide-file-open-folder.patch
      ...
      README.md
    extensions/
      enterprise-chat-core/
        package.json
        src/
          extension.ts
          auth/
            oidc-client.ts
            loopback-server.ts
            secret-storage.ts
          transport/
            bridge.ts             webview RPC server
            sse-pump.ts           SSE → postMessage fanout
          uri/
            chat-provider.ts
            run-provider.ts
            tool-result-provider.ts
            mcp-provider.ts
            skill-provider.ts
            workspace-provider.ts
          state/
            workspace-sync.ts
        tsconfig.json
      enterprise-chat-ui/
        package.json
        src/
          extension.ts
          editors/
            chat-editor.ts
            run-editor.ts
            tool-result-editor.ts
            mcp-card-editor.ts
            ...
          views/
            chats-tree.ts
            workspaces-tree.ts
            mcps-tree.ts
            skills-tree.ts
            runs-tree.ts
            run-inspector.ts      auxiliary bar
          status-bar/
            persona-item.ts
            connectors-item.ts
            tokens-item.ts
          commands/
            new-chat.ts
            switch-workspace.ts
            ...
          webview/
            entrypoint.html       loads chat-surface bundle
            preload.ts
        media/                    built chat-surface bundle copied here
        tsconfig.json
    build/
      mac/
        entitlements.plist
        Info.plist.template
        notarize.ts
      windows/
        wix.xml                   MSI definition
        sign.ps1
      linux/                      CI verification only
      update-server/
        channel.json.template
    scripts/
      rebase-upstream.sh
      build.sh
      package.ts
      sign.ts
      release.ts

packages/
  chat-surface/                   NEW
    package.json
    src/
      index.ts
      shell/
        ChatShell.tsx
        ChatLayout.tsx
      messages/
        MessageList.tsx
        MessageRenderer.tsx
      composer/
        Composer.tsx
      artifacts/
        TranscriptView.tsx
        RunView.tsx
        SubagentView.tsx
        ToolResultView.tsx
        McpCardView.tsx
        McpToolView.tsx
        SkillBundleView.tsx
        WorkspaceSettingsView.tsx
      inspector/
        RunInspector.tsx
        ToolCallList.tsx
        SubagentList.tsx
        CitationsList.tsx
        ApprovalsQueue.tsx
      uri/
        schemes.ts
        parsers.ts
      providers/
        TransportProvider.tsx
        SessionProvider.tsx
    tsconfig.json
    vite.config.ts                builds an embeddable bundle

  chat-transport/                 NEW
    package.json
    src/
      transport.ts                interface
      types.ts                    TypedRequest, Session, RuntimeEventEnvelope
      web/
        WebTransport.ts
        sse.ts                    fetch-based SSE reader
      webview/
        WebviewTransport.ts       client (in webview)
        rpc-protocol.ts           Zod schemas shared with extension
        ExtensionTransportBridge.ts   server (in extension)
    tsconfig.json
```

The planned but unimplemented `apps/mac` and `apps/windows` directories are dropped from the plan. Cross-platform differences live in `apps/desktop/build/{mac,windows}/`.

## 11. Build, signing, distribution

**Build matrix:**

- macOS x86_64
- macOS arm64
- macOS Universal (lipo from the two above) ← shipped artifact
- Windows x86_64 ← shipped artifact
- Windows arm64 (built, not yet shipped)
- Linux x86_64 (CI verification only)

**Signing:**

- macOS: Developer ID Application certificate; notarization via `xcrun notarytool`; Hardened Runtime enabled; entitlements minimal (V8 JIT, library validation; no allow-unsigned)
- Windows: EV code-signing cert on a hardware token, attached to a dedicated release runner; Authenticode timestamp from a known TSA

CI: every PR builds Linux + unsigned Mac/Windows binaries. Releases run on isolated runners with signing material; signing material never reaches PR/dev runners.

**Two channels per OS:**

- `stable` — direct download; Squirrel auto-update enabled
- `enterprise-mdm` — same source, `product.json` flips `enableAutoUpdate: false`; IT pushes updates via Jamf/Intune

**Distribution endpoints (target shape):**

```
https://desktop.acme.com/download/{channel}/{os}/{arch}/latest   → 302 to versioned URL
https://desktop.acme.com/update/{channel}/{os}/{arch}/manifest.json  → Squirrel manifest
https://desktop.acme.com/releases/{channel}/{version}/notes.md
```

**Update server design:**

- Stateless; reads manifests from object storage (S3/GCS); no database
- Release pipeline writes manifests as the last step of a successful sign+notarize
- Force-upgrade flag (`minSupportedVersion`) — clients below the minimum see a blocking dialog until they update; used for security releases
- Graceful degradation: update endpoint unreachable does not block app launch

## 12. Rebase strategy

- Pin to a Code – OSS minor tag at start (selected at phase 1 kickoff; likely the most recent LTS-equivalent minor)
- Track upstream via `git subtree` pull into `apps/desktop/code-oss/`
- Patches applied via quilt-style script during build, ordered by `apps/desktop/patches/series`
- Weekly during active dev: pull upstream tip of pinned minor → reapply patches → smoke test → fix any breakage → commit
- Monthly during stable: consider minor-version bump → larger rebase → broader smoke test
- A patch that breaks two rebases in a row gets rewritten as an extension contribution or accepted as ongoing cost with an explicit owner

A CI gate fails the build if `apps/desktop/patches/` exceeds an agreed line count or patch count without an accompanying CHANGELOG entry.

## 13. Telemetry, crash, logging

**Telemetry:**

- Strip Microsoft telemetry endpoints entirely (patched out in build script — listed in `patches/`)
- Custom event pipeline scoped to product events: app launch, chat opened, run started, run completed, errors with stack but never with chat content
- Opt-out at install time and via Settings; default depends on channel (`stable` defaults on with consent dialog at first launch; `enterprise-mdm` defaults off and is MDM-controlled)

**Crash reporting:**

- Electron crash reporter posts native dumps to our crash endpoint with minimal context (app version, OS version, CPU arch); never includes user content
- Renderer JS errors captured only with explicit consent

**Logging:**

- Structured logs to platform-standard locations: `~/Library/Logs/Enterprise/<version>/` on macOS, `%APPDATA%\Enterprise\logs\` on Windows
- "Reveal Logs" command in command palette
- No automatic log shipping; a "Send Diagnostics" command bundles logs into a zip the user can attach to support tickets

## 14. Phased delivery

Each phase has explicit exit criteria. No phase rolls forward without sign-off.

### Phase 0 — Refactor for substitution (web app unchanged from user's perspective)

- Create `packages/chat-transport` with `Transport` interface and `WebTransport` impl
- Create `packages/chat-surface`; migrate chat shell + message rendering + composer + existing right-panel inspector from `apps/frontend`
- `apps/frontend` consumes both packages; all `fetch`/SSE calls routed through `WebTransport`
- Update `docs/architecture/service-boundaries.md` to record the new shared packages
- **Exit criteria:** web app passes existing tests + manual smoke; `apps/frontend/src/` has zero direct API calls outside the transport; bundle size deltas reviewed

### Phase 1 — Desktop shell

- Vendor Code – OSS into `apps/desktop/code-oss/` via git subtree at pinned tag
- Add `apps/desktop/product/` branding + icons + `product.json`
- Add minimal patch series (rebrand, hide File menu items, set default editor)
- Build pipelines for Mac (universal) and Windows (MSI) — signing wired but not yet executed in CI; unsigned binaries for dev
- App launches with branding, shows blank workbench, opens placeholder welcome editor
- **Exit criteria:** Mac + Windows installers download, install, launch, show branded empty workbench; CI builds reproducibly

### Phase 2 — Bundled chat MVP

- `enterprise-chat-core`: OIDC client + loopback server + SecretStorage; transport RPC bridge; `chat://` URI provider
- `enterprise-chat-ui`: custom editor for `chat://`, Chats tree view in left sidebar, persona status bar item
- Webview entrypoint loads built `chat-surface` bundle and mounts `ChatShell` with `WebviewTransport`
- End-to-end: launch → log in (system browser OIDC) → see chats → open chat → send → stream response
- Right sidebar is a stub view; status bar shows persona only
- **Exit criteria:** smoke test passes on Mac + Windows; tokens stored in OS keychain (verified by inspecting keychain); webview has zero direct network access (verified by CSP + traffic capture); reconnect-on-network-blip works

### Phase 3 — Run inspector + run/tool/subagent artifacts

- Auxiliary bar `RunInspector` view (tool calls, subagents, citations, approvals)
- `run://`, `subagent://`, `tool-result://` URI providers + custom editors
- Click in inspector → opens artifact tab in editor area, pinned next to chat
- Approval interactions (approve/reject pending tool call) from inspector
- **Exit criteria:** full agent run observable end-to-end without leaving the app; approvals round-trip correctly; many-tab perf measured (target: ≤ 500 MB resident with 10 artifact tabs open)

### Phase 4 — MCP / Skill / Workspace surfaces

- `mcp://`, `mcp-tool://`, `skill://`, `workspace://` URI providers + editors + sidebar trees
- Workspace switcher in status bar
- MCP install/auth/uninstall via the MCP card editor
- Skill bundles browseable read-only
- **Exit criteria:** product parity with the web app's MCP/skills/workspace surfaces; visual fidelity verified against the web app

### Phase 5 — Distribution hardening

- Update server live with both `stable` and `enterprise-mdm` channels
- Signing runners operational; every release signed + notarized
- Force-upgrade mechanism tested end-to-end
- Crash reporting endpoint live and validated
- Telemetry event schema agreed with privacy/legal; consent dialog wired
- Pre-flight release checklist documented at `apps/desktop/RELEASE.md`
- **Exit criteria:** `stable` release to internal users; MDM pilot deployment validated by IT

## 15. Risks (open, monitored)

| #   | Risk                                                                    | Mitigation                                                                                                                                              | Validates by                         |
| --- | ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------ |
| R1  | Webview perf with many artifact tabs                                    | Shared lightweight webview for viewers, full webview only for chat                                                                                      | Phase 3 perf gate (10 tabs ≤ 500 MB) |
| R2  | Patch drift against upstream                                            | Weekly rebase + CI gate on patch line count                                                                                                             | Phase 1 onward                       |
| R3  | OIDC complexity (device binding, multi-tenant claims, refresh rotation) | Dedicated mini-spec before phase 2 codifies the flows                                                                                                   | Phase 2 sign-off                     |
| R4  | VS Code muscle memory ("Open File…", terminal, …)                       | Welcome content + command palette curation; "Open File" returns a friendly not-in-scope message                                                         | UX review in phase 3                 |
| R5  | Update server reliability                                               | Object-store-backed manifests, no DB, CDN-cached; graceful degradation on unreachable                                                                   | Phase 5 chaos test                   |
| R6  | MDM channel divergence                                                  | Same source tree, different `product.json` flag; never fork for MDM                                                                                     | CI invariant from phase 5            |
| R7  | Webview RPC protocol drift                                              | Zod schemas in `chat-transport/src/webview/rpc-protocol.ts` shared between webview and extension; `PROTOCOL_VERSION` constant; mismatch rejected loudly | Phase 2 onward                       |
| R8  | Code-signing key compromise                                             | Hardware tokens; signing-only runners; key rotation runbook                                                                                             | Phase 5 readiness review             |
| R9  | Hidden direct API calls in `chat-surface`                               | Lint rule banning `fetch`/`window.*` in `packages/chat-surface/src/`                                                                                    | Phase 0 CI                           |

## 16. Open questions (resolve in dependent specs)

- **Q1** OIDC provider identity — first-party (extends `services/backend`) or vendor (Auth0/WorkOS/Okta)? Determines token shape, federation, and the device-binding story.
- **Q2** Workspace switching semantics — one session with workspace claims, or one cached session per workspace?
- **Q3** Offline behavior — read-only browse of cached conversations, or "no network, no app"?
- **Q4** Skill editing — read-only in MVP, or Monaco-edited skill bundles (`.md`/`.yaml`) in phase 5?
- **Q5** Update-success telemetry — do we report install/update outcomes back to the update server for fleet health?
- **Q6** Crash reporter endpoint — owned or vendored (Sentry/Bugsnag) for MVP?
- **Q7** Future third-party extension API — do we eventually expose an extension surface for partner integrations? If yes, the bundled extensions should be modeled as first-class examples of that API from the start.

## 17. Glossary

- **Code – OSS** — the open-source distribution of VS Code (MIT-licensed; no Microsoft branding/marketplace by default).
- **Workbench** — VS Code's UI shell: activity bar, sidebar, editor area, panel, auxiliary bar, status bar.
- **Auxiliary bar / secondary sidebar** — the right-hand sidebar (`workbench.action.toggleAuxiliaryBar`).
- **Custom editor** — a webview-backed editor registered for a URI scheme or file glob.
- **Webview** — an isolated Chromium context within the workbench, communicating with the extension host via `postMessage`.
- **SecretStorage** — VS Code API backed by OS keychain (macOS Keychain, Windows Credential Manager).
- **Squirrel** — the auto-update framework VS Code ships with (Squirrel.Mac and Squirrel.Windows).
