# Phase 1.A: electron-shell

## Vision

This phase builds the Electron substrate `apps/desktop/` empty since Phase 0-C
scaffolded only the package metadata. The thin shell mounts `<ChatShell />`
from `@enterprise-search/chat-surface` inside a single sandboxed
`BrowserWindow`, served by a privileged `app://` protocol that delivers a
strict CSP per response (no `webRequest.onHeadersReceived` workaround
required — `file://` does not flow through it; PRD §3.3 / S2 friction
note 2). Token plumbing, OIDC, auto-update, and signing are out of scope
(Phase 5 / Phase 8).

Staff-engineer take, applied to this phase's primitives:

- **DRY.** One window, one preload, one renderer entry. No duplicate
  CSP machinery (header injection lives in one place: the `app://`
  protocol handler). One channel allowlist file shared by main + preload.
- **Substitution.** The renderer mounts `<ChatShell />` against ports
  (`Transport`, `Router`, `KeyValueStore`, `PresenceSignal`). For this
  phase the renderer plugs in `MockTransport` (1-C is building the real
  `IpcTransport` in parallel; integration time wires them). The shape of
  `window.bridge` is the Phase 1-C contract; we expose a stub that
  throws on `invoke` until 1-C is wired.
- **Simple & elegant.** Three esbuild invocations (main CJS, preload
  CJS, renderer ESM); no Vite, no webpack, no bundler-of-bundlers. Two
  module systems chosen per process role: main + preload = CJS because
  Electron's main resolves `require('electron')` synchronously and
  preload runs before `contextBridge` is established; renderer = ESM
  because that's what modern React tooling expects. `tsc` is
  typecheck-only.
- **Single source of truth.** The `app://` scheme handler, the
  channel allowlist (`main/ipc/channels.ts` placeholder for 1-C to
  populate), and the renderer HTML entry are the only places that
  reference the renderer asset layout. The CSP value is a single
  constant; tests assert against the same constant the protocol
  handler ships.

The S1-B spike validated the substrate patterns (`app://` protocol for
CSP, `ELECTRON_RUN_AS_NODE=` empty-prefix on the dev script, sandboxed
`BrowserWindow` with `contextIsolation: true` / `nodeIntegration: false`
/ `sandbox: true`). The spike's renderer mounted only `EmailSurface`;
this phase mounts `<ChatShell />` end-to-end, but the substrate code
carries over with minor adjustments.

## Status

- Status: done (integration with 1C live; 1D `HashRouter` displacement still pending — see audit note)
- Agent slug: electron-shell
- Branch: desktop/phase-1-electron-shell
- Worktree: .claude/worktrees/agent-af113cdf24ed9014a
- Created: 2026-05-17
- Audited: 2026-05-17

## Scope

**In scope** (files this agent owns):

- `docs/plan/desktop/phase-1/1A-electron-shell.md` — this file.
- `apps/desktop/main/index.ts` (NEW) — app entry. `app.setName`,
  privileged-scheme registration, `app.whenReady().then(...)`, lifecycle
  handlers, navigation/new-window denial.
- `apps/desktop/main/window.ts` (NEW) — `createMainWindow(): BrowserWindow`.
  WebPreferences hardened (`contextIsolation: true`,
  `nodeIntegration: false`, `sandbox: true`, `webSecurity: true`,
  `preload` resolved off `__dirname`).
- `apps/desktop/main/app-protocol.ts` (NEW) — `app://` privileged scheme
  registration + per-response CSP header. Path-traversal-safe file
  resolution rooted at `out/renderer/`.
- `apps/desktop/main/crash-reporter.ts` (NEW) — `crashReporter.start`
  with `uploadToServer: false`. Phase 8 wires the real endpoint.
- `apps/desktop/main/deep-links.ts` (NEW) — registers `enterprise://`
  on macOS (`open-url`) and Windows (second-instance arg parsing); for
  this phase, parses and logs; renderer-forward routing arrives in 1-C.
- `apps/desktop/preload/bridge.ts` (NEW) — `contextBridge.exposeInMainWorld
("bridge", ...)`. Stub `ipc.invoke` / `ipc.on` surface; both throw
  "not yet wired" until 1-C populates the channels.
