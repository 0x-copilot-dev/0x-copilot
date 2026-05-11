# Refactor PRD — Subagent status projection + shared chat-state package

**Status:** Draft
**Author:** frontend architecture, May 2026
**Tracks:**

- `services/ai-backend/docs/refactor/14-lifecycle-ledger-and-tool-errors.md` §4.5 (FE simplification follow-up)
- Pre-work for the upcoming **VS Code-forked desktop app**, which will reuse the chat state model end-to-end

---

## 1. Problem

The web FE today derives the chat surface's "is anything still running" answer from **two different sources** that are supposed to agree but can drift. After the backend invariant landed in `ai-backend/docs/refactor/14-...` (every `*_STARTED` is paired with `*_COMPLETED` at run end, including reconciliation on failure), the FE can collapse both sources to one — and the one that's already correct.

### 1.1 The two sources in flight today

**Source A — the workspace pane's `SubagentSnapshotMap`** ([subagentReducer.ts](../../src/features/chat/chatModel/subagentReducer.ts)):

```
SubagentSnapshotMap = ReadonlyMap<task_id, SubagentEntry>
applySubagentEvent(map, event) → SubagentSnapshotMap
```

A pure event projection keyed by `task_id`. Each event mutates the entry's status. The Agents tab uses this and is correct.

**Source B — the inline `SubagentFleetCard`'s `running` / `done` counts** ([SubagentFleetTool.tsx:95-127](../../src/features/chat/components/tools/SubagentFleetTool.tsx#L95-L127)):

```
for each child of the fleet's tool part:
    classify child via subagentCardFromArgs(child.args, child.status?.type, child.isError)
    if NON_TERMINAL.has(view.status): running += 1 else: done += 1
```

This reads from the **child tool part's status** — a parallel view of subagent state built from the supervisor's `task`-tool args plus that tool part's lifecycle events. It's not the same as the SubagentSnapshotMap; when the supervisor's `task` tool result never arrives (because the parent run failed first, exactly the `8475dbace42f4e34a2d2fb1555a542e0` shape), the tool part stays `running` and the fleet card shows a stuck subagent. The workspace pane's map, fed by `SUBAGENT_COMPLETED` events (including the synthesized terminal events PRD 14 now guarantees), would say "failed" — but the fleet card never asks.

### 1.2 Why this is one bug, not two views

The chat surface has two answers to the same question — and only one of them is fed by the canonical `SUBAGENT_*` event stream. PRD 14 fixed the backend so the canonical stream is now complete. The fleet card still asks the wrong source.

### 1.3 Why this also matters for the upcoming desktop app

The team is about to begin a VS Code-forked desktop app that reuses the chat surface. Today the chat-state logic lives inside `apps/frontend/src/features/chat/chatModel/` and several modules pull React or browser-only types implicitly through transitively-imported helpers. If the desktop app imports it as-is, every refactor to those modules has to satisfy two consumers at once, and any FE-only assumption that leaks (e.g. `window`, DOM measurement, React state shape) will fork.

This PRD's second phase extracts the pure, React-agnostic chat-state primitives to a shared TypeScript package so both clients consume the same source of truth.

---

## 2. Goals and non-goals

### Goals

1. **Single source of truth for subagent status on the chat surface.** Fleet card status / counts come from the `SubagentSnapshotMap`, not from the supervisor's tool-part view. When the backend emits a synthesized terminal event (PRD 14 reconciliation), the fleet card reflects it without further code change.
2. **Extract the React-agnostic chat-state primitives into a shared package** (`packages/chat-state`) so the upcoming desktop app can consume them unchanged. The web FE keeps its current behavior; the only externally-visible change is import paths.

### Non-goals

- No SSE wire-format change.
- No backend changes. PRD 14 already shipped the invariants this PRD relies on.
- No UI redesign. Fleet card / Agents tab visual surface stays identical.
- No design-system primitives change. This is logic-only.
- No async state-management library (Zustand / Redux). The current `useSyncExternalStore` + reducer pattern stays.
- No new event types. Existing `SUBAGENT_*` shape is sufficient.

### Success criteria

- The §1.1 chat-surface bug is impossible: a leaked subagent that receives a synthesized `SUBAGENT_COMPLETED status=failed` from PRD 14 reconciliation flips the fleet card row to "failed" without the chat client knowing or caring that the event was synthesized.
- The Agents tab and the inline fleet card render identical `running` / `done` counts for the same run — verified by an integration test that drives the same event stream into both and asserts.
- `packages/chat-state` exists, builds, type-checks, and is consumed by `apps/frontend` for the chat-state primitives. No web-only types (DOM, React, browser globals) appear in its public surface.
- All existing web-FE tests pass without skipped/xfailed regressions.
- A short ADR or README in `packages/chat-state` documents what belongs there and what does not, so the desktop app and any future client know the contract.

