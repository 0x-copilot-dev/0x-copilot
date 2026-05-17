# Phase 2.B: thread-canvas

## Vision

`ThreadCanvas` is the surface where the agent's work actually lives — the
left two-thirds of the chat destination where SaaS artifacts render
under host-owned approval chrome. Chat is the right rail, swimlanes are
the floor strip, and the canvas itself is the artifact view. Staff-engineer
discipline applied to the primitives in this phase:

- **DRY.** One grid host (`ThreadCanvas`) composes three regions: a
  tabs-plus-mount column on the left, a chat slot on the right, and a
  swimlane strip across the bottom. There is one URI/tab list (passed in
  via props at this phase, owned by `ChatsDestination` in 2A); the same
  list drives `TcTabs` and the active URI consumed by `TcSurfaceMount`.
- **Substitution.** `TcSurfaceMount` consumes the frozen
  `SurfaceRegistry.resolveAdapter` port (Phase 0-A). It does not know
  which tier resolved the adapter; tier-1, tier-2, and tier-3 all enter
  through the same hole. The host owns Approve/Reject controls (D28) so
  the adapter contract stays pure render.
- **Simple & elegant.** Inline CSS grid, no state-management library, no
  extra layer of indirection between the registry resolution and the
  render call. The Phase 2-B placeholder for "no adapter" is a single
  card; tier-3 `GenericStructuredDiff` lands in Phase 4 and slots in via
  the registry without changes here.
- **Single source of truth.** Tab order, active URI, and close/pin state
  live with the parent destination (2A). `TcTabs` is presentational —
  given a list and an active URI, render. The mount reads the active
  URI from the parent.

`TcSurfaceMount` already exists from Phase 0-A as the host stub with
error-boundary + render-budget timer. This phase extends it (does not
replace it) to surface the host-owned approval controls per D28 and to
expose a clearer "no renderer registered" placeholder distinct from the
generic fallback (the latter is tier-3 in Phase 4).

`TcSwimlanes` (2C) and `TcChat` (2D) are sibling agents; this phase
ships slot placeholders so the grid renders end-to-end and the orchestrator
wires the real components at merge time.

## Status

- Status: in-progress
- Agent slug: `thread-canvas`
- Branch: `desktop/phase-2-thread-canvas`
- Worktree: `.claude/worktrees/agent-ab04adad5a14e4d28`
- Created: 2026-05-17

## Scope

**In scope** (files this agent owns):

- `docs/plan/desktop/phase-2/2B-thread-canvas.md` — this file.
- `packages/chat-surface/src/thread-canvas/ThreadCanvas.tsx` — NEW. The
  grid host: `[tabs + SurfaceMount | TcChat-slot] [TcSwimlanes-slot]`.
- `packages/chat-surface/src/thread-canvas/ThreadCanvas.test.tsx` — NEW.
- `packages/chat-surface/src/thread-canvas/TcTabs.tsx` — NEW. The tab
  strip across the top of the canvas region.
- `packages/chat-surface/src/thread-canvas/TcTabs.test.tsx` — NEW.
- `packages/chat-surface/src/thread-canvas/TcSurfaceMount.tsx` — MODIFY.
  Add host-owned Approve/Reject controls (D28) around the adapter render;
  rename the existing "no adapter" fallback test-id to a clearer
  Phase-2 placeholder slot (`surface-placeholder`). Preserve the existing
  error boundary + render-budget timer + warn-spy test surface.
- `packages/chat-surface/src/thread-canvas/TcSurfaceMount.test.tsx` —
  EXTEND. Keep all existing tests (renamed test-id), add tests for the
  Approve/Reject visibility logic.
- `packages/chat-surface/src/thread-canvas/index.ts` — APPEND ONLY. Add
  a delimited Phase 2-B block re-exporting `ThreadCanvas`, `TcTabs`, and
  their prop types. Existing `TcInlineDiff` / `TcSurfaceMount` exports
  stay.

**Out of scope** (do NOT touch):

- `packages/chat-surface/src/index.ts` — orchestrator wires the package
  re-exports at merge time (per the prompt).
- `packages/chat-surface/src/destinations/**` — Agent 2A's territory.
- `packages/chat-surface/src/thread-canvas/TcSwimlanes.tsx` — Agent 2C
  creates it.
- `packages/chat-surface/src/thread-canvas/TcChat.tsx` and the
  `composer/**` tree — Agent 2D creates them.
- `packages/chat-surface/src/thread-canvas/TcInlineDiff.tsx` — Agent 2E
  may iterate on it; this phase imports nothing from it directly.
- `packages/chat-surface/src/surfaces/**` — frozen Phase 0-A contract;
  no extension here.
- Anything in `apps/**`.

## Functional requirements

