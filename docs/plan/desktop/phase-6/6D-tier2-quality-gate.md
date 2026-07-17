# Phase 6.D: tier2-quality-gate

## Vision

Tier-2 adapters are agent-generated JavaScript that the desktop app loads from `{userData}/adapters/{scheme}-v{n}.js`. The pure-render contract (D28) and the Worker sandbox (D29) are necessary but not sufficient: the loader still needs to (a) refuse modules that do not match the `SaaSRendererAdapter` shape, (b) refuse modules whose source references anything outside the documented import / global allowlist, (c) prove the module can render the host's synthetic minimum payload before it is installed, (d) wrap every live render with an error boundary that records the failure against the offending `{scheme, version}` pair, and (e) demote a version once it errors so subsequent resolutions skip it and regen is queued.

These five checkpoints — Q1, Q2, Q3, Q4 (one of), Q5/Q6's invalidation primitive — are this agent's deliverables. They share one design rule: each gate is a small pure function that takes data in and returns a verdict, so the lifecycle orchestrator (6C) can pipeline them in whatever order it needs without this module owning policy. The only side-effecting primitive is the broken-mark, and that one writes to an append-only audit log and a registry — both are passed in so unit tests inject fakes.

Sandbox execution itself (worker boundary, vm compilation, privileged-global stripping) is **6A's** scope. This agent imports 6A's `ast-allowlist` and `Tier2Loader` through narrow interfaces so the branches can land in any order; the orchestrator wires the real imports at merge time.

## Status

- Status: in-progress
- Agent slug: `phase-6-tier2-quality-gate`
- Branch: `desktop/phase-6-tier2-quality-gate`
- Worktree: `.claude/worktrees/agent-a8d3586bf44c6dbae`
- Created: 2026-05-17

## Scope

**In scope** (files this agent owns):

- `docs/plan/desktop/phase-6/6D-tier2-quality-gate.md` — this file.
- `apps/desktop/main/adapters/quality-gate/schema.ts` — Q1: Zod shape validator.
- `apps/desktop/main/adapters/quality-gate/schema.test.ts` — Q1 catches malformed exports.
- `apps/desktop/main/adapters/quality-gate/allowlist.ts` — Q2: AST allowlist scanner, pluggable on 6A's checker.
- `apps/desktop/main/adapters/quality-gate/allowlist.test.ts` — Q2 catches banned globals / imports / `eval` / `new Function`.
- `apps/desktop/main/adapters/quality-gate/smoke-render.ts` — Q4: pre-install render against synthetic state + diff with budget enforcement.
- `apps/desktop/main/adapters/quality-gate/smoke-render.test.ts` — Q4 catches throw, timeout, and budget overruns.
- `apps/desktop/main/adapters/quality-gate/error-boundary.ts` — Q3/Q5: wrap installed adapter so live render errors fire `onError(scheme, version, error)`.
- `apps/desktop/main/adapters/quality-gate/error-boundary.test.ts` — Q3/Q5 forwards thrown errors without crashing the host.
- `apps/desktop/main/adapters/quality-gate/broken-mark.ts` — Q6: persist append-only audit entry + call `markBroken` on the registry.
- `apps/desktop/main/adapters/quality-gate/broken-mark.test.ts` — Q6 writes the entry and calls the registry exactly once.
- `apps/desktop/main/adapters/quality-gate/index.ts` — public barrel for the Q1-Q6 surface 6C consumes.

**Out of scope** (do NOT touch):

- `apps/desktop/main/adapters/{loader,sandbox,ast-allowlist}.ts` — owned by 6A. This agent stubs them behind injectable interfaces.
- `packages/chat-surface/src/surfaces/Tier2Loader.tsx` — owned by 6A. Same stub strategy.
- `apps/desktop/main/adapters/{registry-host,lifecycle-events,quality-gate}.ts` (the file, not the dir) — owned by 6C. This agent owns the `quality-gate/` directory; 6C imports its barrel.
- `services/ai-backend/**` — 6B's territory.
- `packages/chat-surface/src/surfaces/SurfaceRegistry.ts` — already implements `markBroken`; this agent consumes it through an injectable port.

## Functional requirements

