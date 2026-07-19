# Phase 0.5: spike-electron (S1-B)

## Vision

The substrate decision (PRD D1) is being made empirically by building the
SAME minimal renderer (`<EmailRenderer>` from spike-prep) inside two host
substrates and comparing the cost. This variant is the **custom Electron
app** side of that comparison.

From a staff-engineering perspective, the load-bearing questions for this
spike are not "does it work" (Electron will host any React tree). They are:

1. **How many lines of substrate code** does it take to host the renderer
   end-to-end?
2. **How clean is the seam** between the renderer and the rest of the
   stack? (i.e. can the renderer be byte-identical to the web version?)
3. **What does the security boundary cost** in code and complexity?
   (Bearer never in renderer, CSP locks down `connect-src`, IPC validated
   at both ends.)
4. **What does dev experience cost?** (Time from `npm run …:dev` to
   seeing the renderer; how friendly are stack traces; do React DevTools
   work; do TypeScript errors get caught.)

The renderer must be consumed **unchanged**. If anything inside
`packages/surface-renderers/src/email/` needs editing to host it in
Electron, the spike has revealed a port-design defect and that defect is
flagged in Open questions rather than fixed silently.

Apply DRY / substitution / simple-elegant / single-source-of-truth to the
**actual primitives** Electron provides:

- **DRY** — every line of substrate code I write that the VS Code variant
  also needs is a port-design failure. The renderer side of the IPC
  bridge is a `Transport` adapter — same shape, different wire.
- **Substitution** — `IpcTransport` and the spike-prep `MockTransport`
  are both `Transport`s. The renderer cannot tell them apart.
- **Simple & elegant** — main + preload + renderer, one BrowserWindow.
  No router in main. No state machine in the IPC handlers — the
  IpcTransport in the renderer is the state.
- **Single source of truth** — the `Transport` interface in
  `packages/chat-transport/src/transport.ts` is canonical. The IPC
  schema in this app mirrors that interface 1:1, not a sibling shape.

## Status

- Status: in-progress
- Agent slug: `spike-electron`
- Branch: `desktop/phase-S-spike-electron`
- Worktree: `.claude/worktrees/agent-a0cd0ff93c56abc09`
- Created: 2026-05-17

## Scope

**In scope** (files this agent owns):

- `docs/plan/desktop/phase-0.5/S1-spike-electron.md` — this file.
- `apps/electron-spike/package.json`
- `apps/electron-spike/tsconfig.main.json`
- `apps/electron-spike/tsconfig.renderer.json`
- `apps/electron-spike/eslint.config.js`
- `apps/electron-spike/vitest.config.ts`
- `apps/electron-spike/electron-builder.yml`
- `apps/electron-spike/README.md`
- `apps/electron-spike/.gitignore`
- `apps/electron-spike/main/index.ts`
- `apps/electron-spike/main/window.ts`
- `apps/electron-spike/main/app-protocol.ts`
- `apps/electron-spike/main/transport-bridge.ts`
- `apps/electron-spike/main/ipc/channels.ts` — channel name constants
- `apps/electron-spike/main/ipc/schemas.ts` — Zod schemas for IPC params
- `apps/electron-spike/main/ipc/handlers.ts` — ipcMain handler registration
- `apps/electron-spike/main/ipc/handlers.test.ts`
- `apps/electron-spike/preload/bridge.ts`
- `apps/electron-spike/preload/window-bridge-types.ts`
- `apps/electron-spike/renderer/index.html`
- `apps/electron-spike/renderer/bootstrap.tsx`
- `apps/electron-spike/renderer/IpcTransport.ts`
- `apps/electron-spike/renderer/IpcTransport.test.ts`
- `apps/electron-spike/renderer/EmailSurface.tsx`

**Out of scope** (do NOT touch):

- Everything else, especially:
  - `packages/chat-surface/*`, `packages/chat-transport/*`,
    `packages/surface-renderers/*` — consumed unchanged. The whole point
    of the spike is that the renderer is substrate-portable. If a change
    is required, it is flagged in Open questions, not silently made.
  - `apps/frontend/*`, `apps/vscode-spike/*` (the parallel S1-A variant
    is owned by another agent).
  - `services/*`, root `package.json` (the npm workspace globbing
    `apps/*` auto-registers this new app — no root-`package.json` edit
    needed).