---

## 3. Systems touched

### 3.1 Files added

| File                                                                                      | Purpose                                                                                                       |
| ----------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `packages/chat-state/package.json`                                                        | New TypeScript workspace package, builds to `dist/`                                                           |
| `packages/chat-state/tsconfig.json`                                                       | Strict TypeScript config; emit `.d.ts`                                                                        |
| `packages/chat-state/src/index.ts`                                                        | Public surface re-exports                                                                                     |
| `packages/chat-state/src/subagent/reducer.ts`                                             | `applySubagentEvent` + `seedSubagentMap` + `isRunningStatus` moved from FE                                    |
| `packages/chat-state/src/subagent/counts.ts`                                              | NEW: pure `fleetCountsFromMap(map, task_ids) → {running, done, total, paused, failed}`                        |
| `packages/chat-state/src/run/ui-phase.ts`                                                 | `deriveRunUiState` + `phaseForEvent` moved from `chatRunState.ts`                                             |
| `packages/chat-state/src/events/projection.ts`                                            | Generic per-entity projection helpers that other reducers can reuse                                           |
| `packages/chat-state/README.md`                                                           | Scope contract: what belongs here, what does not                                                              |
| `packages/chat-state/tests/...`                                                           | Vitest unit tests; mirrors current FE test files                                                              |
| `apps/frontend/src/features/chat/components/tools/SubagentFleetTool.integration.test.tsx` | New integration test driving a §1.1-shaped event stream into both pane + fleet card; asserts identical counts |

### 3.2 Files removed

_(none in this PRD)_

Files are **moved** to `packages/chat-state`; the old FE locations re-export from the new package for one release cycle to keep import sites stable, then the re-exports are deleted in a follow-up cleanup PR.

### 3.3 Files changed

| File                                                                                      | Change                                                                                                                                                                                                       |
| ----------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| [`SubagentFleetTool.tsx`](../../src/features/chat/components/tools/SubagentFleetTool.tsx) | Replace tool-part-status-based counting with `fleetCountsFromMap(subagentsByTask, childTaskIds)`. Per-child row status reads `entry.status` (with fallback to tool-part status during the migration window). |
| [`subagentReducer.ts`](../../src/features/chat/chatModel/subagentReducer.ts)              | Re-export from `@enterprise-search/chat-state/subagent`. No behavior change.                                                                                                                                 |
| [`chatRunState.ts`](../../src/features/chat/chatRunState.ts)                              | Re-export `deriveRunUiState` + `phaseForEvent` from `@enterprise-search/chat-state/run`. No behavior change.                                                                                                 |
| [`AgentsTab.tsx`](../../src/features/chat/components/workspace/AgentsTab.tsx)             | `runningCount` derived via `fleetCountsFromMap` so both surfaces share the implementation.                                                                                                                   |
| [`apps/frontend/package.json`](../../package.json)                                        | Adds `@enterprise-search/chat-state` as a workspace dependency.                                                                                                                                              |
| [`package.json`](../../../../package.json) (repo root)                                    | Adds `packages/chat-state` to workspaces array.                                                                                                                                                              |

### 3.4 Files not changing (deliberate)

| File                                                                      | Why it stays                                                                                                |
| ------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| Other `chatModel/*` reducers (citationReducer, sourcesReducer, etc.)      | Out of scope. Extract incrementally as the desktop app actually needs them; do not bulk-move speculatively. |
| FE state-management library (React `useSyncExternalStore` / `useReducer`) | Unchanged. `packages/chat-state` exports pure functions; the React glue stays in the web FE.                |
| `@enterprise-search/api-types`                                            | No change. Event shape is stable; this PRD doesn't alter the wire format.                                   |

---

## 4. Design

### 4.1 Single source: per-entity status from the SubagentSnapshotMap

Today's fleet card scans child tool parts and classifies each via the tool-part's status. Replace with a pure function:

```ts
// packages/chat-state/src/subagent/counts.ts
export interface FleetCounts {
  total: number;
  running: number;
  paused: number;
  done: number;
  failed: number;
}

export function fleetCountsFromMap(
  map: SubagentSnapshotMap,
  childTaskIds: readonly string[],
): FleetCounts;
```

`childTaskIds` is the set of `task_id`s declared by the fleet's tool args (so missing entries — the entity hasn't been seen yet — are counted as `running`, matching today's behavior for unobserved children). Every observed `task_id` is classified by the map's `status` field:

