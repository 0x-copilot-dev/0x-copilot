# Phase 2.C: tc-swimlanes (Time Machine)

## Vision

Atlas's Time Machine is **not a separate destination, not a separate URI scheme,
not a separate file** — it is the swimlane mode inside `ThreadCanvas` (D25).
The swimlane component renders the per-surface bead timeline for a single run,
gives the user a playhead they can scrub with mouse / keyboard / transport
controls, and exposes the two structural recovery actions the time machine
needs: "Branch from here" (fork a new run at the chosen timestamp) and
"Restore this state" (rewind the existing run). The pinned-bead mechanism is
local product state — small enough to persist via `KeyValueStore` keyed by
run, big enough to need to survive a reload.

The bead source-of-truth is the backend run-events stream (`RuntimeEventEnvelope`).
There is no client-side event bus, no renderer-to-renderer push (D26): every
renderer (TcSwimlanes, TcChat, surface renderers) observes the same stream and
reacts independently. That keeps the event model single-source-of-truth on the
backend and prevents the "two truths" failure mode the prototype would have
fallen into if scrub state were broadcast over an in-process bus.

Lane assignment is mechanical: each bead's lane is the surface URI's scheme
(`email`, `sheet-row`, `slack-message`, …) when the payload carries a surface
URI; otherwise the bead lands in a synthetic `system` lane. This matches D14
(renderer-owned snapshot for scrub) — lanes are display grouping, not
semantic ownership.

Staff-engineer take on the playhead model: the playhead has exactly two
states — `"now"` (snapped, follows the stream tail; latest beads keep
arriving) and `{ at: timestamp }` (scrubbed off-now, frozen at a point in
time). Everywhere else in the surface that needs to ask "are we live?" reads
the same union — no boolean flags, no `playhead === Infinity` sentinels, no
`scrubbedAt: number | null`. The scrubbing API surface is `onScrubChange(t |
"now")`; consumers (TcChat ghost-message renderer, Phase 2-D) match the same
discriminator.

## Status

- Status: in progress
- Agent slug: `swimlanes`
- Branch: `desktop/phase-2-swimlanes`
- Worktree: `.claude/worktrees/agent-a8bf8eb298e89a14b`
- Created: 2026-05-17

## Scope

**In scope** (files this agent owns):

- `docs/plan/desktop/phase-2/2C-swimlanes.md` — this file.
- `packages/chat-surface/src/thread-canvas/TcSwimlanes.tsx` — the Time
  Machine swimlane component.
- `packages/chat-surface/src/thread-canvas/TcSwimlanes.test.tsx` — unit
  tests.
- `packages/chat-surface/src/thread-canvas/index.ts` — append the
  delimited Phase 2-C export block (`TcSwimlanes`, `TcSwimlanesProps`,
  `Playhead`).

**Out of scope** (do NOT touch):

- `packages/chat-surface/src/index.ts` — the top-level barrel is owned
  by the orchestrator. The phase-2-C export line for the surface to
  re-export is listed under "Coordination" below.
- `packages/chat-surface/src/thread-canvas/TcInlineDiff.tsx` /
  `TcSurfaceMount.tsx` — frozen.
- `ThreadCanvas` / `TcChat` / `TcTabs` — Phase 2-B / 2-D territory.
- Any provider, port, or shell file.
- Backend endpoints for branch/restore — backend doesn't exist yet;
  this phase calls `Transport.request` with the placeholder paths
  documented under FR-8 and mocks them in tests.

## Functional requirements

- [ ] FR-1 — `TcSwimlanes` is a functional React component. Props:
  - `runId: string` — the run whose events to subscribe to.
  - `onScrubChange?: (playhead: Playhead) => void` — fires whenever the
    playhead moves. `Playhead = "now" | { at: number }` where `at` is a
    millisecond epoch derived from `event.created_at`.
  - `onBranch?: (atMillis: number) => void` — fires after a successful
    `Transport.request` to the branch endpoint.
  - `onRestore?: (atMillis: number) => void` — fires after a successful
    `Transport.request` to the restore endpoint.
