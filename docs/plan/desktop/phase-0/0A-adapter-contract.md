# Phase 0.A: adapter-contract

## Vision

Freeze the load-bearing contract that every renderer (tier-1, tier-2, tier-3) and every host (`TcSurfaceMount`) in the desktop app will speak. The contract is **pure render of state to JSX** (PRD D28): adapters never see a `Transport`, never call `fetch`, never touch `window`. All I/O and approval flow lives in the host, around the adapter's output.

Once frozen, this contract is the single source of truth that lets us:

- Sandbox tier-2 trivially — adapters have no privileged objects to leak (PRD D29).
- Substitute tiers from the host's perspective without per-tier branching (DRY).
- Add SaaS coverage without growing the host's surface area.

The spike-prep already shipped a `SurfaceRegistry` keyed on `{scheme → React.ComponentType}`. That shape was right for one substrate-evaluation session, wrong for the long run — components encode the action flow inside themselves. We rewrite the registry to `{scheme → SaaSRendererAdapter}` here, and keep the old `registerSurface` / `resolveSurface` exports alive (deprecated) so the spike-prep `EmailRenderer` still typechecks until Phase 4-a migrates it.

## Status

- Status: in-progress
- Agent slug: `adapter-contract`
- Branch: `desktop/phase-0-adapter-contract`
- Worktree: `.claude/worktrees/agent-aa1ef69c1f351d61e`
- Created: 2026-05-17

## Scope

**In scope** (files this agent owns):

- `docs/plan/desktop/phase-0/0A-adapter-contract.md` — this file.
- `packages/chat-surface/src/surfaces/SaaSRendererAdapter.ts` (NEW)
- `packages/chat-surface/src/surfaces/SaaSRendererAdapter.test.ts` (NEW)
- `packages/chat-surface/src/surfaces/SurfaceRegistry.ts` (REWRITE)
- `packages/chat-surface/src/surfaces/SurfaceRegistry.test.ts` (REWRITE)
- `packages/chat-surface/src/surfaces/types.ts` (MODIFY)
- `packages/chat-surface/src/surfaces/index.ts` (MODIFY)
- `packages/chat-surface/src/thread-canvas/TcSurfaceMount.tsx` (NEW STUB)
- `packages/chat-surface/src/thread-canvas/TcSurfaceMount.test.tsx` (NEW)
- `packages/chat-surface/src/thread-canvas/index.ts` (MODIFY — re-export `TcSurfaceMount`)
- `packages/chat-surface/src/index.ts` (APPEND-ONLY — clearly delimited Phase 0-A block)

**Out of scope** (do NOT touch):

- `packages/surface-renderers/**` — Phase 4-a rewrites `EmailRenderer` onto the new adapter contract.
- `packages/chat-surface/src/ports/**` — Agent 0-B's territory.
- `apps/desktop/**` — Agent 0-C's territory.
- `docs/architecture/desktop-app-rollout.md` — Agent 0-D deletes it.
- `packages/chat-surface/eslint.config.js` and any other ESLint config — Agent 0-D extends them.

## Functional requirements

