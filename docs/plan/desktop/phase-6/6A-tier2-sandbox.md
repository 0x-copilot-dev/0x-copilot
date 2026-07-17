# Phase 6.A: tier2-sandbox

## Vision

Land the install-and-render safety perimeter for **tier-2 agent-generated** `SaaSRendererAdapter` modules. The output of Phase 6 is a host that can take a string of JavaScript produced by the codegen agent (Phase 6B), validate it statically, execute it in a privileged-globals-free `vm` context, render its output inside a Web Worker the host can `terminate()` at the 100 ms budget, and reconcile the worker's JSON-encoded VDOM back into the host React tree — without ever letting the agent code see `process`, `child_process`, `fetch`, `window`, `document`, or any other privileged primitive.

Phase 4-A's host budget for tier-1 is **measured** (wall-clock around the React call); that is fine for code we wrote. Tier-2 runs agent-generated code so the 100 ms budget must be **enforceable**. Worker termination is the only preemptive mechanism available in the renderer; `setTimeout` cannot interrupt a synchronous React render. The Worker boundary plus the AST allowlist plus the `vm` privileged-globals scrub together make the sandbox.

This phase deliberately ships **no codegen, no quality-gate composition, and no lifecycle wiring**. Those are 6B (codegen-backend, Python — `services/ai-backend`), 6D (`apps/desktop/main/adapters/quality-gate/**`), and 6C (compose 6A loader/sandbox + 6D quality gate + register-on-success), respectively. 6C lands strictly after 6A and 6D. The on-disk path `{userData}/adapters/{scheme}-v{n}.js` is read here but written by 6C.

## Status

- Status: in-progress
- Agent slug: `phase-6-tier2-sandbox`
- Branch: `desktop/phase-6-tier2-sandbox`
- Worktree: `.claude/worktrees/agent-ad0ccf49df145417c`
- Created: 2026-05-17

## Scope

**In scope** (files this agent owns):

- `docs/plan/desktop/phase-6/6A-tier2-sandbox.md` — this file.
- `apps/desktop/main/adapters/ast-allowlist.ts` (NEW) — AST scanner + the `ALLOWED_IMPORTS` allowlist.
- `apps/desktop/main/adapters/ast-allowlist.test.ts` (NEW).
- `apps/desktop/main/adapters/sandbox.ts` (NEW) — Node `vm` context, no privileged globals, returns the exported adapter object.
- `apps/desktop/main/adapters/sandbox.test.ts` (NEW).
- `apps/desktop/main/adapters/loader.ts` (NEW) — file IO + AST scan, returns either compiled source or violations.
- `apps/desktop/main/adapters/loader.test.ts` (NEW).
- `packages/chat-surface/src/surfaces/Tier2Loader.tsx` (NEW) — host-side React component that proxies adapter render calls through a renderer-spawned Web Worker, reconciles the worker's JSON VDOM back into React elements, enforces the 100 ms preemptive budget via `worker.terminate()`.
- `packages/chat-surface/src/surfaces/Tier2Loader.test.tsx` (NEW).

**Out of scope** (other agents):

- `apps/desktop/main/adapters/quality-gate/**` — Phase 6D owns Zod schema validation (Q1), the static analysis CLI wrapper (Q2), smoke-render (Q4), error-boundary instrumentation, and the broken-mark side-effect. 6A's AST allowlist is the underlying primitive 6D's Q2 wraps; 6D imports `astAllowlistScan` from this module.
- `apps/desktop/main/adapters/lifecycle.ts` — Phase 6C composes 6A's `loadAdapterSource` + `compileAdapter` with 6D's quality gate, persists `{scheme}-v{n}.js` after install, and calls `registerAdapter` from chat-surface on success. 6A does not write files; it only reads.
- Codegen — Phase 6B in `services/ai-backend`. Python service. No overlap.
- `packages/chat-surface/src/index.ts` — barrel exports stay as Phase 4 left them; new exports listed under "Interfaces produced" for the orchestrator to surface in a separate pass (orchestrator-only file per CLAUDE.md).
- Production Worker bundle — 6A ships the Worker source as a stub string that includes only the framework plumbing (message protocol, JSON VDOM serialization). The renderer-loadable worker bundle (with React + design-system primitives shipped via an esbuild Web Worker target) is wired in 6C. Tests mock the `Worker` global.

## Functional requirements

