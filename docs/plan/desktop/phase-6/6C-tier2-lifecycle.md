# Phase 6.C: tier2-lifecycle

## Vision

Compose the four Phase-6 Round-1 primitives (6A sandbox + 6B codegen + 6D quality gate, plus Tier2Loader's renderer-side worker) into a complete tier-2 adapter lifecycle. The lifecycle subscribes to `adapter_generated` run events, drives the Q1→Q5 install pipeline, hot-swaps adapters into chat-surface's registry, listens for live render errors (Q6) and demotes the offending version, enforces a bounded retry budget (3 attempts × ~5 s each), and persists every state transition to an append-only audit log.

No surface in 6C executes adapter code with privileged scope. The 6A `vm` sandbox owns AST + privileged-globals stripping; the renderer's `Tier2Loader` Web Worker owns preemptive render isolation (D29). 6C is purely orchestration: subscribe, pipeline, persist, hot-swap.

## Status

- Status: in-progress
- Agent slug: `phase-6-tier2-lifecycle`
- Branch: `desktop/phase-6-tier2-lifecycle`
- Worktree: `.claude/worktrees/agent-acd9f3f4bdd7eb5ed`
- Created: 2026-05-17

## Scope

**In scope** (files this agent owns):

- `docs/plan/desktop/phase-6/6C-tier2-lifecycle.md` — this file.
- `apps/desktop/main/adapters/registry-host.ts` (NEW) — main-side facade over the chat-surface registry. Drives Q1→Q5 (schema → allowlist → smoke render → boundary-wrap), persists `{userData}/adapters/{scheme}-v{n}.js`, then ships `tier2.install` IPC to the renderer (which calls `registerAdapter`). Owns `installAdapter`, `uninstallAdapter`, `markBrokenFromBoundary`.
- `apps/desktop/main/adapters/registry-host.test.ts` (NEW).
- `apps/desktop/main/adapters/lifecycle.ts` (NEW) — orchestrator. Subscribes to a `LifecycleEventSource` (the bridge feeds parsed `adapter_generated` envelopes here), runs the install pipeline through `registry-host`, enforces the retry budget, emits `lifecycle-exhausted` on overflow.
- `apps/desktop/main/adapters/lifecycle.test.ts` (NEW) — happy-path round-trip, broken-adapter regen, budget exhaustion.
- `apps/desktop/main/adapters/lifecycle-events.ts` (NEW) — append-only JSONL audit-log primitive. Event kinds: `requested`, `generated`, `validated`, `installed`, `render-error`, `marked-broken`, `regen-queued`, `lifecycle-exhausted`. APIs: `appendLifecycleEvent`, `readLifecycleEvents`.
- `apps/desktop/main/adapters/lifecycle-events.test.ts` (NEW) — append-only contract, filter-by-scheme read, durable across "restart" (re-open the file).
- `apps/desktop/main/adapters/integrate.ts` (MODIFIED) — adds `wireSmokeRenderExecutor` that wires 6D's `setDefaultSmokeRenderExecutor` to a main-process executor backed by 6A's `compileAdapter`.
- `apps/desktop/main/adapters/smoke-render-executor.ts` (NEW) — the executor itself, wired into 6D's `SmokeRenderExecutor` port. Uses 6A's `compileAdapter` to obtain the adapter object, calls `renderCurrent` / `renderDiff` with the synthetic state / diff, races a measured-timeout. Documented limitations below.
- `apps/desktop/main/adapters/smoke-render-executor.test.ts` (NEW).
- `apps/desktop/main/adapters/tier2-installer.ts` (NEW) — small main-side helper that persists the adapter source to disk under `{userData}/adapters/{scheme}-v{n}.js` (called by `registry-host.installAdapter`). Owns directory + filename layout.
- `apps/desktop/main/adapters/tier2-installer.test.ts` (NEW).
- `apps/desktop/main/index.ts` (MODIFIED) — `startTier2Lifecycle({...})` from `app.whenReady`.
- `apps/desktop/main/ipc/handlers.ts` (MODIFIED) — registers `tier2.install` / `tier2.uninstall` handlers (renderer-invoked) for the renderer's lifecycle bridge; also adds a main-to-renderer dispatch helper for outbound `tier2.install` push messages.
- `apps/desktop/main/ipc/handlers.test.ts` (MODIFIED) — tests for the new channels.
- `packages/chat-transport/src/ipc/rpc-protocol.ts` (MODIFIED) — adds `tier2.install` / `tier2.uninstall` channels, Zod schemas, and `Tier2InstallPayload` / `Tier2UninstallPayload` types. Exports added to the channel allowlist.
- `apps/desktop/renderer/bootstrap.tsx` (MODIFIED) — wires a renderer-side `Tier2Bridge` that listens for `tier2.install` / `tier2.uninstall` and calls `registerAdapter` / `unregisterAdapter` from chat-surface.
- `apps/desktop/renderer/Tier2Bridge.ts` (NEW) — the renderer-side install handler. Builds an adapter object whose `renderCurrent` / `renderDiff` mount `<Tier2Loader>` with the persisted source.
- `apps/desktop/renderer/Tier2Bridge.test.ts` (NEW).

**Out of scope** (do NOT touch):

- `apps/desktop/main/adapters/{loader,sandbox,ast-allowlist}.ts` — 6A's primitives. Imported.
- `apps/desktop/main/adapters/quality-gate/**` — 6D's primitives. Imported via the barrel.
- `services/ai-backend/**` — 6B's territory. Imported only via the `RuntimeEventEnvelope` shape and the `AdapterGeneratedPayload` type from `@enterprise-search/api-types`.
- `packages/chat-surface/src/surfaces/{Tier2Loader,SurfaceRegistry,SaaSRendererAdapter}.ts` — chat-surface owns these; 6C consumes them through the public package API (`registerAdapter`, `unregisterAdapter`, `markBroken`, `Tier2Loader`).
- Production worker bundle for `Tier2Loader` — out of scope for Phase 6. Tier2Loader's `workerFactory` is the seam; 6C's renderer-side bridge passes a stub factory that fails-closed if a production bundle is not wired. The Phase-8 build pipeline lands the real bundle.

## Functional requirements

### Audit log (`lifecycle-events.ts`)

- [ ] FR-1: `appendLifecycleEvent(event, deps)` appends one JSON-Lines record to `{deps.logPath}`. Uses `fs.appendFile` exclusively — never `writeFile`, `truncate`, or `unlink`. Mirrors 6D's `broken-mark` design.
- [ ] FR-2: Event kinds enumerated: `requested`, `generated`, `validated`, `installed`, `render-error`, `marked-broken`, `regen-queued`, `lifecycle-exhausted`. Each carries at minimum `ts`, `kind`, `scheme`, `version`, and an optional `detail: string`.
- [ ] FR-3: `readLifecycleEvents(opts, deps)` returns every persisted record, optionally filtered by `scheme` and capped by `limit`. Implementation reads the entire log into memory and filters — Phase 6's audit log is bounded by tenant adapter count × lifecycle events per adapter (≤ a few thousand records in practice). Backpressure is a Phase 8 concern.
- [ ] FR-4: Tests cover: append-only (`writeFile`/`truncate`/`unlink` never called via spies), `readLifecycleEvents` returns persisted records in insertion order, filter-by-scheme, durable across "restart" (close and re-open the file handle), and graceful behavior on a missing file (`readLifecycleEvents` returns `[]`).

### Tier-2 installer (`tier2-installer.ts`)

- [ ] FR-5: `persistAdapterSource({adapterDir, scheme, version, source}, deps)` writes the source to `{adapterDir}/{scheme}-v{version}.js`. Creates the parent directory recursively. Uses `fs.writeFile` (the source file is replaced atomically per version; only the audit log is append-only).
- [ ] FR-6: `uninstallAdapterFile({adapterDir, scheme, version}, deps)` removes the file. No-op if missing.
- [ ] FR-7: Filename layout matches 6A's `loader.adapterFilePath` exactly so reload-from-disk works without translation.

### Smoke-render executor (`smoke-render-executor.ts`)

- [ ] FR-8: `MainProcessSmokeRenderExecutor` implements 6D's `SmokeRenderExecutor`. The `execute(adapter, payload, budgetMs)` method calls `adapter[payload.method](payload.input)` inside a try-catch wrapped in a `Promise.race([call(), timeout])`.
- [ ] FR-9: On throw → `{ ok: false, kind: 'throw', error }`. On timeout (the measured timer fires before the call resolves) → `{ ok: false, kind: 'timeout', error }`. If the returned value is not a plausible React element (no `$$typeof` symbol, no `type`/`props` shape) → `{ ok: false, kind: 'not-element', error }`. Otherwise → `{ ok: true }`.
- [ ] FR-10: The executor takes the **already-compiled** adapter object from 6A's sandbox. It does NOT re-compile the source.
- [ ] FR-11: Synthetic state: `{ id: '__smoke__', tier2_smoke: true }`. Synthetic diff: `{ field_changes: [], resource: { id: '__smoke__' } }`. These are deliberately permissive — the smoke test answers "does this code execute and produce SOMETHING" — the live render against real state is where rich validation happens, and that's the Worker's job.

### Registry host (`registry-host.ts`)

- [ ] FR-12: `installAdapter({scheme, version, source, generatedAt, generatorModel}, deps)` runs the Q1→Q5 pipeline in order. Each step short-circuits on failure and emits a `validated` audit event with the failure detail before returning.
  - Q1 (schema): use 6A's `compileAdapter` to produce a candidate object; pass it through 6D's `validateAdapterSchema`. Fail → `{ ok: false, gate: 'schema', detail }`.
  - Q2 (allowlist): call 6D's `staticAnalyze(source)`. Fail → `{ ok: false, gate: 'allowlist', detail }`.
  - Q3/Q4 (smoke render): call 6D's `runSmokeRender(adapter, syntheticState, syntheticDiff)`. Fail → `{ ok: false, gate: 'smoke', detail }`.
  - On all-pass: persist the source via `tier2-installer.persistAdapterSource`, then wrap the adapter with 6D's `wrapWithBoundary` registering an `onError` listener that forwards through `deps.boundaryNotifier`, then dispatch a `tier2.install` IPC message to the renderer carrying `{scheme, version, source, generatedAt, generatorModel}`. Append `installed` audit event. Return `{ ok: true, scheme, version }`.
- [ ] FR-13: `uninstallAdapter({scheme, version}, deps)` removes the on-disk file and dispatches a `tier2.uninstall` IPC message. Append a synthetic audit event (`kind: 'marked-broken'` with reason `uninstall`). The chat-surface registry's actual `unregisterAdapter` call happens in the renderer.
- [ ] FR-14: `markBrokenFromBoundary({scheme, version, method, error}, deps)` is the bridge from Q6 (live render error) to the registry's `markBroken`. Persists the `render-error` and `marked-broken` audit events and dispatches an IPC `tier2.mark-broken` message so the renderer's `markBroken` call lands. Returns `void`. The lifecycle calls this through 6D's `BoundaryListener` contract.
- [ ] FR-15: Registry host is the only main-side surface that talks to the renderer about tier-2 — lifecycle.ts never sends IPC itself, only registry-host does.

### Lifecycle orchestrator (`lifecycle.ts`)

- [ ] FR-16: `startTier2Lifecycle(deps)` returns a `Tier2LifecycleHandle` with `stop()`. The handle subscribes to an injected `LifecycleEventSource` whose `onAdapterGenerated(handler)` fires whenever a backend `adapter_generated` event arrives. The handler is registered synchronously; on stop, the unsubscribe is called.
- [ ] FR-17: For each `AdapterGeneratedPayload`:
  - Append `generated` audit event.
  - Read the per-scheme attempt counter. If it has reached the budget (default 3), append `lifecycle-exhausted`, emit through `deps.onExhausted(scheme)` (so a host UI can show "still working on a renderer for this"), and stop. Subsequent generations for the same scheme reset the counter only when a successful install lands — schema drift after success is a fresh budget.
  - Otherwise increment the counter, then call `registry-host.installAdapter` inside a per-attempt 5-second deadline timer (`AbortController` + `setTimeout` race).
  - On install success: append `installed`, clear the counter for this scheme, return.
  - On install failure: append `regen-queued` (a marker indicating the budget will burn down on the agent's next attempt). Do NOT proactively re-request generation — the orchestrator records the failure; the agent will invoke the capability again when re-prompted. (Documented design: the run stream is the source of truth for new generations; explicit regen pings are a Phase 7 server-side concern.)
- [ ] FR-18: The retry budget is per-(scheme) and reset on a successful install. Token-bucket: an unsigned counter that increments on each failed attempt and resets on success. The "~5 s each" PRD figure is per-attempt wall clock, enforced by the per-attempt deadline timer.
- [ ] FR-19: On a Q6 boundary event (live render failure forwarded from the renderer via IPC): `registry-host.markBrokenFromBoundary` runs (appending `render-error` + `marked-broken` audit events), and the lifecycle increments the attempt counter for that scheme so the next `adapter_generated` event for the same scheme respects the budget. If the counter is already at budget, the next generation is rejected immediately with `lifecycle-exhausted` — the broken adapter does not get infinite regen.
- [ ] FR-20: Clock is injectable (`deps.clock`) and the per-attempt deadline timer accepts an injectable `setTimeout`-shaped function for tests. Timing assertions in tests never `sleep` — they advance an injectable clock.

### IPC additions (`rpc-protocol.ts` + `handlers.ts`)

- [ ] FR-21: New channel `tier2.install` carries `{scheme, version, source, generatedAt, generatorModel}`. Renderer-direction (main → renderer push via `webContents.send`) is the production usage; renderer-invoke is supported for tests that want to assert the renderer-side bridge's behavior. Zod schema: `scheme: string ≥ 1`, `version: int ≥ 0`, `source: string ≥ 1`, `generatedAt: string` (ISO), `generatorModel: string`. All required.
- [ ] FR-22: New channel `tier2.uninstall` carries `{scheme, version}`. Zod-validated.
- [ ] FR-23: New channel `tier2.mark-broken` carries `{scheme, version, method: "renderCurrent"|"renderDiff", reason: string}`. Zod-validated. Used by registry-host to tell the renderer "call markBroken on this version".
- [ ] FR-24: New channel `tier2.boundary-error` carries `{scheme, version, method, message}`. Renderer → main. Fires when the renderer's error boundary catches a live render throw; the lifecycle on the main side uses this to trigger `markBrokenFromBoundary`.
- [ ] FR-25: All four channels added to `CHANNELS` constant; `CHANNEL_VALUES` updated automatically (it's derived from `CHANNELS`); `isAllowedChannel` therefore validates them at preload's bridge boundary. New schemas exported from `chat-transport`.
- [ ] FR-26: `handlers.ts` registers handlers for `tier2.boundary-error` (the only renderer-→-main channel; the others are main-→-renderer push). The handler validates the payload, invokes a `Tier2InboundDispatcher` port (injected by `index.ts`), and returns `{ ok: true }`.

### Renderer-side bridge (`apps/desktop/renderer/Tier2Bridge.ts`)

- [ ] FR-27: `Tier2Bridge.attach(bridge)` registers IPC listeners for `tier2.install`, `tier2.uninstall`, `tier2.mark-broken`. On `tier2.install`, builds a `SaaSRendererAdapter` whose `renderCurrent` and `renderDiff` return `<Tier2Loader adapterSource={…} scheme={…} version={…} state={state} pendingDiff={null|{diff}} />` and calls `registerAdapter`. On `tier2.uninstall`, calls `unregisterAdapter`. On `tier2.mark-broken`, calls `markBroken`.
- [ ] FR-28: The renderer bridge installs a window-level `error` listener for the Tier2Loader's `onFailure` callback path: when the loader surfaces a failure, the bridge sends a `tier2.boundary-error` IPC back to main so the lifecycle (Q6) trips. Adapter throw in production reaches Tier2Loader → `onFailure` → bridge → main → registry-host.markBrokenFromBoundary.
- [ ] FR-29: The bridge accepts a `workerFactory` injection so tests pass a stub. In production the desktop renderer will wire a real worker bundle (Phase 6 ships none — the loader's default workerFactory throws fail-closed, which means tier-2 visibly fails to render until the bundle lands; tier-3 fallback covers in the meantime). This is the documented Phase-6 limitation.

### Wire-up in `index.ts`

- [ ] FR-30: `app.whenReady` calls `wireQualityGateForTier2()` (existing) **plus** `wireSmokeRenderExecutorForTier2()` (new — calls `setDefaultSmokeRenderExecutor` with `MainProcessSmokeRenderExecutor`).
- [ ] FR-31: `app.whenReady` calls `startTier2Lifecycle({...})` with the configured `LifecycleEventSource` (Phase 6 stub: empty source that wires when the run-stream consumer lands in Phase 7), the audit log path under `app.getPath('userData')`, and a renderer dispatcher that calls `mainWindow.webContents.send(channel, payload)`.
- [ ] FR-32: Teardown: `app.on('before-quit')` calls the lifecycle handle's `stop()`.

## Non-functional requirements

- TypeScript strict; no `any`; `readonly` on every exposed result interface.
- No comments by default; security-relevant invariants get one line. The lifecycle's retry-budget invariant gets a comment explaining why the counter resets only on successful install.
- No new third-party dependencies. `better-sqlite3` is NOT a workspace dep (see "SQLite vs JSONL" below) — the audit log is JSON Lines via `node:fs/promises`.
- All file IO goes through injectable `fs` so unit tests write to tmp dirs.
- Injectable clock + injectable `setTimeout`-shaped scheduler for the per-attempt deadline timer. Tests advance time deterministically.
- D28 + D29: the lifecycle never runs adapter code with privileged scope. 6A's `compileAdapter` runs the source inside a `vm` context with no privileged globals. The smoke render call executes the resulting plain JS function in the main process — same scope-restriction as the sandbox guarantee, so no main-process privileged globals are reachable from inside the adapter (the adapter object's functions close over the `vm` context's globals only). The production live-render path uses `Tier2Loader`'s Web Worker for preemptive termination; the smoke render in main uses a measured timeout because no synchronous JS can be preempted on the main thread without a worker, and standing up a worker per smoke render is unjustified overhead at the install-time budget.

## Architectural decisions

### A. Renderer-vs-main split for adapter evaluation

The decision: **the persisted artifact is the source string; both main and renderer evaluate it independently.**

- **Main process (install pipeline).** Uses 6A's `compileAdapter` (Node `vm`) to produce an adapter object. Runs Q1 (schema check via Zod against the in-memory object), Q2 (AST scan via 6A's `astAllowlistScan` against the source), Q3/Q4 (smoke render — call `renderCurrent` / `renderDiff` on the compiled object inside a measured-timeout race). On all-pass, persist `{userData}/adapters/{scheme}-v{n}.js` and push `tier2.install` to the renderer.
- **Renderer process (live render).** Receives the source over IPC. Builds a thin `SaaSRendererAdapter` whose `renderCurrent` / `renderDiff` mount `<Tier2Loader adapterSource={source} ... />`. The Tier2Loader spawns a Web Worker that re-evaluates the source and renders. **Preemptive termination is enforced here** (D29) — `worker.terminate()` kills a misbehaving render at the 100 ms budget.

Why this split and not (a) compile-once-and-ship-an-adapter-object or (b) evaluate-only-in-the-worker:

- (a) Adapter objects contain functions; functions cannot be structured-cloned across the Electron IPC boundary (`postMessage` rejects them with `DataCloneError`). Shipping the source string and re-evaluating is the only IPC-safe option.
- (b) Smoke render must run before persistence so a generated-but-broken adapter never lands on disk. Standing up a renderer-side Worker from main is awkward (no renderer at app start before the window opens, and even afterward main-process orchestration of a renderer Worker is more complex than main-process `vm` evaluation). The 100 ms wall-clock preemption requirement is for the **live** render against real data; a smoke render with a synthetic state running in a measured-timeout race in main is adequate for catching `throw` / runaway / no-element. Live render still uses the Worker.

Defense-in-depth: 6A's sandbox strips privileged globals from the `vm` context, so the smoke render in main cannot reach `process`, `fs`, `child_process`, etc. — even though it executes on the main thread.

### B. SQLite vs JSON Lines

`better-sqlite3` is a native module (requires per-platform rebuild) and is NOT in any workspace `package.json`. The 6D `broken-mark` primitive already established the JSONL precedent at `{userData}/audit/adapter-lifecycle.log`. 6C uses the same file with an expanded set of event kinds. No new dependency.

When the audit log grows large enough to need indexed queries (probably Phase 8 once telemetry consumers attach), the migration is single-file: re-implement `appendLifecycleEvent` / `readLifecycleEvents` against SQLite. The callers don't change.

### C. IPC channel additions

The post-Phase-5 channel set is stable; 6C's additions are strictly additive. The new channel names are:

| Channel                | Direction       | Payload                                                                 |
| ---------------------- | --------------- | ----------------------------------------------------------------------- |
| `tier2.install`        | main → renderer | `{scheme, version, source, generatedAt, generatorModel}` (all required) |
| `tier2.uninstall`      | main → renderer | `{scheme, version}`                                                     |
| `tier2.mark-broken`    | main → renderer | `{scheme, version, method, reason}`                                     |
| `tier2.boundary-error` | renderer → main | `{scheme, version, method, message}` — Q6 forwarding from Tier2Loader   |

The four channels are added to the `CHANNELS` constant, exported types, and Zod schemas. The preload `isAllowedChannel` check passes automatically (it derives from `CHANNELS`).

Main-→-renderer channels use `webContents.send`. The renderer side listens via the same `bridge.ipc.on(channel, handler)` API the streaming pipeline already uses.

## Interfaces consumed

- `SaaSRendererAdapter`, `Tier2Loader`, `registerAdapter`, `unregisterAdapter`, `markBroken` — from `@enterprise-search/chat-surface`.
- `astAllowlistScan` — from 6A (`./ast-allowlist`).
- `compileAdapter` — from 6A (`./sandbox`).
- `validateAdapterSchema`, `staticAnalyze`, `runSmokeRender`, `setDefaultSmokeRenderExecutor`, `wrapWithBoundary`, `markAdapterBroken`, `setDefaultAstAllowlistChecker` — from 6D (`./quality-gate`).
- `AdapterGeneratedPayload`, `AdapterLayoutTemplate` — from `@enterprise-search/api-types`.
- `CHANNELS`, IPC schemas — from `@enterprise-search/chat-transport`.

## Interfaces produced

```ts
// apps/desktop/main/adapters/lifecycle-events.ts
export type LifecycleEventKind =
  | "requested"
  | "generated"
  | "validated"
  | "installed"
  | "render-error"
  | "marked-broken"
  | "regen-queued"
  | "lifecycle-exhausted";

export interface LifecycleAuditEntry {
  readonly ts: number;
  readonly kind: LifecycleEventKind;
  readonly scheme: string;
  readonly version: number;
  readonly detail?: string;
}

export interface LifecycleEventsDeps {
  readonly logPath: string;
  readonly fs: {
    appendFile(p: string, data: string): Promise<void>;
    mkdir(p: string, o: { recursive: true }): Promise<string | undefined>;
    readFile(p: string, enc: "utf8"): Promise<string>;
  };
}
export function appendLifecycleEvent(
  entry: LifecycleAuditEntry,
  deps: LifecycleEventsDeps,
): Promise<void>;
export function readLifecycleEvents(
  opts: { readonly scheme?: string; readonly limit?: number },
  deps: LifecycleEventsDeps,
): Promise<readonly LifecycleAuditEntry[]>;

// apps/desktop/main/adapters/tier2-installer.ts
export interface PersistAdapterSourceOpts {
  readonly adapterDir: string;
  readonly scheme: string;
  readonly version: number;
  readonly source: string;
}
export interface InstallerDeps {
  readonly fs: {
    writeFile(p: string, data: string): Promise<void>;
    mkdir(p: string, o: { recursive: true }): Promise<string | undefined>;
    unlink(p: string): Promise<void>;
  };
}
export function persistAdapterSource(
  opts: PersistAdapterSourceOpts,
  deps: InstallerDeps,
): Promise<void>;
export function uninstallAdapterFile(
  opts: { adapterDir: string; scheme: string; version: number },
  deps: InstallerDeps,
): Promise<void>;

// apps/desktop/main/adapters/smoke-render-executor.ts
export class MainProcessSmokeRenderExecutor implements SmokeRenderExecutor {
  constructor(opts?: {
    readonly setTimeout?: typeof setTimeout;
    readonly clearTimeout?: typeof clearTimeout;
  });
  execute(
    adapter: SaaSRendererAdapter,
    payload: { method: SmokeMethod; input: unknown },
    budgetMs: number,
  ): Promise<{ ok: true } | { ok: false; kind: SmokeFailKind; error: Error }>;
}

// apps/desktop/main/adapters/registry-host.ts
export type InstallGate = "schema" | "allowlist" | "smoke" | "compile";
export type InstallResult =
  | { readonly ok: true; readonly scheme: string; readonly version: number }
  | { readonly ok: false; readonly gate: InstallGate; readonly detail: string };

export interface RendererDispatcher {
  send(
    channel: "tier2.install" | "tier2.uninstall" | "tier2.mark-broken",
    payload: unknown,
  ): void;
}

export interface RegistryHostDeps {
  readonly adapterDir: string;
  readonly clock: () => number;
  readonly dispatcher: RendererDispatcher;
  readonly audit: LifecycleEventsDeps;
  readonly installer: InstallerDeps;
}

export interface InstallAdapterArgs {
  readonly scheme: string;
  readonly version: number;
  readonly source: string;
  readonly generatedAt: string;
  readonly generatorModel: string;
}
export function installAdapter(
  args: InstallAdapterArgs,
  deps: RegistryHostDeps,
): Promise<InstallResult>;
export function uninstallAdapter(
  args: { scheme: string; version: number },
  deps: RegistryHostDeps,
): Promise<void>;
export function markBrokenFromBoundary(
  args: {
    scheme: string;
    version: number;
    method: "renderCurrent" | "renderDiff";
    reason: string;
  },
  deps: RegistryHostDeps,
): Promise<void>;

// apps/desktop/main/adapters/lifecycle.ts
export interface LifecycleEventSource {
  onAdapterGenerated(handler: (p: AdapterGeneratedPayload) => void): () => void;
  onBoundaryError(
    handler: (info: {
      scheme: string;
      version: number;
      method: "renderCurrent" | "renderDiff";
      reason: string;
    }) => void,
  ): () => void;
}

export interface Tier2LifecycleDeps {
  readonly source: LifecycleEventSource;
  readonly host: RegistryHostDeps;
  readonly retryBudget?: number;
  readonly attemptTimeoutMs?: number;
  readonly setTimeout?: typeof setTimeout;
  readonly clearTimeout?: typeof clearTimeout;
  readonly clock?: () => number;
  readonly onExhausted?: (scheme: string) => void;
}

export interface Tier2LifecycleHandle {
  stop(): void;
  attempts(scheme: string): number;
}
export function startTier2Lifecycle(
  deps: Tier2LifecycleDeps,
): Tier2LifecycleHandle;

// packages/chat-transport/src/ipc/rpc-protocol.ts (additions)
export const Tier2InstallPayloadSchema: z.ZodTypeAny;
export type Tier2InstallPayload = z.infer<typeof Tier2InstallPayloadSchema>;
export const Tier2UninstallPayloadSchema: z.ZodTypeAny;
export type Tier2UninstallPayload = z.infer<typeof Tier2UninstallPayloadSchema>;
export const Tier2MarkBrokenPayloadSchema: z.ZodTypeAny;
export type Tier2MarkBrokenPayload = z.infer<
  typeof Tier2MarkBrokenPayloadSchema
>;
export const Tier2BoundaryErrorPayloadSchema: z.ZodTypeAny;
export type Tier2BoundaryErrorPayload = z.infer<
  typeof Tier2BoundaryErrorPayloadSchema
>;

// apps/desktop/renderer/Tier2Bridge.ts
export interface Tier2BridgeOptions {
  readonly bridge: WindowBridge;
  readonly workerFactory?: () => Tier2WorkerLike;
}
export class Tier2Bridge {
  constructor(opts: Tier2BridgeOptions);
  attach(): () => void;
}
```

## Tests

- `lifecycle-events.test.ts`: append-only, append + read, filter by scheme, limit, durable across "restart" (close + reopen tmp file path), graceful missing-file.
- `tier2-installer.test.ts`: persist creates parent dir, persist + reload via 6A's `loadAdapterSource` roundtrip, uninstall is no-op on missing.
- `smoke-render-executor.test.ts`: successful render returns `{ ok: true }`; throw → `{ ok: false, kind: 'throw' }`; timeout → `{ ok: false, kind: 'timeout' }`; non-element → `{ ok: false, kind: 'not-element' }`.
- `registry-host.test.ts`: Q1→Q5 pipeline runs in order (assert via call-order spy), Q1 failure short-circuits before Q2, install dispatches `tier2.install` IPC with correct payload, uninstall dispatches `tier2.uninstall`, markBrokenFromBoundary dispatches `tier2.mark-broken` AND persists audit events in order (`render-error` → `marked-broken`).
- `lifecycle.test.ts`:
  - Happy path: `adapter_generated` event → install pipeline succeeds → dispatcher receives `tier2.install` → counter resets to 0 — all on the timing seam well under 5 s.
  - Broken adapter: boundary-error arrives → `markBrokenFromBoundary` runs → counter increments by 1; subsequent `adapter_generated` for the same scheme retries once; if the next attempt fails, counter is at 2; after the third failure, the fourth `adapter_generated` is rejected with `lifecycle-exhausted` and `onExhausted` fires.
  - Budget exhaustion: with budget=1, two failed attempts → `lifecycle-exhausted` fires on the second; tier-3 visibility is the chat-surface registry's job — we assert by checking the IPC dispatch never received a second `tier2.install`.
  - Per-attempt deadline timeout: register a `setTimeout` mock that fires the 5-s timer immediately; install hangs → attempt is aborted, counter increments.
- `handlers.test.ts` (additions): `tier2.boundary-error` channel validates payload via Zod; well-formed payload calls the dispatcher; malformed payload throws `IpcValidationError`.
- `Tier2Bridge.test.ts`: on `tier2.install`, registerAdapter is called with the right scheme + version and the produced adapter's `renderCurrent` returns a `Tier2Loader` element with the right source; on `tier2.uninstall`, `unregisterAdapter` is called; on `tier2.mark-broken`, `markBroken` is called.

## Rules

- **D28 / D29**: the lifecycle never runs adapter code with privileged scope. Sandbox + Worker handle isolation; the lifecycle orchestrates.
- **No `any`. No comments by default** except for security-relevant invariants (the smoke-render in-main scope-restriction, the per-scheme retry-budget reset on success).
- **Bounded retry budget**: 3 attempts × ~5 s each. Token-bucket counter, per-attempt deadline timer. Counter resets only on successful install.
- **Injectable clocks and timers** in tests. Never `sleep`.

## Done criteria

- [ ] All FRs met.
- [ ] `npm test --workspace @enterprise-search/desktop` passes.
- [ ] `npm test --workspace @enterprise-search/chat-surface` passes.
- [ ] `npm test --workspace @enterprise-search/chat-transport` passes.
- [ ] `npm run typecheck --workspace @enterprise-search/desktop` passes.
- [ ] `npm run typecheck --workspace @enterprise-search/chat-transport` passes.
- [ ] No new top-level npm dependency.
- [ ] No edits to chat-surface's `SurfaceRegistry.ts` or `SaaSRendererAdapter.ts`.
- [ ] Sub-PRD documents the renderer-vs-main split, the SQLite-vs-JSONL choice, and the IPC channel additions.

## Open questions

- **Q1 — Why not run smoke render in the renderer's Web Worker?** The Worker bundle does not exist before the window opens, and Phase 6 does not ship the production bundle. The smoke render must run before the install completes so the adapter is persisted only if it actually works. Running in main via 6A's `vm` sandbox is the available, deterministic, dependency-free option. Documented as the install-time choice; the **live** render in the renderer Worker is where preemption matters (the user can otherwise see a 100 ms render budget violated by an adapter that ran the smoke fine but blocks on real data).
- **Q2 — Why does the lifecycle not proactively re-request generation on failure?** The capability emits `adapter_generated` events from the run stream; explicit "regen now" pings would require either an HTTP back-channel to ai-backend (Phase 7) or a synthetic capability invocation from main (out of scope for Phase 6). Phase 6 ships the audit log entry (`regen-queued`) and the counter accounting; the agent's next invocation produces the next attempt. Documented.
- **Q3 — Why is the renderer-side bridge a separate file from the existing `bootstrap.tsx`?** Separation of concerns: bootstrap wires the global app shell; the tier-2 bridge owns adapter install/uninstall IPC and the worker factory. Keeps the bridge unit-testable without React rendering.
- **Q4 — Why does the per-attempt timeout use measured `setTimeout` and not a Worker?** The install pipeline runs in main; main is the orchestration thread. The adapter code itself runs in 6A's `vm` context with `script.runInContext(...)` capped at 1 s by Node's `vm` `timeout` option (already enforced inside 6A's `compileAdapter`). The 5 s per-attempt timer is for the surrounding pipeline (file IO, IPC dispatch, etc.) — preemptive there is overkill, and a hung file write would mean the OS is broken, which is not the threat model.
- **Q5 — Why doesn't lifecycle.ts dispatch IPC directly?** Single-source-of-truth: registry-host owns every IPC message about tier-2. Lifecycle is the orchestrator; registry-host is the boundary. Two seams instead of one make every "did the registry get told?" question take a hop to answer.

## Notes for orchestrator review

- This is the final Phase 6 agent. Phase 7 begins after this merges.
- The Tier2Loader's production worker bundle is **not** in this scope. The renderer-side bridge passes the loader's default `workerFactory` (which fails closed); tier-3 fallback covers in the meantime. Phase 8 builds the worker bundle.
- The IPC additions are strictly additive. No existing channel is renamed, removed, or had its schema changed.
- No edits to `packages/chat-surface/src/index.ts` — the public surface is unchanged; 6C consumes existing exports.
- The audit log path matches 6D's. Both `broken-mark` events and lifecycle events append to the same file; lifecycle reads can filter by kind.