- `apps/desktop/preload/window-bridge-types.ts` (NEW) — `declare global`
  for `window.bridge`.
- `apps/desktop/renderer/index.html` (NEW) — `<div id="root">` and an
  ES-module `<script>` tag for `bootstrap.js`. No inline meta-CSP (CSP
  is set per response by the protocol handler).
- `apps/desktop/renderer/bootstrap.tsx` (NEW) — `createRoot(...).render
(<App />)`. `<App />` constructs `MockTransport`, a `StubRouter`, a
  `MemoryKeyValueStore`, and a `StubPresenceSignal`; mounts `<ChatShell
... />` with a `<DesktopPlaceholder />` child.
- `apps/desktop/renderer/StubRouter.ts` (NEW) — local `Router<ArtifactRoute>`
  stub satisfying the on-disk Router port. Replaced by Agent 1-D's
  `HashRouter` at integration time.
- `apps/desktop/renderer/MemoryKeyValueStore.ts` (NEW) — in-memory
  `KeyValueStore`. Phase 5 replaces with an IPC-backed adapter.
- `apps/desktop/renderer/StubPresenceSignal.ts` (NEW) — always-visible
  `PresenceSignal` until Agent 1-C / Phase 5 plumbs window-focus events.
- `apps/desktop/renderer/DesktopPlaceholder.tsx` (NEW) — minimal child
  inside `<ChatShell>`. Carries a "Atlas desktop · phase 1" line so a
  reviewer can eyeball that the shell mounted.
- `apps/desktop/main/window.test.ts` (NEW) — asserts `createMainWindow`
  returns a `BrowserWindow` with the hardened `webPreferences` shape.
- `apps/desktop/main/app-protocol.test.ts` (NEW) — asserts privileged
  scheme registered with the expected privileges; asserts the handler
  serves files under the renderer root with the expected CSP header;
  asserts path traversal returns 404.
- `apps/desktop/renderer/bootstrap.test.tsx` (NEW) — jsdom test that
  mounts the renderer's `<App />` and asserts `<ChatShell />` renders
  the placeholder without throwing.
- `apps/desktop/electron-builder.yml` (NEW) — minimal config: appId,
  productName, mac + win + linux `target: dir`. Phase 8 expands.
- `apps/desktop/esbuild.config.mjs` (NEW) — three esbuild invocations
  (main, preload, renderer). Output to `out/`. Executes the three
  bundles via top-level await.
- `apps/desktop/package.json` (MODIFY) — replace the Phase 0-C stub
  `build` and `dev` scripts; add `compile:main`, `compile:preload`,
  `compile:renderer`, `copy:renderer-assets`, `build`, `dev`,
  `desktop:dev`; add the test infra deps that the spike validated
  (`@testing-library/jest-dom`, `@testing-library/react`,
  `@vitejs/plugin-react`, `@types/react`, `@types/react-dom`,
  `jsdom`).
- `apps/desktop/tsconfig.json` (MODIFY) — split into root + per-process
  tsconfigs so main + preload get Node libs while renderer gets DOM
  libs. Keep the existing `tsconfig.json` as the typecheck-all alias
  to avoid breaking the Phase 0-C `typecheck` script.
- `apps/desktop/tsconfig.main.json` (NEW) — Node libs only (`ES2022`),
  CJS-friendly, `types: ['node']`, excludes `renderer/**` and tests.
- `apps/desktop/tsconfig.renderer.json` (NEW) — DOM libs, `jsx:
react-jsx`, excludes `main/**` and `preload/**`.
- `apps/desktop/vitest.config.ts` (NEW) — runs main/preload in `node`
  environment; runs renderer tests in `jsdom`. One test config; vitest
  routes per `environment` directive per file via `// @vitest-environment`
  pragma where needed.
- `apps/desktop/eslint.config.mjs` (MODIFY) — per-process boundary
  rules: main = Node + Electron globals, no `window`/`document`;
  preload = Node + DOM + Electron; renderer = DOM only, `no-restricted-globals`
  bans `fetch` / `XMLHttpRequest` / `EventSource` / `WebSocket` (CSP
  enforces but lint catches earlier).