- [ ] FR-1: `ThreadCanvas` renders a CSS grid with two rows
      (`1fr auto` — canvas body on top, swimlane slot on bottom) and two
      columns (`1fr 360px` — canvas on left, TcChat slot on right). The
      bottom row spans both columns. The two slot regions render with
      `data-testid="swimlanes-slot"` and `data-testid="tc-chat-slot"` so
      orchestrator-merge wiring (2C / 2D) lands in clearly-marked holes.
- [ ] FR-2: `ThreadCanvas` accepts `{ conversationId: string }` plus the
      tab list and active URI as props (so 2A owns the tab state). The
      canvas body renders `TcTabs` on top and `TcSurfaceMount` directly
      below; `TcSurfaceMount` receives the active URI.
- [ ] FR-3: `TcTabs` renders a horizontal scrolling strip of tabs. Each
      tab is keyboard-reachable (`tabIndex={0}`, role="tab", Enter/Space
      activates). The active tab carries `aria-current="page"` and a
      visual treatment. Clicking the tab calls `onActivate(uri)`;
      clicking the close button calls `onClose(uri)` and does NOT
      activate the tab (event stopPropagation). Close button is rendered
      only for non-pinned tabs.
- [ ] FR-4: `TcTabs` overflows horizontally (`overflow-x: auto`), so a
      large number of tabs scrolls rather than wrapping. The active tab
      indicator is a 2 px bottom border in the lime accent; the rest of
      the tab is muted text on the dark surface.
- [ ] FR-5: `TcSurfaceMount` reads `resolveAdapter(uri)` (existing). When
      the registry returns null, render a placeholder card with
      `data-testid="surface-placeholder"` and a "No renderer registered"
      message that includes the scheme. (Tier-3 `GenericStructuredDiff`
      lands in Phase 4 and registers under `*`; once registered, the
      registry returns it for any URI, so this placeholder is unreachable
      after Phase 4. Until then, this is the visible state when no
      adapter is registered.)
- [ ] FR-6: `TcSurfaceMount` accepts optional `onApprove?: () => void`,
      `onReject?: () => void`, and `pendingDiff?: unknown | null` props.
      The host-owned Approve / Reject buttons render OUTSIDE the
      adapter's output (D28: adapter is pure render, host owns action
      chrome). The buttons only render when `pendingDiff` is non-null.
- [ ] FR-7: Preserve existing Phase 0-A behavior: error boundary on
      adapter render, 100 ms render-budget timer, warning logs on throw
      or timeout, fallback to the placeholder on either condition.
- [ ] FR-8: Public exports: append a delimited Phase 2-B block in
      `packages/chat-surface/src/thread-canvas/index.ts` re-exporting
      `ThreadCanvas`, `TcTabs`, `ThreadCanvasProps`, `TcTabsProps`,
      `TcTab`. Existing `TcInlineDiff`, `TcSurfaceMount` exports stay.

## Non-functional requirements

- TypeScript strict; no `any` (use `unknown` and narrow). `readonly` on
  all interface fields.
- React functional components only (the existing error-boundary class
  in `TcSurfaceMount` stays — error boundaries require a class per
  PRD §6.4).
- Substrate-port discipline: no `window` / `document` / `fetch` /
  `localStorage` / `EventSource` references. Enforced by the existing
  ESLint rule.
- Inline styles only — `style={{}}` with the dark palette already
  established by `TcInlineDiff` (lime accent `#c2ff5a`, card-bg
  `#181a1c`, card-border `#2a2d31`, text-hi `#f4f5f6`, text-lo
  `#9aa0a6`). No new CSS files.
- No `useEffect` for derived state; the active URI is computed inline
  from props.
- Test coverage: one `.test.tsx` per component. Tests assert by role
  and accessible text or `data-testid`; no class-based queries; no
  snapshot tests.
- No comments by default; one short line only when the WHY is
  non-obvious.

## Interfaces consumed

- `resolveAdapter` from `../surfaces/SurfaceRegistry` (already used by
  the existing `TcSurfaceMount`).
- `SaaSRendererAdapter` type from `../surfaces/SaaSRendererAdapter`.
- `Transport` type from `@enterprise-search/chat-transport` (the
  existing `TcSurfaceMount` carries this prop; preserved unchanged).
- `React` types (`ReactElement`, `ReactNode`, `CSSProperties`).

This phase does NOT consume `Router` directly — tab activation is a
parent-controlled callback. The destination (2A) wires `onActivate` to
the router.

## Interfaces produced

```ts
// packages/chat-surface/src/thread-canvas/TcTabs.tsx (NEW)

export interface TcTab {
  readonly uri: string;
  readonly title: string;
  readonly pinned?: boolean;
}

export interface TcTabsProps {
  readonly tabs: readonly TcTab[];
  readonly activeUri: string;
  readonly onActivate: (uri: string) => void;
  readonly onClose: (uri: string) => void;
}

export function TcTabs(props: TcTabsProps): ReactElement;
```