- [ ] **FR-1 AST allowlist scan.** `astAllowlistScan(source: string): AstScanResult` parses the adapter source with `@typescript-eslint/parser` (already a `@0x-copilot/desktop` devDependency; supports JSX + TS shapes without a typecheck pass) and walks the AST. It rejects on any of:
  - `ImportDeclaration` whose source value is **not** in `ALLOWED_IMPORTS` (the keyset).
  - `ImportDeclaration` whose specifiers include a name **not** in the listed allowlist for that module (e.g., `import { useEffect } from 'react'` rejects because `useEffect` is not in the `react` allowlist).
  - `ImportExpression` (dynamic `import()`).
  - `CallExpression` whose callee is the identifier `require`.
  - `Identifier` reference (in a non-property-key position) to any forbidden global: `process`, `global`, `globalThis`, `child_process`, `fs`, `net`, `http`, `https`, `crypto`, `path`, `os`, `window`, `document`, `fetch`, `XMLHttpRequest`, `WebSocket`, `EventSource`, `Buffer`, `setImmediate`.
  - `CallExpression` whose callee is the identifier `eval`.
  - `NewExpression` whose callee is the identifier `Function`.
  - `CallExpression` whose callee is `Function` (i.e., `Function(...)` without `new`).
  - Any property access named `__proto__` (`MemberExpression` with `property.name === '__proto__'`) or `prototype` written via `AssignmentExpression` left-hand `MemberExpression` whose property is `prototype` (i.e., `Foo.prototype = …`).
  - Returns `{ ok: true }` or `{ ok: false, violations: Array<{ line, kind, detail }> }`. The violations array is non-empty when `ok` is false.
- [ ] **FR-2 ALLOWED_IMPORTS surface.** Exported as a readonly `const` so `sandbox.ts` and the future Phase 6D static-analysis CLI consume the same shape. Contents:
  - `react`: `['createElement', 'Fragment', 'useState']` — explicitly NOT `useEffect`, NOT `useLayoutEffect`, NOT `useRef` (each is a side-effect or escape hatch the adapter cannot need under D28).
  - `react-dom`: `[]` — present as a sanctioned target for type-only references; no value imports allowed.
  - `@0x-copilot/design-system`: a documented set of pure-render primitives — `['Button', 'Badge', 'Card', 'TextInput', 'Select', 'Switch', 'Toggle', 'Field', 'IconButton', 'StatusPill', 'AppIcon', 'HarnessRow', 'StatusLine', 'ConnectorChip', 'classNames']`. `ThemeProvider`, `useTheme`, `Menu`, `Popover*` are excluded — they touch `document` / `window` / `localStorage` or render context that an adapter must not own.