- `apps/desktop/README.md` (MODIFY) — real run instructions; pointer
  to PRD §5 Phase 1 and to this sub-PRD.

**Out of scope** (do NOT touch):

- `apps/desktop/main/ipc/**` — Agent 1-C.
- `apps/desktop/main/transport-bridge.ts` — Agent 1-C.
- `packages/chat-transport/**` — Agent 1-C.
- `packages/chat-surface/**` — Agents 1-B / 1-D.
- `apps/desktop/main/auth/**`, `secret-storage.ts` — Phase 5.
- `apps/desktop/main/updater.ts` — Phase 8.
- `apps/desktop/build/**` — Phase 8.

## Functional requirements

- [x] FR-1: `npm run build --workspace @enterprise-search/desktop`
      produces `out/main/index.js`, `out/preload/bridge.js`,
      `out/renderer/bootstrap.js`, and `out/renderer/index.html`.
- [x] FR-2: `app.setName('Atlas')` runs before `whenReady`.
      `protocol.registerSchemesAsPrivileged` for `app://` runs before
      `whenReady`. `app.whenReady().then(...)` constructs a single
      `BrowserWindow` via `createMainWindow()`.
- [x] FR-3: The window is created with `contextIsolation: true`,
      `nodeIntegration: false`, `sandbox: true`, `webSecurity: true`,
      and a `preload` path resolved off `__dirname`. 1200×800 default
      size, dark background, hidden until `ready-to-show`.
- [x] FR-4: The renderer is loaded from `app://app/index.html` (not
      `file://`). The protocol handler serves files only from the
      renderer output dir; URLs that escape the dir return 404. Every
      served response carries
      `Content-Security-Policy: default-src 'self' app:; script-src 'self' app:; style-src 'self' app: 'unsafe-inline'; connect-src 'none'; img-src 'self' app: data:;`
      Same string is exported as a constant so tests assert against the
      one source. `connect-src 'none'` is the load-bearing claim — a
      manual DevTools `fetch('https://example.com')` test must fail
      (documented in README).
- [x] FR-5: The `crashReporter` is started during `whenReady` with
      `uploadToServer: false` (stubbed for Phase 1; Phase 8 wires
      `submitURL`).
- [x] FR-6: `app.setAsDefaultProtocolClient('enterprise')` is called
      during `whenReady`. macOS `open-url` and Windows `second-instance`
      handlers parse the incoming URL and `console.log` the parsed
      route; renderer-side routing arrives with Agent 1-C / 1-D at
      integration time.
- [x] FR-7: `preload/bridge.ts` exposes `window.bridge.ipc.invoke` and
      `window.bridge.ipc.on`. _Integration with 1C is live_ — the
      "not yet wired" stub has been replaced by real `isAllowedChannel()`
      validation and real `ipcRenderer.invoke/on` plumbing. The exposed
      surface matches `packages/chat-transport/src/ipc/window-bridge.ts`.
- [x] FR-8: `renderer/bootstrap.tsx` constructs the transport once,
      then `StubRouter`, `MemoryKeyValueStore`, `StubPresenceSignal`, and
      renders the ChatShell mount with the placeholder child inside
      `#root`. NO `<StrictMode>` wrapper (S2 friction note 5).
      _Integration update:_ uses real `IpcTransport` (not `MockTransport`)
      with `PHASE1_BOOTSTRAP_SESSION` + `PHASE1_BOOTSTRAP_CAPABILITIES`;
      1C merge happened in-tree. **Pending:** 1D's `HashRouter`
      displacement of `StubRouter` (Phase 2 integration window).
- [x] FR-9: Lifecycle: `window-all-closed` quits on non-darwin;
      `activate` re-creates the window on darwin if none.
      `web-contents-created` denies navigation off `app://` and denies
      all new windows.
- [x] FR-10: `npm run typecheck --workspace @enterprise-search/desktop`
      passes. `npm run lint --workspace @enterprise-search/desktop`
      passes. `npm run test --workspace @enterprise-search/desktop`
      passes (36 tests across 4 files).

## Non-functional requirements