```ts
// packages/chat-surface/src/thread-canvas/ThreadCanvas.tsx (NEW)

export interface ThreadCanvasProps {
  readonly conversationId: string;
  readonly tabs: readonly TcTab[];
  readonly activeUri: string;
  readonly onActivateTab: (uri: string) => void;
  readonly onCloseTab: (uri: string) => void;
  readonly transport: Transport;
  readonly onApprove?: () => void;
  readonly onReject?: () => void;
  readonly pendingDiff?: unknown | null;
}

export function ThreadCanvas(props: ThreadCanvasProps): ReactElement;
```

```ts
// packages/chat-surface/src/thread-canvas/TcSurfaceMount.tsx (MODIFIED)
// New optional props; existing required props unchanged.

export interface TcSurfaceMountProps {
  readonly uri: string;
  readonly transport: Transport;
  readonly onApprove?: () => void;
  readonly onReject?: () => void;
  readonly pendingDiff?: unknown | null;
}
```

## Open questions

1. **Tab list ownership.** Per the orchestrator prompt, 2A owns
   `ChatsDestination` and the tab list lives there. This phase accepts
   the tab list as props for clean substitution. If a future phase
   needs cross-destination tab persistence, the right home is a hook
   in 2A (which then passes the same shape into `ThreadCanvas`); no
   change here.

2. **`pendingDiff` type.** Today the diff payload's shape is
   adapter-specific (each tier-1 renderer defines its own `TDiff`).
   Until the host fetches diffs over MCP (Phase 4-A's adapter-contract
   plumbing and Phase 5's auth), Phase 2-B has nothing to pass into
   `renderDiff`. The prop is typed `unknown | null` and only used to
   gate the visibility of the Approve / Reject buttons. Phase 4-A will
   tighten this type at the same time it wires the real diff source.

3. **TcChat slot column width.** 360 px matches the design's
   conversation-rail width. 2D may iterate; the slot's
   `data-testid="tc-chat-slot"` is the stable handle.

4. **Right-side TcChat or right-rail TcChat?** The PRD §3.2 puts
   `TcChat` inside the thread-canvas grid (per row "2D"). The shell's
   `RightRail` (Phase 1-B) is a different thing — the workspace-level
   "Atlas conversation" rail. `ThreadCanvas`'s TcChat slot is the
   per-thread chat. This phase mounts the per-thread slot inside the
   grid; 2D fills it.

## Done criteria

- [ ] All FRs met
- [ ] `npm run typecheck --workspace @enterprise-search/chat-surface`
      passes
- [ ] `npm test --workspace @enterprise-search/chat-surface` passes
      (including all pre-existing Phase 0-A `TcSurfaceMount` tests)
- [ ] `npm run lint --workspace @enterprise-search/chat-surface` passes
- [ ] No imports outside scope
- [ ] No bare browser primitives anywhere in this scope — enforced by
      the existing ESLint rule
- [ ] No new third-party dependency
- [ ] `packages/chat-surface/src/thread-canvas/index.ts` only gains the
      delimited Phase 2-B block; all pre-existing exports untouched
- [ ] `packages/chat-surface/src/index.ts` NOT modified (orchestrator
      wires re-exports at merge time)

## Notes for orchestrator review

- The existing `TcSurfaceMount` fallback used
  `data-testid="tc-surface-mount-fallback"`. Phase 2-B renames it to
  `surface-placeholder` so the role is unambiguous (the fallback now
  serves two callers: "no adapter for scheme" and "adapter threw /
  timed out"). Existing tests are updated to query the new id.
- Approve / Reject buttons render only when `pendingDiff` is non-null.
  Until Phase 4-A wires the real diff source, callers will not pass
  this prop and the buttons stay hidden — matching the Phase-2 surface
  area in the design.
- `TcSurfaceMount` calls `adapter.renderCurrent({})` (Phase 0-A's
  placeholder argument). Phase 4-A wires the real state source. This
  phase does not change that argument shape.
- The grid template intentionally uses `auto` for the bottom row so the
  swimlane strip can be height-driven by its own content in 2C. The
  right column is a fixed 360 px so 2D's TcChat has predictable space.
- The lines the orchestrator needs to append to
  `packages/chat-surface/src/index.ts` at merge time are reported in
  the agent return message.

### Lines orchestrator adds to `packages/chat-surface/src/index.ts`

Append a new delimited Phase 2-B block immediately after the Phase 0-A
adapter contract block:

```ts
// === Phase 2-B thread-canvas ===
export {
  ThreadCanvas,
  type ThreadCanvasProps,
  TcTabs,
  type TcTabsProps,
  type TcTab,
} from "./thread-canvas";
// === end Phase 2-B ===
```