## Functional requirements

- [ ] FR-1 — `npm run spike:electron:dev` launches the app with one
      command: compiles main + preload (`tsc`), compiles the renderer
      bundle (`esbuild`), and starts `electron .`.
- [ ] FR-2 — On launch, a single `BrowserWindow` opens at 1200×800,
      `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`,
      `webSecurity: true`, with a preload script that exposes a typed
      `window.bridge` via `contextBridge.exposeInMainWorld`.
- [ ] FR-3 — The renderer mounts the spike-prep `<EmailRenderer>` from
      `@enterprise-search/surface-renderers` unchanged, wired to an
      `IpcTransport` instance (the renderer-side `Transport`
      implementation that proxies every method over IPC to main).
- [ ] FR-4 — `IpcTransport.request({ method: 'GET', path: '/drafts/draft-1' })`
      round-trips through main: ipcMain validates with Zod, calls the
      shared `MockTransport.request(...)` instance held in main, returns
      the result, ipcRenderer resolves the renderer promise.
- [ ] FR-5 — `IpcTransport.subscribeServerSentEvents({ path: '/drafts/draft-1/events', onMessage })` returns an
      `SseSubscription` synchronously; main starts a `MockTransport`
      subscription tagged with a renderer-generated `subscriptionId`,
      and forwards every emitted event back over IPC on a
      `stream-event` channel; calling `subscription.close()` sends an
      unsubscribe message that closes the underlying `MockTransport`
      subscription on main.
- [ ] FR-6 — The streaming `Gmail.draft.create` event sequence
      (`tool_call_start` at t=0, five `tool_call_chunk` events at
      t=400/800/1200/1600/2000, `tool_call_end` at t=2400,
      `pending_diff_appeared` at t=2700) renders inside the
      `<EmailRenderer>` composer's PENDING block; the
      `EmailDiffOverlay` card appears with Approve and Reject buttons.
- [ ] FR-7 — Clicking Approve invokes `IpcTransport`'s outbound
      `bridge:diff:approve` channel which logs to the main-process
      console; the renderer-side state transitions to `accepted`.
      Same for Reject.
- [ ] FR-8 — Every IPC params payload is validated at the main-side
      boundary with a Zod schema (`apps/electron-spike/main/ipc/schemas.ts`).
      A deliberately-malformed payload triggers a rejected promise in
      the renderer with a typed error (no silent acceptance).
- [ ] FR-9 — Subscriptions opened by a `webContents` are automatically
      closed when that `webContents` is destroyed (window closed,
      reload), and explicit `transport.unsubscribe` also closes them.

## Non-functional requirements

- **Security boundary** — Bearer (in this spike: nothing, since
  `MockTransport.getSession()` returns `{ bearer: null }`) never crosses
  to the renderer. The renderer's IpcTransport sees `getSession()` as
  a synchronous accessor; the value is cached after first invoke.
  In production this becomes: main attaches the bearer to outbound HTTP
  on its way through `transport-bridge.ts`; renderer only ever sees
  opaque session metadata. The spike's contract is identical — only the
  payload shape changes when production swaps `MockTransport` for a
  real `WebTransport`-style HTTP+SSE pump.
- **CSP** — Renderer receives a strict `Content-Security-Policy`
  delivered via a custom `app://` protocol handler in main (set on the
  `Response` headers). The policy is `default-src 'self'; script-src
'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:;
font-src 'self'; connect-src 'none'; object-src 'none';
base-uri 'none'; form-action 'none'; frame-ancestors 'none'`. The
  `connect-src 'none'` clause is what blocks the renderer from making
  any outbound network request — verified with a deliberate
  `fetch('https://example.com')` in DevTools (must throw).
- **IPC surface minimality** — `window.bridge` exposes one method:
  `invoke(channel, payload)`, plus an `on(channel, handler)`
  unsubscribe-returning subscriber. The list of allowed channel names
  is enforced at both ends with a hardcoded allowlist (`CHANNELS`
  constant in `ipc/channels.ts`).
- **Type discipline** — `tsconfig.main.json` targets Node (CommonJS
  output for the Electron main process), `tsconfig.renderer.json`
  targets browser. Both extend the repo's `tsconfig.base.json` for
  shared strict settings. No `any` (use `unknown` and narrow). No
  non-null assertions.