- [ ] FR-1: `SaaSRendererAdapter<TResource, TDiff>` interface matches PRD §3.3 verbatim. `scheme: string`, `matches(uri): boolean`, `renderCurrent(state): ReactElement`, `renderDiff(diff): ReactElement`, `metadata: { origin, generatedAt?, generatorModel?, schemaVersion }`. All fields `readonly`. Defaults: `TResource = unknown`, `TDiff = unknown`.
- [ ] FR-2: `registerAdapter(adapter)` indexes the adapter under its `scheme`. Multiple versions for the same scheme are allowed and stored ordered by `metadata.schemaVersion` descending.
- [ ] FR-3: `resolveAdapter(uri)` returns: (a) the highest non-broken version whose `scheme` matches the URI's scheme **and** whose `matches(uri)` returns true; otherwise (b) the highest non-broken adapter registered under the wildcard scheme `'*'` whose `matches(uri)` returns true; otherwise `null`. Tier-3 fallback adapters MUST register under `scheme: '*'`. The wildcard is matched only after exact-scheme adapters have all missed (either no scheme match or every matching adapter's `matches(uri)` returned false or every version is broken).
- [ ] FR-4: `unregisterAdapter(scheme)` removes every version for that scheme. `unregisterAdapter(scheme, version)` removes only the specified version. Both are no-ops when nothing matches.
- [ ] FR-5: `markBroken(scheme, version, reason)` flags the version so `resolveAdapter` skips it. A subsequent `registerAdapter` of the same `{scheme, version}` clears the broken flag (re-installs the adapter; tier-2 hot-swap).
- [ ] FR-6: `clearRegistry()` empties everything. Test-only; callers in production code path are a lint smell.
- [ ] FR-7: Deprecated re-exports `registerSurface(scheme, Component)` / `resolveSurface(uri)` / `clearRegistry()` continue to typecheck so the spike-prep `EmailRenderer` and any `apps/{electron-spike,vscode-spike}` consumer keeps loading. Implementation: `registerSurface` constructs a minimal wrapping `SaaSRendererAdapter` (origin `'first-party'`, schemaVersion `1`) whose `renderCurrent` rejects (host-driven contract — old API has no current-state input) and `resolveSurface` returns the underlying component when one was registered via the deprecated path. Both carry `@deprecated` JSDoc pointing to `registerAdapter` / `resolveAdapter`. Phase 4-a removes these.
- [ ] FR-8: `SurfaceRendererProps` and `PendingDiff` continue to typecheck and export from `packages/chat-surface`. Both carry one-line `@deprecated` JSDoc.
- [ ] FR-9: `TcSurfaceMount` is a React component with props `{ uri: string; transport: Transport }`. It calls `resolveAdapter(uri)`; on miss, renders a fallback `<div>` saying "No adapter registered for {scheme}"; on hit, calls `adapter.renderCurrent({})` inside an error boundary + 100 ms render timeout, falling back to the same null-state UI on throw/timeout (with a `console.warn`). It does not yet wire up state fetching, `renderDiff`, approval, or hot-swap — those land in Phase 4-a. This is an explicit STUB.
- [ ] FR-10: Tests cover register / resolve / unregister / markBroken / version disambiguation / tier-3 wildcard fallback / `matches` filter / deprecated wrapper round-trip. `TcSurfaceMount` tests cover: no adapter → fallback, adapter found → calls renderCurrent, adapter throws → error boundary fallback, adapter exceeds timeout → fallback.

## Non-functional requirements

- Performance: `resolveAdapter` is hot-path; lookups are `O(versions for scheme)`, expected ≤ 3. No sorting on every call — versions are kept in sorted order at register time.
- Test coverage: every function in `SurfaceRegistry.ts` has at least one positive and one negative test; `TcSurfaceMount` covers all four branches above. Vitest, React Testing Library, query by role/text/test-id.
- Substrate-port discipline: this work is wholly inside `packages/chat-surface/src/`. No `window`, `document`, `fetch`, `localStorage`, `EventSource`. The existing chat-surface ESLint rule enforces.
- TypeScript strict everywhere. No `any` (use `unknown` and narrow). `readonly` on every interface field. Type-only imports use `import type`.
- React functional + hooks only.
- Comments: per PRD §6.1, default to none. One short line for the deprecated-wrapper trade-off (why `renderCurrent` rejects) and for the timeout race in `TcSurfaceMount` — both are non-obvious.

## Interfaces consumed

