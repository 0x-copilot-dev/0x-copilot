# Phase 1.C: ipc-transport

## Vision

The desktop renderer is `packages/chat-surface` mounted in a Chromium
`BrowserWindow`. It cannot speak HTTP directly: bearer tokens live in main
(D24 / PRD §6.7), TLS pinning + bearer injection happen in main, the renderer
is sandboxed. The seam between the renderer and main is the `Transport` port.
Two implementations exist on disk: `WebTransport` (browser, direct fetch + SSE)
and — once this agent ships — `IpcTransport` (Electron renderer, proxies every
method via `window.bridge` to main, where the actual HTTP / SSE pump runs).

Staff-engineer cuts that shape this work:

1. **Implement the on-disk `Transport`, not the aspirational PRD §3.3 shape.**
   The on-disk interface is `subscribeServerSentEvents(opts): SseSubscription`
   (sync return) and `getSession(): Session` (sync accessor). PRD §3.3
   describes `subscribeRunStream(...)` returning a `Promise<void>` — that's
   the future shape for after the runtime is fully wired. Today's renderer
   already calls the on-disk interface (`WebTransport`, `MockTransport`),
   tests are written against it, and the `Transport` re-export from
   `chat-surface/ports` flows the on-disk shape. Diverging here would break
   substitution. The PRD §3.3 contract drift is a future migration, not a
   Phase 1 concern. Recorded as an open question for the orchestrator.

2. **Sync/async impedance (S2 friction note 3-4).**
   - `SseSubscription` returns synchronously from `subscribeServerSentEvents`,
     but the IPC subscribe is async. Resolve by generating the
     `subscriptionId` in the renderer (`crypto.randomUUID()`), registering
     the renderer-side record synchronously, then firing the IPC in the
     background. Errors from subscribe arrive on the stream-event channel as
     `kind: "error"` — same shape a backend SSE error would take, so the
     renderer's `onError` handler doesn't need a special "subscribe failed"
     case.
   - `getSession()` / `capabilities()` are synchronous on the on-disk
     contract; IPC can't satisfy that without `await`. Resolve by passing a
     `bootstrapSession` + `bootstrapCapabilities` at construction time.
     Production fetches these once at sign-in (Phase 5); Phase 1 uses static
     stub values (`{ bearer: null }` and the desktop-webview capability
     shape).

3. **Race-avoidance (the user's headline constraint).**
   The S1-B spike shipped a fire-and-forget subscribe that interacted badly
   with `<StrictMode>`'s synchronous double-mount-cleanup + the renderer's
   `useRef`-based `hasMounted` guard. Root cause was a renderer-wrapper bug,
   not the IPC pattern — but to keep the Phase-1 IPC layer robust under
   future renderer churn we add a **subscribe-ack ordering guarantee**:
   - Renderer generates `subscriptionId`, registers
     `Map<subscriptionId, opts>` SYNCHRONOUSLY before firing the IPC.
   - Main's `transport.subscribe` handler registers its own subscription
     map entry AND starts the underlying `Transport.subscribeServerSentEvents`
     call SYNCHRONOUSLY before returning `{ ok: true }`. The ack does not
     return until both registrations are complete.
   - Because both registrations are synchronous before any I/O round-trip
     (renderer-side: synchronous before `invoke`; main-side: synchronous
     inside the `ipcMain.handle` body), any `stream-event` emitted after the
     subscribe-IPC fires will (a) find the renderer's record (set before
     IPC), and (b) come from a main-side subscription that's already
     registered (set inside the handler before the underlying transport is
     started).
   - **Buffer-and-replay** is the defensive belt: if a `stream-event`
     arrives in the renderer for a `subscriptionId` it doesn't know about,
     buffer it for the duration of a single microtask flush and re-dispatch
     after — which catches the (theoretical) case where a `stream-event` is
     delivered to the renderer's listener before the very first
     `subscribeServerSentEvents` call has finished its synchronous
     registration. The buffer has a hard cap (16 events) and a 1-tick TTL
     to prevent unbounded growth on a genuinely-orphan subscriptionId.

   The two together mean: events for a known subscriptionId always reach
   the renderer's handler; events for an unknown subscriptionId are
   silently dropped after the buffer expires (audit-logged at a future
   phase).