- [ ] FR-Q1: `validateAdapterSchema(adapter: unknown)` returns `{ ok: true, value }` for any object that matches the `SaaSRendererAdapter` shape (string `scheme`, callable `matches`, callable `renderCurrent`, callable `renderDiff`, `metadata.origin ∈ {first-party, agent-generated, community}`, integer `metadata.schemaVersion`, optional `metadata.generatedAt` and `metadata.generatorModel` as strings). Returns `{ ok: false, errors: ZodIssue[] }` for any other shape — empty object, missing field, wrong type, wrong origin literal, etc.
- [ ] FR-Q2: `staticAnalyze(source: string)` returns `{ ok: true }` only when the source passes 6A's AST allowlist. Returns `{ ok: false, violations }` otherwise. The default checker delegates to 6A's `ast-allowlist`; tests inject a fake checker. The default fails-closed if 6A's module is not yet on disk (returns a synthetic violation with a clear "6A not wired" message).
- [ ] FR-Q3 (smoke render): `runSmokeRender(adapter, synthState, synthDiff, opts?)` returns `{ ok: true }` only when the adapter's `renderCurrent(synthState)` and `renderDiff(synthDiff)` both complete inside the budget (default 100 ms each) and both return a React element. Returns `{ ok: false, kind: 'throw' | 'timeout' | 'not-element', error }` on failure. The default executor delegates to 6A's `Tier2Loader` worker; tests inject a fake executor.
- [ ] FR-Q4: `wrapWithBoundary(adapter, onError)` returns a new adapter whose `renderCurrent` and `renderDiff` call the underlying methods inside a try-catch. On throw, the wrapper fires `onError({ scheme: adapter.scheme, version: adapter.metadata.schemaVersion, method, error })` and rethrows so the host's React error boundary still surfaces tier-3 fallback per PRD §3.4. The wrapper does not introduce any privileged globals.
- [ ] FR-Q5: `markAdapterBroken(scheme, version, reason, deps)` appends one line to the audit log (default `{userData}/audit/adapter-lifecycle.log`, JSON Lines, append-only) and calls `deps.registry.markBroken(scheme, version, reason)`. The function is side-effecting; it returns `void`. `deps` is injectable (`logPath`, `clock`, `registry`, `fs`); the defaults wire to `app.getPath('userData')`, `Date.now`, chat-surface's `markBroken`, and `node:fs/promises`.
- [ ] FR-Index: `apps/desktop/main/adapters/quality-gate/index.ts` exports `validateAdapterSchema`, `staticAnalyze`, `runSmokeRender`, `wrapWithBoundary`, `markAdapterBroken` plus the result-types used by callers (6C).

## Non-functional requirements