- `Transport` from `@enterprise-search/chat-transport` (only the type — no calls in this phase; `TcSurfaceMount` accepts it so Phase 4-a's wire-up has the prop already).
- `parseArtifactUri` from `../routing/uri/parser` is **not** used inside `resolveAdapter`: the parser whitelists `ArtifactScheme`, but tier-2 schemes (`hubspot-deal`, `linear-issue`, …) and the wildcard `'*'` aren't in that whitelist. The registry parses scheme from URI with a one-line `indexOf('://')` so it doesn't constrain what schemes tier-2 can register. The whitelist still gates the URL-parsing layer above us.
- `React`, `useEffect`, `useState`, error boundary primitive (class component is fine here; React error boundaries require a class — that's why D29's "no class components" rule has the documented exception "except for React error boundaries").

## Interfaces produced

```ts
// packages/chat-surface/src/surfaces/SaaSRendererAdapter.ts
export interface SaaSRendererAdapter<TResource = unknown, TDiff = unknown> {
  readonly scheme: string;
  readonly matches: (uri: string) => boolean;
  readonly renderCurrent: (state: TResource) => React.ReactElement;
  readonly renderDiff: (diff: TDiff) => React.ReactElement;
  readonly metadata: SaaSRendererAdapterMetadata;
}

export interface SaaSRendererAdapterMetadata {
  readonly origin: "first-party" | "agent-generated" | "community";
  readonly generatedAt?: string;
  readonly generatorModel?: string;
  readonly schemaVersion: number;
}

export const TIER3_SCHEME = "*";

// packages/chat-surface/src/surfaces/SurfaceRegistry.ts
export function registerAdapter(adapter: SaaSRendererAdapter): void;
export function resolveAdapter(uri: string): SaaSRendererAdapter | null;
export function unregisterAdapter(scheme: string, version?: number): void;
export function markBroken(
  scheme: string,
  version: number,
  reason: string,
): void;
export function clearRegistry(): void;

/** @deprecated Use registerAdapter. Removed in Phase 4-a. */
export function registerSurface(
  scheme: string,
  component: ComponentType<SurfaceRendererProps>,
): void;
/** @deprecated Use resolveAdapter. Removed in Phase 4-a. */
export function resolveSurface(
  uri: string,
): ComponentType<SurfaceRendererProps> | null;

// packages/chat-surface/src/thread-canvas/TcSurfaceMount.tsx
export interface TcSurfaceMountProps {
  readonly uri: string;
  readonly transport: Transport;
}
export function TcSurfaceMount(props: TcSurfaceMountProps): React.ReactElement;
```

## Open questions

- **Q1 — Versioning resolution order.** PRD §9.5.4 says "`resolveAdapter` returns the highest non-broken version." That's a per-scheme rule. What about between exact-scheme adapters from different origins (e.g. first-party `email` v1, community `email` v2)? **Resolution adopted:** treat origin as orthogonal to version. Highest non-broken `schemaVersion` wins regardless of origin. If we ever need to demote community adapters across the board, that's a registry-level filter (Phase 7 will add `metadata.origin` filtering when the opt-out lands), not a per-call branch. Recorded here so the orchestrator can override at merge time if needed.
- **Q2 — Should `matches(uri)` participate in version selection?** Yes. The registry walks versions highest-to-lowest and returns the first whose `matches(uri)` returns true. A high-version adapter that doesn't match this URI falls through to the next. This is the contract that lets a tier-2 v2 narrow its match (e.g. only `email://draft-*`) and a tier-2 v1 still serve `email://archive-*`. Recorded here because it's a load-bearing detail not spelled out in the PRD.
- **Q3 — `clearRegistry` in production?** Kept for test parity with the spike-prep API. Not currently used in production code. No lint rule blocks it; if we ever need to, that's a one-line `no-restricted-syntax` addition. Flagging for orchestrator visibility.
- **Q4 — Error boundary in chat-surface.** React error boundaries require class components, which the PRD §6.4 bans by default. The required reading explicitly mandates a 100 ms timeout + error boundary inside `TcSurfaceMount`, so the class is a contract-driven exception. Scoped to one private class inside `TcSurfaceMount.tsx`; no public surface. Recorded for the orchestrator.
- **Q5 — Render timeout semantics.** React renders are synchronous; you cannot interrupt a hung render with `setTimeout`. The 100 ms timeout therefore has to be a heuristic: the host kicks a `setTimeout(100ms)` immediately after the mount commits; if the timer fires before the adapter's effect-driven readiness signal (or as a wall-clock measurement against `performance.now()`), the host swaps to the fallback on the next render. In this stub we adopt the simpler shape: measure `performance.now()` around `renderCurrent` at the call site; if it exceeded the budget, swap to fallback on the next commit and log. A true preemptive timeout is impractical without a worker. Phase 4-a / Phase 6 may move tier-2 into a worker if profiling demands it. Recorded so the orchestrator and the Phase 4-a / 6 agents inherit the constraint, not invent it.

## Done criteria

- [ ] All FRs met
- [ ] `npm run typecheck --workspace @enterprise-search/chat-surface` passes
- [ ] `npm test --workspace @enterprise-search/chat-surface` passes
- [ ] `npm run lint --workspace @enterprise-search/chat-surface` passes
- [ ] `npm run typecheck --workspace @enterprise-search/surface-renderers` passes (spike-prep `EmailRenderer` still typechecks against the deprecated `SurfaceRendererProps`)
- [ ] No imports outside scope
- [ ] No bare browser primitives in chat-surface
- [ ] No new third-party dependency
- [ ] `packages/chat-surface/src/index.ts` Phase 0-A block is delimited so Agent 0-B's append can co-exist

## Notes for orchestrator review

- The deprecated `registerSurface` wrapper is the riskiest piece. It exists solely to keep the spike-prep `EmailRenderer` typechecking until Phase 4-a runs. The wrapper's `renderCurrent` deliberately rejects (throws) because the old API's `<EmailRenderer transport={...} />` shape pulls state internally — there is no `state` argument to forward. `resolveSurface` returns the wrapped component, not a `SaaSRendererAdapter`, so old callers get their old shape back; new callers using `resolveAdapter` get the new shape. The two paths are independent and don't cross-contaminate.
- The wildcard tier-3 scheme `'*'` is registry-level magic. We don't validate it against `isArtifactScheme` because that whitelist exists for the URL-parsing layer (chat-surface's `parseArtifactUri`), not the registry. Documented above (Interfaces consumed).
- The 100 ms render timeout in `TcSurfaceMount` is **measured, not preemptive** — see Q5. This matches what's physically possible inside React 19's synchronous render path without a worker. If the orchestrator wants a real preemption, Phase 6's tier-2 worker model is where that lands.
