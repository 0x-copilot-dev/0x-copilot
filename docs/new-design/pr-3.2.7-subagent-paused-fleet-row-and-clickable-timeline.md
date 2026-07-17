# PR 3.2.7 — Frontend paused state + clickable fleet rows with inline timeline

> **Status:** Drafted · v1
> **Plan reference:** Phase 4 — final piece of the subagent runtime correctness train. Phase 1 ([`pr-3.2.5-subagent-call-id-propagation.md`](./pr-3.2.5-subagent-call-id-propagation.md)) made `parent_task_id` deterministic; Phase 2 (executor restructure inside [`streaming_executor.py:175-219`](../../services/ai-backend/src/runtime_worker/streaming_executor.py#L175-L219)) made siblings keep streaming during a peer's interrupt; Phase 3 ([`pr-3.2.6-subagent-paused-resumed-events.md`](./pr-3.2.6-subagent-paused-resumed-events.md)) put `subagent_paused` / `subagent_resumed` on the wire and projected them in the reducer. **This PR is the visual surface.**
> **Owner:** apps/frontend (components, CSS, tests). No backend, api-types, migration.
> **Size:** **M.** ≈ 200 LoC components + ~120 LoC CSS + tests. No new deps. No new shared primitives.
> **Depends on:** ✅ Phase 1 (deterministic linkage). ✅ Phase 2 (sibling drain). ⛔ Phase 3 ([`pr-3.2.6-...`](./pr-3.2.6-subagent-paused-resumed-events.md)) — without `status === "paused"` in the reducer this PR has nothing to render.
> **Reads alongside:**
>
> - [`pr-3.2.6-subagent-paused-resumed-events.md`](./pr-3.2.6-subagent-paused-resumed-events.md) — the reducer state this PR consumes.
> - [`pr-3.2.4-fleet-nests-compact-rows.md`](./pr-3.2.4-fleet-nests-compact-rows.md) — the row component this PR extends with click-to-expand.
> - [`pr-3.2.2-subagent-card-shared-primitive.md`](./pr-3.2.2-subagent-card-shared-primitive.md) — the SubagentCard primitive this PR routes the inline timeline through.
> - [`apps/frontend/CLAUDE.md`](../../apps/frontend/CLAUDE.md) — frontend rules (api-types as contract, design-system primitives, no direct backend calls).

---

## 0 · TL;DR

After Phases 1–3 land, every subagent's lifecycle is observable on the wire (`SUBAGENT_STARTED → _PROGRESS* → _PAUSED? → _RESUMED? → _COMPLETED`) and projected into `SubagentEntry.status`. The remaining problem is that the FE renders all in-flight subagents identically — a single round indicator and a creeping progress bar — so a user looking at a 3-agent fleet during an approval gate has no way to tell which one is blocked.

**Fix:** four small UX changes, all driven by reducer state already produced by Phase 3:

1. **Paused visual** on `<FleetSubagentRow>` and `<SubagentCard>`: amber indicator, paused chip, frozen progress bar, "Paused — waiting on approval/auth/answer" sub-label.
2. **Clickable fleet rows.** Click on a row to toggle a per-row inline `<SubagentActivityList>` disclosure (the same timeline `<SubagentCard>` shows). Multiple rows can be open simultaneously per the user's stated preference (independent disclosures, no accordion).
3. **One-click jump** from a paused row to the gating approval card on the main thread (`onJumpToApproval` reuses the existing `scrollChatToCitation` infrastructure with an approval-anchor variant).
4. **Pane parity.** The Agents tab card (`SubagentCard`) renders the same paused chrome and exposes the same jump affordance.

No new packages. No new shared primitives. The visual contract reuses tokens (`tone="warning"`) and the existing native `<details>` disclosure pattern.

---

## 1 · PRD

### 1.1 Problem

Three observed failures with parallel-fleet runs once Phase 3 lands and the data is correct:

1. **Paused state invisible.** A user dispatches a 3-agent fleet. Subagent B hits `MCP_AUTH_REQUIRED`. The reducer correctly flips B's status to `paused`. But the only consumer (`<FleetSubagentRow>`) reads `view.status` and falls into the `queued | running` indicator branch, rendering identical chrome to A and C. The user sees three spinning circles and no signal that B is the one waiting.
2. **No way to inspect a fleet member.** PR 3.2.4 made the row compact deliberately (single line, no disclosure). The "View in workspace →" footer on the fleet card jumps to the pane, which is a 3-click detour. Users open the fleet card, _think_ they want details on a specific row, and have to learn the pane is the answer. Per the user's stated preference: "I want them to be clickable... show inline timeline."
3. **No path back to the approval.** When B is paused on `MCP_AUTH_REQUIRED`, the gating card is somewhere on the main thread (potentially scrolled far off). The fleet card has no link to it. The user has to scroll-hunt to find what to click.

### 1.2 Goals

1. **Paused chrome on the row + the card.** A `paused` row reads as paused at a glance: amber indicator, a "Paused" chip, a sub-label that names the reason (`waiting on approval` / `waiting for auth` / `waiting on user answer`), progress bar frozen at its last value with a subtle pulse animation. Same chrome on `<SubagentCard>` for pane parity.
2. **Click-to-expand on `<FleetSubagentRow>`.** A click toggles an inline `<SubagentActivityList>` disclosure underneath the row, scoped to that subagent's `args.activities` (the same data the standalone `<SubagentCard>` and the pane card already consume). Independent state per row — opening one doesn't close another.
3. **Click-to-jump from paused row.** A "Review approval →" affordance appears on paused rows when the row's `source_event_id` matches a known approval / auth / question card on the main thread. Clicking it scrolls to and momentarily highlights the gating card. Reuses the existing `scrollChatToCitation` pattern from PR 3.7.x.
4. **Keyboard + screen-reader parity.** The expandable row is `role="button" tabindex="0"`; the inline timeline is announced via `aria-expanded`. The paused chip has `aria-label="Paused, waiting on approval"`. Color is supplemental, never load-bearing.
5. **No layout shift.** Inline timeline expansion uses CSS `grid-template-rows: auto 0fr`/`auto 1fr` per the existing PR 3.2.1 pattern. The fleet card's height grows; surrounding chat content reflows once.
6. **Compose, don't fork.** Reuse `<SubagentActivityList>` for the inline timeline (same component used by `<SubagentCard>`'s disclosure). Reuse `tone="warning"` from `@0x-copilot/design-system` for the paused chip. No new shared primitives.
7. **Cheap to roll back.** The new disclosure state is local to `<FleetSubagentRow>` (a single `useState<boolean>`); ripping the click-to-expand reverts to the PR 3.2.4 compact row in one line.

### 1.3 Non-goals

- ❌ **Resume affordance from the FE.** The user resumes via the existing approval / MCP-auth / ask-a-question card on the main thread. No "Resume this subagent" button on the row — that would be a duplicate control and risks divergence with the actual handler logic.
- ❌ **Cancellation affordance from the row.** Cancel is run-level, not subagent-level. Per-subagent cancel is a separate runtime feature; not in scope here.
- ❌ **Timeline filtering / sorting / search.** The inline timeline is a verbatim render of `args.activities` — same as the standalone card. Filtering UI is out of scope.
- ❌ **Animations beyond the existing tokens.** The paused-pulse is `@keyframes` on the indicator only, derived from the running spinner with a different `animation-duration` and amber tint. No new motion primitives.
- ❌ **A separate Approvals tab badge.** Tracked as a paper cut in [`pr-3.2.6-...`](./pr-3.2.6-subagent-paused-resumed-events.md) §6. Could be added in a follow-up if real users need it.
- ❌ **Mobile layout adjustments.** Atlas is desktop-first; the row's hit target shrinks proportionally on narrow viewports but the layout is unchanged.
- ❌ **Telemetry on click-to-expand or click-to-jump.** Add only if real usage data is needed.

### 1.4 Acceptance criteria

| #     | Criterion                                                                                                                                                                                                                                                       | Verified by                                                                         |
| ----- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- | ----------------- | ------------------------------------------------------------------------- |
| AC-1  | When `view.status === "paused"`, `<FleetSubagentRow>` renders: `data-status="paused"` on the root and indicator, an amber-toned indicator glyph, a "Paused" chip in the row, and a sub-label naming the reason from `view.pauseReason` (one of `approval        | mcp_auth                                                                            | ask_a_question`). | `FleetSubagentRow.test.tsx::renders_paused_chrome_when_status_is_paused`. |
| AC-2  | When `view.status === "paused"`, the row's progress bar is **frozen** at its last `progress` value (or 0 if never reported) and has `data-paused="true"` so CSS can apply a subtle pulse without animating fill width.                                          | `FleetSubagentRow.test.tsx::progress_bar_freezes_on_pause`.                         |
| AC-3  | The row is clickable (`role="button"`, `tabindex="0"`, `onClick` and `onKeyDown` handlers for `Enter` / `Space`). Clicking toggles a local `useState<boolean>` controlling visibility of an inline `<SubagentActivityList>` underneath the row.                 | `FleetSubagentRow.test.tsx::click_toggles_inline_timeline`.                         |
| AC-4  | Independent disclosure state per row: opening row A does not close row B. (Tested by mounting two `<FleetSubagentRow>` in a fixture and toggling them independently.)                                                                                           | `FleetSubagentRow.test.tsx::independent_disclosure_per_row`.                        |
| AC-5  | The inline timeline reuses `<SubagentActivityList>` exactly as `<SubagentCard>` does. Empty activities → renders the calm "Single-shot response" fallback or the truncated `result_summary`, mirroring the standalone card's empty state.                       | `FleetSubagentRow.test.tsx::inline_timeline_matches_subagent_card_empty_state`.     |
| AC-6  | When `view.pauseReason` resolves to a `source_event_id` that maps to a known approval / auth / question card on the thread, a "Review approval →" link appears in the inline expansion (only when expanded). Click invokes `onJumpToApproval(source_event_id)`. | `FleetSubagentRow.test.tsx::jump_to_approval_visible_when_source_event_id_present`. |
| AC-7  | When no `source_event_id` resolves (rare; reconnect mid-flight before reducer-side pairing), the jump affordance is hidden — the row still expands, the paused chrome still renders.                                                                            | `FleetSubagentRow.test.tsx::jump_to_approval_hidden_when_unresolved`.               |
| AC-8  | `<SubagentCard>` (used by both in-thread and pane callsites) gains the same paused chrome: badge tone `warning` with text "Paused", reason sub-label, frozen progress hint. No layout shift between running and paused.                                         | `SubagentCard.test.tsx::renders_paused_chrome_when_status_is_paused`.               |
| AC-9  | `<SubagentCard>` paused state preserves the disclosure: clicking the `<details>`/summary still reveals the timeline. `defaultOpen` behavior is unchanged.                                                                                                       | `SubagentCard.test.tsx::paused_does_not_force_close_disclosure`.                    |
| AC-10 | Pane Agents tab card surfaces a "Review approval →" link when the underlying entry is paused, mirroring the row affordance. Clicking jumps the chat scroll using `scrollChatToCitation` (or an analogous approval-anchored helper).                             | `AgentsTab.test.tsx::pane_card_jump_to_approval_when_paused`.                       |
| AC-11 | Visual + axe checks pass:<br>• Paused chip contrast ≥ 4.5:1 against row background.<br>• Click target ≥ 32×32px.<br>• `aria-expanded` reflects state.<br>• Indicator glyph has `aria-hidden`; semantic state lives on the chip + row data attribute.            | `axe-core` snapshot in `FleetSubagentRow.test.tsx`; visual review against design.   |
| AC-12 | Reducer + visual integration: an in-thread fleet card that receives `SUBAGENT_PAUSED` flips one row to paused without re-render of siblings (React keys remain stable). After `SUBAGENT_RESUMED`, the row reverts to running chrome with no flicker.            | New integration test `SubagentFleetTool.integration.test.tsx::pause_then_resume`.   |
| AC-13 | Existing tests preserved: `FleetSubagentRow.test.tsx` cases that asserted PR 3.2.4 behavior still pass (running chrome, terminal status word, formatting). `SubagentCard.test.tsx` cases for non-paused states unchanged.                                       | Full FE suite: `npm test --workspace @0x-copilot/frontend`.                         |
| AC-14 | Build + typecheck clean: `npm run typecheck --workspace @0x-copilot/frontend` and `npm run build --workspace @0x-copilot/frontend`.                                                                                                                             | CI.                                                                                 |

### 1.5 Risks

| Risk                                                                                                                                                                                                   | Mitigation                                                                                                                                                                                                                                                                                                                                                                                 |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Click-on-row collides with the existing fleet card "View in workspace →" footer** — user clicks meaning the footer, hits a row instead, sees an unexpected disclosure expand.                        | Maintain visual + spatial separation: rows are inside a vertical stack with hover styles; the footer is a button below the stack with its own hover. Pointer events stop at the row root; the footer is outside that subtree. Existing tests for the footer affordance still pass.                                                                                                         |
| **Inline timeline duplicates the standalone `<SubagentCard>`'s timeline when both render.** A user with a fleet card open and the pane open at the same time sees two timelines for the same subagent. | Acceptable: each lives in its own surface (chat thread vs pane). They reflect the same data; the user is in control of which to look at. We don't try to single-instance them across surfaces.                                                                                                                                                                                             |
| **`source_event_id` resolution lives in the reducer or in component-level lookup logic that we don't currently have wired.**                                                                           | The `source_event_id` is on the `SubagentPausedPayload` (Phase 3 wire shape). Reducer projection in [`pr-3.2.6-...`](./pr-3.2.6-subagent-paused-resumed-events.md) writes it into `SubagentEntry` (additive optional field — paired with the status flip). Component reads it and asks the chat-scroll registry whether the event is on the visible thread. Cheap lookup; no global state. |
| **`<details>`/`<summary>` and `role="button"` on the row collide.** A row that's clickable AND uses `<details>` for its inline-timeline disclosure can break expected `Enter`/`Space` semantics.       | Don't use `<details>` for the row's inline timeline. The row controls the disclosure with local `useState` + `aria-expanded` + an animated grid-row reveal (the same CSS pattern used in the workspace pane Agents tab from PR 3.2.1). `<details>` stays on the standalone `<SubagentCard>` where the click-target is the summary, not the whole card.                                     |
| **Keyboard users may struggle to find the inline-timeline close affordance** since the whole row is the toggle.                                                                                        | The row toggles open / closed on Enter/Space and click. `aria-expanded` announces state. The row's hover + focus styles indicate clickability. No separate close button needed; that's standard disclosure UX.                                                                                                                                                                             |
| **Long-running runs accumulate many paused rows** if the user authorizes one approval, the subagent immediately hits another, etc.                                                                     | Each pause/resume cycle is two events; the reducer projects on each. Visual chrome updates per status flip. No memory leak; no unbounded list. Rows expand/collapse by user action, default closed.                                                                                                                                                                                        |
| **The amber pulse animation is distracting in a fleet of 5+ paused rows.**                                                                                                                             | `prefers-reduced-motion: reduce` disables the pulse and falls back to a static amber dot. Tested via the existing reduced-motion media query branch in `styles.css`.                                                                                                                                                                                                                       |
| **`onJumpToApproval` re-uses `scrollChatToCitation` but the function was built for citations.**                                                                                                        | Add a thin wrapper that takes an `event_id` instead of a `citation_id` and resolves the chat anchor by event id. Same module, same testing path. New helper: `scrollChatToEvent(event_id)` in [`scrollChatToCitation.ts`](../../apps/frontend/src/features/chat/components/citations/scrollChatToCitation.ts) co-located file. Tests added to its sibling test file.                       |
| **A subagent that completes while the user has its inline timeline open should still feel coherent.**                                                                                                  | The disclosure stays open after `_COMPLETED` flips status to `completed`; the user reads the final timeline calmly. Closing is the user's choice. (Tested: `pause_then_resume_then_complete_keeps_open`.)                                                                                                                                                                                  |

### 1.6 Unit testing

Per [`apps/frontend/CLAUDE.md`](../../apps/frontend/CLAUDE.md) and the existing component-test conventions in [`FleetSubagentRow.test.tsx`](../../apps/frontend/src/features/chat/components/subagents/FleetSubagentRow.test.tsx) / [`SubagentCard.test.tsx`](../../apps/frontend/src/features/chat/components/subagents/SubagentCard.test.tsx):

**`FleetSubagentRow.test.tsx`** — extend with:

- `renders_paused_chrome_when_status_is_paused` — paused view renders amber indicator + "Paused" chip + reason sub-label.
- `progress_bar_freezes_on_pause` — `data-paused="true"` set; `transform: scaleX(...)` retains last value.
- `click_toggles_inline_timeline` — click → expanded; click → collapsed.
- `keyboard_enter_and_space_toggle_inline_timeline` — keyboard parity.
- `independent_disclosure_per_row` — two rows, toggle each independently.
- `inline_timeline_matches_subagent_card_empty_state` — empty `args.activities` → fallback copy.
- `inline_timeline_renders_activities_when_present` — non-empty `args.activities` → list of activity cards.
- `jump_to_approval_visible_when_source_event_id_present` — affordance shown when `source_event_id` resolves.
- `jump_to_approval_hidden_when_unresolved` — affordance hidden otherwise.
- `jump_to_approval_invokes_callback_with_source_event_id` — callback fires.
- `paused_chrome_respects_prefers_reduced_motion` — JSDOM matchMedia mock; pulse disabled.
- `axe_violations_paused_state` — axe-core check.

**`SubagentCard.test.tsx`** — extend with:

- `renders_paused_chrome_when_status_is_paused` — badge tone, reason sub-label, frozen progress hint.
- `paused_does_not_force_close_disclosure` — `<details>` still toggles.
- `paused_pane_card_renders_jump_to_approval` — pane callsite (compact prop) shows jump link.

**`AgentsTab.test.tsx`** — extend with:

- `pane_card_jump_to_approval_when_paused` — workspace pane wires `onJumpToApproval` correctly.

**`SubagentFleetTool.integration.test.tsx`** (new file or extension) — extend with:

- `pause_then_resume` — drive `SUBAGENT_STARTED → _PAUSED → _RESUMED → _COMPLETED`; assert visual transitions and React key stability.
- `pause_then_resume_then_complete_keeps_open` — disclosure state persists across status flips.

**`scrollChatToCitation.test.ts`** — extend with:

- `scrollChatToEvent_resolves_event_anchor` — new helper resolves by event_id.
- `scrollChatToEvent_no_op_when_anchor_missing` — graceful no-op.

**No backend tests added.** Backend behavior unchanged; this PR is FE-only.

---

## 2 · Spec

### 2.1 Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ BEFORE (post Phase 3)                                                       │
│                                                                              │
│  reducer: SubagentEntry { status: "paused", source_event_id?: ... }          │
│       ▼                                                                     │
│  view-model: { status: "paused", pauseReason: ... }   ← NEW field on VM     │
│       ▼                                                                     │
│  <FleetSubagentRow>: shows generic "running" indicator   ← ACTUAL BUG       │
│  <SubagentCard>:    shows generic "running" badge        ← ACTUAL BUG       │
└──────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────┐
│ AFTER (this PR)                                                              │
│                                                                              │
│  view-model: { status: "paused", pauseReason: "approval"|... ,              │
│                pauseSourceEventId?: string }                                │
│       ▼                                                                     │
│  <FleetSubagentRow>:                                                         │
│    - data-status="paused" → amber indicator, frozen progress, paused chip   │
│    - role="button"; click toggles local `expanded` useState                 │
│    - when `expanded`: <SubagentActivityList activities={view.activities}/>  │
│      + (paused && pauseSourceEventId) ? <JumpToApprovalLink/> : null        │
│  <SubagentCard>:                                                             │
│    - same paused chrome variant on existing chrome                          │
│    - existing `<details>`/`<summary>` disclosure unchanged                  │
│  workspace pane AgentsTab:                                                  │
│    - <SubagentCard compact onJumpToApproval={resolveAndScroll}/>            │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Module boundaries

| Layer                                                                               | Module                                                                                                                                                                                                                                                                                                              | Owns                                |
| ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| `apps/frontend/src/features/chat/components/subagents/subagentCardViewModel.ts`     | **EXTEND.** Add `pauseReason?: "approval"                                                                                                                                                                                                                                                                           | "mcp_auth"                          | "ask_a_question"`and`pauseSourceEventId?: string`to`SubagentCardViewModel`. Mapper extracts them from `SubagentEntry`(or, in the in-thread reducer's case, from the latest`subagent_paused`payload merged into the`args` accumulator). | View-model contract for both `<FleetSubagentRow>` and `<SubagentCard>`. |
| `apps/frontend/src/features/chat/components/subagents/FleetSubagentRow.tsx`         | **EXTEND.** Local `expanded` state. Render paused chrome (`data-status`, paused chip, reason sub-label, frozen progress). Render inline `<SubagentActivityList>` under the row when `expanded`. Render `<JumpToApprovalLink>` when paused + source resolvable. Keyboard handlers.                                   | Row visual + click-to-expand state. |
| `apps/frontend/src/features/chat/components/subagents/SubagentCard.tsx`             | **EXTEND.** Paused chrome variant — badge tone `warning` with "Paused" + reason sub-label. No structural change to existing chrome.                                                                                                                                                                                 | Card visual variant.                |
| `apps/frontend/src/features/chat/components/citations/scrollChatToCitation.ts`      | **EXTEND.** Add `scrollChatToEvent(event_id)` helper alongside `scrollChatToCitation`. Same anchor mechanism, different lookup key.                                                                                                                                                                                 | Scroll/jump helper.                 |
| `apps/frontend/src/features/chat/components/workspace/AgentsTab.tsx`                | **EXTEND.** Pass `onJumpToApproval` to `<SubagentCard>` when entry is paused. Resolves `pauseSourceEventId` → `scrollChatToEvent`.                                                                                                                                                                                  | Pane parity.                        |
| `apps/frontend/src/styles.css`                                                      | **EXTEND.** New rules under `.subagent-fleet-row[data-status="paused"]` (amber indicator, paused chip, frozen-progress pulse). New rules for clickable row (`.subagent-fleet-row--clickable`, focus-visible, `[aria-expanded="true"]`). New `--paused-amber` token referencing existing design-system color tokens. | CSS variant. ~120 LoC.              |
| `apps/frontend/src/features/chat/components/subagents/FleetSubagentRow.test.tsx`    | **EXTEND.** New cases per §1.6.                                                                                                                                                                                                                                                                                     | Row regressions.                    |
| `apps/frontend/src/features/chat/components/subagents/SubagentCard.test.tsx`        | **EXTEND.** New cases per §1.6.                                                                                                                                                                                                                                                                                     | Card regressions.                   |
| `apps/frontend/src/features/chat/components/workspace/AgentsTab.test.tsx`           | **EXTEND.** Pane jump-to-approval case.                                                                                                                                                                                                                                                                             | Pane regressions.                   |
| `apps/frontend/src/features/chat/components/citations/scrollChatToCitation.test.ts` | **EXTEND.** `scrollChatToEvent` cases.                                                                                                                                                                                                                                                                              | Helper regressions.                 |

**Not changed**: `packages/api-types/` (Phase 3 already added `paused`), backend, migrations, design-system primitives, the in-thread reducer's `args` accumulator structure (Phase 3 already covers it).

### 2.3 The view-model extension

```typescript
// subagentCardViewModel.ts (excerpt)

export type SubagentPauseReason = "approval" | "mcp_auth" | "ask_a_question";

export interface SubagentCardViewModel {
  // ...existing fields (taskId, name, task, finding, status, terminal,
  // startedAt, durationMs, fullResult, ...)

  /** Phase 4 — set when status === "paused" so the row/card can render
   *  the right copy. Comes from the most recent `subagent_paused` payload
   *  merged into the entry. */
  pauseReason?: SubagentPauseReason;

  /** Phase 4 — event_id of the gating interrupt event on the same thread.
   *  Used by the row's "Review approval →" link to anchor-scroll. */
  pauseSourceEventId?: string;
}
```

### 2.4 Row state machine

The row owns one piece of local state: `expanded: boolean`. It is decoupled from `view.status` — opening on a `running` row is fine, opening on a `paused` row reveals the same `<SubagentActivityList>` plus a "Review approval →" link, opening on a `completed` row reveals the final timeline. No automatic expansion on pause; we considered it and rejected it as user-hostile (steals scroll position, surprises in dense fleets).

```typescript
// FleetSubagentRow.tsx (excerpt)

const [expanded, setExpanded] = useState(false);
const isPaused = view.status === "paused";
const showJump = isPaused && view.pauseSourceEventId !== undefined && onJumpToApproval !== undefined;

// Click handler (also handles Enter / Space via onKeyDown).
const toggle = () => setExpanded((s) => !s);

return (
  <>
    <div
      className={`subagent-fleet-row subagent-fleet-row--clickable`}
      data-status={view.status}
      data-paused={isPaused ? "true" : undefined}
      data-task-id={view.taskId ?? undefined}
      role="button"
      tabIndex={0}
      aria-expanded={expanded}
      onClick={toggle}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          toggle();
        }
      }}
    >
      {/* indicator, name, task, progress (frozen when paused), elapsed */}
      {isPaused ? <PausedChip reason={view.pauseReason!} /> : null}
    </div>
    {expanded ? (
      <div className="subagent-fleet-row__inline-timeline" role="region">
        <SubagentActivityList
          activities={activities}
          className="subagent-fleet-row__activity-list"
        />
        {showJump ? (
          <button
            type="button"
            className="subagent-fleet-row__jump-link"
            onClick={(e) => {
              e.stopPropagation();
              onJumpToApproval!(view.pauseSourceEventId!);
            }}
          >
            Review {jumpLabelFor(view.pauseReason!)} →
          </button>
        ) : null}
      </div>
    ) : null}
  </>
);
```

### 2.5 The PausedChip subcomponent

Inline, file-local; no separate primitive.

```typescript
function PausedChip({ reason }: { reason: SubagentPauseReason }): ReactElement {
  return (
    <Badge
      tone="warning"
      className="subagent-fleet-row__paused-chip"
      aria-label={`Paused, ${ariaLabelFor(reason)}`}
    >
      Paused · {labelFor(reason)}
    </Badge>
  );
}

function labelFor(reason: SubagentPauseReason): string {
  switch (reason) {
    case "approval":       return "waiting on approval";
    case "mcp_auth":       return "waiting on connector";
    case "ask_a_question": return "waiting for answer";
  }
}

function ariaLabelFor(reason: SubagentPauseReason): string {
  switch (reason) {
    case "approval":       return "waiting on approval";
    case "mcp_auth":       return "waiting on connector authentication";
    case "ask_a_question": return "waiting for user answer";
  }
}

function jumpLabelFor(reason: SubagentPauseReason): string {
  switch (reason) {
    case "approval":       return "approval";
    case "mcp_auth":       return "connector auth";
    case "ask_a_question": return "question";
  }
}
```

### 2.6 CSS variant

```css
/* styles.css (excerpt; ~120 LoC across the new + paused selectors) */

.subagent-fleet-row--clickable {
  cursor: pointer;
}
.subagent-fleet-row--clickable:hover,
.subagent-fleet-row--clickable:focus-visible {
  background: var(--surface-hover);
}
.subagent-fleet-row--clickable:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}

.subagent-fleet-row[data-status="paused"] .subagent-fleet-row__indicator {
  color: var(--paused-amber);
  animation: subagent-row-pause-pulse 1.6s ease-in-out infinite;
}

@media (prefers-reduced-motion: reduce) {
  .subagent-fleet-row[data-status="paused"] .subagent-fleet-row__indicator {
    animation: none;
  }
}

.subagent-fleet-row[data-paused="true"] .subagent-fleet-row__progress-fill {
  /* Frozen — last `transform: scaleX(...)` value is preserved by inline style.
     A subtle pulse on the bar itself signals "this isn't moving forward". */
  animation: subagent-row-progress-pulse 2s ease-in-out infinite;
}

.subagent-fleet-row__paused-chip {
  margin-left: var(--space-2);
  flex-shrink: 0;
}

.subagent-fleet-row__inline-timeline {
  padding: var(--space-2) var(--space-3) var(--space-2)
    calc(var(--space-3) + var(--row-indent));
  border-top: 1px solid var(--border-subtle);
  background: var(--surface-recessed);
}

.subagent-fleet-row__jump-link {
  margin-top: var(--space-2);
  font-size: var(--text-sm);
  color: var(--accent);
  background: none;
  border: none;
  cursor: pointer;
  padding: 0;
}
.subagent-fleet-row__jump-link:hover,
.subagent-fleet-row__jump-link:focus-visible {
  text-decoration: underline;
}

@keyframes subagent-row-pause-pulse {
  0%,
  100% {
    opacity: 0.6;
  }
  50% {
    opacity: 1;
  }
}
@keyframes subagent-row-progress-pulse {
  0%,
  100% {
    opacity: 0.4;
  }
  50% {
    opacity: 0.7;
  }
}
```

`--paused-amber` references an existing design-system warning token; we don't add a new color value.

### 2.7 Jump-to-approval

```typescript
// scrollChatToCitation.ts (excerpt — adds a sibling export)

export function scrollChatToEvent(event_id: string): void {
  const anchor = document.querySelector<HTMLElement>(
    `[data-event-id="${cssEscape(event_id)}"]`,
  );
  if (anchor === null) return; // no-op when event isn't on the visible thread
  anchor.scrollIntoView({ behavior: "smooth", block: "center" });
  anchor.dataset.flashHighlight = "true";
  window.setTimeout(() => {
    delete anchor.dataset.flashHighlight;
  }, 1200);
}
```

The approval / auth / ask-a-question card components already render a `data-event-id` attribute on their root (used by the existing focus-management infrastructure). No new wiring there.

### 2.8 Failure modes

| Failure                                                                                                                | Behavior                                                                                                                                                                                                                        |
| ---------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pauseSourceEventId` set, but the matching event isn't in the rendered chat thread (reconnect, scrolled out of mount). | `scrollChatToEvent` no-ops on missing anchor. The row's "Review approval →" link is still visible (we don't pre-validate); clicking it does nothing visible. Acceptable trade-off vs. an extra registry lookup on every render. |
| `pauseReason` arrives as a value not in the union (server bug or future extension).                                    | The view-model mapper coerces unknown values to `undefined`, falling back to a generic "Paused" chip without a sub-label. Status flip still works.                                                                              |
| User clicks the row while a pause/resume cycle is in flight (race between click and reducer flip).                     | `expanded` toggles regardless of status — the inline timeline reflects whatever state is current at render time. No-op race.                                                                                                    |
| User has reduced-motion preference.                                                                                    | Pulse animations replaced with steady amber per the `@media (prefers-reduced-motion: reduce)` branch. Functionality preserved.                                                                                                  |
| Many rows expanded in a dense fleet.                                                                                   | Browser-handled. Each disclosure is independent; no virtualization needed at typical fleet sizes (≤ 10).                                                                                                                        |
| `view.activities` are mutating in real-time while expanded.                                                            | `<SubagentActivityList>` re-renders on each event flush — same as it does in the standalone `<SubagentCard>` and the pane card. No special handling needed.                                                                     |

---

## 3 · Library evaluation

### 3.1 Inline-disclosure mechanism

| Approach                                                              | Pro                                                                | Con                                                                                                                                                                                                                                                                    |
| --------------------------------------------------------------------- | ------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A. Local `useState<boolean>` + CSS grid-row reveal (this PR).**     | Zero deps. Independent per row. Matches the PR 3.2.1 pane pattern. | Manual `aria-expanded` wiring. Manual keyboard handlers.                                                                                                                                                                                                               |
| B. Native `<details>`/`<summary>` (as `<SubagentCard>` already uses). | Free a11y, free keyboard.                                          | Conflicts with making the whole row clickable — `<summary>` IS the click target. We'd have to either drop click-on-row or nest `<details>` inside a stop-propagation outer click region (anti-pattern). **Rejected: incompatible with the row-as-button requirement.** |
| C. Radix Disclosure primitive.                                        | Strong a11y out of the box.                                        | New dep, new bundle weight. The PR 3.2.1 pattern (manual state + aria-expanded) is already proven and lighter. **Rejected: no new dep needed.**                                                                                                                        |

**Decision: A.** Matches existing patterns; no new deps.

### 3.2 Paused chip

| Approach                                                                  | Pro                                      | Con                                                                                                                                                                                                     |
| ------------------------------------------------------------------------- | ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A. `Badge tone="warning"` from `@0x-copilot/design-system` (this PR).** | One line. Matches Atlas tone vocabulary. | None.                                                                                                                                                                                                   |
| B. New `PausedChip` design-system primitive.                              | Reuse if needed elsewhere.               | Premature abstraction — only used in two places, both in the chat feature. Per [`packages/design-system/CLAUDE.md`](../../packages/design-system/CLAUDE.md): "Feature workflows stay in apps/frontend." |

**Decision: A.**

### 3.3 Jump-to-approval helper

| Approach                                                                              | Pro                                  | Con                                                                          |
| ------------------------------------------------------------------------------------- | ------------------------------------ | ---------------------------------------------------------------------------- |
| **A. Sibling `scrollChatToEvent` next to existing `scrollChatToCitation` (this PR).** | Co-located. Reuses anchor mechanism. | Two helpers with similar shape.                                              |
| B. Generalize to one `scrollChatToAnchor(selector)`.                                  | One function.                        | Couples callsites to selector strings. Ergonomics worse.                     |
| C. New `useScrollToEvent` hook.                                                       | React-native API.                    | No state to track; a hook is over-spend for a single imperative scroll call. |

**Decision: A.**

---

## 4 · File change summary

```
apps/frontend/src/features/chat/components/subagents/
  subagentCardViewModel.ts                           EXTEND   ~+15  LoC   add pauseReason / pauseSourceEventId fields + mapper
  FleetSubagentRow.tsx                               EXTEND   ~+90  LoC   useState expanded + paused chrome + inline timeline + jump link
  SubagentCard.tsx                                   EXTEND   ~+15  LoC   paused badge variant
  FleetSubagentRow.test.tsx                          EXTEND   ~+250 LoC   12 new cases per §1.6
  SubagentCard.test.tsx                              EXTEND   ~+60  LoC   3 new cases
  subagentCardViewModel.test.ts                      EXTEND   ~+30  LoC   pauseReason / pauseSourceEventId mapping cases

apps/frontend/src/features/chat/components/citations/
  scrollChatToCitation.ts                            EXTEND   ~+25  LoC   scrollChatToEvent helper
  scrollChatToCitation.test.ts                       EXTEND   ~+40  LoC   2 new cases

apps/frontend/src/features/chat/components/workspace/
  AgentsTab.tsx                                      EXTEND   ~+15  LoC   wire onJumpToApproval into <SubagentCard>
  AgentsTab.test.tsx                                 EXTEND   ~+50  LoC   1 new case

apps/frontend/src/features/chat/components/tools/
  SubagentFleetTool.integration.test.tsx             NEW or EXTEND ~+120 LoC  2 new integration cases

apps/frontend/src/styles.css                          EXTEND   ~+120 LoC   paused chrome + clickable row + inline-timeline grid reveal

# nothing else changes
services/                                            0
packages/api-types/                                  0
packages/design-system/                              0
migrations/                                          0
```

Net new ≈ 200 LoC component code + 120 LoC CSS + 550 LoC tests.

---

## 5 · Verification checklist

- [ ] `npm run typecheck --workspace @0x-copilot/frontend` → clean.
- [ ] `npm run build --workspace @0x-copilot/frontend` → clean.
- [ ] `npm test --workspace @0x-copilot/frontend` → all green; new cases pass; existing PR 3.2.4 / 3.2.2 cases unchanged.
- [ ] `axe-core` violations: zero on paused state, zero on clickable row, zero on inline timeline.
- [ ] Manual canary on `make dev`:
  - Trigger a 3-agent fleet with one subagent gated by `MCP_AUTH_REQUIRED`.
  - Confirm: paused row shows amber indicator + "Paused · waiting on connector" chip; siblings keep ticking.
  - Click the paused row: inline timeline expands; "Review connector auth →" link present.
  - Click the link: chat scrolls to and highlights the gating MCP auth card.
  - Authorize the gate: row reverts to running chrome; siblings unchanged.
  - Re-trigger with `APPROVAL_REQUESTED` and `ASK_A_QUESTION`: same behavior, different reason copy.
- [ ] Reduced-motion check: emulate via DevTools, confirm pulse animations off, static amber indicator.
- [ ] Browser visual review: paused chip contrast, click target ≥ 32×32, no layout shift on row expansion.
- [ ] `git diff packages/api-types/` is empty.
- [ ] `git diff services/` is empty.

---

## 6 · Out of scope (follow-ups)

- **Approvals tab badge for paused subagents.** A live badge on the workspace pane Approvals tab counting paused subagents. Cheap once the data is there; adds a registry subscriber. Tracked here.
- **Per-subagent cancel.** Today cancel is run-level. A "Cancel this subagent" affordance on the row would be a runtime feature, not a visual one — outside this PR's surface.
- **Telemetry on click-through.** How often users open the inline timeline; how often they click "Review approval →". Useful for refining the affordance hierarchy if real usage shows the pane jump being ignored.
- **Paused-chrome variant in the conversation history list.** The sidebar shows conversation summaries; a paused subagent doesn't surface there. Could add a pulse on the conversation row if a subagent in the most recent run is paused. Not urgent.
- **Animation refinement.** If the amber pulse + progress pulse compounds visually with many paused rows, we may want a single shared reduced animation. Address only if real users complain.

---

## References

- [`docs/new-design/pr-3.2.6-subagent-paused-resumed-events.md`](./pr-3.2.6-subagent-paused-resumed-events.md) — Phase 3, the wire + reducer.
- [`docs/new-design/pr-3.2.5-subagent-call-id-propagation.md`](./pr-3.2.5-subagent-call-id-propagation.md) — Phase 1 deterministic linkage.
- [`docs/new-design/pr-3.2.4-fleet-nests-compact-rows.md`](./pr-3.2.4-fleet-nests-compact-rows.md) — the row component this PR extends.
- [`docs/new-design/pr-3.2.2-subagent-card-shared-primitive.md`](./pr-3.2.2-subagent-card-shared-primitive.md) — the card primitive this PR extends.
- [`docs/new-design/pr-3.2.1-agents-tab-expandable-timeline.md`](./pr-3.2.1-agents-tab-expandable-timeline.md) — the disclosure pattern this PR copies.
- [`apps/frontend/src/features/chat/components/subagents/FleetSubagentRow.tsx`](../../apps/frontend/src/features/chat/components/subagents/FleetSubagentRow.tsx) — current PR 3.2.4 row.
- [`apps/frontend/src/features/chat/components/subagents/SubagentCard.tsx`](../../apps/frontend/src/features/chat/components/subagents/SubagentCard.tsx) — current PR 3.2.2 card.
- [`apps/frontend/src/features/chat/components/citations/scrollChatToCitation.ts`](../../apps/frontend/src/features/chat/components/citations/scrollChatToCitation.ts) — existing scroll helper.
- [`packages/design-system/src`](../../packages/design-system/src) — `Badge` primitive (`tone="warning"`).