- `queued` / `running` → `running`
- `paused` → `paused`
- `completed` → `done`
- `cancelled` / `failed` → `failed`
- All `failed` + `done` are summed into the existing `done` field that the fleet card already renders, so the visual surface is unchanged unless we want to expose `failed` separately (out of scope for this PRD).

When PRD 14 emits a synthesized `SUBAGENT_COMPLETED status=failed` for a leaked subagent, the existing `applySubagentEvent` reducer already projects it onto the map (via `onCompleted`). `fleetCountsFromMap` then reflects it on the fleet card next render — no fleet-card-specific code path needed.

### 4.2 What goes in `packages/chat-state`

**Belongs:**

- Event-projection reducers that don't touch React, the DOM, or browser globals (subagent, citations, sources, etc.) — but only extract them when the desktop app needs them. Subagent is the first.
- Pure derivation functions: `deriveRunUiState`, `phaseForEvent`, `isRunningStatus`, `fleetCountsFromMap`, etc.
- Event-stream replay helpers (apply events in order, return final state).

**Does not belong:**

- React components, hooks, or `useSyncExternalStore` glue (lives in `apps/frontend`).
- HTTP / SSE clients (live in `apps/frontend/src/api`).
- Browser globals (`window`, `document`, `localStorage`).
- Anything from `@enterprise-search/design-system` (UI primitives).

The package's `README.md` codifies this so the boundary doesn't drift as the desktop app starts importing things.

### 4.3 Package shape

```
packages/chat-state/
├── package.json          # @enterprise-search/chat-state
├── tsconfig.json         # extends repo root config; emits .d.ts
├── README.md             # scope contract
├── src/
│   ├── index.ts          # re-exports public surface
│   ├── subagent/
│   │   ├── reducer.ts    # applySubagentEvent + seed + isRunningStatus
│   │   └── counts.ts     # fleetCountsFromMap
│   ├── run/
│   │   └── ui-phase.ts   # deriveRunUiState + phaseForEvent
│   └── events/
│       └── projection.ts # generic per-entity replay helper
└── tests/
    ├── subagent-reducer.test.ts
    ├── subagent-counts.test.ts
    └── run-ui-phase.test.ts
```

The package depends only on `@enterprise-search/api-types`. No React, no DOM, no fetch.

### 4.4 Migration safety

Keep one cycle of re-exports from the old FE locations so unrelated chat-feature work doesn't have to rebase on import-path changes:

```ts
// apps/frontend/src/features/chat/chatModel/subagentReducer.ts
export {
  applySubagentEvent,
  seedSubagentMap,
  isRunningStatus,
  type SubagentSnapshotMap,
} from "@enterprise-search/chat-state/subagent";
```

A follow-up cleanup PR replaces every `from "../chatModel/subagentReducer"` with `from "@enterprise-search/chat-state/subagent"` and removes the re-export shim.

### 4.5 Desktop app readiness

The desktop app, when it starts, gets `@enterprise-search/chat-state` for free with no further extraction work for the subagent + run-state slice. As the desktop app implementation reveals which other chat-state primitives it actually needs (citations, sources, drafts), each one moves incrementally with the same shim-then-cleanup pattern. **Do not bulk-extract everything now** — it's speculative and locks in a shape that may not match the desktop app's real needs.

---

## 5. Migration / rollout

Three phases, each independently revertable.

### Phase 1 — Package skeleton + subagent extraction

- Create `packages/chat-state` with `package.json`, `tsconfig.json`, `README.md`, and empty `src/index.ts`.
- Add to root `workspaces` array. Verify `npm install` + workspace resolution.
- Move `subagentReducer.ts` content to `packages/chat-state/src/subagent/reducer.ts`.
- Old `subagentReducer.ts` becomes a re-export shim.
- Verify web FE typecheck + build pass with no source changes elsewhere.

### Phase 2 — `fleetCountsFromMap` + fleet card single-source rewire

- Add `packages/chat-state/src/subagent/counts.ts` with `fleetCountsFromMap`.
- Update `SubagentFleetTool.tsx` to compute `running` / `done` via `fleetCountsFromMap(subagentsByTask, childTaskIds)`.
- Per-row status: prefer `subagentsByTask.get(childTaskId)?.status`, fall back to tool-part status when the entry isn't seeded yet (e.g. very fast renders where the SSE event hasn't projected to the map).
- Add the integration test asserting `running` / `done` parity between Agents tab and fleet card under a §1.1-shaped event stream.

### Phase 3 — Run-state extraction

- Move `deriveRunUiState` + `phaseForEvent` from `chatRunState.ts` to `packages/chat-state/src/run/ui-phase.ts`.
- Old `chatRunState.ts` becomes a re-export shim.
- Tests follow. Existing pinning of "every active phase shows the planning pulse" (apps/frontend/CLAUDE.md → "Planning-pulse visibility") moves with it.