- **Test coverage** — Vitest. Round-trip tests against a mocked
  `ipcRenderer` / `ipcMain` for request, subscribe (event delivery),
  unsubscribe (lifecycle), and Zod validation rejection. The
  Email/UI rendering is already covered by spike-prep tests in
  `packages/surface-renderers`.

## Interfaces consumed

- `Transport`, `SseSubscribeOptions`, `SseSubscription`, `Session`,
  `TypedRequest`, `TransportCapabilities`, `MockTransport`, `EMAIL_FIXTURE`
  from `@enterprise-search/chat-transport`
  (`packages/chat-transport/src/index.ts`).
- `EmailRenderer` from `@enterprise-search/surface-renderers`
  (`packages/surface-renderers/src/index.ts`). The spike-prep registry
  helper `registerAll()` is also imported for completeness, though we
  mount the component directly.
- `PendingDiff`, `SurfaceRendererProps` from
  `@enterprise-search/chat-surface`
  (`packages/chat-surface/src/index.ts`) — for the typed activeDiff
  state in the renderer wrapper.
- `electron` — `app`, `BrowserWindow`, `protocol`, `ipcMain`,
  `contextBridge`, `ipcRenderer`, `session`, `webContents`.
- `node:path`, `node:url`, `node:fs/promises` — main-side path/file
  helpers for the `app://` protocol handler.
- `react` 19, `react-dom/client` — mount the renderer.
- `zod` — schema validation at the IPC boundary.

## Interfaces produced

The Electron-spike workspace itself produces no public exports — it is
an app, not a library. The TypeScript contracts that matter are the
contextBridge surface and the IPC channel schemas, both private to the
app.

```ts
// apps/electron-spike/preload/window-bridge-types.ts
//
// Shape exposed on window.bridge via contextBridge.exposeInMainWorld.
// Renderer-side `IpcTransport` consumes only this interface.
export interface WindowBridge {
  readonly ipc: {
    /**
     * Round-trip RPC. The channel name must be one of the values in
     * the CHANNELS allowlist; main rejects unknown channels and Zod-
     * invalid payloads.
     */
    invoke<T = unknown>(channel: string, payload: unknown): Promise<T>;
    /**
     * Subscribe to pushes from main. Returns an unsubscribe function.
     * Handler receives the payload only (the ipcRenderer event object
     * is hidden — it is a privileged reference into Electron internals
     * and must never leak to renderer code).
     */
    on(channel: string, handler: (payload: unknown) => void): () => void;
  };
}

declare global {
  interface Window {
    readonly bridge: WindowBridge;
  }
}
```

```ts
// apps/electron-spike/main/ipc/channels.ts
//
// Hardcoded allowlist. Adding a channel here is the only way to expand
// the IPC surface. Mirrored on the renderer side via the same import.
export const CHANNELS = {
  transportRequest: "transport:request",
  transportGetSession: "transport:getSession",
  transportCapabilities: "transport:capabilities",
  transportSubscribe: "transport:subscribe",
  transportUnsubscribe: "transport:unsubscribe",
  streamEvent: "transport:stream-event",
  diffApprove: "diff:approve",
  diffReject: "diff:reject",
} as const;

export type ChannelName = (typeof CHANNELS)[keyof typeof CHANNELS];
export const CHANNEL_VALUES: ReadonlySet<string> = new Set(
  Object.values(CHANNELS),
);
```

```ts
// apps/electron-spike/main/ipc/schemas.ts (selected)
//
// One discriminated union per channel direction. Renderer→main payloads
// are validated by main; main→renderer payloads are validated by the
// renderer-side IpcTransport on receipt.
export const TransportRequestParams = z.object({
  method: z.enum(["GET", "POST", "PATCH", "PUT", "DELETE"]),
  path: z.string().min(1),
  query: z.record(z.string(), z.unknown()).optional(),
  body: z.unknown().optional(),
  headers: z.record(z.string(), z.string()).optional(),
});
export const TransportSubscribeParams = z.object({
  subscriptionId: z.string().min(1),
  path: z.string().min(1),
  query: z.record(z.string(), z.unknown()).optional(),
  eventName: z.string().optional(),
});
export const TransportUnsubscribeParams = z.object({
  subscriptionId: z.string().min(1),
});
export const DiffApproveParams = z.object({ diffId: z.string().min(1) });
export const DiffRejectParams = z.object({ diffId: z.string().min(1) });
export const StreamEventPayload = z.object({
  subscriptionId: z.string().min(1),
  kind: z.enum(["message", "open", "error", "closed"]),
  message: z.string().optional(),
  errorMessage: z.string().optional(),
});
```

