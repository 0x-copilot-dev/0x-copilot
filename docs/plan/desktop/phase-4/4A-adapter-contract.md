# Phase 4.A: adapter-contract

## Vision

Freeze the load-bearing `SaaSRendererAdapter` contract that every renderer (tier-1 hand-built, tier-2 agent-generated, tier-3 generic fallback) and every host (`TcSurfaceMount`) speaks for the rest of Phase 4 and beyond. The contract is **pure render of state to JSX** (PRD D28). All I/O — fetch state, compute diff, apply, approve, reject, suggest-changes — lives in the host around the adapter's output.

Phase 0-A scaffolded the contract and a measurement-style render budget + error boundary. Phase 4-A:

- Documents `PURE RENDER ONLY (D28)` on the contract surface itself so generated tier-2 reviewers can read it without leaving the file.
- Hardens `SurfaceRegistry` resolution semantics with a re-read against PRD §3.3 and §9.5.4 (version disambiguation, hot-swap idempotency, exact-scheme exhaustion before tier-3 fallback).
- Wires the host-side glue in `TcSurfaceMount`: resolve adapter → call `renderCurrent` or `renderDiff` → wrap with host-owned `Approve` / `Reject` / `Suggest changes` controls (D28) → on adapter throw or 100 ms wall-clock budget overrun, fall back to the tier-3 adapter (D29) rather than the empty placeholder, so the user always sees something useful.
- Extends `@0x-copilot/surface-renderers`'s ESLint rule to ban `Transport`, `fetch`, `XMLHttpRequest`, `EventSource`, `WebSocket`, `window`, `document`, `localStorage`, `sessionStorage`, `navigator.clipboard.write*`, `document.cookie`, and dynamic `import()` of non-allowlisted modules — enforcing D28/D29 at lint time for tier-1. Tier-2's AST scanner is a separate Phase 6 deliverable.

The contract that ships here does not change in Phase 6 / 7. Only its consumers grow.

## Status

- Status: in-progress
- Agent slug: `phase-4-adapter-contract`
- Branch: `desktop/phase-4-adapter-contract`
- Worktree: `.claude/worktrees/agent-a3b503f3e840b02a9`
- Created: 2026-05-17

## Scope

**In scope** (files this agent owns):

- `docs/plan/desktop/phase-4/4A-adapter-contract.md` — this file.
- `packages/chat-surface/src/surfaces/SaaSRendererAdapter.ts` (DOC FREEZE — add the PURE RENDER ONLY contract block; signatures already match PRD §3.3 from Phase 0-A).
- `packages/chat-surface/src/surfaces/SurfaceRegistry.ts` (NO API CHANGE; minor doc, internal cleanup).
- `packages/chat-surface/src/surfaces/SurfaceRegistry.test.ts` (EXTEND — version disambiguation explicit, broken→fallback to tier-3, hot-swap visibility).
- `packages/chat-surface/src/thread-canvas/TcSurfaceMount.tsx` (EXTEND — host-owned controls, tier-3 fallback path, broader props surface).
- `packages/chat-surface/src/thread-canvas/TcSurfaceMount.test.tsx` (EXTEND — controls round-trip, tier-3 fallback after throw/timeout, renderDiff path).
- `packages/surface-renderers/eslint.config.js` (EXTEND — additional bans listed in §4 of this doc).
- `packages/surface-renderers/src/__lint-negatives__/` (NEW — ESLint negative-test files; excluded from tsc / vitest discovery).
- `packages/surface-renderers/tsconfig.json` (MODIFY — exclude `__lint-negatives__`).
- `packages/surface-renderers/vitest.config.ts` (MODIFY — exclude `__lint-negatives__`).

**Out of scope** (do NOT touch):

- `packages/surface-renderers/src/email/**` — Phase 4-C migrates `EmailRenderer` onto the adapter contract; Phase 4-A leaves the deprecated `registerSurface` wrapper intact.
- `packages/chat-surface/src/index.ts` — barrel exports are stable; if new public symbols are added in 4-A they are listed here for the orchestrator to surface.
- `apps/**` — none of them depend on the new control props yet.
- Tier-3 implementation — Phase 4-B owns `GenericStructuredDiff` and registers it under `scheme: '*'`. Phase 4-A only ensures the host falls through to whatever is registered under `'*'`.

## Functional requirements

- [x] FR-1: `SaaSRendererAdapter<TResource, TDiff>` interface continues to match PRD §3.3 verbatim. File header documents the D28 contract: adapters MUST NOT call `Transport`, `fetch`, `XMLHttpRequest`, `EventSource`, `WebSocket`, or touch `window` / `document` / `localStorage` / `sessionStorage` / `history` / `navigator`. ESLint enforces for tier-1; AST scanner (Phase 6) enforces for tier-2; the file comment is the canonical English statement.
- [x] FR-2: `SurfaceRegistry` semantics (already implemented; verified by extended tests):
  - `registerAdapter` keeps versions for a scheme sorted by `metadata.schemaVersion` desc.
  - `resolveAdapter(uri)` walks the highest-version-first list for the URI's scheme; for each entry, skips broken, then evaluates `matches(uri)`; returns the first hit; otherwise walks the wildcard list with the same rule.
  - `unregisterAdapter(scheme)` removes all versions; `unregisterAdapter(scheme, version)` removes one.
  - `markBroken(scheme, version, reason)` hides that version; a subsequent `registerAdapter` of `{scheme, version}` re-installs and clears the broken flag (hot-swap).