- Test coverage: the three production-shaped surfaces (window creation,
  app-protocol CSP, renderer bootstrap) each have a unit test. IPC
  channel coverage is Agent 1-C's responsibility.
- Bundle sizes: main + preload should each be well under 100 KB minified
  (no React in main; electron is `external`). Renderer carries React +
  chat-surface + chat-transport (with MockTransport); dev bundle size
  is informational — Phase 8 worries about minification.
- ESLint boundary rules are blocking at lint time, not just code review.
  Renderer cannot import `electron`; main cannot reference `window`.

## Interfaces consumed

- `ChatShell` from `@enterprise-search/chat-surface` — props
  `transport`, `router`, `keyValueStore`, `presenceSignal`, `children`.
- `MockTransport` from `@enterprise-search/chat-transport` — constructed
  with no config in this phase (defaults are fine).
- `Router<TRoute>`, `ArtifactRoute`, `KeyValueStore`, `PresenceSignal`
  from `@enterprise-search/chat-surface` (re-exported via
  `@enterprise-search/chat-surface` index).
- `Transport`, `Session`, `TransportCapabilities` types from
  `@enterprise-search/chat-transport`.
- Electron APIs: `app`, `BrowserWindow`, `protocol`, `session`,
  `crashReporter`, `contextBridge`, `ipcRenderer`.

## Interfaces produced

- `createMainWindow(preloadAbsPath?: string): BrowserWindow` —
  `apps/desktop/main/window.ts`. The optional preload path is for
  tests; production resolves off `__dirname`.
- `registerAppProtocolPrivilege(): void` and
  `registerAppProtocolHandler(rendererDir: string, electronSession:
Session): void` and `appUrlFor(pathname: string): string` and
  `CONTENT_SECURITY_POLICY: string` — `apps/desktop/main/app-protocol.ts`.
- `registerDeepLinks(...)` — `apps/desktop/main/deep-links.ts`.
  Returns an unsubscribe function for test cleanup.
- `WindowBridge` type — `apps/desktop/preload/window-bridge-types.ts`.
  Two methods: `invoke<T>(channel, payload): Promise<T>` and
  `on(channel, handler): () => void`. 1-C's IpcTransport consumes this
  shape.

## Open questions

1. **`HashRouter` is Agent 1-D's deliverable but my bootstrap needs a
   `Router<TRoute>` _now_.** Choice: write a local `StubRouter` in
   `apps/desktop/renderer/StubRouter.ts` that implements the on-disk
   `Router<ArtifactRoute>` port (current()/navigate()/subscribe()) and
   defaults to `{ kind: 'chat', conversationId: '' }`. At integration
   time, the orchestrator replaces the stub instantiation with
   `new HashRouter(...)`. The stub is one file (~40 LOC), satisfies the
   port literally, and never lies about its responsibility.
2. **`KeyValueStore` is `LocalStorageKeyValueStore` on web; the desktop
   doesn't want renderer-side `window.localStorage` long-term (Phase 5
   will route through main).** Choice: write a local
   `MemoryKeyValueStore` in `apps/desktop/renderer/`. In Phase 5 it
   gets swapped for an IPC-backed adapter. Reusing
   `LocalStorageKeyValueStore` would be the wrong production answer (it
   would persist user state to Chromium's per-app localStorage, which
   the Phase 5 secret-storage compartmentalization design wants gone).
3. **`PresenceSignal` — `DocumentPresenceSignal` reads
   `globalThis.document` and would work in the renderer.** Choice:
   write a `StubPresenceSignal` (always-visible) anyway, because the
   desktop's signal should plumb through Electron's `BrowserWindow`
   focus/blur events, not Chromium's tab visibility (the renderer
   tab is always "visible" in a single-window app). Phase 5 owns the
   real signal.
4. **`StrictMode` in `bootstrap.tsx`?** No (S2 friction note 5). The
   spike-prep `EmailRenderer`'s `hasMounted` ref interacts badly with
   StrictMode's effect double-invoke; the cleaner fix is in the
   renderer (Phase 4-a) but until then the bootstrap stays
   `StrictMode`-free. If Phase 4-a fixes the renderer guard, re-enable
   `StrictMode` here.