- [ ] FR-2 — On mount, the component subscribes to the run stream via
      `useTransport().subscribeServerSentEvents({ path:
"/v1/agent/runs/{runId}/stream", ... })`. The subscription is torn
      down on unmount and re-established when `runId` changes. Bead state
      is held internally; each received `RuntimeEventEnvelope` (validated
      via `isRuntimeEventEnvelope`) becomes one bead, deduped by
      `event_id`. Malformed messages are silently dropped — the swimlane
      is a viewer, not a parser.
- [ ] FR-3 — Lane assignment: a bead's lane is `surfaceSchemeOf(event)`.
  - If `event.payload.surface_uri` is a string with a `scheme://body`
    shape, the scheme is the lane.
  - Otherwise the bead lands in the `system` lane.
  - Lanes render in stable order: schemes are sorted lexicographically;
    the `system` lane is always last.
- [ ] FR-4 — Playhead default: `"now"`. While `"now"`, new beads tail in
      and the playhead visually sits at the right edge. When scrubbed
      off-now, the playhead is `{ at: ms }`; new beads still accumulate
      in state but the visual playhead stays where the user left it.
- [ ] FR-5 — Click on a bead snaps the playhead to that bead's
      timestamp. Click on empty timeline area moves the playhead to the
      clicked horizontal coordinate (rounded to the nearest bead).
- [ ] FR-6 — Transport controls (`role="toolbar"`):
  - **Back** — moves to the previous bead's timestamp (across all
    lanes, sorted by `created_at`); no-op at the first bead.
  - **Play / Pause** — toggles playback. Play advances the playhead
    bead-by-bead at a fixed cadence (≈ 500 ms / bead — feels like
    scrub, not real-time). Reaching the last bead snaps to `"now"`
    and stops.
  - **Forward** — moves to the next bead's timestamp; advancing past
    the last bead snaps to `"now"`.
  - **Snap to now** — visible only when the playhead is not `"now"`;
    snaps the playhead.
- [ ] FR-7 — Keyboard: when the component's container is focused,
      `←` / `→` step beads (same as Back / Forward), `Esc` snaps to
      `"now"`. Bindings are React `onKeyDown` on the container; **no**
      `document` or `window` listeners. The container has `tabIndex={0}`
      so it can take focus.
- [ ] FR-8 — Branch / Restore actions: rendered only when the
      playhead is `{ at: ms }`. Both call `Transport.request` against
      placeholder paths (backend not built yet):
  - **Branch from here** —
    `POST /v1/agent/runs/{runId}/branch?at={ms}`; on resolution,
    fires `onBranch(ms)`.
  - **Restore this state** —
    `POST /v1/agent/runs/{runId}/restore?at={ms}`; on resolution,
    fires `onRestore(ms)`. Failures log via `console.warn` and do not
    crash the surface.
- [ ] FR-9 — Pinned beads: each bead has a pin toggle (small button on
      the bead). Pinning persists to `useKeyValueStore()` under the key
      `swimlanes:pinned:{runId}`, stored as a JSON string array of
      `event_id`s. Re-mount restores pinned state. Unpinning removes the
      id; an empty pin set writes `null` (delete) rather than `"[]"`.
- [ ] FR-10 — Empty state: while no beads have arrived, the component
      renders a `role="status"` placeholder ("Listening for run events…").
      Transport controls render disabled. No keyboard bindings change.

## Non-functional requirements

- TypeScript strict; no `any`; `readonly` on interface fields by default.
- No bare browser globals — substrate touchpoints route via the ports.
  No `window`, `document`, `localStorage`, `fetch`, `EventSource`.
- No new third-party dependency. Inline styles consistent with
  TcInlineDiff (same palette where possible).
- Component file ≤ 350 LOC including styles.
- Tests cover:
  - Initial render shows the empty-state placeholder.
  - Beads from SSE render in their correct lanes; `system` lane for
    missing `surface_uri`; multiple lanes for a multi-surface stream.
  - Click on bead moves playhead; `onScrubChange` fires with `{at}`.
  - Keyboard `←` / `→` steps beads; `Esc` snaps to now.
  - Branch / Restore actions visible only off-now.
  - Branch / Restore call `Transport.request` with expected path and
    fire the corresponding callback.
  - Pinning persists to the KV store; reloading the component
    restores pinned state.
  - Snap-to-now button visible only off-now; clicking it returns
    playhead to `"now"`.