- [x] FR-3: `TcSurfaceMount` resolves the adapter for the URI, calls `renderCurrent` (no `pendingDiff` prop) or `renderDiff` (with `pendingDiff` prop), wraps the output in an error boundary and a 100 ms wall-clock budget. On throw or budget overrun: re-resolves the URI against the wildcard (tier-3) and renders that adapter's output. On both adapter and tier-3 absence: renders the existing `surface-placeholder` empty state — never crashes.
- [x] FR-4: `TcSurfaceMount` surrounds the rendered output with host-owned `Approve` / `Reject` / `Suggest changes` controls. Controls are rendered only when a `pendingDiff` is present. The adapter's own JSX never includes these buttons (D28). The controls fire `onApprove(diffId)`, `onReject(diffId)`, `onSuggestChanges(diffId)` from `props`. The same control band wraps both the adapter render path and the tier-3 fallback path — the host's UX does not depend on which tier resolved.
- [x] FR-5: ESLint rule for `packages/surface-renderers/src/**` (excluding `__lint-negatives__/`):
  - Existing global bans (`window`, `document`, `history`, `navigator`, `location`, `localStorage`, `sessionStorage`, `fetch`, `EventSource`, `XMLHttpRequest`, `WebSocket`, `crypto`) preserved.
  - Added globals: `clipboard`.
  - Member-expression ban: `document.cookie`, `navigator.clipboard.writeText`, `navigator.clipboard.write`.
  - Import bans: `@0x-copilot/chat-transport`, `@0x-copilot/chat-transport/*`, plus the new allowlist for `@0x-copilot/chat-surface` (allowed for design tokens / `TcInlineDiff`), `react`, `react-dom`, `@0x-copilot/design-system`. Dynamic `import()` of any other specifier fails.
  - Existing `chat-surface/shell` import-ban preserved.
- [x] FR-6: ESLint negative-test files in `packages/surface-renderers/src/__lint-negatives__/` deliberately violate each ban. A script (`lint-negatives.sh`) runs ESLint on the directory and asserts every file errors out. Wired into a `npm run lint:negatives --workspace @0x-copilot/surface-renderers` script and exercised in this phase's verification (not as a vitest test; ESLint is the assertion).
- [x] FR-7: New `TcSurfaceMount` props remain optional so the existing Phase 0-A consumers (`<TcSurfaceMount uri transport />`) keep compiling. Adding `pendingDiff`, `state`, `onApprove`, `onReject`, `onSuggestChanges` is additive.
- [x] FR-8: Tests cover: hot-swap (A → A' visible on re-resolve), miss → tier-3 fallback, `markBroken` → tier-3 fallback, render-with-timeout fallback to tier-3, error boundary fallback to tier-3, version disambiguation (v2 wins, fall through to v1 when v2's `matches` rejects, `markBroken(v2)` → v1 resolves). `TcSurfaceMount` tests cover: controls absent when no `pendingDiff`, controls present + firing handlers when `pendingDiff`, controls present around tier-3 fallback, tier-3-and-empty case → placeholder.

## Non-functional requirements

- Performance: resolution path is O(versions for scheme) + O(wildcard versions); both bounded to <5 in practice.
- Substrate-port discipline: nothing added that touches `window` / `document` / `fetch` / `localStorage` / `EventSource`. Existing chat-surface ESLint enforces.
- TypeScript strict everywhere. No `any`. `readonly` on every interface field. Type-only imports use `import type`.
- React functional + hooks only. The single class component in `TcSurfaceMount` is the documented error-boundary exception (PRD §6.4).
- Comments: per PRD §6.1, default to none. Brief lines for the two non-obvious behaviors are allowed (timeout race seam, tier-3 re-resolve order).

## Interfaces consumed

- `Transport` from `@0x-copilot/chat-transport` — type only; `TcSurfaceMount` forwards it for Phase 4-onwards consumers. Phase 4-A does not call it.
- `PendingDiff` from `packages/chat-surface/src/surfaces/types.ts` — used as the type of the `pendingDiff.meta` prop on `TcSurfaceMount`.
- `TcInlineDiff` from `packages/chat-surface/src/thread-canvas/TcInlineDiff.tsx` — **not** used in 4-A. The Approve / Reject / Suggest band is a plain control row; per-renderer inline-diff cards are the adapter's job (when applicable). Tier-1 renderers may use `TcInlineDiff` inside their `renderDiff` if it fits their layout.

## Interfaces produced