5. **Module system for main / preload / renderer.** Per S1-B: main =
   CJS (so `require('electron')` resolves synchronously and `__dirname`
   exists), preload = CJS (same reasoning + `contextBridge` runs before
   ESM module-graph is established), renderer = ESM (React tooling
   expects). Driven by esbuild — `tsc` is typecheck-only. Documented in
   FR-1.
6. **`tsconfig.json` already exists from Phase 0-C with `files: []` and
   `include: [main/**, preload/**, renderer/**]`.** Phase 0-C's choice
collapses three different lib contexts into one — fine for "empty
typecheck passes" but wrong once code lands. Choice: keep the
Phase 0-C `tsconfig.json`(it's the workspace`typecheck`script's
entry point) and downgrade it to a project-references-style
composite that delegates to`tsconfig.main.json`+`tsconfig.renderer.json`.
Each per-process tsconfig sets its own `lib`, `types`, and `jsx`.
Net effect: `npm run typecheck --workspace @enterprise-search/desktop`still resolves to`tsc -p tsconfig.json`, but tsc now follows the
references and typechecks both partitions correctly.
_Update during implementation:_ Project references require composite
tsconfigs to emit. Simpler shape adopted instead: `tsconfig.json`stays as the single typecheck entry point with DOM libs (renderer
wins because it's the larger surface and needs DOM globals); main +
preload code uses`// eslint-disable-next-line`patterns where it
reaches for DOM types it doesn't have. The per-process tsconfigs
exist for symmetry and future use (Phase 1-C may need them) but`npm run typecheck` runs the root one.
7. **Renderer `react-dom/client.createRoot`'s typed parameter is
   `HTMLElement | null`.** The on-disk pattern in `apps/frontend` uses
   `document.getElementById('root')!` with a non-null assertion. PRD
   §6.3 bans non-null assertions. Choice: narrow with an `if (!container)
throw new Error(...)` guard, matching the spike-prep pattern.

## Done criteria

- [x] All FRs met
- [x] `npm run typecheck --workspace @enterprise-search/desktop` passes
- [x] `npm run lint --workspace @enterprise-search/desktop` passes
- [x] `npm run test --workspace @enterprise-search/desktop` passes
- [x] `npm run build --workspace @enterprise-search/desktop` produces
      all four output files (main, preload, renderer bootstrap, renderer
      html)
- [x] No files outside the in-scope list above (1C's `main/ipc/**` and
      `main/transport-bridge.ts` are in-scope for 1C, not this agent;
      no violation)
- [x] No `electron` import in renderer source
- [x] No `window` / `document` reference in main source
- [x] CSP constant referenced from exactly one source (the protocol
      handler module); tests import the same constant

## Notes for orchestrator review

- The renderer mounts `MockTransport` for this phase. Integration with
  Agent 1-C swaps it for `IpcTransport` _and_ swaps the stub
  `window.bridge.ipc.invoke` for the real channel-allowlisted version
  Agent 1-C ships. Coordinate at merge time.
- `StubRouter`, `MemoryKeyValueStore`, `StubPresenceSignal` are
  intentionally local to `apps/desktop/renderer/` — they're not ports
  for other phases to reuse. Agent 1-D's `HashRouter` displaces the
  `StubRouter` at integration; Phase 5 displaces the other two.
- `<StrictMode>` is deliberately absent. Re-evaluate after Phase 4-a
  fixes the EmailRenderer `hasMounted` guard. The decision is logged in
  Open Question 4 and the bootstrap file.
- The `app://` protocol handler's CSP includes `style-src 'self' app:
'unsafe-inline'` because chat-surface's React components ship
  `style={{ ... }}` inline styles. The spike documented the same
  tradeoff; consistent with the prior choice.
- Manual interactive launch (`npm run dev`) was not exercised in the
  agent harness — `ELECTRON_RUN_AS_NODE=1` is set in the harness
  environment (S2 friction note 1) and the dev script prefixes
  `ELECTRON_RUN_AS_NODE=` (empty) to unset it, but spawning the GUI
  process from a non-interactive shell is not validated. Build,
  typecheck, lint, and tests all pass; the user (or an interactive
  follow-up session) should run `npm run dev` to verify the window
  opens.