4. **Bearer plumbing is a stub.** Real OIDC arrives in Phase 5. The
   transport-bridge in this phase wraps `MockTransport` directly and does
   not look at headers. The shape of the bridge (`createTransportForWindow`
   returning a `Transport`) is what Phase 5 will swap.

## Status

- Status: done (FR-12 shipped as a `TransportBridge` class rather than a
  `createTransportForWindow` factory — semantically equivalent; see
  audit note)
- Agent slug: `ipc-transport`
- Branch: `desktop/phase-1-ipc-transport`
- Worktree: `.claude/worktrees/agent-aed6ec5d539b1decb`
- Created: 2026-05-17
- Audited: 2026-05-17

## Scope

**In scope** (files this agent owns):

- `docs/plan/desktop/phase-1/1C-ipc-transport.md` — this file.
- `packages/chat-transport/src/ipc/IpcTransport.ts` — NEW renderer-side class.
- `packages/chat-transport/src/ipc/rpc-protocol.ts` — NEW Zod schemas + types
  for every IPC channel.
- `packages/chat-transport/src/ipc/window-bridge.ts` — NEW typed view of the
  `window.bridge` contract.
- `packages/chat-transport/src/ipc/index.ts` — NEW barrel.
- `packages/chat-transport/src/ipc/IpcTransport.test.ts` — NEW Vitest suite.
- `packages/chat-transport/src/index.ts` — APPEND a delimited Phase 1-C
  block exporting `IpcTransport` + types. No other edits.
- `apps/desktop/main/ipc/schemas.ts` — NEW (re-export wrapper around the
  chat-transport rpc-protocol schemas).
- `apps/desktop/main/ipc/handlers.ts` — NEW main-side IPC handler registrar.
- `apps/desktop/main/ipc/handlers.test.ts` — NEW Vitest suite against a fake
  `ipcMain` + fake `webContents`.
- `apps/desktop/main/transport-bridge.ts` — NEW main-side HTTP+SSE pump
  wrapper (Phase 1: thin wrapper around `MockTransport`).
- `apps/desktop/vitest.config.ts` — NEW minimal config so the desktop
  workspace's `npm test` script resolves. (The package was scaffolded in
  Phase 0 with a `test` script but no vitest config; tests won't run
  without one.)

**Out of scope** (do NOT touch):

- `apps/desktop/main/{index.ts, window.ts, crash-reporter.ts, deep-links.ts}`
  — Agent 1-A.
- `apps/desktop/preload/bridge.ts` — Agent 1-A.
- `apps/desktop/renderer/**` — Agent 1-A.
- `packages/chat-surface/**` — Agents 1-B and 1-D.
- `eslint.config.*` anywhere — already in place from Phase 0.
- Any file in `services/*`.
- `packages/chat-transport/src/transport.ts` — the on-disk contract. We
  consume it; we do not change it.

## Functional requirements

- [x] FR-1 — `IpcTransport` implements `Transport` from
      `@enterprise-search/chat-transport` (the on-disk shape: synchronous
      `subscribeServerSentEvents`, synchronous `getSession`, synchronous
      `capabilities`).
- [x] FR-2 — `IpcTransport.request<T>(req)` calls
      `bridge.ipc.invoke('transport.request', payload)` where `payload` is
      the `TypedRequest` minus `signal` (signal is renderer-local;
      `AbortSignal` doesn't structured-clone across IPC; production needs a
      token-based cancel side channel, flagged for Phase 5).
- [x] FR-3 — `IpcTransport.subscribeServerSentEvents(opts)` returns
      synchronously with a `{ close }` handle. Internally:
      (a) generate `subscriptionId` via `crypto.randomUUID()` (or
      configurable test seam), (b) synchronously register
      `Map<subscriptionId, opts>`, (c) fire
      `invoke('transport.subscribe', { subscriptionId, path, query,
eventName })`, (d) on invoke rejection, dispatch `onError` via the
      same path a backend SSE error would take, then evict the record.
- [x] FR-4 — `close()` evicts the record and fires
      `invoke('transport.unsubscribe', { subscriptionId })`. Best-effort;
      no error surface (the on-disk `SseSubscription.close` returns void).
