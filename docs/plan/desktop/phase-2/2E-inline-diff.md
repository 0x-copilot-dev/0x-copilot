# Phase 2.E: tc-inline-diff

## Vision

`TcInlineDiff` already exists from Phase 0-A's spike-prep — a pure visual
primitive that displays a streaming / pending / approved / rejected card and
exposes `onApprove` / `onReject` callbacks. The host
(`TcSurfaceMount`) owns the action wiring; the adapter renders the diff body;
`TcInlineDiff` wraps the body in the standard chrome.

Phase 2-E hardens the primitive in two ways:

1. **State machine.** Today the consumer drives `state` as a freeform prop —
   any string the host wants. That works for the spike but lets bugs through:
   "accepted → streaming" is meaningless, "idle → accepted" loses the
   audit-relevant intermediate, etc. We add a pure transition function
   (`nextInlineDiffState`) and a `useInlineDiffReducer` hook that wraps it.
   Hosts that adopt the reducer get illegal-transition rejection for free;
   hosts that prefer to drive `state` directly are unaffected (the prop API
   is unchanged).
2. **Suggest-changes affordance.** PRD D28 says the host owns Approve /
   Reject / Suggest-changes. Approve and Reject already render in the
   `pending` state. We add `onSuggestChanges` + `suggestLabel` props so the
   host can opt-in to a third action. Same wiring story — `TcInlineDiff` is
   still a primitive and never calls anything itself.

The fixture file (`TcInlineDiff.fixtures.tsx`) gives reviewers a single
import that covers every state for visual-review without needing a Storybook
runtime in this phase. It is dev-only data, exported under a `__dev__`
namespace so the bundler can tree-shake it from production builds.

## Status

- Status: in-progress
- Agent slug: `tc-inline-diff`
- Branch: `desktop/phase-2-inline-diff`
- Worktree: `.claude/worktrees/agent-a240dcdc555ab842f`
- Created: 2026-05-17

## Scope

**In scope** (files this agent owns):

- `docs/plan/desktop/phase-2/2E-inline-diff.md` — this file.
- `packages/chat-surface/src/thread-canvas/TcInlineDiff.tsx` — extend (state
  machine, reducer hook, suggest-changes button, provenance dot color).
- `packages/chat-surface/src/thread-canvas/TcInlineDiff.fixtures.tsx` — NEW.
- `packages/chat-surface/src/thread-canvas/TcInlineDiff.test.tsx` — extend.
- `packages/chat-surface/src/thread-canvas/index.ts` — append a Phase 2-E
  delimited block.

**Out of scope** (do NOT touch):

- `packages/chat-surface/src/index.ts` — orchestrator owns the package-root
  exports. The thread-canvas barrel is re-exported from there via
  `./thread-canvas`, so additions here flow up automatically without
  editing the root index.
- `packages/chat-surface/src/thread-canvas/{TcSurfaceMount,ThreadCanvas,TcTabs,TcChat,TcSwimlanes}.{ts,tsx}` —
  other Phase 2 agents own those.
- `packages/surface-renderers/**` — Phase 4 territory.
- Anything in `apps/**` or `services/**`.

## Functional requirements

- [ ] FR-1 — `nextInlineDiffState(current, event)` is a pure function with
      no React imports. The full transition table: - `idle` + `'stream_start'` → `streaming` - `streaming` + `'stream_end'` → `pending` - `streaming` + `'cancel'` → `idle` - `pending` + `'approve'` → `accepted` - `pending` + `'reject'` → `rejected` - `accepted` + `'reset'` → `idle` - `rejected` + `'reset'` → `idle`
      Every other `(current, event)` pair throws
      `InvalidInlineDiffTransitionError` with a message naming both.
- [ ] FR-2 — `InlineDiffEvent` is exported as the union type
      `'stream_start' | 'stream_end' | 'cancel' | 'approve' | 'reject' | 'reset'`.
- [ ] FR-3 — `InvalidInlineDiffTransitionError` is exported as a value
      (subclass of `Error`) so hosts can `instanceof`-check it.
- [ ] FR-4 — `useInlineDiffReducer(initial?)` is a React hook that returns
      `{ state: InlineDiffState; dispatch: (event: InlineDiffEvent) => void }`.
      Default initial state is `'idle'`. `dispatch` delegates to
      `nextInlineDiffState`; illegal transitions therefore throw inside the
      reducer. The hook does no I/O (no fetch / window / etc.) — it is a
      pure local state machine.
- [ ] FR-5 — `TcInlineDiff` accepts new optional props
      `onSuggestChanges?: () => void` and `suggestLabel?: string`. When
      `onSuggestChanges` is provided AND the state is `'pending'`, a third
      button renders alongside Approve / Reject, labeled `suggestLabel`
      (default `"Suggest changes"`). When `onSuggestChanges` is omitted,
      no suggest button renders (backward-compatible).
- [ ] FR-6 — Provenance pill: when `provenance` prop is non-empty, a small
      pill renders in the header row with the provenance label and a small
      colored dot whose color matches the state accent. (Backward-compatible:
      the existing test only asserts the text content, not chrome.)