### (Out of scope here — for the desktop-app PR sequence)

- Extracting `citationReducer`, `sourcesReducer`, `eventReducer`, `presentation`, `partFactories`, etc. happens as the desktop app drives demand for each one. Each extraction is a one-PR shim-then-cleanup.

---

## 6. Testing

### Unit (new, in `packages/chat-state/tests`)

- `applySubagentEvent`: starts seed an entry, completes flip to terminal, paused/resumed transitions, synthesized completion from PRD 14 (event with `payload.synthesized = true, status = failed`) flips entry to failed.
- `fleetCountsFromMap`:
  - empty map + N declared `task_id`s → all `running` (unobserved children optimistically count as running).
  - partial map (3 declared, 1 completed, 1 paused, 1 missing) → `{ running: 1, paused: 1, done: 1, failed: 0, total: 3 }`.
  - synthesized failure flips a child from `running` → `failed` without retouching the fleet card.
- `deriveRunUiState`: every active phase yields `showPlanningIndicator: true`; terminal phases yield `false`. Mirrors the existing pinned tests.

### Integration (apps/frontend)

- **`SubagentFleetTool.integration.test.tsx`** (new) — drives a §1.1-shaped event stream:
  1. `SUBAGENT_STARTED` × 3 with `task_id` ∈ `{alpha, beta, gamma}`
  2. `SUBAGENT_COMPLETED` × 2 for `alpha`, `beta`
  3. `SUBAGENT_COMPLETED status=failed payload.synthesized=true` for `gamma` (synthesized by coordinator reconciliation)
  4. `RUN_FAILED`
  - Assert: Agents tab shows `running: 0, done: 2, failed: 1` and the fleet card shows the same counts.
- **`AgentsTab.test.tsx`** existing tests must continue to pass.

### Regression

- Full web FE Vitest suite passes with the re-export shims in place.
- Typecheck passes for both `apps/frontend` and `packages/chat-state`.
- The `apps/frontend/CLAUDE.md` "Planning-pulse visibility" pin still holds after the run-state move.

---

## 7. Risks and open questions

### Risks

- **Re-export shims rot.** If we leave the shim files in place too long, refactors will keep touching the indirection layer. Mitigation: one cleanup PR per shimmed file, scheduled within the same milestone as the original extraction.
- **The fleet card's optimistic "missing → running" heuristic.** Today an unobserved child is counted as `running` because the supervisor's tool-part is the source of truth. After this PRD, an unobserved child stays `running` until an event arrives. Both behaviors look the same on the FE but the _reason_ changes; if the SSE stream drops the start event, the count is now stale until reconnect. Mitigation: relies on `?after_sequence=N` resume, which already exists.
- **Drift between `paused` semantics in two places.** `chatModel/subagentReducer.ts` excludes `paused` from `running`; `fleetCountsFromMap` introduces a separate `paused` bucket. Both render summed as "not running" in the fleet card, but expose the breakdown to callers. Mitigation: integration test covers the fleet card's rendering identity; pane and card sum to identical totals.
- **`packages/chat-state` becomes a dumping ground.** Without a clear scope contract, every chat-feature module gets pulled in over time. Mitigation: the `README.md` codifies what belongs and what does not; PR review checks it.
- **Desktop app reveals an API the package didn't anticipate.** Bigger risk than expected because the desktop app surface is still being designed. Mitigation: this PRD intentionally extracts only the subagent + run-state slice — the smallest surface the upcoming bug-fix flow needs. Everything else stays in the web FE until the desktop app explicitly pulls it.

### Open questions

- Should `fleetCountsFromMap` expose `failed` as a separate first-class count to the fleet card (vs. summing into `done`)? Visual treatment is a design call — keep summed-into-`done` for now; revisit if the desktop app surfaces failure prominently.
- Are there chat-feature React hooks that should also move (e.g. a `useSubagentSnapshot` hook)? Probably not — keep React glue in the consuming app, package stays React-agnostic.
- Versioning strategy for `packages/chat-state` once two clients depend on it — workspace `*` for now; pin a real semver when we have a third consumer.

---

## 8. Out of scope

- New event types or wire-format changes.
- Anything in `services/*` — PRD 14 already provided the backend invariants this relies on.
- Bulk extraction of every `chatModel/*` reducer.
- Redesign of the fleet card or Agents tab UX.
- VS Code-fork desktop-app implementation (this PRD lays the runway; the app itself is a separate stream of work).
- Migration of `@enterprise-search/api-types` shape.
- Frontend state-management library swap.