## Interfaces consumed

- `useTransport` from `../providers/TransportProvider`
- `useKeyValueStore` from `../providers/KeyValueStoreProvider`
- `RuntimeEventEnvelope`, `isRuntimeEventEnvelope` from
  `@enterprise-search/api-types`
- `SseSubscribeOptions`, `SseSubscription` from `@enterprise-search/chat-transport`

## Interfaces produced

```ts
// packages/chat-surface/src/thread-canvas/TcSwimlanes.tsx
export type Playhead = "now" | { readonly at: number };

export interface TcSwimlanesProps {
  readonly runId: string;
  readonly onScrubChange?: (playhead: Playhead) => void;
  readonly onBranch?: (atMillis: number) => void;
  readonly onRestore?: (atMillis: number) => void;
}

export function TcSwimlanes(props: TcSwimlanesProps): ReactNode;
```

## Coordination

- This sub-PRD does **NOT** modify `packages/chat-surface/src/index.ts`.
  The orchestrator should append the following delimited block when
  merging:

  ```ts
  // === Phase 2-C swimlanes ===
  export {
    TcSwimlanes,
    type TcSwimlanesProps,
    type Playhead,
  } from "./thread-canvas";
  // === end Phase 2-C ===
  ```

- The thread-canvas barrel `packages/chat-surface/src/thread-canvas/index.ts`
  gains the delimited block:

  ```ts
  // === Phase 2-C swimlanes ===
  export {
    TcSwimlanes,
    type TcSwimlanesProps,
    type Playhead,
  } from "./TcSwimlanes";
  // === end Phase 2-C ===
  ```

- Phase 2-D (TcChat ghost messages) is expected to read the same
  `Playhead` discriminator. The orchestrator can wire `onScrubChange`
  → ThreadCanvas → TcChat in 2-E (canvas-integration) without
  changing this file.

## Open questions

1. **Backend endpoints for branch / restore do not yet exist.** The
   placeholder paths in FR-8 follow the existing
   `/v1/agent/runs/{run_id}/...` convention but the server route is
   not registered. The component calls the path anyway — failures
   `console.warn` and the component does not crash. The orchestrator
   should schedule a backend task (or a temporary frontend mock) to
   make these endpoints real before Phase 4. Tests stub `Transport`
   with a `vi.fn()` that records the path and resolves immediately.

2. **`surface_uri` is a soft contract.** Backend events carry payload
   shape per `RuntimeEventPresentation` and per-tool conventions; no
   single `surface_uri` field is universally present today. The
   swimlane reads `event.payload.surface_uri` defensively and falls
   back to the `system` lane otherwise. When Phase 3 (real renderers)
   lands, payloads from MCP tool events should carry a `surface_uri`
   (e.g. `email://draft-7`) — that's a separate backend contract
   change. Until then, most beads will land in `system`; lane
   visualization is still correct, just less rich.

3. **No undo on Restore.** "Restore this state" rewrites the run's
   tail-end. Phase 4 will revisit whether this is destructive (and
   needs an approval gate) or non-destructive (Restore is itself a
   journaled event so the original tail is recoverable). For now,
   the component does not gate Restore — it fires `Transport.request`
   immediately and trusts the backend to enforce the right semantics.

## Done criteria

- [ ] All FRs met.
- [ ] `npm run typecheck --workspace @enterprise-search/chat-surface` passes.
- [ ] `npm test --workspace @enterprise-search/chat-surface` passes
      (existing 135 tests + new TcSwimlanes tests).
- [ ] `npm run lint --workspace @enterprise-search/chat-surface` passes.
- [ ] No bare browser globals, no `any`, no `fetch`/`EventSource`/
      `localStorage`/`window`/`document` references.
- [ ] `packages/chat-surface/src/thread-canvas/index.ts` gains only
      the delimited Phase 2-C block; pre-existing exports untouched.
- [ ] `packages/chat-surface/src/index.ts` untouched (orchestrator
      appends the top-level export at merge time).