- [ ] FR-7 — `TcInlineDiff.fixtures.tsx` exports
      `inlineDiffFixtures: readonly { label: string; props: TcInlineDiffProps }[]`
      covering at minimum: idle, streaming (indeterminate), streaming
      (determinate 64%), pending (no provenance), pending (with provenance),
      pending (with onSuggestChanges), accepted, rejected. This is a dev-only
      data export and is re-exported as `__dev__inlineDiffFixtures` from the
      thread-canvas barrel.
- [ ] FR-8 — `thread-canvas/index.ts` keeps its pre-existing exports and
      adds a delimited `// === Phase 2-E inline-diff state-machine ===`
      block that re-exports `nextInlineDiffState`,
      `useInlineDiffReducer`, `InvalidInlineDiffTransitionError`, and the
      `InlineDiffEvent` type, plus the fixture array under the
      `__dev__inlineDiffFixtures` name.

## Non-functional requirements

- TypeScript strict; no `any`; `readonly` on every interface field.
- Type-only imports via `import type`.
- Functional React components only. The reducer hook uses `useReducer`
  internally (no class components, no `useState` chains).
- No comments by default. Two short non-obvious lines are permitted: one
  on `InvalidInlineDiffTransitionError` to document the
  fail-fast intent, one on the fixture file to mark it as dev-only.
- No new third-party dependency.
- Pre-existing tests in `TcInlineDiff.test.tsx` continue to pass without
  modification. Behaviour for the existing prop surface is unchanged.
- Pre-existing root-level export (`packages/chat-surface/src/index.ts`) is
  not edited — additions in the barrel flow up automatically.

## Animation

The streaming progress bar uses CSS `@keyframes` only (existing inline
styles already use a CSS-in-JS shape, but per the chat-surface rules we
keep zero JS animation loops — no `requestAnimationFrame`, no `setInterval`).
Indeterminate vs determinate is a CSS class / width swap.

## Interfaces consumed

- `react`: `useReducer`, `type CSSProperties`, `type ReactNode`. No DOM
  primitives (`window`, `document`, …) per chat-surface's ESLint boundary.

## Interfaces produced

```ts
// packages/chat-surface/src/thread-canvas/TcInlineDiff.tsx

export type InlineDiffState =
  | "idle"
  | "streaming"
  | "pending"
  | "accepted"
  | "rejected";

export type InlineDiffEvent =
  | "stream_start"
  | "stream_end"
  | "cancel"
  | "approve"
  | "reject"
  | "reset";

export class InvalidInlineDiffTransitionError extends Error {
  readonly from: InlineDiffState;
  readonly event: InlineDiffEvent;
  constructor(from: InlineDiffState, event: InlineDiffEvent);
}

export function nextInlineDiffState(
  current: InlineDiffState,
  event: InlineDiffEvent,
): InlineDiffState;

export function useInlineDiffReducer(initial?: InlineDiffState): {
  readonly state: InlineDiffState;
  readonly dispatch: (event: InlineDiffEvent) => void;
};

export interface TcInlineDiffProps {
  readonly state: InlineDiffState;
  readonly progressPercent?: number;
  readonly provenance?: string;
  readonly title: string;
  readonly description?: string;
  readonly onApprove?: () => void;
  readonly onReject?: () => void;
  readonly onSuggestChanges?: () => void;
  readonly approveLabel?: string;
  readonly rejectLabel?: string;
  readonly suggestLabel?: string;
}
```

## Open questions

1. **Reducer + prop coexistence.** Hosts that adopt `useInlineDiffReducer`
   pass `state` as `reducer.state`; hosts that drive `state` directly are
   unaffected. We do NOT make the reducer mandatory — that would force
   every consumer to migrate. The reducer is opt-in.
2. **Reducer throws inside React.** When `dispatch` is called with an
   illegal transition the reducer throws synchronously. This is intentional
   — it surfaces logic bugs at the call site instead of silently no-oping.
   The host can wrap dispatch in a try/catch if production needs softer
   handling (we expect approval flows to validate first).
3. **No `cancel` from `pending`.** PRD describes cancellation only during
   streaming. If product later wants "cancel a pending diff", that's a
   `reject` semantically — the transition stays explicit.
4. **No `streaming → streaming` self-transition.** Progress updates flow
   through the `progressPercent` prop, not through events. The state
   machine encodes only state changes, not progress updates.

## Done criteria

- [ ] All FRs met
- [ ] `npm run typecheck --workspace @enterprise-search/chat-surface` passes
- [ ] `npm test --workspace @enterprise-search/chat-surface` passes
- [ ] `npm run lint --workspace @enterprise-search/chat-surface` passes
- [ ] No imports outside scope
- [ ] No bare browser primitives in `TcInlineDiff.tsx` /
      `TcInlineDiff.fixtures.tsx`
- [ ] No new third-party dependency
- [ ] Pre-existing TcInlineDiff tests pass without modification
- [ ] Reducer hook does no I/O — verified by reading the source
