# Phase 0.5: S1-A spike-vscode

## Vision

Substrate variant for the empirical spike that picks the desktop substrate
(PRD §5 Phase S, decisions D1/D7/D8/D11/D13/D14). This is the **VS Code
marketplace-extension** variant — runs against vanilla VS Code or VS Code
Insiders the developer already has installed. It is not a fork, does not
modify VS Code source, and does not propose patches.

Staff-engineer principles applied directly to this variant's primitives:

- **DRY** — the renderer code in `packages/surface-renderers/src/email/`
  is consumed unchanged from `packages/*`. The substrate-specific code
  in this variant is the host scaffolding only (extension manifest,
  CustomEditorProvider, RPC bridge, webview bootstrap, CSP).
- **Substitution** — the webview-side `WebviewTransport` implements the
  same on-disk `Transport` interface from `@enterprise-search/chat-transport`
  that `WebTransport` and the spike-prep `MockTransport` implement. The
  renderer cannot tell which `Transport` it has.
- **Single source of truth** — the Transport contract, the URI scheme,
  the renderer DOM and styles, the `SurfaceRegistry`, the email fixture,
  and the streaming event semantics all live in `packages/*` exactly
  once. This extension consumes them; it does not redefine any of them.
- **Simple & elegant** — one extension manifest, one CustomEditorProvider,
  one RPC schema module, one host-side bridge, one webview bootstrap, one
  webview transport. The seam between Node-side host and Chromium-side
  webview is one well-typed message channel.

## Status

- Status: in-progress
- Agent slug: `spike-vscode`
- Branch: `desktop/phase-S-spike-vscode`
- Worktree: `.claude/worktrees/agent-a485fe52733da55c2`
- Created: 2026-05-17

## Scope

**In scope** (files this agent owns):

- `docs/plan/desktop/phase-0.5/S1-spike-vscode.md` — this file
- `apps/vscode-spike/package.json` — VS Code extension manifest +
  npm scripts (`spike:vscode:dev`, `spike:vscode:package`, `compile`,
  `watch`, `typecheck`, `lint`, `test`)
- `apps/vscode-spike/tsconfig.json` — extends repo base; emits CJS for
  extension host code; webview code compiled by esbuild
- `apps/vscode-spike/tsconfig.host.json` — host-only typecheck (Node libs)
- `apps/vscode-spike/tsconfig.webview.json` — webview-only typecheck
  (DOM libs)
- `apps/vscode-spike/eslint.config.js` — extends repo style; bans
  bare browser primitives in `src/host-rpc/` and `src/extension.ts`;
  bans `vscode` import in webview code
- `apps/vscode-spike/.vscodeignore` — excludes `node_modules`, `src`,
  `tsconfig*.json`, tests from the packaged .vsix
- `apps/vscode-spike/README.md` — what the spike demonstrates, how to
  run it, expected behavior, where to inspect the security boundary
- `apps/vscode-spike/vitest.config.ts` — vitest config for host-rpc
  tests (jsdom optional; defaults to node)
- `apps/vscode-spike/esbuild.config.mjs` — single bundler config that
  produces `out/extension.js` (CJS, node target, externalizes `vscode`)
  and `out/webview.js` (IIFE, browser target, bundles React + renderer)
- `apps/vscode-spike/src/extension.ts` — `activate()` entrypoint;
  registers the `enterprise.email` CustomEditorProvider and the
  `enterprise-search-spike.openSample` command
- `apps/vscode-spike/src/editors/EmailEditorProvider.ts` — implements
  `vscode.CustomReadonlyEditorProvider<EmailDocument>`; on
  `resolveCustomEditor`, builds the webview HTML with strict CSP,
  bootstraps a `HostBridge` for that webview, and tears the bridge
  down on dispose
- `apps/vscode-spike/src/host-rpc/HostBridge.ts` — receives RPC from
  webview; one `MockTransport` per subscription; logs approve/reject
  to the extension's output channel
- `apps/vscode-spike/src/host-rpc/rpc-schemas.ts` — Zod schemas for
  every webview ↔ host message; discriminated unions per method
- `apps/vscode-spike/src/host-rpc/HostBridge.test.ts` — round-trip
  tests for request and SSE subscription; lifecycle / cancellation;
  schema-violation rejection