- TypeScript strict; no `any`; `readonly` on every exposed result interface field.
- No comments by default; security-relevant invariants get a one-line comment (D28 / D29 boundary, append-only audit log).
- Zod 3.x (already a workspace dep — `apps/desktop/package.json` line 25). No new third-party dep.
- All FS interaction goes through injectable `fs` so unit tests can write to tmp dirs.
- Audit log is **append-only** — implementation uses `fs.appendFile` and never `writeFile`, `truncate`, or `unlink`.
- D28: this module must not execute adapter code with privileged globals. Smoke render delegates to 6A's worker; the error-boundary wrapper is a thin try-catch that runs in main but cannot introduce privileged globals into the adapter's scope (the adapter is already loaded by 6A's sandbox).

## Interfaces consumed

- `SaaSRendererAdapter`, `SaaSRendererAdapterMetadata`, `SaaSRendererAdapterOrigin`, `TIER3_SCHEME` from `@0x-copilot/chat-surface` (re-exported from `packages/chat-surface/src/surfaces/SaaSRendererAdapter.ts`).
- `markBroken` from `@0x-copilot/chat-surface` — called by `markAdapterBroken` through the injected `registry` dep.
- `app.getPath('userData')` from `electron` — only resolved at call time inside the default `deps` factory so unit tests never load Electron.
- 6A artifacts (consumed via narrow injectable ports; orchestrator wires the real imports at merge time):
  - `AstAllowlistChecker` (port) — `staticAnalyze` delegates here.
  - `SmokeRenderExecutor` (port) — `runSmokeRender` delegates here; the production implementation calls 6A's `Tier2Loader` worker.

## Interfaces produced

```ts
// schema.ts
export interface SchemaOk {
  readonly ok: true;
  readonly value: SaaSRendererAdapter;
}
export interface SchemaFail {
  readonly ok: false;
  readonly errors: readonly ZodIssue[];
}
export function validateAdapterSchema(input: unknown): SchemaOk | SchemaFail;

// allowlist.ts
export interface Violation {
  readonly kind:
    | "import"
    | "global"
    | "member-access"
    | "eval"
    | "function-ctor"
    | "dynamic-import"
    | "internal";
  readonly message: string;
  readonly loc?: { readonly line: number; readonly column: number };
}
export interface AstAllowlistChecker {
  check(source: string): { ok: true } | { ok: false; violations: Violation[] };
}
export function staticAnalyze(
  source: string,
  checker?: AstAllowlistChecker,
): { ok: true } | { ok: false; violations: readonly Violation[] };

// smoke-render.ts
export interface SmokeRenderOk {
  readonly ok: true;
}
export interface SmokeRenderFail {
  readonly ok: false;
  readonly kind: "throw" | "timeout" | "not-element";
  readonly error: Error;
  readonly method: "renderCurrent" | "renderDiff";
}
export interface SmokeRenderExecutor {
  execute(
    adapter: SaaSRendererAdapter,
    payload: { method: "renderCurrent" | "renderDiff"; input: unknown },
    budgetMs: number,
  ): Promise<
    | { ok: true }
    | { ok: false; kind: "throw" | "timeout" | "not-element"; error: Error }
  >;
}
export function runSmokeRender(
  adapter: SaaSRendererAdapter,
  synthState: unknown,
  synthDiff: unknown,
  opts?: { executor?: SmokeRenderExecutor; budgetMs?: number },
): Promise<SmokeRenderOk | SmokeRenderFail>;

// error-boundary.ts
export interface BoundaryError {
  readonly scheme: string;
  readonly version: number;
  readonly method: "renderCurrent" | "renderDiff";
  readonly error: Error;
}
export function wrapWithBoundary(
  adapter: SaaSRendererAdapter,
  onError: (info: BoundaryError) => void,
): SaaSRendererAdapter;

// broken-mark.ts
export interface BrokenMarkDeps {
  readonly logPath: string;
  readonly clock: () => number;
  readonly registry: {
    markBroken(scheme: string, version: number, reason: string): void;
  };
  readonly fs: {
    appendFile(path: string, data: string): Promise<void>;
    mkdir(path: string, opts: { recursive: true }): Promise<void>;
  };
}
export function markAdapterBroken(
  scheme: string,
  version: number,
  reason: string,
  deps: BrokenMarkDeps,
): Promise<void>;
```

## Open questions

- **Q1 — Should the audit log be SQLite or JSON Lines?** Adopted: JSON Lines, file `{userData}/audit/adapter-lifecycle.log`, append-only. SQLite has a richer query surface but every "append-only" guarantee requires schema lockdown and triggers, and 6C's needs (replay the lifecycle on startup) only require a sequential scan. JSON Lines is simpler-and-elegant and trivially append-only via `fs.appendFile`. Recorded so the orchestrator can flip if 6C needs SQL queries.
- **Q2 — Default scheme version mismatch behavior.** Adopted: schema validator rejects any `metadata.schemaVersion` that is not a non-negative integer. The capability-drift check from PRD §9.5.4 ("auto-mark broken when host's `schemaVersion` changes") is 6C's responsibility — 6C calls `validateAdapterSchema` first, then asks the host whether the adapter's `metadata.schemaVersion` is still supported, then calls `markAdapterBroken` if not.
- **Q3 — Worker-vs-main split for smoke render.** Adopted: this module owns the **gate** logic and budget enforcement, not the worker. Production wires the executor to 6A's `Tier2Loader` worker. Tests inject a synchronous fake executor. If 6A's branch lands first, the default executor will be a thin pass-through; if 6D lands first, the default executor returns a synthetic failure so the lifecycle code fails-closed.
- **Q4 — Error-boundary placement vs React `componentDidCatch`.** Adopted: this module's wrapper is the **first** boundary the adapter's thrown error hits — fires the audit callback, then rethrows. The host's existing `TcSurfaceMount` React error boundary (Phase 4A) catches the rethrow and falls back to tier-3. The two are complementary: ours records, theirs renders. Recorded so a reviewer doesn't conclude we duplicated boundaries.

## Done criteria

- [ ] All FRs met.
- [ ] `npm test --workspace @0x-copilot/desktop` passes (baseline 36 tests + new tests, no regression).
- [ ] `npm run typecheck --workspace @0x-copilot/desktop` passes.
- [ ] `npm run lint --workspace @0x-copilot/desktop` passes.
- [ ] No new third-party dependency.
- [ ] No imports outside scope (quality-gate dir + chat-surface adapter contract + node std lib + zod + react types).
- [ ] No DOM globals in main-process code (ESLint enforces).

## Notes for orchestrator review

- This module's API is the surface 6C will pipeline. Keep functions pure-ish: schema and allowlist are pure; smoke render returns a Promise; wrap-with-boundary returns a new adapter; mark-adapter-broken is the single side-effecting primitive (Promise<void>, audit log + registry call).
- The two stubbed dependencies (6A's `ast-allowlist` and `Tier2Loader`) are isolated behind narrow injectable ports. The default implementations fail-closed when 6A's exports are absent — this is intentional: a missing static analyzer or smoke render should refuse the install, not silently allow it.
- The audit log path defaults to `{userData}/audit/adapter-lifecycle.log`. The constructor accepts an injected path so vitest writes into a tmp dir. Electron's `app.getPath('userData')` is resolved lazily — never imported at module-load time — so unit tests do not need to mock Electron.
- The error boundary wrapper preserves the adapter's identity (`scheme`, `matches`, `metadata`) and only intercepts the two render methods. Hot-swap via `registerAdapter` continues to work because the wrapped adapter has the same `{scheme, metadata.schemaVersion}` pair.
- All five gate primitives are independently testable: each test file targets a single Q from §9.5 and demonstrates the gate catches its category per PRD's "Each Q has a unit test that proves it catches its category."
