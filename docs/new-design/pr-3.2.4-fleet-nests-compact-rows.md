# PR 3.2.4 — Fleet card nests its subagents as compact rows

> **Status:** Draft (PRD + Spec) · v1
> **Plan reference:** Wave 3 follow‑up to [`pr-3.2.2-subagent-card-shared-primitive.md`](./pr-3.2.2-subagent-card-shared-primitive.md) and [`pr-3.2.3-subagent-backend-completion.md`](./pr-3.2.3-subagent-backend-completion.md). Closes the visible "duplicate rendering" bug observed after PR 3.2.2 shipped (3 standalone `<SubagentCard>` blocks + 1 empty fleet card carrying nothing).
> **Owner:** frontend only. ai‑backend / facade / api‑types / migrations: **none**.
> **Size:** **S.** One new primitive (`<FleetSubagentRow>`), one render‑time reshape in the message dispatcher, one chat‑tree helper, ~80 LoC CSS. Net code ~+260 LoC counting tests.
> **Depends on:** ✅ PR 3.2.1 (timeline + selector) · ✅ PR 3.2.2 (`<SubagentCard>` + `subagentCardViewModel`) · existing `SubagentFleetCard` (accepts `children` already). PR 3.2.3 lands independently.
> **Reads alongside:** [`pr-3.2.2-subagent-card-shared-primitive.md`](./pr-3.2.2-subagent-card-shared-primitive.md), [`apps/frontend/CLAUDE.md`](../../apps/frontend/CLAUDE.md).

---

## 0 · TL;DR

The current chat thread renders **two visuals for the same three subagents**:

```
┌──────────────────────────────────────────┐
│  ✓  GENERAL PURPOSE                      │   ← <SubagentCard> (standalone)
│     Writing a self-contained Python…     │
│     Completed in 2.0s ▾                  │
└──────────────────────────────────────────┘
┌──────────────────────────────────────────┐
│  ✓  GENERAL PURPOSE                      │   ← <SubagentCard> (standalone)
│     …whether an integer n is prime.       │
│     Completed in 2.7s ▾                  │
└──────────────────────────────────────────┘
┌──────────────────────────────────────────┐
│  ✓  GENERAL PURPOSE                      │   ← <SubagentCard> (standalone)
│     …prime factors of an integer n…       │
│     Completed in 3.3s ▾                  │
└──────────────────────────────────────────┘
┌──────────────────────────────────────────┐
│  ⌘  Dispatched 3 subagents in parallel   │   ← <SubagentFleetCard> (no children!)
│     They'll keep working while we draft.  │
│     0:03                                  │
└──────────────────────────────────────────┘
```

The design wants **one card with three nested compact rows**:

```
┌────────────────────────────────────────────────────────────────────┐
│  ⌘  Dispatched 3 subagents in parallel        3 running · 0/3 done│
│  They'll keep working while we draft. Live status in Agents tab.  │
│                                                                    │
│  ○  Doc reader                                                    │
│     Read positioning + GTM plan, extract claims     ━━━━━     11s│
│  ○  Press scout                                                   │
│     Summarize last 18 #launch-aurora messages      ━━━       10s│
│  ○  Voice reviewer                                                │
│     Check existing draft against Brand voice 2026   ━━━━     11s│
│                                                                    │
│  ☷  Subagents run in parallel — keep chatting…   View in workspace→│
└────────────────────────────────────────────────────────────────────┘
```

Two pieces of work:

1. **A new compact primitive** `<FleetSubagentRow>` — single‑line row with status dot · name · 1‑line task · progress bar · elapsed time. **No disclosure. No badge. No header.** This is the visual the design specs as a fleet child.
2. **Render‑time reshape** in the message dispatcher: when a `run_subagent` part has `parent_fleet_id` matching a sibling `subagent_fleet` part, attach it as a child of the fleet card and hide it from the standalone subagent path. Standalone subagents (no fleet) keep rendering as `<SubagentCard>` exactly as today.

**Zero backend, zero migration, zero new dep.** The chat‑tree shape is unchanged; only the renderer's interpretation of it changes.

LoC estimate: frontend ≈ +200 (new primitive + dispatcher reshape) − 30 (deletes from MessageParts paths) = **net ~+170 LoC** plus tests.

---

## 1 · PRD

### 1.1 Problem

Live screenshot of the prime‑checker scenario (3 subagents in one fleet) shows:

- Three full‑chrome `<SubagentCard>` blocks rendered above the fleet card
- One fleet card ("Dispatched 3 subagents in parallel · 3/3 done") rendered standalone, **with zero rows inside it**

The fleet card today is a label without a body. The three subagents are rendered without their parent context. To a non‑engineer scanning the thread:

1. **It's three big cards saying almost the same thing** — visually noisy, can't scan.
2. **The fleet card looks broken** — "Dispatched 3 subagents in parallel" with no subagents inside.
3. **There's no visible "this work runs in parallel" framing** — the 3‑up nesting and the per‑row progress bar are what tell the user "these run side‑by‑side; check the workspace pane for live status."

The design ([handoff bundle, `messages.jsx:207-243`](/tmp/design-fetch/extracted/0x-copilot/project/messages.jsx) — `SubagentFleet`) shows the fleet card carrying compact one‑line rows for each child subagent. Each row has a status indicator, name, brief task, progress bar (animated while running), and elapsed time. **The fleet card IS the container.**

The codebase has the right contract — `SubagentFleetCard` already accepts `children?: ReactNode` (per [`SubagentFleetCard.tsx:30`](../../apps/frontend/src/features/chat/components/messages/SubagentFleetCard.tsx#L30)). What's missing:

- A compact row primitive to fill the children slot
- Render‑time logic to identify which `run_subagent` parts belong to which fleet and pass them in as children
- Suppression of standalone `<SubagentCard>` for any subagent that's already nested under a fleet

### 1.2 Goals

1. **One card per fleet, three rows inside it.** Match the design's `SubagentFleet` rendering one‑for‑one.
2. **Standalone subagents (no fleet) keep their current `<SubagentCard>` rendering.** Fleet rendering does not regress single‑subagent flows.
3. **Compact row primitive is decoupled from `<SubagentCard>`.** Different visual purpose, different layout — a fleet row is a list item, not a card. Sharing the card would cost more in CSS overrides than reuse.
4. **Reuse the same data adapter.** `<FleetSubagentRow>` consumes a tightened slice of the existing `SubagentCardViewModel` (same source of truth — name, task, status, durationMs, terminal). No second projection.
5. **Live progress bar.** While `status === "running"`, the row shows a continuous progress animation (CSS only, no JS). On `completed/cancelled/failed`, the bar fills to its terminal state and freezes.
6. **No disclosure on the fleet row.** Verification details live in the workspace pane (per the design — "View in workspace →" footer link is the affordance). The thread surfaces compact status only.
7. **Suppress the orphan.** A `run_subagent` part whose `parent_fleet_id` matches a rendered `subagent_fleet` part in the same message is **rendered exactly once** — inside the fleet card, never standalone.
8. **Zero backend / contract change.** Frontend reshape only.

### 1.3 Non‑goals

- ❌ **Restructure the chat‑tree reducer to nest subagent parts as children of fleet parts.** The reducer keeps emitting flat `content[]` per its current shape; nesting happens at render time. (Reducer changes are riskier — affects archive replay, event idempotence, every reducer test.)
- ❌ **Per‑row click‑to‑expand inline timeline.** Compact rows do not own a disclosure; the workspace pane is the verification surface (PR 3.2.1). Adding a per‑row expand muddies the design's intent (the fleet card body becomes its own scroll surface) and re‑introduces the duplicate‑disclosure problem.
- ❌ **Per‑row jump‑to‑pane affordance.** The fleet footer's "View in workspace →" link is the single navigation point. Cluttering each row with its own link is noise.
- ❌ **Fleet of 1.** When a single subagent runs without a fleet wrapper (the `task` tool was invoked once, no `subagent_fleet` part was emitted), it renders as a standalone `<SubagentCard>`. Wrapping a single subagent in a fleet card would add chrome for no benefit.
- ❌ **Cancel from a fleet row.** Cancel lives on `<SubagentCard>` per PR 3.2.3. Fleet rows stay clean.
- ❌ **Backend per‑row token usage.** Same reasoning as PR 3.2.3 §1.3.
- ❌ **Promote `<FleetSubagentRow>` to design‑system.** Premature; same reasoning as PR 3.2.2 §1.3.

### 1.4 Acceptance criteria

| #     | Criterion                                                                                                                                                                                                                                                                                                        | Verified by                                         |
| ----- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------- |
| AC‑1  | When an assistant message contains one `subagent_fleet` part with `fleet_id = X` and three `run_subagent` parts with `parent_fleet_id = X`, the rendered DOM has exactly one `<SubagentFleetCard>` containing three `<FleetSubagentRow>` elements. There is no standalone `<SubagentCard>` for any of the three. | RTL test on `MessageParts` with a fixture.          |
| AC‑2  | Each `<FleetSubagentRow>` renders: status indicator (spinner / ✓ / ✕ / ‑), name (formatted via `formatAgentName`), 1‑line task (truncated to ~80 chars with ellipsis), progress bar, elapsed time. No badge, no disclosure, no jump button.                                                                      | RTL test on `<FleetSubagentRow>`.                   |
| AC‑3  | Progress bar is **animated** while `status ∈ {running, queued}` and **freezes filled** when terminal. Animation is CSS‑only and respects `prefers-reduced-motion: reduce` (no animation, just a static partial bar).                                                                                             | CSS audit + RTL data attribute test.                |
| AC‑4  | A `run_subagent` part with no `parent_fleet_id` (or with `parent_fleet_id` not matching any sibling fleet part) renders as a standalone `<SubagentCard>` exactly as it does today. Existing PR 3.2.2 tests stay green.                                                                                           | Existing tests + new "no fleet" RTL fixture.        |
| AC‑5  | A fleet card without any matched children (e.g., the dispatch event arrived but the children haven't streamed yet) renders the head + footer with an empty body — the card is still visible, the user sees "Dispatched N subagents in parallel · 0/N running" and waits.                                         | RTL test with a fleet part + zero matched children. |
| AC‑6  | A fleet child whose status is `cancelled`/`failed` renders with the status indicator color and a status word ("cancelled", "failed") inline next to the elapsed time.                                                                                                                                            | RTL test (one per status).                          |
| AC‑7  | The fleet card head's `running / done` count is computed from the children's actual status (e.g., 1 running · 2/3 done) rather than the static `total / completed` flags on the fleet part. Mid‑stream, the head updates as children flip status.                                                                | RTL test simulating a status flip.                  |
| AC‑8  | The fleet card foot retains the existing "View in workspace →" link and the "Subagents run in parallel — keep chatting and they'll report back." copy.                                                                                                                                                           | RTL test.                                           |
| AC‑9  | Bundle delta ≤ +1.5 KB gz vs PR 3.2.3 baseline. No new npm dep.                                                                                                                                                                                                                                                  | `npm run build` size diff.                          |
| AC‑10 | `prefers-reduced-motion: reduce` honored: progress‑bar animation disabled; spinner becomes a static dot.                                                                                                                                                                                                         | CSS audit.                                          |
| AC‑11 | Existing tests stay green: PR 3.2.1 disclosure tests, PR 3.2.2 `<SubagentCard>` tests, all `chatModel` reducer tests. The fleet fix does not break any of them (it only changes how fleet _children_ render).                                                                                                    | Full vitest suite.                                  |
| AC‑12 | Narrow viewport (< 600 px chat column): fleet rows wrap gracefully (task line clamps to 1 line; progress bar narrows; elapsed stays right‑aligned). No horizontal scroll.                                                                                                                                        | CSS audit + manual narrow viewport check.           |

### 1.5 Risks

| Risk                                                                                                                                                             | Mitigation                                                                                                                                                                                                                                            |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Render‑time reshape (looking up sibling parts within a message) is O(n²) on a large message with many tool calls.                                                | The reshape is a single pass: build `Set<fleet_id>` of all fleet parts in O(n), then map each `run_subagent` part to fleet O(1) via the set. Total O(n). Fixture test asserts behavior with 50+ tool calls in one message.                            |
| A `parent_fleet_id` arrives before the matching `subagent_fleet` part (out‑of‑order events).                                                                     | The reshape runs at every render. Once both parts are present, they reconcile naturally — no special handling needed. Until then, the orphan `run_subagent` part renders standalone (graceful degradation, never lost). Asserted in a test fixture.   |
| Live status flip from `running` → `completed` causes the row to re‑mount (animation restarts).                                                                   | `<FleetSubagentRow>` keys by `task_id`. Status is rendered through props; animation lives in CSS keyed by `[data-status]`. No re‑mount; the bar transitions in place.                                                                                 |
| Fleet card head count drifts from real children counts (because the worker emits `total/done` separately from the per‑child events).                             | The head computes `running / done` directly from the children we actually rendered (AC‑7). The worker's flags become advisory; live state is derived. Tested.                                                                                         |
| Backwards compat: archive reads of conversations with old fleet events render correctly.                                                                         | Existing reducer behavior unchanged. The reshape is purely a render‑time projection over the existing tree. Archive parity test (PR 3.2.1 AC‑5 pattern) covers this.                                                                                  |
| Standalone subagent (no fleet) regression — the renderer accidentally absorbs it into a non‑existent fleet.                                                      | The reshape only nests when a sibling `subagent_fleet` part exists with the matching `fleet_id`. If no fleet match, the `run_subagent` part falls through to the standalone path (its existing behavior). Explicit "no fleet" test fixture (AC‑4).    |
| The compact row's truncation hides important state (e.g., a failed subagent's error code).                                                                       | Failed/cancelled rows display the status word inline ("failed · 8s", "cancelled · 12s") so the lifecycle is visible. The full error text lives in the workspace pane card (PR 3.2.2's `<SubagentCard>`) which is one click away via the fleet footer. |
| Same‑role badge on every standalone card was identified as a UX miss in the previous diagnosis (every card said "GENERAL PURPOSE"). PR 3.2.4 doesn't address it. | Out of scope. Same‑role badge handling lives in `<SubagentCard>` and ships in PR 3.2.5 (TBD). Compact fleet rows don't show a role badge at all, so the badge‑monotony issue doesn't propagate into the new visual.                                   |

### 1.6 Unit testing requirements

- `apps/frontend/src/features/chat/components/subagents/FleetSubagentRow.test.tsx` — NEW. AC‑2, 3, 6.
- `apps/frontend/src/features/chat/components/subagents/fleetReshape.test.ts` — NEW. Pure function: takes a `ThreadMessageContent` array and returns `{ fleetParts: Map<fleet_id, ...>, orphanedSubagents: ToolCallPart[], absorbedTaskIds: Set<task_id> }`. AC‑1, 4, 5, 7.
- `apps/frontend/src/features/chat/runtime/components/MessageParts.test.tsx` (extend) — integration: render a fixture message with a fleet + 3 children → DOM has 1 fleet card with 3 rows; render a fixture with a lone subagent → DOM has 1 `<SubagentCard>`.
- `apps/frontend/src/features/chat/components/messages/SubagentFleetCard.test.tsx` (extend if exists, else NEW) — head count derives from children (AC‑7).

---

## 2 · Spec

### 2.1 Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│  Existing chat tree (unchanged)                                    │
│    ChatItem.kind === "message"                                     │
│      content: [                                                    │
│        ToolCallPart{ toolName: "subagent_fleet", args.fleet_id="F1"},│
│        ToolCallPart{ toolName: "run_subagent",   args.parent_fleet_id="F1", task_id="T1"},│
│        ToolCallPart{ toolName: "run_subagent",   args.parent_fleet_id="F1", task_id="T2"},│
│        ToolCallPart{ toolName: "run_subagent",   args.parent_fleet_id="F1", task_id="T3"},│
│        ToolCallPart{ toolName: "run_subagent",   args (no parent_fleet_id), task_id="T4"},│
│        TextPart{ ... assistant prose ... },                        │
│      ]                                                             │
└────────────────────────┬───────────────────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────────────────────┐
│  NEW render-time reshape (per message, in MessageParts dispatch)   │
│    reshapeFleetTree(content) →                                     │
│      {                                                             │
│        fleetIds: Set("F1"),                                        │
│        childrenByFleet: Map("F1" → [run_subagent T1, T2, T3]),     │
│        absorbedTaskIds: Set("T1","T2","T3"),                       │
│      }                                                             │
│                                                                    │
│  In the dispatch loop:                                             │
│    for (const part of content) {                                   │
│      if (part.toolName === "subagent_fleet") {                     │
│        render <SubagentFleetCard ...>                              │
│          {childrenByFleet.get(part.args.fleet_id).map(child =>     │
│            <FleetSubagentRow                                       │
│              view={subagentCardFromArgs(child.args, …)}            │
│              progress={child.args.progress}                        │
│            />)}                                                    │
│      }                                                             │
│      else if (part.toolName === "run_subagent" &&                  │
│               absorbedTaskIds.has(part.toolCallId)) {              │
│        // skip — already rendered as a fleet child                 │
│      }                                                             │
│      else if (part.toolName === "run_subagent") {                  │
│        render <SubagentTool ...>  ← existing path (PR 3.2.2)       │
│      }                                                             │
│    }                                                               │
└────────────────────────────────────────────────────────────────────┘
```

The reshape is **pure** and **per‑message**. It runs once per render of an assistant message; cost is O(n) in the message's content length.

### 2.2 Module boundaries

| Layer                                                                           | Module                                                                                                                                                                                                                            | Owns                                                                                                                                                                      |
| ------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `apps/frontend/src/features/chat/components/subagents/FleetSubagentRow.tsx`     | **NEW** — pure presentational compact row. Status indicator + name + 1‑line task + progress bar + elapsed.                                                                                                                        | UI only. Reads a tightened slice of `SubagentCardViewModel` (`name`, `status`, `task`, `terminal`, `durationMs`, `startedAt`) plus an optional numeric `progress` (0..1). |
| `apps/frontend/src/features/chat/components/subagents/fleetReshape.ts`          | **NEW** — pure helper. `reshapeFleetTree(content) → { childrenByFleet, absorbedTaskIds, fleetIds }`. ~40 LoC.                                                                                                                     | Single source of truth for "which subagents belong to which fleet." Tested without React.                                                                                 |
| `apps/frontend/src/features/chat/runtime/components/MessageParts.tsx`           | **EXTEND** — call `reshapeFleetTree(content)` once per message; in the dispatch loop, skip absorbed `run_subagent` parts and pass children to fleet card.                                                                         | Render dispatch only. ~20 LoC.                                                                                                                                            |
| `apps/frontend/src/features/chat/components/tools/SubagentFleetTool.tsx`        | **EXTEND** — pass `children` through to `<SubagentFleetCard>`. Compute `running/done` from children's lifecycle status rather than static flags. Drop the existing `agentIds.length` count derivation in favor of children count. | Plug‑through. ~20 LoC.                                                                                                                                                    |
| `apps/frontend/src/features/chat/components/messages/SubagentFleetCard.tsx`     | **MINOR EXTEND** (already accepts `children`) — confirm head count uses derived counts; no other change.                                                                                                                          |                                                                                                                                                                           |
| `apps/frontend/src/features/chat/components/subagents/subagentCardViewModel.ts` | **UNCHANGED**. Existing adapter feeds the row via the same `subagentCardFromArgs` builder.                                                                                                                                        |                                                                                                                                                                           |
| `apps/frontend/src/styles.css`                                                  | **EXTEND** — `subagent-fleet-row` BEM block (~80 LoC). Includes `prefers-reduced-motion` opt‑out for the progress animation.                                                                                                      |                                                                                                                                                                           |

**Not touched:** any backend, any reducer, any event variant, any contract.

### 2.3 What we do NOT add

- ❌ A reducer change to nest fleet children at chat‑tree assembly time. The renderer is the right layer for view‑shape concerns.
- ❌ A new tool name or event type.
- ❌ A new design‑system primitive.
- ❌ A second projection. `<FleetSubagentRow>` consumes the same `SubagentCardViewModel` slice that `<SubagentCard>` consumes.
- ❌ A new dep. CSS animations only; no `framer-motion`, no `react-spring`.

### 2.4 `<FleetSubagentRow>` shape

```tsx
// apps/frontend/src/features/chat/components/subagents/FleetSubagentRow.tsx

import type { ReactElement } from "react";
import type { SubagentCardViewModel } from "./subagentCardViewModel";
import { useElapsedSeconds } from "../tools/useElapsedSeconds";

export interface FleetSubagentRowProps {
  view: SubagentCardViewModel;
  /** 0..1 advisory; fed by the worker's progress events when present. */
  progress?: number | null;
}

export function FleetSubagentRow({
  view,
  progress,
}: FleetSubagentRowProps): ReactElement {
  const elapsed = useElapsedSeconds(!view.terminal, view.startedAt);
  const elapsedLabel =
    view.terminal && view.durationMs !== null
      ? formatDuration(view.durationMs)
      : `${elapsed}s`;
  const fillFraction = view.terminal
    ? 1
    : typeof progress === "number"
      ? Math.max(0, Math.min(1, progress))
      : null;
  return (
    <div
      className="subagent-fleet-row"
      data-status={view.status}
      data-task-id={view.taskId ?? undefined}
    >
      <span className="subagent-fleet-row__indicator" aria-hidden="true">
        {indicator(view.status)}
      </span>
      <div className="subagent-fleet-row__text">
        <div className="subagent-fleet-row__name">{view.name}</div>
        {view.task ? (
          <div className="subagent-fleet-row__task">{view.task}</div>
        ) : null}
      </div>
      <span
        className="subagent-fleet-row__progress"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={1}
        aria-valuenow={fillFraction ?? undefined}
      >
        <span
          className="subagent-fleet-row__progress-fill"
          style={
            fillFraction !== null
              ? { transform: `scaleX(${fillFraction})` }
              : undefined
          }
        />
      </span>
      <span className="subagent-fleet-row__elapsed">
        {elapsedLabel}
        {(view.status === "failed" || view.status === "cancelled") &&
        view.terminal
          ? ` · ${view.status}`
          : ""}
      </span>
    </div>
  );
}
```

`indicator(status)` returns `○` (queued / running — animated dot via CSS), `✓` (completed), `✕` (failed/timed_out), `‑` (cancelled). All glyphs are accessible via `aria-hidden`; the status is also encoded in `[data-status]` for testing + the screen‑reader announcement comes from the parent fleet card's count.

### 2.5 `reshapeFleetTree` — pure helper

```ts
// apps/frontend/src/features/chat/components/subagents/fleetReshape.ts

import { isToolCallPart } from "../../chatModel/recordHelpers";
import { asRecord, stringValue } from "../../utils/jsonUtils";
import type {
  ThreadMessageContent,
  ThreadToolCallPart,
} from "../../chatModel/types";

export interface FleetReshape {
  childrenByFleet: ReadonlyMap<string, readonly ThreadToolCallPart[]>;
  absorbedTaskIds: ReadonlySet<string>;
  fleetIds: ReadonlySet<string>;
}

export function reshapeFleetTree(content: ThreadMessageContent): FleetReshape {
  const fleetIds = new Set<string>();
  for (const part of content) {
    if (!isToolCallPart(part) || part.toolName !== "subagent_fleet") continue;
    const id = stringValue(asRecord(part.args).fleet_id);
    if (id) fleetIds.add(id);
  }
  if (fleetIds.size === 0) {
    return EMPTY_RESHAPE;
  }
  const childrenByFleet = new Map<string, ThreadToolCallPart[]>();
  const absorbedTaskIds = new Set<string>();
  for (const part of content) {
    if (!isToolCallPart(part) || part.toolName !== "run_subagent") continue;
    const fleetId = stringValue(asRecord(part.args).parent_fleet_id);
    if (!fleetId || !fleetIds.has(fleetId)) continue;
    const list = childrenByFleet.get(fleetId) ?? [];
    list.push(part);
    childrenByFleet.set(fleetId, list);
    if (part.toolCallId) absorbedTaskIds.add(part.toolCallId);
  }
  return { childrenByFleet, absorbedTaskIds, fleetIds };
}

const EMPTY_RESHAPE: FleetReshape = {
  childrenByFleet: new Map(),
  absorbedTaskIds: new Set(),
  fleetIds: new Set(),
};
```

Single pass on entry, single pass on `run_subagent` parts. O(n) total.

### 2.6 `MessageParts.tsx` dispatcher change

The existing dispatcher iterates `content` and renders one component per part. The change:

```tsx
// MessageParts.tsx — sketch

const reshape = useMemo(() => reshapeFleetTree(content), [content]);

return content.map((part, index) => {
  if (isToolCallPart(part) && part.toolName === "subagent_fleet") {
    const fleetId = asRecord(part.args).fleet_id;
    const children =
      reshape.childrenByFleet.get(stringValue(fleetId) ?? "") ?? [];
    return (
      <SubagentFleetTool
        key={part.toolCallId ?? index}
        {...part}
        nestedChildren={children}
      />
    );
  }
  if (
    isToolCallPart(part) &&
    part.toolName === "run_subagent" &&
    part.toolCallId !== undefined &&
    reshape.absorbedTaskIds.has(part.toolCallId)
  ) {
    return null; // absorbed by sibling fleet card
  }
  // …existing dispatch for everything else (run_subagent standalone, text, reasoning, other tools)…
});
```

`SubagentFleetTool` is updated to accept `nestedChildren?: readonly ThreadToolCallPart[]`, build a `<FleetSubagentRow>` per child via `subagentCardFromArgs`, and pass them through to `<SubagentFleetCard>`'s `children` slot. Head counts (`running/done`) derive from the children's view‑model statuses (AC‑7).

### 2.7 CSS sketch

```css
/* PR 3.2.4 — fleet child row (compact). */
.subagent-fleet-row {
  align-items: center;
  display: grid;
  grid-template-columns: 16px minmax(0, 1fr) 96px 56px;
  gap: 12px;
  padding: 6px 0;
}

.subagent-fleet-row__indicator {
  align-items: center;
  display: inline-flex;
  height: 16px;
  justify-content: center;
  width: 16px;
}

.subagent-fleet-row[data-status="running"] .subagent-fleet-row__indicator,
.subagent-fleet-row[data-status="queued"] .subagent-fleet-row__indicator {
  color: var(--color-accent-strong);
  animation: subagent-fleet-row-pulse 1.4s ease-in-out infinite;
}

.subagent-fleet-row[data-status="completed"] .subagent-fleet-row__indicator {
  color: var(--color-success);
}

.subagent-fleet-row[data-status="failed"] .subagent-fleet-row__indicator,
.subagent-fleet-row[data-status="timed_out"] .subagent-fleet-row__indicator {
  color: var(--color-danger);
}

.subagent-fleet-row[data-status="cancelled"] .subagent-fleet-row__indicator {
  color: var(--color-warning);
}

.subagent-fleet-row__name {
  color: var(--color-text);
  font-weight: 500;
  font-size: 13px;
}

.subagent-fleet-row__task {
  color: var(--color-text-muted);
  font-size: 11.5px;
  line-height: 1.35;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.subagent-fleet-row__progress {
  background: var(--color-surface-raised);
  border-radius: 999px;
  display: block;
  height: 4px;
  overflow: hidden;
  width: 100%;
}

.subagent-fleet-row__progress-fill {
  background: var(--color-accent-strong);
  display: block;
  height: 100%;
  transform-origin: left center;
  transform: scaleX(0);
  transition: transform 220ms ease;
}

.subagent-fleet-row[data-status="running"] .subagent-fleet-row__progress-fill,
.subagent-fleet-row[data-status="queued"] .subagent-fleet-row__progress-fill {
  animation: subagent-fleet-row-progress 2.4s ease-in-out infinite;
}

.subagent-fleet-row[data-status="completed"]
  .subagent-fleet-row__progress-fill {
  background: var(--color-success);
  transform: scaleX(1);
}

.subagent-fleet-row[data-status="failed"] .subagent-fleet-row__progress-fill,
.subagent-fleet-row[data-status="timed_out"]
  .subagent-fleet-row__progress-fill {
  background: var(--color-danger);
  transform: scaleX(1);
}

.subagent-fleet-row[data-status="cancelled"]
  .subagent-fleet-row__progress-fill {
  background: var(--color-warning);
  transform: scaleX(1);
}

.subagent-fleet-row__elapsed {
  color: var(--color-text-subtle);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  text-align: right;
}

@keyframes subagent-fleet-row-pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.4;
  }
}

@keyframes subagent-fleet-row-progress {
  0% {
    transform: scaleX(0.05);
  }
  50% {
    transform: scaleX(0.55);
  }
  100% {
    transform: scaleX(0.85);
  }
}

@media (prefers-reduced-motion: reduce) {
  .subagent-fleet-row[data-status="running"] .subagent-fleet-row__indicator,
  .subagent-fleet-row[data-status="queued"] .subagent-fleet-row__indicator,
  .subagent-fleet-row[data-status="running"] .subagent-fleet-row__progress-fill,
  .subagent-fleet-row[data-status="queued"] .subagent-fleet-row__progress-fill {
    animation: none;
  }
  .subagent-fleet-row[data-status="running"] .subagent-fleet-row__progress-fill,
  .subagent-fleet-row[data-status="queued"] .subagent-fleet-row__progress-fill {
    transform: scaleX(0.45);
  }
}

@media (max-width: 600px) {
  .subagent-fleet-row {
    grid-template-columns: 16px minmax(0, 1fr) 64px 48px;
    gap: 8px;
  }
}
```

### 2.8 Streaming + persistence walk‑through

The data path is unchanged from PR 3.2.1 / 3.2.2 / 3.2.3:

1. Worker emits `subagent_fleet_started` → reducer adds a `subagent_fleet` tool part to the assistant message content.
2. Worker emits `subagent_started/progress/completed` for each child → reducer adds/updates a `run_subagent` tool part with `args.parent_fleet_id` set.
3. Inner tool calls / reasoning still nest under the child via `parent_task_id` (PR 3.2.1 invariant — preserved).
4. **NEW:** at render time, `MessageParts` calls `reshapeFleetTree(content)` to identify which `run_subagent` parts are absorbed under which fleet, then dispatches accordingly.
5. Live updates: the reducer mutates the tree (immutable update); React re‑renders; the reshape recomputes (cheap O(n)); fleet rows reflect new statuses without unmount.
6. Archive replay: same path. Reducer rebuilds the tree from event replay; renderer reshapes; visuals match live exactly.

No new event variant. No new endpoint. No new persistence column. RLS + encryption posture identical.

### 2.9 Failure modes

| Failure                                                                                       | Behavior                                                                                                                                                                           |
| --------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `subagent_fleet` part exists but no children have streamed yet.                               | Fleet card renders head + footer with empty body. `running/done` count shows `0/N` from the fleet's `agent_ids.length`. As children arrive, rows append.                           |
| A child arrives without a matching `subagent_fleet` part (worker bug or out‑of‑order events). | Reshape doesn't absorb it; renders standalone as `<SubagentCard>`. Graceful degradation, never lost. Logged via the existing event observer.                                       |
| All children present but the fleet part has no `fleet_id`.                                    | Reshape returns `EMPTY_RESHAPE`; children render standalone. (This shouldn't happen in practice — the worker always stamps `fleet_id` — but defensive.)                            |
| Fleet has 1 child only.                                                                       | Renders fleet card with one row. Visually correct (mirrors the design). Matches the user's mental model — "Atlas spawned a parallel batch; it just happened to be a batch of one." |
| Mixed: one fleet with 3 children, plus a separate standalone subagent in the same message.    | Fleet card renders 3 rows; standalone subagent renders as `<SubagentCard>` after the fleet card (in source order). No duplicate, no collision. Tested.                             |
| Reduced motion: animation disabled but progress still informative.                            | Static partial fill at 0.45 scale; status indicator stops pulsing. Status word + elapsed time still convey lifecycle.                                                              |
| Narrow viewport (< 380 px chat column).                                                       | Grid template tightens; task line clamps to one line; elapsed and progress narrow. Tested at 320 px floor (mobile‑equivalent).                                                     |

---

## 3 · Library evaluation

Per PR 3.2.1 §3 / PR 3.2.2 §3 — same posture. Native CSS + zero new deps.

| Candidate                                             | Verdict                                                                                                                                                            |
| ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `framer-motion` for progress animation                | ❌ CSS keyframes do this in ~12 LoC. No JS required. `prefers-reduced-motion` is honored declaratively.                                                            |
| `react-spring` for progress animation                 | ❌ Same.                                                                                                                                                           |
| `@radix-ui/react-progress`                            | ❌ Adds a runtime dep for what `<span>` + `transform: scaleX(x)` does at the same accessibility quality (we set `role="progressbar"` + `aria-valuenow` ourselves). |
| `react-window` / `react-virtualized` for large fleets | ❌ Real fleets are 2–5 children. Virtualisation is unnecessary; CSS grid handles 100+ rows fine.                                                                   |

**Decision:** zero new deps.

---

## 4 · File change summary

```
apps/frontend/src/features/chat/components/subagents/
  FleetSubagentRow.tsx                               ~+90 LoC   NEW compact row primitive
  FleetSubagentRow.test.tsx                          ~+95 LoC   AC-2, 3, 6
  fleetReshape.ts                                    ~+45 LoC   NEW pure helper
  fleetReshape.test.ts                               ~+85 LoC   AC-1, 4, 5

apps/frontend/src/features/chat/components/tools/
  SubagentFleetTool.tsx                              ~+25 LoC   accept nestedChildren; derive head counts
  SubagentFleetTool.test.tsx                         ~+30 LoC   AC-7

apps/frontend/src/features/chat/runtime/components/
  MessageParts.tsx                                   ~+15 LoC   call reshapeFleetTree; skip absorbed parts
  MessageParts.test.tsx                              ~+50 LoC   AC-1, 4 (integration)

apps/frontend/src/features/chat/components/messages/
  SubagentFleetCard.test.tsx                         ~+30 LoC   AC-7 (head count from children)

apps/frontend/src/styles.css                          ~+90 LoC   .subagent-fleet-row BEM

# nothing else changes
services/*                                            0
packages/*                                            0
migrations/*                                          0
package.json                                          0 deps
```

---

## 5 · Verification checklist

- [ ] `npm run typecheck --workspace @0x-copilot/frontend` clean.
- [ ] `npm run test --workspace @0x-copilot/frontend` clean. New tests pass; PR 3.2.1 / 3.2.2 / 3.2.3 tests stay green.
- [ ] `npm run build --workspace @0x-copilot/frontend` clean. Bundle delta ≤ +1.5 KB gz.
- [ ] `make dev`:
  - **Multi‑subagent fleet** (e.g., "run 3 subagents to write GCD, prime check, prime factors"): one fleet card with three nested rows; rows show name + 1‑line task + animated progress + elapsed; no standalone `<SubagentCard>` blocks above or below; head shows live `running/done` derived from children.
  - **Single subagent** (e.g., "write a prime checker"): renders as `<SubagentCard>` exactly as before. No fleet card, no compact row.
  - **Mixed**: fleet of 3 plus a standalone subagent in the same assistant turn → fleet renders 3 rows; standalone renders as `<SubagentCard>` after the fleet.
  - **Failed child mid‑fleet**: row turns red; status word "failed" inline; head count flips to `2/3 done · 1 failed`.
  - **Cancelled fleet (cancel from card via PR 3.2.3)**: child rows show `cancelled` status; progress freezes filled; fleet head shows the right counts.
  - **Narrow viewport** at 380 px: layout doesn't break; task truncates; elapsed stays right‑aligned.
  - **Reduced motion**: animations off; static partial fills.
- [ ] No new entry in `package.json` `dependencies`.

---

## 6 · Out of scope (follow‑ups)

- **PR 3.2.5 — same‑role badge de‑emphasis** when every subagent in a fleet has the same `subagent_name`. Current behavior shows "GENERAL PURPOSE" three times across cards. Worth quieting.
- **PR 3.2.6 — per‑row status flip animation polish** (subtle scale/colour transition when a child flips from running→completed). Out of scope; current CSS transition is sufficient for v1.
- **PR 3.2.7 — fleet row hover preview** (hover a row → tooltip with the result summary). Worth considering once PR 3.2.3's `result_summary` projection ships.
- **Fleet card collapse/expand** (the entire fleet body collapses to "3 done · expand" when the user has read past it and the conversation continues). Quality‑of‑life; tracked.
- **Cross‑message fleet** (a fleet that started in one message and finished in another). Not currently a real case in the worker; placeholder.

---

## References

- [`docs/new-design/pr-3.2.2-subagent-card-shared-primitive.md`](./pr-3.2.2-subagent-card-shared-primitive.md) — `<SubagentCard>` primitive used by the standalone path.
- [`docs/new-design/pr-3.2.3-subagent-backend-completion.md`](./pr-3.2.3-subagent-backend-completion.md) — backend summary projection that will improve the truncated task lines users see in fleet rows.
- [`apps/frontend/src/features/chat/components/messages/SubagentFleetCard.tsx`](../../apps/frontend/src/features/chat/components/messages/SubagentFleetCard.tsx) — already accepts `children`; this PR fills the slot.
- [`apps/frontend/src/features/chat/components/tools/SubagentFleetTool.tsx`](../../apps/frontend/src/features/chat/components/tools/SubagentFleetTool.tsx) — extended to pass children through.
- [`apps/frontend/src/features/chat/runtime/components/MessageParts.tsx`](../../apps/frontend/src/features/chat/runtime/components/MessageParts.tsx) — render dispatcher; gains the reshape call.
- Design prototype reference: `0x-copilot/project/messages.jsx:207-243` (`SubagentFleet` + per‑row layout) from the Anthropic Design handoff bundle.