```ts
// packages/chat-surface/src/thread-canvas/TcSurfaceMount.tsx
export interface TcSurfaceMountProps {
  readonly uri: string;
  readonly transport: Transport;
  readonly state?: unknown;
  readonly pendingDiff?: {
    readonly diff: unknown;
    readonly meta: PendingDiff;
  } | null;
  readonly onApprove?: (diffId: string) => void;
  readonly onReject?: (diffId: string) => void;
  readonly onSuggestChanges?: (diffId: string) => void;
}
```

No new exports from `packages/chat-surface/src/index.ts`. The Phase 0-A block already re-exports `TcSurfaceMount` and the adapter registry surface; the new optional props are picked up automatically.

## ESLint rule details

`packages/surface-renderers/eslint.config.js`:

- Globals ban (`no-restricted-globals`): `window`, `document`, `history`, `navigator`, `location`, `localStorage`, `sessionStorage`, `fetch`, `EventSource`, `XMLHttpRequest`, `WebSocket`, `crypto`, `clipboard`.
- Property-access ban (`no-restricted-syntax`): `MemberExpression[object.name='document'][property.name='cookie']`, `MemberExpression[object.object.name='navigator'][object.property.name='clipboard']` (matches `navigator.clipboard.*`).
- Import ban (`no-restricted-imports`):
  - Bans `@0x-copilot/chat-transport` (D28 — adapters do not call Transport).
  - Bans `apps/*`, `@0x-copilot/frontend`, `@0x-copilot/desktop`.
  - Bans `@0x-copilot/chat-surface/shell` (renderers are leaves, not layout).
- Dynamic-import ban (`no-restricted-syntax`): `ImportExpression` is allowed only when its argument is a string literal in `['react', 'react-dom', '@0x-copilot/design-system', '@0x-copilot/chat-surface']`. Anything else errors.

Allowlist next to the rule, documented in the same file's header comment block:

```
ALLOWED IMPORTS (static or dynamic):
  - react
  - react-dom
  - @0x-copilot/design-system
  - @0x-copilot/chat-surface   (design tokens, TcInlineDiff primitive)
```

## Open questions

- **Q1 — Should the host control band live inside `TcSurfaceMount` or a sibling `TcHostControls` component?** Adopted: inside `TcSurfaceMount`. The controls are the host's responsibility; lifting them to a sibling would require a second mounting point in every consumer. Phase 4-A keeps it together. If a later phase needs to reuse the control row outside the surface mount, factor it out then.
- **Q2 — Tier-3 fallback re-entrancy.** If the tier-3 adapter itself throws or times out, we render the static placeholder. We do not re-resolve again. Recorded so the orchestrator can flip this to "render plain JSON dump" if tier-3 reliability ever degrades.
- **Q3 — Suggest-changes label and behavior.** The host control is a button with `Suggest changes` text that calls `onSuggestChanges(diffId)`. The downstream UX (modal for feedback, regen queue) is Phase 5 / 6 territory; the contract is just the callback. Recorded for the orchestrator.
- **Q4 — Lint negatives discoverability.** ESLint negative tests live under `__lint-negatives__/` and are excluded from both `tsc` and `vitest`. They are run via `npm run lint:negatives --workspace @0x-copilot/surface-renderers`, which calls `eslint src/__lint-negatives__` and asserts the exit code is non-zero. This avoids polluting unit test counts while still exercising the rule. Recorded so 4-B / 4-C / 4-D / 4-E / 4-F agents know the lint exit semantics.

## Done criteria

- [x] All FRs met.
- [x] `npm test --workspace @0x-copilot/chat-surface` passes (existing 135 tests + new tests).
- [x] `npm run typecheck --workspace @0x-copilot/chat-surface` passes.
- [x] `npm run lint --workspace @0x-copilot/chat-surface` passes.
- [x] `npm run typecheck --workspace @0x-copilot/surface-renderers` passes (deprecated `EmailRenderer` still typechecks).
- [x] `npm run lint --workspace @0x-copilot/surface-renderers` passes on production code.
- [x] `npm run lint:negatives --workspace @0x-copilot/surface-renderers` fails as expected (each negative file errors).
- [x] No new third-party dependency.
- [x] No imports outside scope.

## Notes for orchestrator review

- The registry already had hot-swap, version disambiguation, `markBroken`, and wildcard fallback from Phase 0-A. Phase 4-A's net change to `SurfaceRegistry.ts` is minimal — the freeze is mostly contract-clarity work (doc + verified semantics via extended tests).
- The host's tier-3 fallback in `TcSurfaceMount` is the meat of 4-A. Before this change, an adapter that threw or timed out fell back to the empty placeholder. Now it falls back to the tier-3 adapter (if registered), so end users see a structured-diff card even when the tier-1 / tier-2 renderer breaks.
- The ESLint rule is the lint-time enforcement of D28 for tier-1 only. Tier-2 enforcement is the AST scanner in Phase 6D. The two are intentionally separate: ESLint is the developer-feedback path; the AST scanner is the install-time safety gate.
- The deprecated `registerSurface` / `resolveSurface` exports remain alive. Phase 4-C is the only place that should remove them; doing it here would break the spike-prep `EmailRenderer` and Phase 4-A is supposed to ship the contract, not migrate consumers.