- [ ] **FR-3 Sandbox execution.** `compileAdapter(source: string): SandboxCompileResult` runs the validated source inside a Node `vm.createContext(...)` with an explicit `globals` object containing **only**: `React` (the renderer's React handle), `DesignSystem` (the curated design-system shape), `Math`, `JSON`, `Date`, `RegExp`, `Number`, `String`, `Array`, `Object`, `Boolean`, `Symbol`, `Error`, `TypeError`, `RangeError`, `console` (frozen, no-op `log` / `warn` / `error`), and a `module` object the adapter assigns to. The `globals` object is deep-frozen and does NOT contain `process`, `global`, `globalThis`, `Function`, `eval`, `require`, or any constructor that would let the adapter reach back to the host. Returns `{ ok: true, adapter }` where `adapter` is a `SaaSRendererAdapter`-shaped object, or `{ ok: false, error }` on syntax error / runtime throw / missing `module.exports`.
- [ ] **FR-4 Loader composition.** `loadAdapterSource(opts: { adapterDir: string; scheme: string; version: number }): Promise<LoadResult>`:
  - Reads `{adapterDir}/{scheme}-v{version}.js` from disk (`fs/promises.readFile` — UTF-8).
  - Runs `astAllowlistScan` on the source.
  - On AST violation: returns `{ ok: false, reason: 'ast-violation', violations }` without ever passing the source to `compileAdapter`.
  - On file-read failure: returns `{ ok: false, reason: 'file-error', detail }`.
  - On success: returns `{ ok: true, source }`. Compilation is a separate call so the lifecycle agent (6C) can run the quality-gate (6D) on the validated source before committing to a sandbox compile.
- [ ] **FR-5 Tier2Loader host component.** `Tier2Loader` is a React component with props `{ adapterSource, scheme, version, state, pendingDiff, workerFactory }` (workerFactory is the test seam — see Non-functional). On render:
  - Spawns a Worker via `workerFactory()` (defaults to a stub that the production Phase-6C bundle replaces).
  - Posts `{ kind: 'render', adapterSource, scheme, version, mode: pendingDiff ? 'diff' : 'current', payload }`.
  - Starts a 100 ms timer; if the worker has not posted back a `kind: 'rendered' | 'failed'` message before the timer fires, calls `worker.terminate()` and surfaces a `{ kind: 'timeout' }` state. **Preemptive** — the worker is dead.
  - On `{ kind: 'rendered', tree }`: reconciles the JSON VDOM tree into a React element using a fixed factory map (allowlisted tag → React component) and renders it.
  - On `{ kind: 'failed', reason, detail }`: renders `null`; the host `TcSurfaceMount` (Phase 4-A) sees the `null` returned by the adapter and the existing tier-3 fallback covers it.
  - JSON VDOM shape: `{ tag: string, props: Record<string, unknown>, children: Array<JsonTree | string | number | null> }`. Reconciliation enforces the same allowlisted tag set as the worker serializer; an unknown tag is rendered as a `span` with a `data-tier2-unsafe-tag` attribute so the failure is visible in dev and harmless in prod.
- [ ] **FR-6 Allowlisted reconciliation tags.** The host's factory map covers React intrinsics (`div`, `span`, `p`, `ul`, `ol`, `li`, `h1`–`h6`, `strong`, `em`, `code`, `pre`, `a`, `img`, `section`, `article`, `header`, `footer`, `nav`, `aside`, `table`, `thead`, `tbody`, `tr`, `td`, `th`, `figure`, `figcaption`, `blockquote`, `hr`, `br`) plus a `ds:` namespace mapping (`ds:Button`, `ds:Badge`, `ds:Card`, …) one-to-one to the FR-2 design-system allowlist. Props are filtered to a known prop allowlist (no `dangerouslySetInnerHTML`, no `on*` handlers — adapters do not own behaviour per D28).
- [ ] **FR-7 Tests in `Tier2Loader.test.tsx`:**
  - successful render: a stub `Worker` posts back `{ kind: 'rendered', tree }` and the host reconciles into the DOM under the same node;
  - throwing adapter: stub posts `{ kind: 'failed' }`; host renders `null` (parent host glue / tier-3 covers);
  - infinite-loop adapter: stub never posts; the 100 ms timer fires; `worker.terminate()` is called; host surfaces timeout;
  - 1 GB string adapter: stub posts `{ kind: 'failed', reason: 'oom' }`; host renders `null`;
  - `fetch`-calling adapter is **never reached at this layer** — the AST scanner rejects it; the corresponding test lives in `ast-allowlist.test.ts`.
- [ ] **FR-8 Tests in `ast-allowlist.test.ts`:** each forbidden pattern from FR-1 has at least one positive (rejected) and one negative (allowed) test. The `useEffect`-from-react test is included as the canonical D28 enforcement case (no side-effects in adapter render).
- [ ] **FR-9 Tests in `sandbox.test.ts`:**
  - executes a known-good adapter (export shape: `module.exports = { scheme, matches, renderCurrent, renderDiff, metadata }`);
  - rejects a syntactically invalid source;
  - rejects an adapter whose `module.exports` is missing or wrong shape;
  - confirms `process`, `global`, `globalThis`, `require`, `Function`, and `eval` are all `undefined` inside the sandbox context (assert via a synthetic adapter that probes them).
- [ ] **FR-10 Tests in `loader.test.ts`:**
  - reads a file from a temp dir and round-trips through the AST scan;
  - rejects a file with a forbidden import (returns `reason: 'ast-violation'`);
  - returns `reason: 'file-error'` when the file is missing.

## Non-functional requirements

- TypeScript strict everywhere. No `any`. `readonly` on every input interface field. Type-only imports use `import type`.
- No comments by default. The two security-relevant invariants where the WHY matters MAY have a comment: (1) the deep-frozen globals list in `sandbox.ts` (why each is included or excluded), (2) the worker-termination preemption claim in `Tier2Loader.tsx` (why `setTimeout` alone is not sufficient).
- Worker production bundle is **out of scope** — `Tier2Loader` accepts a `workerFactory` prop so tests inject a stub `Worker` (a class exposing `postMessage`, `terminate`, `addEventListener('message' | 'error')`). Phase 6C wires the production bundle.
- `apps/desktop/main/adapters/sandbox.ts` MUST NOT import from another deployable component's `src/`. It imports only Node built-ins and the AST-scan module sibling.
- `packages/chat-surface/src/surfaces/Tier2Loader.tsx` MUST NOT import from `electron`, `node:*`, `@0x-copilot/desktop`, or `@0x-copilot/chat-transport`. It imports `react` + `@0x-copilot/design-system` for the reconciliation factory map only.
- The AST allowlist is purely structural — no name-mangling, no shadow detection, no static-typing. Workarounds via aliased reassignment (`const f = fetch; f();`) are caught by the `Identifier`-reference pass, not by tracking the assignment. If a future bypass is found, the answer is to extend the scanner, not to defang the sandbox.
- React 19 functional components only. No class components in the new files.

## Interfaces consumed

- `SaaSRendererAdapter` from `packages/chat-surface/src/surfaces/SaaSRendererAdapter.ts` — the contract the sandboxed module must export.
- `@typescript-eslint/parser` — already in `apps/desktop` devDependencies (`8.59.3`). No new dependency.
- Node built-ins: `node:fs/promises` (loader), `node:path` (loader), `node:vm` (sandbox).
- React + `@0x-copilot/design-system` primitives (reconciliation map).

## Interfaces produced

```ts
// apps/desktop/main/adapters/ast-allowlist.ts
export const ALLOWED_IMPORTS: {
  readonly react: readonly ["createElement", "Fragment", "useState"];
  readonly "react-dom": readonly [];
  readonly "@0x-copilot/design-system": readonly [
    "Button",
    "Badge",
    "Card",
    "TextInput",
    "Select",
    "Switch",
    "Toggle",
    "Field",
    "IconButton",
    "StatusPill",
    "AppIcon",
    "HarnessRow",
    "StatusLine",
    "ConnectorChip",
    "classNames",
  ];
};
export type AstViolation = {
  readonly line: number;
  readonly kind: string;
  readonly detail: string;
};
export type AstScanResult =
  | { readonly ok: true }
  | { readonly ok: false; readonly violations: readonly AstViolation[] };
export function astAllowlistScan(source: string): AstScanResult;

// apps/desktop/main/adapters/sandbox.ts
export type SandboxCompileResult =
  | { readonly ok: true; readonly adapter: SaaSRendererAdapter }
  | {
      readonly ok: false;
      readonly reason: "syntax" | "runtime" | "shape";
      readonly detail: string;
    };
export function compileAdapter(source: string): SandboxCompileResult;

// apps/desktop/main/adapters/loader.ts
export type LoadResult =
  | { readonly ok: true; readonly source: string }
  | {
      readonly ok: false;
      readonly reason: "file-error";
      readonly detail: string;
    }
  | {
      readonly ok: false;
      readonly reason: "ast-violation";
      readonly violations: readonly AstViolation[];
    };
export function loadAdapterSource(opts: {
  readonly adapterDir: string;
  readonly scheme: string;
  readonly version: number;
}): Promise<LoadResult>;

// packages/chat-surface/src/surfaces/Tier2Loader.tsx
export interface Tier2WorkerLike {
  postMessage(value: unknown): void;
  terminate(): void;
  addEventListener(
    type: "message" | "error",
    listener: (event: { data: unknown }) => void,
  ): void;
  removeEventListener(
    type: "message" | "error",
    listener: (event: { data: unknown }) => void,
  ): void;
}
export interface Tier2LoaderProps {
  readonly adapterSource: string;
  readonly scheme: string;
  readonly version: number;
  readonly state?: unknown;
  readonly pendingDiff?: { readonly diff: unknown } | null;
  readonly workerFactory?: () => Tier2WorkerLike;
  readonly budgetMs?: number;
}
export function Tier2Loader(props: Tier2LoaderProps): ReactElement | null;
```

`packages/chat-surface/src/index.ts` is **not** modified by this agent (per the hard rule). The orchestrator surfaces the new `Tier2Loader` + types in its own integration pass. Other new exports go via direct module paths.

## Worker bundling strategy (for Phase 6C)

The Worker bundle that 6C builds must contain:

- A constrained `React` value (`createElement`, `Fragment`, `useState` only).
- A `DesignSystem` object exposing exactly the FR-2 allowlist of design-system functions.
- The message protocol: `{ kind: 'render', adapterSource, scheme, version, mode, payload } → { kind: 'rendered', tree } | { kind: 'failed', reason, detail }`.
- An in-worker AST scan (defense in depth — the host has already scanned, but the worker MUST refuse anything that fails its own scan in case 6C ever bypasses the host loader by mistake).
- A serializer that converts the React element tree to the FR-6 JSON shape with the same allowlist enforcement.

Phase 6A ships the protocol types and the host-side stub Worker used in tests. The real bundle lands in 6C.

## Open questions

- **Q1 — Why `@typescript-eslint/parser` over Acorn?** Both are present. The parser is already a desktop devDependency, supports JSX, supports TypeScript (in case a 6C-step compiles `.ts` adapters), and exposes ESTree-compatible AST nodes. Acorn would require `acorn-jsx`. The parser also gives consistent line numbers, which matters for the violation report. Adopted.
- **Q2 — Why exclude `useRef` / `useEffect` / `useLayoutEffect`?** D28 forbids side effects in adapter render. `useEffect` is by definition a side effect; `useLayoutEffect` is a DOM-measuring side effect; `useRef` is the common escape hatch for storing mutable state across renders, which is also a D28 violation. Adapters use `useState` only for pure derived UI state (open/closed disclosure, hover highlight).
- **Q3 — Is freezing the `console` adequate?** A real adversary could still attempt prototype pollution to escape the frozen object. The defense-in-depth answer is: the vm context is constructed per-compile, the adapter never returns a function the host invokes other than `renderCurrent` / `renderDiff`, and those calls happen inside the Worker, not in the host process. A poisoned `console.log` inside the worker cannot reach the main thread; it dies with `worker.terminate()`. Adopted.
- **Q4 — Why does the host re-render `null` on adapter throw rather than the tier-3 fallback?** Because the tier-3 fallback is a `TcSurfaceMount` concern (Phase 4-A), not a `Tier2Loader` concern. `Tier2Loader` is the inner unit; `TcSurfaceMount` is the outer host. When `Tier2Loader` returns `null`, `TcSurfaceMount`'s adapter call sees a falsy element and applies its existing tier-3 fallback path. Phase 6C wires the two together via the registry: a tier-2 registration is an adapter whose `renderCurrent` / `renderDiff` internally mount `<Tier2Loader …>`.
- **Q5 — Why does the AST scan not need to be sound under aliasing?** A `const f = fetch` line trips the `fetch` identifier scan at line 1 — the assignment is itself a reference. The scanner walks every `Identifier` reference, not just call sites. This is intentional and documented in `ast-allowlist.ts`.

## Done criteria

- [ ] All FRs met.
- [ ] `npm test --workspace @0x-copilot/desktop` passes (existing tests + 3 new adapter test files).
- [ ] `npm test --workspace @0x-copilot/chat-surface` passes (existing tests + `Tier2Loader.test.tsx`).
- [ ] `npm run typecheck --workspace @0x-copilot/desktop` passes.
- [ ] `npm run typecheck --workspace @0x-copilot/chat-surface` passes.
- [ ] `npm run lint --workspace @0x-copilot/desktop` passes.
- [ ] No new third-party dependency.
- [ ] No imports outside scope (the `vm` module, `node:fs/promises`, `node:path`, `@typescript-eslint/parser` are pre-existing).
- [ ] No edit to `packages/chat-surface/src/index.ts`.

## Notes for orchestrator review

- The AST scanner is the only file in 6A that is also consumed by 6D (`quality-gate/allowlist.ts` re-uses `astAllowlistScan` for Q2). 6D ships a thin wrapper. The single-source-of-truth for the allowlist is `ast-allowlist.ts` here.
- The Worker stub used in tests is a class in the test file, not a shipped file. Phase 6C will land `apps/desktop/main/adapters/worker-bundle/*` (the real bundle) and a `Worker` factory the renderer side calls. The `workerFactory` prop on `Tier2Loader` is the seam.
- `Tier2Loader` does not call `registerAdapter` itself. Registration is 6C's job. 6A only proves the inner loader, sandbox, and host wiring work in isolation.
- The reconciler intentionally strips `on*` props from the JSON VDOM during reconciliation, even though the worker should never serialise them. Belt and braces — the adapter has no way to express a click handler (the host owns Approve / Reject / Suggest changes per D28), so any `on*` prop in the tree is either a bug or an attempt to inject a serialised function. Either way, the host drops it.