- `apps/vscode-spike/src/host-rpc/rpc-schemas.test.ts` — schema-positive
  and schema-negative cases per method
- `apps/vscode-spike/src/webview/webview-bootstrap.tsx` — webview
  entrypoint; constructs `WebviewTransport`, mounts a small wrapper
  that tracks the latest `pending_diff_appeared` event as `activeDiff`,
  forwards `onApprove` / `onReject` over RPC, renders `<EmailRenderer>`
- `apps/vscode-spike/src/webview/WebviewTransport.ts` — webview-side
  implementation of `Transport`; correlates requests/subscriptions by
  id; uses `acquireVsCodeApi()` (or injected stub in tests) to
  `postMessage` to the host
- `apps/vscode-spike/src/webview/WebviewTransport.test.ts` — verifies
  every `Transport` method posts a typed message and resolves on the
  matching response
- `apps/vscode-spike/src/webview/webview.html` — minimal HTML loaded
  by the webview; CSP forbids arbitrary network egress; loads
  `out/webview.js` only

**Out of scope** (do NOT touch):

- Everything else, especially:
  - `packages/*` — the renderer, the `SurfaceRegistry`, `TcInlineDiff`,
    `MockTransport`, the URI parser, the email fixture are all consumed
    unchanged. If something forces a change there, that is a spike-prep
    bug to flag, not a fix to make here.
  - `apps/frontend/*`
  - `apps/desktop/*` (does not exist; S1-B's scope)
  - `apps/electron-spike/*` (S1-B's scope)
  - `services/*`
  - `docs/architecture/*`, `docs/plan/desktop/PRD.md`,
    `docs/plan/desktop/phase-0.5/S0-spike-prep.md`,
    `docs/plan/desktop/phase-0.5/S2-decision.md`
  - root `package.json` workspaces — `apps/*` already covers
    `apps/vscode-spike/`, no change required

## Functional requirements

- [ ] FR-1 — `npm run spike:vscode:dev` compiles both bundles
      (`out/extension.js` and `out/webview.js`) and opens the developer's
      installed VS Code (or VS Code Insiders if present) with
      `--extensionDevelopmentPath` pointing at `apps/vscode-spike/` and
      `--new-window`. The Extension Development Host window loads the
      extension automatically. No `code` CLI is required on PATH —
      script auto-locates the binary on macOS, Windows, and Linux and
      falls back to a clear error message if nothing is found.
- [ ] FR-2 — From the Command Palette inside the Extension Development
      Host, running "Enterprise Search Spike: Open Sample" opens a new
      tab whose contents are the spike renderer. The URI used is
      `email://draft-1`.
- [ ] FR-3 — On open, the renderer streams in over ~3 seconds: empty
      composer → PENDING block accumulates body chunks → STREAMING
      approval card appears. The visible state matches what
      `EmailRenderer` produces against `MockTransport` in
      `packages/surface-renderers/src/email/EmailRenderer.test.tsx`.
- [ ] FR-4 — Clicking Approve in the rendered diff sends a typed
      `diff.approve` RPC from webview to host; the host logs the
      diff id to the "Enterprise Search Spike" output channel.
      Clicking Reject sends `diff.reject` with the same shape.
- [ ] FR-5 — The webview's CSP includes `connect-src 'none'`. A
      deliberate `fetch('https://example.com')` invoked from the webview
      DevTools console is blocked by CSP and surfaces a security
      violation. (This is the "the webview cannot egress" verification
      the README documents.)
- [ ] FR-6 — Every RPC message is Zod-validated at the receiver. If
      either side receives a malformed message, it posts a typed
      `rpc.error` back and logs the failure; it does not throw.
- [ ] FR-7 — Closing the webview tab tears down every active SSE
      subscription's timers in the host's `MockTransport` instances
      (verified by the HostBridge test).
- [ ] FR-8 — Bearer/session handling: the webview's `WebviewTransport`
      never holds a bearer. `getSession()` returns the host-supplied
      session payload (the spike uses MockTransport's
      `{ bearer: null }`). The host is the only place a real bearer
      would be injected in production.

## Non-functional requirements

- **Bearer never in webview** — verifiable by reading
  `apps/vscode-spike/src/webview/*` and finding zero references to
  any token/bearer/secret string. The host attaches sessions; the
  webview only sees opaque session handles.
- **Webview CSP forbids network egress** — `connect-src 'none'`,
  `default-src 'none'`, `script-src 'nonce-…'`, `style-src
${webview.cspSource} 'unsafe-inline'`, `img-src ${webview.cspSource}
data:`. `unsafe-inline` for styles is unavoidable because the renderer
  uses inline `CSSProperties` style props (consistent with
  `EmailRenderer.tsx`'s `pageStyle`, `cardStyle`, etc.). Nonces on
  scripts give a stricter posture than the spec's
  `script-src ${webview.cspSource}` while remaining functional with
  VS Code's webview loader.
- **RPC contract is type-safe at both ends** — `rpc-schemas.ts` is
  the single source of truth for the wire format. Both host (via
  Zod parse) and webview (via Zod parse + TS type narrowing) validate
  every message.
- **One developer command** — `npm run spike:vscode:dev` is the
  one-liner; no manual `vsce package` then "open in VS Code" two-step.
- **Pure consumption of `packages/*`** — verified by reading the imports
  list: every non-Node, non-vscode, non-React import in
  `src/webview/` resolves to `@enterprise-search/{chat-transport,
chat-surface, surface-renderers}`. Every non-Node import in
  `src/host-rpc/` resolves to `@enterprise-search/chat-transport`
  (for MockTransport) and `zod`.
- **Test coverage** — HostBridge: request round-trip, subscribe →
  receive ≥1 message → unsubscribe stops timers, getSession,
  capabilities, schema rejection of unknown methods, schema rejection
  of malformed params, diff.approve / diff.reject logging.
  WebviewTransport: every Transport method posts the correct message,
  resolves on matching response, rejects on `rpc.error` response,
  cleans up correlation handlers on subscription close. RPC schemas:
  every method's positive and negative cases.

## Interfaces consumed

From `vscode` (extension host only):

- `vscode.ExtensionContext`, `vscode.window`, `vscode.commands`,
  `vscode.Uri`, `vscode.Webview`, `vscode.WebviewPanel`,
  `vscode.CustomReadonlyEditorProvider`, `vscode.CustomDocument`,
  `vscode.CancellationToken`, `vscode.OutputChannel`,
  `vscode.Disposable`

From `@enterprise-search/chat-transport` (host only, for MockTransport
ownership):

- `MockTransport`, `EMAIL_FIXTURE` — only loaded in
  `host-rpc/HostBridge.ts`. The webview never imports this package
  except for the `Transport` _type_ in `WebviewTransport.ts`.
- Types: `Transport`, `TypedRequest`, `SseSubscribeOptions`,
  `SseSubscription`, `Session`, `TransportCapabilities`,
  `UnauthorizedError`

From `@enterprise-search/chat-surface` (webview only, for the
renderer's prop types):

- Types: `SurfaceRendererProps`, `PendingDiff`

From `@enterprise-search/surface-renderers` (webview only):

- `EmailRenderer`

From `zod` (both sides):

- `z` — schema constructors

From `react`, `react-dom/client` (webview only):

- `useState`, `useEffect`, `useMemo`, `createRoot`

## Interfaces produced

```ts
// apps/vscode-spike/src/host-rpc/rpc-schemas.ts
import { z } from "zod";

export const TypedRequestSchema = z.object({
  method: z.enum(["GET", "POST", "PATCH", "PUT", "DELETE"]),
  path: z.string(),
  query: z
    .record(
      z.string(),
      z.union([z.string(), z.number(), z.boolean(), z.undefined()]),
    )
    .optional(),
  body: z.unknown().optional(),
  headers: z.record(z.string(), z.string()).optional(),
});

export const WebviewToHostSchema = z.discriminatedUnion("method", [
  z.object({
    method: z.literal("transport.request"),
    id: z.string(),
    params: TypedRequestSchema,
  }),
  z.object({
    method: z.literal("transport.subscribeServerSentEvents"),
    id: z.string(),
    subscriptionId: z.string(),
    params: z.object({
      path: z.string(),
      query: z
        .record(
          z.string(),
          z.union([z.string(), z.number(), z.boolean(), z.undefined()]),
        )
        .optional(),
      eventName: z.string().optional(),
    }),
  }),
  z.object({
    method: z.literal("transport.unsubscribe"),
    id: z.string(),
    params: z.object({ subscriptionId: z.string() }),
  }),
  z.object({
    method: z.literal("transport.getSession"),
    id: z.string(),
    params: z.object({}).optional(),
  }),
  z.object({
    method: z.literal("transport.capabilities"),
    id: z.string(),
    params: z.object({}).optional(),
  }),
  z.object({
    method: z.literal("diff.approve"),
    id: z.string(),
    params: z.object({ diffId: z.string() }),
  }),
  z.object({
    method: z.literal("diff.reject"),
    id: z.string(),
    params: z.object({ diffId: z.string() }),
  }),
]);

export const HostToWebviewSchema = z.discriminatedUnion("method", [
  z.object({
    method: z.literal("rpc.response"),
    id: z.string(),
    result: z.unknown(),
  }),
  z.object({
    method: z.literal("rpc.error"),
    id: z.string(),
    error: z.object({ code: z.string(), message: z.string() }),
  }),
  z.object({
    method: z.literal("sse.open"),
    subscriptionId: z.string(),
  }),
  z.object({
    method: z.literal("sse.message"),
    subscriptionId: z.string(),
    raw: z.string(),
  }),
  z.object({
    method: z.literal("sse.error"),
    subscriptionId: z.string(),
    message: z.string(),
  }),
]);

export type WebviewToHostMessage = z.infer<typeof WebviewToHostSchema>;
export type HostToWebviewMessage = z.infer<typeof HostToWebviewSchema>;

// apps/vscode-spike/src/host-rpc/HostBridge.ts
export interface HostBridgeOptions {
  readonly webview: vscode.Webview;
  readonly output: vscode.OutputChannel;
}

export class HostBridge implements vscode.Disposable {
  constructor(opts: HostBridgeOptions);
  dispose(): void;
}

// apps/vscode-spike/src/webview/WebviewTransport.ts
export interface WebviewTransportOptions {
  // Test seam — production wiring uses vscode webview's acquireVsCodeApi
  readonly postMessage: (msg: WebviewToHostMessage) => void;
  readonly addMessageListener: (
    handler: (msg: HostToWebviewMessage) => void,
  ) => () => void;
  readonly newId?: () => string;
}

export class WebviewTransport implements Transport {
  constructor(opts: WebviewTransportOptions);
  request<TRes>(req: TypedRequest): Promise<TRes>;
  subscribeServerSentEvents(opts: SseSubscribeOptions): SseSubscription;
  getSession(): Session;
  capabilities(): TransportCapabilities;
  // Test helper — drains pending handlers
  dispose(): void;
}
```

## Open questions

These are non-obvious choices made during sub-PRD drafting. The agent
proceeded with the best answer for each and flags them here for
orchestrator adjudication at merge.

1. **`CustomReadonlyEditorProvider` vs `CustomEditorProvider` vs
   `WebviewPanel` opened by command.** The prompt's scope mandates
   `CustomEditorProvider` for `email://*`. The Atlas product model
   treats artifacts as tabs (per the design's tab strip), so a custom
   editor is the right pattern. Three concrete variants:
   - `CustomEditorProvider<T>` — has explicit edit/save semantics
     (`onDidChangeCustomDocument`, `saveCustomDocument`,
     `backupCustomDocument`). The spike doesn't write anything from
     the renderer side (MockTransport is one-way), so save/backup
     would be dead code.
   - `CustomReadonlyEditorProvider<T>` — same tab semantics, no
     save/backup contract. **Chosen.** Best fit for the spike: the
     extension hosts a webview that renders state from a mocked
     stream, edits happen via the inline diff approve/reject flow
     which the host logs (not "save the document").
   - `WebviewPanel` opened by command — works, but loses the
     "artifact-as-tab" identity that `vscode.openWith(uri,
viewType)` gives. In production this matters: every artifact has
     a URI and is a tab. The spike must demonstrate this.

   `CustomReadonlyEditorProvider<EmailDocument>` is the chosen pattern
   for the spike. The "document" is an opaque marker — `uri` is the
   only meaningful field. VS Code accepts arbitrary URI schemes for
   custom editors; nothing forces `file://`.

2. **Activation events.** The prompt suggests `"onUri:email"`. That
   activation event matches URIs of the shape
   `vscode://your.publisher.name/...?...` and is unrelated to custom
   editors with the `email://` URI scheme. The correct activation
   shape for this spike is `"onCommand:enterprise-search-spike.openSample"`
   plus the implicit activation from the `customEditors` contribution
   (`"onCustomEditor:enterprise.email"` is auto-derived). Documenting
   this here so the orchestrator knows the prompt's hint was
   imprecise — the implementation uses the correct activation events,
   not the prompt's suggested string.

3. **One MockTransport per subscription vs one shared.** The prompt
   says: "One `MockTransport` instance per `subscribeServerSentEvents`
   subscription". MockTransport's `subscribeServerSentEvents`
   internally creates a fresh event schedule on every call, so
   sharing one instance across N subscriptions yields the same
   observable behavior as instantiating N instances. The spike uses
   one shared `MockTransport` for `request`, `getSession`, and
   `capabilities` (all stateless) and a fresh `MockTransport` per
   subscription (per the prompt). This is one extra allocation per
   subscribe and makes the "each subscription has its own lifecycle"
   intent explicit at the construction site.

4. **Bundler: `tsc` vs `esbuild`.** The PRD prefers `tsc` for the
   extension main and `esbuild` for the webview. In practice the
   extension main imports `@enterprise-search/chat-transport`
   (TS source); under VS Code's CommonJS extension loader, those
   imports must be resolved at build time. `tsc` does not bundle.
   We use `esbuild` for both bundles — `out/extension.js` (CJS,
   node target, `vscode` externalized) and `out/webview.js`
   (IIFE, browser target, bundles React + the renderer). `tsc
--noEmit` is used in the `typecheck` script. This is the
   standard pattern for shipped VS Code extensions and is
   recommended in VS Code's own extension samples.

5. **Webview CSP `script-src`: nonce vs cspSource.** The prompt
   spec gives `script-src ${webview.cspSource}`. We use
   `script-src 'nonce-<random>'` instead — VS Code's webview
   recommended-best-practices include nonces, and the stricter
   policy still allows our single bundled script to execute. Net
   result: same dev experience, stricter security boundary. If the
   orchestrator wants the looser `cspSource` policy for spike
   comparison purity, swap one string.

6. **`unsafe-inline` for `style-src`.** Required because the
   renderer (and `TcInlineDiff`, etc.) use inline `CSSProperties`
   style props. Removing it would require rewriting the renderer
   to use CSS classes or CSS Modules — out of scope for the spike
   (and would violate the "renderer consumed unchanged" rule).
   This is a one-line fact about React's `style={{...}}` model.

7. **`activeDiff` wiring.** Two valid interpretations of the prompt's
   "use a small useState hook in a wrapper component to track the
   latest `pending_diff_appeared` event from the MockTransport
   stream":
   - (a) Wrapper subscribes to the stream too, parses the pending
     diff event, passes the parsed object as `activeDiff` — exercises
     the "parent drives the diff" prop path the spike-prep PRD
     introduced for variant agents.
   - (b) Wrapper passes `onApproveDiff` / `onRejectDiff` only; the
     renderer's internal `streamedDiff` tracking fires the callbacks
     with the right diffId on click.
     (a) is what the prompt literally says. Implementation goes with
     (a) — wrapper subscribes via the same `WebviewTransport`, parses
     `pending_diff_appeared`, passes `activeDiff`. One useState hook,
     ~20 LOC, exercises the prop seam end-to-end. Flagged here in
     case the orchestrator wants (b) instead.

8. **Workspace not required.** VS Code's Extension Development
   Host can launch with no folder open (the default when only
   `--extensionDevelopmentPath=.` is passed). The Command Palette
   command works without a workspace folder, so no `.vscode/` is
   created in this repo for the dev host. This keeps the worktree
   clean.

9. **The renderer's internal stream subscription.** `EmailRenderer`
   already calls `transport.subscribeServerSentEvents` in its
   `useEffect`. The wrapper also subscribes (for `activeDiff`).
   That means each "open" results in two `transport.subscribe...`
   calls — two subscriptions, two timers, two emissions of the same
   event sequence into the webview. Each is independently rendered
   correctly (the renderer's body accumulator only listens to its
   own subscription; the wrapper's state only listens to its
   own). Considered consolidating by having the wrapper own
   subscription and pass `activeDiff` only, but: that requires
   modifying the renderer to take its draft state from props
   instead of from transport, which violates the "renderer
   consumed unchanged" rule. Flagged for orchestrator. The fix in
   Phase 4 (per PRD D28 "pure render only") will move all
   subscription ownership to the host (`TcSurfaceMount`), making
   this two-subscriptions-per-mount pattern go away.

10. **Bundle size budget for the webview.** Not explicitly
    specified. The webview bundles React + react-dom + chat-surface
    - chat-transport + surface-renderers. Expected: well under
      500 KB minified. Will be measured and reported in the return
      spec.

## Done criteria

- [ ] All FRs met
- [ ] `npm run typecheck --workspace @enterprise-search/vscode-spike`
      passes (host + webview tsconfigs)
- [ ] `npm run test --workspace @enterprise-search/vscode-spike` passes
- [ ] `npm run lint --workspace @enterprise-search/vscode-spike` passes
- [ ] `npm run compile --workspace @enterprise-search/vscode-spike`
      produces `out/extension.js` and `out/webview.js`
- [ ] No imports from `apps/*` (per `surface-renderers` substrate-port
      rule — applies here transitively)
- [ ] No bearer / token / secret string in `src/webview/*`
- [ ] No new third-party dependency without a one-line justification
      here (dependencies in this spike: `react`, `react-dom`, `zod`,
      `@enterprise-search/{chat-transport,chat-surface,surface-renderers}`;
      devDependencies: `typescript`, `vitest`, `esbuild`, `@types/vscode`,
      `@types/react`, `@types/react-dom`, `@types/node`, `vsce`,
      `@vitejs/plugin-react`, `jsdom`, `@testing-library/react`,
      `@testing-library/jest-dom`. Justifications: `zod` is the
      load-bearing RPC validator (PRD §3.3 prescribes Zod-validated
      IPC); `esbuild` bundles both extension and webview into single
      files (industry-standard VS Code extension pattern); `vsce` is
      the official packaging tool (only needed if we ever want to
      produce a .vsix; included so `spike:vscode:package` works);
      React testing libraries match what `surface-renderers` already
      uses)
- [ ] Renderer consumed unchanged (`packages/surface-renderers/src/*`
      and `packages/chat-surface/src/*` and
      `packages/chat-transport/src/*` are not touched)
- [ ] CSP `connect-src 'none'` verified in `webview.html`

## Notes for orchestrator review

- The prompt's hint `activationEvents: ["onUri:email"]` is technically
  wrong for this use case (see Open Q2). Implementation uses
  `"onCommand:enterprise-search-spike.openSample"` plus VS Code's
  auto-activation from the `customEditors` contribution.
- The webview's CSP uses `script-src 'nonce-<random>'` (stricter
  than the prompt's `script-src ${webview.cspSource}`). See Open Q5.
- `CustomReadonlyEditorProvider` is used instead of
  `CustomEditorProvider` (see Open Q1) because the spike has no
  edit/save semantics; save/backup methods would be dead code.
- This sub-PRD does not document Q-bar (Q1–Q6) — those govern tier-2
  adapter generation, not substrate spikes.
- The substrate friction observations (which are the actual deliverable
  for the comparison) are captured in the return-spec message to the
  orchestrator after implementation, per the prompt's "Return spec"
  section. They include: VS Code's CustomEditor API forced us into
  document-shaped abstractions even though our resource is a URI;
  the webview ↔ host RPC requires a separate Zod schema layer (with
  separate test files) that adds substrate-port LOC to the spike
  beyond what the renderer needs; CSP requires `unsafe-inline` for
  styles because the renderer uses inline style props (this is
  inherent to React, not to the substrate, but each variant pays it).