## Open questions

The following non-obvious choices were made during sub-PRD authoring. Each
is flagged for orchestrator review at S2 (decision) time. None are blockers
for implementation; all proceed with the documented best-guess per D21
(spec-first-then-continue).

1. **`SseSubscription` is synchronous in the on-disk Transport, but IPC
   subscribe is async.** `transport.subscribeServerSentEvents(opts)`
   returns the subscription handle right away — the renderer cannot
   await IPC. Resolution: the **renderer generates the
   `subscriptionId` locally** (via `crypto.randomUUID()`), fires the
   `transport:subscribe` IPC in the background (returning a handle
   immediately), and registers a `transport:stream-event` listener
   that filters by `subscriptionId`. The handle's `close()` sends a
   `transport:unsubscribe` IPC with the same id and removes the
   listener. If `subscribe` rejects on main (Zod or otherwise), the
   error is forwarded to the renderer via the `stream-event` channel
   with `kind: 'error'` so the renderer's `onError` fires — same
   behavior the renderer would see for a backend SSE error. This
   keeps `subscribe()` synchronous from the renderer's POV, which
   matches the on-disk Transport contract verbatim.

2. **`getSession()` and `capabilities()` are also synchronous on the
   on-disk Transport.** Solution: the renderer-side `IpcTransport`
   takes a `bootstrapSession` and `bootstrapCapabilities` at
   construction time (passed in from `bootstrap.tsx`, which obtains
   them via a one-shot `invoke` during the React mount-time effect, or
   uses sensible defaults). For the spike, `MockTransport.getSession()`
   is `{ bearer: null }` and capabilities are the stock defaults, so
   the bootstrap snapshot is hardcoded as a fallback in
   `bootstrap.tsx`. In production this becomes a one-shot fetch at
   sign-in time, cached for the renderer's lifetime; the renderer
   never sees the live bearer — only a stable `{ bearer: '<opaque>' }`
   marker if any. This preserves the "no secrets in the renderer"
   invariant from D24/§6.7.

3. **CSP delivery: custom `app://` protocol vs `<meta>` tag vs
   `webRequest.onHeadersReceived`.** The spike instructions prefer
   header-injected CSP for the security boundary. Background:
   `webRequest.onHeadersReceived` does NOT intercept `file://` loads;
   `<meta http-equiv="Content-Security-Policy">` works for file:// but
   the instructions explicitly avoid it. Resolution: **register a
   custom `app://` protocol via `protocol.handle` in main** that serves
   files from the renderer build dir, set the CSP directly on the
   `Response` headers. This gives the CSP a real origin (`app://`),
   lets `connect-src 'none'` apply, and matches the production model
   (where the renderer is served by a stable URL, not the filesystem).
   The window calls `win.loadURL('app://renderer/index.html')` instead
   of `win.loadFile(...)`. Documented in the README so the
   orchestrator can reproduce.

4. **Channel allowlist enforcement on the preload side, not just the
   main side.** The spike instructions note "Constrain the allowed
   `channel` strings with a small switch / allowlist if time permits".
   The cost is two lines in `preload/bridge.ts`; doing it. If the
   renderer code asks for an unknown channel, the preload throws
   before the IPC even fires. This narrows the attack surface — a
   compromised renderer cannot send arbitrary channel names hoping to
   find an undocumented main-side handler.

5. **`style-src 'unsafe-inline'` in the CSP.** The `<EmailRenderer>`
   uses inline `style={{}}` attributes pervasively (pure
   React inline styles via `CSSProperties` objects, not inline
   `<style>` blocks). The CSP spec requires `style-src 'unsafe-inline'`
   (technically `style-src-attr 'unsafe-inline'` in CSP3, but for
   widest browser support we use the older form) to allow style
   attributes. **Trade-off:** this loosens style-injection protection,
   but: (a) we're not loading untrusted user content, (b) the
   alternative is to refactor the renderer to use stylesheets, which
   would require touching surface-renderers (out of scope and
   substrate-coupling). Flag for orchestrator: this is the same
   constraint the VS Code variant will hit — webview CSP also
   needs `'unsafe-inline'` for the renderer's style attributes. The
   substrate comparison is unaffected.

