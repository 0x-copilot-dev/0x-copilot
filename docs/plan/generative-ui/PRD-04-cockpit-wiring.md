# PRD-04 — Cockpit wiring: tabs, activeUri, spec merge, pendingDiff, decisions (Wave 1)

**Goal:** light up the dark seams in the live Run cockpit: Studio tabs auto-populate from projected surface state, the center pane follows the run, late-arriving specs merge in, and the on-surface Approve/Reject controls actually fire the decision endpoint. After this PR + PRD-02 + PRD-03, a real run shows live surfaces on desktop end-to-end.

**Depends on:** PRD-01. **Scope:** `packages/chat-surface` only (desktop binder already passes through; do not touch hosts).

## Scope — files

| File | Change |
|---|---|
| `src/thread-canvas/eventProjector.ts` | EXTEND — (a) `surface_spec_generated` handling: merge `payload.spec` into `surfaceState[payload.surface_uri].spec` (a spec never clobbers newer `data`; keyed merge, replay-idempotent like all reducers here); (b) expose `surfaceTabs` in `ProjectedState`: `[{uri, archetype?, title?, lastSeq}]` ordered by `lastSeq` desc, derived from `surfaceState` + envelope `surface.archetype`/spec `title_path` best-effort (title falls back to the URI tail). Pure derivation inside the single projection — NOT a second subscription |
| `src/destinations/run/RunDestination.tsx` | EXTEND — replace the empty `useState` tabs/activeUri (lines ~212–213): tabs ⇐ `projection.surfaceTabs` (cap 8, "+N more" overflow menu later — just cap now); `activeUri` auto-follows the newest tab while the user hasn't pinned (a manual tab click pins; a new URI un-pins only via explicit "follow live" affordance — reuse the scrub-banner pattern for the copy); pass `pendingDiff` + `onApprove/onReject/onSuggestChanges` into `ThreadCanvas` (it already forwards to `TcSurfaceMount`) |
| `src/destinations/run/_surfaceDiffs.ts` | NEW — pure selector `projectSurfaceDiffs(events)`: approval-shaped events whose payload carries a `surface` envelope with `diff` → `{diffId: approvalId, uri, diff}`; latest-unresolved-per-uri wins. Follows the `projectApprovals`/`_approvals-stub` conventions (and shares its TODO(merge) discipline) |
| `src/thread-canvas/TcSurfaceMount.tsx` | MINIMAL EXTEND — accept an optional `onOpenExternal(url)` passthrough? NO — defer. Only change: ensure `pendingDiff` clears when the underlying approval resolves (prop-driven; no internal state) |

## Behavior (normative)

- **One projection rule holds:** everything derives from `useEventProjector`'s single pass or pure selectors over the same array. No new SSE subscriptions, no `useEffect` fetch loops.
- **Decisions:** `onApprove/onReject(diffId)` POST the existing `/v1/agent/approvals/{id}/decision` via the Transport port with optimistic local decision + SSE reconciliation — reuse the exact `resolveApproval` machinery in `RunDestination` (do not fork it; extract if needed).
- **Scrub semantics:** while scrubbed off-live, tabs render the projected-at state and pendingDiff/approve controls hide (existing rule for chat approvals — apply the same predicate).
- `onSuggestChanges` remains a no-op callback surfaced to the host (PRD-09 fills it). Button renders only when the handler is provided.

## Acceptance criteria

1. Projector tests: feeding the PRD-01/02 fixture event sequence (tool_result with envelope → surface_spec_generated later) yields `surfaceState[uri].spec` populated post-merge, and replaying the array twice is idempotent (dedup by event_id).
2. `surfaceTabs` test: 3 URIs across 6 events → 3 tabs ordered by last mutation; same-URI updates don't duplicate.
3. RunDestination interaction tests: newest surface auto-opens; clicking an older tab pins; approve on a pendingDiff optimistically resolves and POSTs the decision path (Transport mock asserts URL + body).
4. Scrubbed state hides diff controls (existing test pattern extended).
5. Package typecheck + vitest green; no eslint substrate violations.

## Non-goals / guardrails

- No host app changes (desktop binder signature unchanged — verify, don't edit).
- No edit surface, no commit executor (PRD-09). No text diff (PRD-06).
- Do not restructure ThreadCanvas grid/modes; do not add a second chat mount; preserve the single-mount invariant documented at the top of `ThreadCanvas.tsx`.