- [x] FR-5 — `IpcTransport.getSession()` returns the
      `bootstrapSession` passed at construction (Phase 1 stub:
      `{ bearer: null }`).
- [x] FR-6 — `IpcTransport.capabilities()` returns the
      `bootstrapCapabilities` passed at construction (Phase 1 stub:
      `substrate: 'desktop-webview'`, the four boolean flags reflect what
      the desktop substrate will actually expose by Phase 8; for Phase 1
      they're `nativeSecretStorage: true, fileSystemAccess: false,
clipboardWrite: false, openExternal: false`).
- [x] FR-7 — A single `bridge.ipc.on('stream-event', ...)` listener is
      installed in the `IpcTransport` constructor. Every stream-event for
      every active subscription routes through this one listener.
- [x] FR-8 — Stream events for unknown `subscriptionId` are buffered for
      one microtask and re-dispatched. After microtask flush, still-unknown
      events are dropped silently. Buffer cap: 16 entries. Belt-and-suspenders
      against the S1-B `<StrictMode>` race pattern.
- [x] FR-9 — `apps/desktop/main/ipc/handlers.registerIpcHandlers({
ipcMain, bridge, logger? })` registers four handlers:
      `transport.request`, `transport.subscribe`, `transport.unsubscribe`,
      `transport.session-snapshot` (returns the cached session +
      capabilities atomically; lets the renderer bootstrap in one
      round-trip if it ever needs to refresh post-construction). Returns a
      teardown function that removes all handlers AND closes any active
      subscriptions.
- [x] FR-10 — Every handler validates incoming params with Zod from
      `rpc-protocol.ts`. On validation failure, throws `IpcValidationError`
      which propagates as a rejected promise across IPC.
- [x] FR-11 — Main-side subscription tracking via
      `Map<subscriptionId, { webContentsId, underlying: SseSubscription }>`.
      A renderer-specific cleanup
      (`bridge.unsubscribeForWebContents(webContentsId)`) is exported so
      Agent 1-A can wire it to the `webContents.on('destroyed', …)` event
      from `window.ts`.
- [x] FR-12 — `apps/desktop/main/transport-bridge.ts` ships the
      main-side wrapper as a `TransportBridge` class (constructor:
      `(emit: StreamEventEmitter, opts?: { transport?: Transport })`,
      defaulting `transport` to `new MockTransport()`). _Shape divergence
      from the PRD's `createTransportForWindow(webContents)` factory_:
      the bridge takes an injected `StreamEventEmitter` callback (which
      Agent 1-A wires to `webContents.send(CHANNELS.streamEvent, …)`)
      and tracks per-`webContentsId` subscriptions internally — same
      job, factored as a long-lived bridge per main process rather than
      one-per-window. Phase 5 swaps the default `MockTransport` for the
      real HTTP+SSE pump with bearer injection.
- [x] FR-13 — `packages/chat-transport/src/index.ts` gains a Phase 1-C
      delimited block exporting `IpcTransport`, `IpcTransportConfig`, and
      the channel-name constants + types. No other edits to the existing
      block.

## Non-functional requirements

- Performance: no measurable overhead beyond a single IPC round-trip
  per `request` call and one IPC fire per `subscribe`. Stream events do
  not allocate beyond the one event object per fire.
- Accessibility: N/A (no rendered output).
- Test coverage: every method on `IpcTransport` exercised; every channel
  exercised on `handlers`; subscribe-ack ordering verified via
  out-of-order-event scenario; subscribe-then-immediate-close race
  verified; Zod validation rejection verified.

## Interfaces consumed

- `Transport`, `Session`, `SseSubscribeOptions`, `SseSubscription`,
  `TransportCapabilities`, `TypedRequest`, `QueryParamValue`,
  `HttpMethod`, `UnauthorizedError` from `@enterprise-search/chat-transport`.
- `MockTransport` from `@enterprise-search/chat-transport` (Phase 1
  backing transport for `transport-bridge.ts`).
- `ipcMain`, `IpcMainInvokeEvent`, `WebContents` types from `electron`
  (main process only).
- `z` from `zod`.

## Interfaces produced

```ts
// packages/chat-transport/src/ipc/rpc-protocol.ts
export const CHANNELS: {
  readonly transportRequest: "transport.request";
  readonly transportSubscribe: "transport.subscribe";
  readonly transportUnsubscribe: "transport.unsubscribe";
  readonly transportSessionSnapshot: "transport.session-snapshot";
  readonly streamEvent: "transport.stream-event";
};
export type ChannelName = (typeof CHANNELS)[keyof typeof CHANNELS];
export const TransportRequestParamsSchema: z.ZodType<...>;
export const TransportSubscribeParamsSchema: z.ZodType<...>;
export const TransportUnsubscribeParamsSchema: z.ZodType<...>;
export const EmptyParamsSchema: z.ZodType<{}>;
export const StreamEventPayloadSchema: z.ZodType<...>;
export type StreamEventPayload = z.infer<typeof StreamEventPayloadSchema>;
export class IpcValidationError extends Error { ... }

// packages/chat-transport/src/ipc/window-bridge.ts
export interface WindowBridge {
  readonly ipc: {
    invoke<T = unknown>(channel: ChannelName, payload?: unknown): Promise<T>;
    on(channel: ChannelName, handler: (payload: unknown) => void): () => void;
  };
}

// packages/chat-transport/src/ipc/IpcTransport.ts
export interface IpcTransportConfig {
  readonly bridge: WindowBridge;
  readonly bootstrapSession: Session;
  readonly bootstrapCapabilities: TransportCapabilities;
  readonly randomId?: () => string; // test seam
}
export class IpcTransport implements Transport { ... }

// apps/desktop/main/transport-bridge.ts
export interface TransportBridgeOptions {
  readonly transport?: Transport;
}
export class TransportBridge {
  constructor(emit: StreamEventEmitter, opts?: TransportBridgeOptions);
  request<T>(req: TypedRequest): Promise<T>;
  sessionSnapshot(): { session: Session; capabilities: TransportCapabilities };
  subscribe(subscriptionId: string, webContentsId: number,
            opts: Pick<SseSubscribeOptions, "path"|"query"|"eventName">): void;
  unsubscribe(subscriptionId: string): boolean;
  unsubscribeForWebContents(webContentsId: number): void;
  activeSubscriptionCount(): number;
}
export type StreamEventEmitter = (
  webContentsId: number,
  payload: StreamEventPayload,
) => void;

// apps/desktop/main/ipc/handlers.ts
export interface RegisterHandlersDeps {
  readonly ipcMain: IpcMain;
  readonly bridge: TransportBridge;
  readonly logger?: IpcLogger;
}
export function registerIpcHandlers(deps: RegisterHandlersDeps): () => void;
```

## Open questions

- **OQ-1: Transport contract drift (PRD §3.3 vs on-disk).** PRD §3.3 describes
  `subscribeRunStream(runId, afterSequence, handler, signal): Promise<void>`
  and async `getSession()` / async `reauthenticate()`. The on-disk
  `Transport` has `subscribeServerSentEvents(opts): SseSubscription` (sync)
  and `getSession(): Session` (sync). The PRD §3.3 IPC schemas
  (`transport.subscribeRunStream`, `transport.cancelSubscription`,
  `transport.reauthenticate`) describe the future shape. **I implement
  against the on-disk shape** (per the agent prompt's explicit instruction
  and to keep `WebTransport`/`MockTransport`/`IpcTransport` interchangeable
  through `chat-surface/ports`). The drift between PRD §3.3 and the
  on-disk shape is a separate task (probably a Phase 2 or 4 contract
  update). I add channel names that match the on-disk semantics
  (`transport.subscribe` not `transport.subscribeRunStream`) to keep the
  IPC layer aligned with what it actually does. Orchestrator: do you want
  me to add a follow-up note in the PRD?

- **OQ-2: `transport.session-snapshot` channel.** I added a single channel
  that returns `{ session, capabilities }` together. PRD §3.3 lists
  separate `transport.getSession` and `transport.reauthenticate` channels.
  The on-disk `Transport.getSession()` and `Transport.capabilities()` are
  sync accessors that don't normally cross IPC at all — the renderer
  bootstraps with cached values at construction. The session-snapshot
  channel exists as a future seam for "renderer asked main for a refresh
  after a 401" rather than being called per-method. Phase 5 will likely
  replace it with `reauthenticate`. Marked as such in `rpc-protocol.ts`.

- **OQ-3: Buffer-and-replay TTL.** I chose 1 microtask flush
  (`queueMicrotask`) + 16-entry cap. The cap protects against unbounded
  growth from a genuinely-orphan subscriptionId. The 1-tick TTL is
  conservative — if a real renderer cleanup → re-subscribe sequence under
  StrictMode takes more than one microtask (e.g. it includes a `Promise`
  resolution), events in the gap will still be dropped. Phase 4's renderer
  cleanup is the right place to fix the StrictMode source of the race;
  this buffer is a backstop, not a primary defense.

- **OQ-4: `AbortSignal` across IPC.** Dropped on the renderer side with a
  comment. Production needs a token-based cancellation side channel —
  every `request` would carry a `cancelToken: string`, and the renderer
  fires `invoke('transport.cancel', { cancelToken })` on abort. Out of
  scope for Phase 1 per the agent prompt; flagged for Phase 5.

## Done criteria

- [x] All FRs met (FR-12 shipped as `TransportBridge` class; see note).
- [x] `npm run typecheck --workspace @enterprise-search/chat-transport`
      passes.
- [x] `npm run typecheck --workspace @enterprise-search/desktop` passes.
- [x] `npm test --workspace @enterprise-search/chat-transport` passes
      (existing tests + new `IpcTransport` suite — 13 cases).
- [x] `npm test --workspace @enterprise-search/desktop` passes (new
      `handlers` + `TransportBridge` suites — 20 cases).
- [x] `npm run lint --workspace @enterprise-search/desktop` passes (Phase
      0's ESLint config is unchanged; not edited).
- [x] No imports outside scope.
- [x] No bare browser primitives in `IpcTransport` (renderer-side code):
      `globalThis.crypto.randomUUID` is the only one, prefixed with
      `globalThis.` per PRD §6.5; or the `randomId` test seam overrides it.
- [x] `IpcTransport` does not import from `electron`.
- [x] `handlers.ts` / `transport-bridge.ts` may import from `electron`
      (main process is Node).

### Carried forward to Phase 5

- Token-based cancellation side channel for `AbortSignal` parity (OQ-4).
- Real HTTP+SSE pump replaces `MockTransport` as the bridge's default
  transport, with per-`(workspace_id, server)` bearer injection.
- `transport.session-snapshot` likely yields to `transport.reauthenticate`
  on first 401 (OQ-2).

## Notes for orchestrator review

- **Subscribe-ack ordering**: I rely on synchronous registration on both
  sides (renderer: `subscriptions.set` before `invoke`; main:
  `bridge.subscribe` body runs synchronously before `ack` resolution).
  This is the same shape as the S1-B spike. The user's concern about the
  S1-B race was specifically the StrictMode × `useRef hasMounted` guard
  in the **renderer wrapper**, which is a Phase 4 renderer concern. The
  IPC layer itself has a clean ordering story. The buffer-and-replay
  belt is the only thing I'd consider non-essential here; I added it
  because the user explicitly asked for race-avoidance — it costs ~30
  LOC and zero perf, and it makes a future renderer bug at most "events
  arrive 1 tick late" instead of "events lost".

- **`transport-bridge.ts` is a thin shell over `MockTransport`.** This
  matches Phase 1's bearer-plumbing-is-a-stub directive. Phase 5
  replaces `MockTransport` with the real fetch+SSE pump and adds
  per-`(workspace_id, server)` bearer injection. The
  `createTransportForWindow(webContents)` shape is what Phase 5 swaps
  inside.

- **No `apps/desktop/main/index.ts` integration.** Per scope. Agent 1-A
  owns `main/index.ts` and will wire `registerIpcHandlers` +
  `TransportBridge` at the orchestrator's merge time. I make this
  trivial: `registerIpcHandlers` takes a fully-constructed
  `TransportBridge` and returns a teardown, so 1-A's `index.ts` adds two
  lines.

- **`window.bridge` shape**: declared in `window-bridge.ts` for Agent
  1-A's preload to satisfy. I do not construct it. I do not export
  anything that depends on it being globally present (the `IpcTransport`
  takes the bridge as a constructor argument — substrate touchpoint by
  injection, not by ambient access).