6. **CommonJS vs ESM for main process.** Electron 28+ supports ESM in
   main, but `__dirname` and module-resolution behavior differ in
   ways that add complexity. The spike uses CommonJS for main +
   preload (via `tsconfig.main.json` `"module": "CommonJS"`). The
   renderer is ESM (via esbuild `--format=esm`). This is the standard
   Electron pattern and minimises spike code.

7. **Sandboxed renderer + preload script: how does `crypto.randomUUID`
   work?** Sandboxed renderers run in a separate process with
   restricted Node APIs, but `crypto.randomUUID` is a Web Crypto API
   surface, available in any modern Chromium. Verified to exist on
   `globalThis.crypto` in sandboxed contexts. No polyfill needed.

8. **Renderer state for activeDiff / approve callbacks lives in a
   small wrapper component, not the EmailRenderer itself.** The
   spike-prep `EmailRenderer` already derives the diff internally
   from its own subscription. We pass `transport={ipcTransport}` and
   leave `activeDiff` undefined — the renderer drives its own state.
   The wrapper `<EmailSurface>` exists only to (a) hold a
   `onApproveDiff` / `onRejectDiff` that calls the diff:approve /
   diff:reject IPC channels, and (b) keep the bootstrap minimal. This
   minimises substrate-side renderer code.

## Done criteria

- [ ] All FRs met.
- [ ] `npm install` at repo root resolves and links the new workspace.
- [ ] `npm run typecheck --workspace @enterprise-search/electron-spike`
      passes.
- [ ] `npm run lint --workspace @enterprise-search/electron-spike` passes.
- [ ] `npm test --workspace @enterprise-search/electron-spike` passes
      (Zod validation, IpcTransport round-trip, subscription lifecycle).
- [ ] `npm run build --workspace @enterprise-search/electron-spike`
      produces `out/main/index.js`, `out/preload/bridge.js`,
      `out/renderer/bootstrap.js` and copies `renderer/index.html` to
      the renderer dir.
- [ ] No new third-party dependency without a one-line justification in
      `package.json` comments or this sub-PRD. Justified additions:
      `electron` (the substrate itself), `electron-builder` (referenced
      for `package` script — no signing in spike, `--dir` only),
      `esbuild` (renderer bundling, faster than vite for a single
      entry), `zod` (schema validation at IPC boundary), `@types/node`
      (Node typings for main).
- [ ] No imports from `apps/frontend/*` or `apps/vscode-spike/*`.
- [ ] No edits to anything under `packages/*`.
- [ ] `apps/electron-spike/README.md` exists and documents:
      prerequisites, dev/build commands, expected behavior, the CSP
      verification step, and the security boundary's location.

## Notes for orchestrator review

- The CSP `connect-src 'none'` test (FR / Open Q 3) is the single
  most important security claim in this spike. The orchestrator should
  run it manually: open DevTools console, type
  `fetch('https://example.com')`, confirm the browser blocks it with a
  CSP report message. Documented in the README.
- The renderer's `EmailRenderer` is consumed **without any
  modification** — verified by `git diff` showing no changes under
  `packages/`. If a future change to the renderer needs the substrate
  to do something different, that is a port-design issue and surfaces
  at Phase 4 contract freeze time, not in this spike.
- No `electron-builder` signing or notarization in this spike. The
  `package` script runs `electron-builder --dir` (unpacked) only. The
  point is to measure substrate cost, not to ship binaries — signing
  is Phase 8.
- The IPC schema in `ipc/schemas.ts` was deliberately kept narrow: only
  the methods the spike actually exercises. This is honest: the spike
  is not a complete production transport. The `WebTransport` (already
  on disk) is the production reference for what HTTP+SSE main-side
  shape will look like; the IPC schema in this spike is the shape that
  _renderer-side_ code will see, regardless of how main fetches the
  data.
- LOC accounting for the substrate comparison: every file under
  `apps/electron-spike/{main,preload,renderer}` counts as substrate
  code. Tests are reported separately. Config files
  (`package.json`, `tsconfig*.json`, `eslint.config.js`,
  `vitest.config.ts`, `electron-builder.yml`) count as build
  complexity, reported as a separate metric.
