# Focus details rail (Agents/Approvals/Sources + collapse strip) — v3 parity plan

## Problem statement

Focus is the v3 **default** Run layout (`defaultDestinationForProfile → "run"`, and
the mode a returning user most often lands in). In the v3 design it is a two‑column
cockpit: a centered reading chat column (`.fx`, `max-width:730`, 13px messages) **plus
a 324px right rail** — `.sd` "Run details" — that carries `[Agents · Approvals ·
Sources]` and collapses to a 46px `.sd-strip` vertical icon rail. That rail is how a
user in Focus sees who is working (Agents), what is holding for sign‑off (Approvals,
with an amber count), and the receipts behind the run (Sources) **without leaving the
calm chat column**.

What ships today is chat‑only. `ThreadCanvas`'s Focus grid is a single centered
`minmax(0, 760px)` column (`gridStyleFor`, ThreadCanvas.tsx:527). `RunWorkspaceRail`
in Focus forces `effectiveTab = "chat"`, suppresses the tablist chrome, and gates the
three panels behind `isStudio` (RunWorkspaceRail.tsx:162, :205, :232/:251/:269), so
Agents/Approvals/Sources are **unreachable in Focus**. No `.sd-strip` exists anywhere.
The #148 keystone gave Focus a real streaming transcript + composer, but it is still a
bare column: a user in the default mode has no way to review a pending payout, watch a
subagent fleet, or open a source — they must switch to Studio to do the very things the
cockpit exists for. That is a real hole in the flagship surface, and it sits on the
default path.

The fix is **pure composition**. `RunDestination` already computes every projection the
details rail needs off the single event stream — `projectSubagents(session.events)`
(RunDestination.tsx:419), `useRunSources(...)` (:441), and
`projectApprovals → toApprovalsQueue` (:462/:474). The tab bodies `SourcesTab`,
`AgentsTab`, `ApprovalsTab` are already hoisted and presentational. No new data, no new
contract — we mount the same bodies in a new Focus slot and add a collapse strip.

## Functional requirements

- **FR‑1 — Focus gets a details rail.** In Focus mode the cockpit MUST render a right
  rail beside the chat column containing exactly three tabs — **Agents, Approvals,
  Sources** (no Chat tab; Chat is the center column). Studio is unchanged
  (`RunWorkspaceRail` keeps its `[Chat · Agents · Approvals · Sources]` tabset).
- **FR‑2 — Reuse the hoisted bodies.** The three tab bodies MUST be the already‑hoisted
  `AgentsTab` / `ApprovalsTab` / `SourcesTab` fed the SAME normalized props
  `RunWorkspaceRail` receives (`subagents`, `approvalsQueue`, `sources` + loading/error).
  No fork, no re‑implementation, no mock `AgentsPanel`/`ApprovalsPanel`/`SourcesPanel`.
- **FR‑3 — Default open, collapsible.** The rail MUST default to open and be
  collapsible via a header chevron (`.sd-h`). Collapsed, it MUST become a 46px vertical
  `.sd-strip` icon rail with an expand chevron and three 32×32 buttons
  (Agents=`activity`, Approvals=`shield`, Sources=`doc`). Clicking a strip icon MUST
  expand the rail AND select that tab.
- **FR‑4 — Persisted collapse state.** The open/collapsed state MUST persist across
  sessions through the `KeyValueStore` port (no bare `localStorage`), mirroring
  `useRailWidth`. Default (nothing stored) = open.
- **FR‑5 — Grid widths match the design.** Focus grid MUST be
  `grid-template-columns: minmax(0,1fr) 324px` open and `minmax(0,1fr) 46px` collapsed
  (copilot-v3.css `.ws3[data-mode="focus"] .ws3-main`). Studio grid is untouched.
- **FR‑6 — Single TcChat, zero remount (crux).** Switching Studio↔Focus, toggling the
  rail collapse, or switching a details tab MUST NOT remount `TcChat`. The one `TcChat`
  stays at its existing stable tree position (inside `RunWorkspaceRail`'s chat panel,
  itself in `ThreadCanvas`'s chat grid slot); the details rail is an **additive**
  sibling grid slot, never a re‑parent of `TcChat`.
- **FR‑7 — Amber Approvals badge.** The Approvals tab (expanded) and the Approvals
  strip button (collapsed) MUST show the pending count from `approvalsQueue.pending`,
  styled amber (`.sd-tab .b.hot` / `.sd-strip .bd`). Zero pending → no badge. The
  Agents tab MAY show its "N live"/total count (parity with `RunWorkspaceRail`); the
  strip Agents/Sources buttons carry no badge (design shows a badge only on Approvals).
- **FR‑8 — Scrubbed guard.** While the cockpit is scrubbed off‑now (`isScrubbed`), the
  Approvals tab and its badge MUST be suppressed in the details rail (you cannot approve
  a past state), exactly as `RunWorkspaceRail` does. If Approvals was the active details
  tab, fall back to Agents. Snap‑to‑now restores it.
- **FR‑9 — Studio unaffected.** In Studio the details rail MUST NOT render; the details
  slot collapses out of the grid so Studio's `surface | handle | chat` layout is
  byte‑identical to today.
- **FR‑10 — Empty/idle unaffected.** When `session.runId === null` the empty‑state goal
  composer still owns the canvas slot; the details rail only exists once `ThreadCanvas`
  mounts (a run is bound).

## Non-functional requirements

- **NFR‑1 — One event projection (FR‑3.3).** The details rail MUST read only the
  existing projections `RunDestination` already derives from `useRunSession.events`
  (`subagentProjection`, `useRunSources`, `approvalProjection/toApprovalsQueue`). It
  MUST NOT open a second SSE subscription, call `useEventProjector` again, or
  re‑`projectSubagents`/`projectApprovals`. It receives normalized props only.
- **NFR‑2 — Single mount (FR‑3.9).** Position is CSS `grid-area` only. The details rail
  is a new grid slot; `TcChat` never changes tree position across mode/collapse/tab
  switches. Verified by a "no remount" test (see Test plan T‑4).
- **NFR‑3 — Substrate boundary.** `RunDetailsRail` is framework‑agnostic — no
  `window`/`document`/`fetch`/`localStorage`. Collapse persistence goes through
  `useKeyValueStore()` (the same port `useRailWidth`/`useRunMode` use). Icons come from
  the `<Icon name>` SSOT (`activity`, `shield`, `doc`, `chevronDown`).
- **NFR‑4 — Host‑fed / presentational.** `RunDetailsRail` takes normalized props +
  callbacks; all fetching/projection stays in `RunDestination`. No data‑binding inside
  the component.
- **NFR‑5 — Honest data.** Every field rendered is backed by an existing projection.
  The design's mock `AgentsPanel`/`ApprovalsPanel`/`SourcesPanel` (scheduled runs,
  "Treasury watch", progress bars, model names) are NOT rebuilt — we mount the real
  hoisted bodies, which show only what the backend produces (see Descopes).
- **NFR‑6 — Tokens + both themes.** Colors are design‑system tokens only
  (`--color-bg`/`--color-bg-elevated`/`--color-border`/`--color-accent`; amber via the
  existing approvals‑badge token path). Verified in light + dark.
- **NFR‑7 — a11y.** Expanded rail: `role="tablist"` + three `role="tab"` (reuse
  `WorkspaceTabs`), each panel `role="tabpanel"` with `aria-label`. Header collapse
  button + every strip button MUST have an accessible name (`aria-label` "Collapse run
  details" / "Expand run details" / "Agents"/"Approvals"/"Sources"). The Approvals
  strip badge MUST carry `aria-label="${n} pending approvals"`.
- **NFR‑8 — Perf.** No new memo of events; the rail consumes already‑memoized
  projections. Collapse toggle is a single boolean state write. Grid transition reuses
  the existing 300ms `grid-template` animation.
- **NFR‑9 — Tests required.** Unit tests for `RunDetailsRail` (tabs, strip, scrubbed
  guard, badge), `useRunDetailsCollapsed` (KV read/write/default), and a `RunDestination`
  integration test asserting Focus renders the rail fed from the projections and that
  `TcChat` survives a Studio→Focus→collapse→Studio cycle without remount.

## Architecture & plan

### Components / hooks introduced

1. **`RunDetailsRail`** (new — `destinations/run/RunDetailsRail.tsx`). A small
   presentational composition of the three hoisted bodies + a collapse header/strip.
   NOT a fork of `RunWorkspaceRail`: it hosts no `TcChat`, has no Chat tab, and adds the
   `.sd-strip` collapsed variant `RunWorkspaceRail` does not have. It shares the count
   helpers via extraction (below). Props:

   ```ts
   export type RunDetailsTabId = "agents" | "approvals" | "sources";
   export interface RunDetailsRailProps {
     collapsed: boolean;
     onToggleCollapsed: (next: boolean) => void;
     scrubbed?: boolean; // FR-8
     // Agents
     subagents?: SubagentSnapshotMap;
     subagentsLoading?: boolean;
     subagentsError?: string | null;
     onJumpToSubagent?: (s: SubagentEntry) => void;
     subagentActivitiesByTask?: SubagentActivitiesByTask;
     subagentHistoryGroups?: readonly SubagentHistoryGroup[];
     // Approvals
     approvalsQueue?: ApprovalsQueueProjection;
     onJumpToApproval?: (approvalId: string, messageId: string) => void;
     onApprove?: (approvalId: string) => void;
     onReject?: (approvalId: string) => void;
     // Sources
     sources?: SourceEntryMap;
     sourcesLoading?: boolean;
     sourcesError?: string | null;
     sourcesSearching?: boolean;
     onSelectSource?: (s: SourceEntry) => void;
     onJumpToChatSource?: (s: SourceEntry) => void;
     SourceRowComponent?: SourceRowSlot;
     defaultTab?: RunDetailsTabId; // default "agents"
   }
   ```

   Internal state: `useState<RunDetailsTabId>(defaultTab ?? "agents")`. `effectiveTab`
   applies the scrubbed→agents fallback (mirror RunWorkspaceRail.tsx:162‑166). Expanded
   render = `.sd-h` header (title "Run details" + collapse chevron button) → the reused
   `WorkspaceTabs` (three items) → the active body in a `role="tabpanel"`. Collapsed
   render = a `.sd-strip` column: expand chevron button, then Agents/Approvals/Sources
   32×32 icon buttons (`<Icon name="activity|shield|doc" />`), each calling
   `onToggleCollapsed(false)` + selecting its tab; the Approvals button overlays the
   amber `.bd` count when `pending > 0` and not scrubbed.

2. **`useRunDetailsCollapsed`** (new — `destinations/run/useRunDetailsCollapsed.ts`).
   KeyValueStore‑backed, GLOBAL (one preference for every run), a direct mirror of
   `useRailWidth`:

   ```ts
   export const RUN_DETAILS_COLLAPSED_KEY = "chats.run_details_collapsed";
   export function readRunDetailsCollapsed(store: {
     get(k): string | null;
   }): boolean {
     return store.get(RUN_DETAILS_COLLAPSED_KEY) === "1"; // default open
   }
   export function useRunDetailsCollapsed(): {
     collapsed: boolean;
     setCollapsed: (c: boolean) => void;
   };
   ```

   (Global chosen to match `useRailWidth`; if a reviewer prefers per‑conversation,
   swap to the `useRunMode` per‑conversation key shape — the hook signature is stable
   either way.)

3. **`railBadges.tsx`** (new — `destinations/run/railBadges.tsx`). Extract the three
   private helpers currently in RunWorkspaceRail.tsx:291‑338 — `countRunning`,
   `agentsBadge`, `approvalsBadge` — verbatim, plus a new `approvalsStripBadge(pending)`
   for the `.sd-strip .bd` variant. `RunWorkspaceRail` imports them (behavior‑preserving
   refactor); `RunDetailsRail` imports the same. This is the "share the logic, don't
   fork the rail" seam: the count/badge semantics live in one place so the two rails can
   never disagree (they already must agree per PRD FR‑3.12).

### Why RunDetailsRail (not a "details-only" mode on RunWorkspaceRail)

`RunWorkspaceRail`'s core invariant is that it hosts the single `TcChat` at a stable
position and always mounts the Chat panel (RunWorkspaceRail.tsx:216‑227, FR‑3.9). A
"chatless details mode" would carry that chat‑hosting machinery it must not use, and —
decisively — the `.sd-strip` collapsed icon rail is a Focus‑only concept entirely absent
from `RunWorkspaceRail`. Overloading one component with "hosts chat + full tabset" AND
"no chat + 3 tabs + collapse strip" muddies its single responsibility. A ~120‑line
`RunDetailsRail` that reuses the same bodies + shared badge helpers keeps each rail with
one job. `TcChat`'s single mount is preserved structurally: `RunWorkspaceRail` (with
`TcChat` inside) stays the chat grid slot in BOTH modes; `RunDetailsRail` is a separate
grid slot that hosts no chat.

### Data flow

```
useRunSession.events ──(single stream, already in RunDestination)
  ├─ projectSubagents        → subagentProjection.subagents ─┐
  ├─ useRunSources           → sources ─────────────────────┤→ RunDetailsRail (props)
  └─ projectApprovals→queue  → approvalsQueue ──────────────┘   (Focus only)
                                        │
ThreadCanvas grid: focus → "chat details"; RunDetailsRail in gridArea "details"
TcChat: unchanged position inside RunWorkspaceRail (gridArea "chat"), never re-parented
```

### Exact edit points

**`packages/chat-surface/src/thread-canvas/ThreadCanvas.tsx`**

- `ThreadCanvasProps` (after `rightRail`, ThreadCanvas.tsx:135): add
  `readonly detailsRail?: ReactNode;` (Focus‑only details slot; omitted → single
  centered column, current behavior) and `readonly detailsCollapsed?: boolean;`
  (drives the 324px vs 46px column; default `false`).
- Destructure both in the props block (ThreadCanvas.tsx:163‑185).
- `gridStyleFor` (ThreadCanvas.tsx:509‑532) — change signature to
  `gridStyleFor(mode, railWidthPx, opts: { showDetails: boolean; detailsCollapsed: boolean })`
  and rewrite the focus branch (currently :524‑531):

  ```ts
  // focus
  if (!opts.showDetails) {
    return {
      ...baseGridStyle,
      gridTemplateColumns: "minmax(0, 760px)",
      gridTemplateRows: "auto auto 1fr auto",
      gridTemplateAreas: '"switcher" "tabs" "chat" "mini"',
      justifyContent: "center",
    };
  }
  const detailsCol = opts.detailsCollapsed ? "46px" : "324px";
  return {
    ...baseGridStyle,
    gridTemplateColumns: `minmax(0, 1fr) ${detailsCol}`,
    gridTemplateRows: "auto auto 1fr auto",
    gridTemplateAreas:
      '"switcher switcher" "tabs tabs" "chat details" "mini mini"',
  };
  ```

  (No `justifyContent:center` when details present — `TcChat`'s own
  `focusContainerStyle` `maxWidth:760;margin:0 auto` (TcChat.tsx:789‑796) centers the
  reading column inside the 1fr, matching `.fx-col`.)

- Update the call site (ThreadCanvas.tsx:325):
  `...gridStyleFor(mode, railWidthPx, { showDetails, detailsCollapsed })` where
  `const showDetails = mode === "focus" && detailsRail !== undefined;`.
- Render the details slot inside `SwimlaneScrubProvider`, as a sibling of the chat slot
  (after the chat slot block, ThreadCanvas.tsx:407): `{showDetails ? <div
data-testid="tc-details-slot" style={detailsSlotStyle}>{detailsRail}</div> : null}`.
- Add `detailsSlotStyle: CSSProperties = { gridArea: "details", minWidth: 0, minHeight:
0, borderLeft: "1px solid var(--color-border, #22252e)", overflow: "hidden" }`.

**`packages/chat-surface/src/destinations/run/RunDestination.tsx`**

- Import `RunDetailsRail` and `useRunDetailsCollapsed`.
- Add `const { collapsed: detailsCollapsed, setCollapsed: setDetailsCollapsed } =
useRunDetailsCollapsed();` near the `useRailWidth` call (RunDestination.tsx:207).
- Build the details node (after `rightRail`, RunDestination.tsx:547), feeding the
  projections it already has (`subagentProjection.subagents`, `sources`+loading/error,
  `approvalsQueue`, `handleApprove`/`handleReject`, `isScrubbed`):

  ```tsx
  const detailsRail = (
    <RunDetailsRail
      collapsed={detailsCollapsed}
      onToggleCollapsed={setDetailsCollapsed}
      scrubbed={isScrubbed}
      subagents={subagentProjection.subagents}
      sources={sources}
      sourcesLoading={sourcesLoading}
      sourcesError={sourcesError}
      approvalsQueue={approvalsQueue}
      onApprove={handleApprove}
      onReject={handleReject}
    />
  );
  ```

- Pass to `ThreadCanvas` (RunDestination.tsx:609‑634): add
  `detailsRail={detailsRail}` and `detailsCollapsed={detailsCollapsed}`. `mode` already
  passed; `ThreadCanvas` renders the slot only in Focus.

**`packages/chat-surface/src/destinations/run/RunWorkspaceRail.tsx`**

- Replace the inline `countRunning`/`agentsBadge`/`approvalsBadge` (RunWorkspaceRail.tsx:
  291‑338) with imports from `./railBadges`. Behavior‑preserving; keeps FR‑3.12 in one
  place. No other change.

**Barrels**

- `destinations/run/index.ts` (after the RunWorkspaceRail export block, :14‑18): export
  `RunDetailsRail` + `RunDetailsRailProps`/`RunDetailsTabId`, and `useRunDetailsCollapsed`
  - `RUN_DETAILS_COLLAPSED_KEY`.
- `src/index.ts` — add to the Run block near :1189 a delimited sub‑export for
  `RunDetailsRail` (mirroring the RunWorkspaceRail comment) so both hosts can reach it
  if ever needed directly. (Hosts don't need to wire anything new — `RunDestination`
  owns the composition; the desktop/web binders are unchanged.)

**No `api-types` / `service-contracts` change.** This front is pure composition over
existing projections. Explicitly: **zero** contract edits.

### Ordered, independently-shippable commits

1. **Extract badge helpers** — new `railBadges.tsx`; repoint `RunWorkspaceRail` imports.
   Pure refactor; existing `RunWorkspaceRail` tests stay green. (No behavior change.)
2. **`useRunDetailsCollapsed`** — hook + unit test. Standalone, unused yet.
3. **`RunDetailsRail`** — component + unit tests (expanded tabs, strip, scrubbed guard,
   badges). Not yet mounted anywhere.
4. **`ThreadCanvas` details slot** — `detailsRail`/`detailsCollapsed` props + focus grid
   - slot render + style. Backward‑compatible (omitted → today's single column). Add a
     ThreadCanvas grid test for the focus‑with‑details template.
5. **Wire in `RunDestination`** — compose `detailsRail`, pass to `ThreadCanvas`. Add the
   integration + no‑remount test. This commit lights the feature up.
6. **Barrels** — export the new symbols (can fold into 3/5).

## Descopes & rationale

- **Mock `AgentsPanel` / `ApprovalsPanel` / `SourcesPanel`** (copilot-run-side.jsx:16,
  60, 114) — the design panels hard‑code scheduled runs, a cross‑run "Treasury watch"
  agent, progress bars ("step 6 of 9", "64%"), model names, and an "Auto‑approved today"
  log. **DESCOPE (substitute):** we mount the real hoisted `AgentsTab`/`ApprovalsTab`/
  `SourcesTab`, which render only backend‑produced projections. We do NOT rebuild the
  mock panels. Any richer per‑agent telemetry (progress %, scheduled runs) is a separate
  cross‑stack contract, out of scope here (NFR‑5, honest data).
- **Agents strip badge / Sources strip badge** — the design puts a `.bd` count only on
  Approvals (copilot-run-side.jsx via `.sd-strip .bd`; Workspace3 renders `.bd` solely on
  the Approvals strip button, copilot-workspace3.jsx:168). **In‑scope as designed:** only
  the Approvals strip button carries a badge; Agents/Sources do not. (The expanded
  Agents tab keeps its "N live" tab badge for parity with `RunWorkspaceRail`.)
- **Draggable details‑rail width** — Studio's rail is resizable (ThreadCanvas rail
  handle, #136); Focus `.sd` is a **fixed 324px** in the design (copilot-v3.css, no resize
  affordance). **DESCOPE:** fixed width via constant; no resize handle for the details
  rail. Matches design.
- **`.fx-note` "The run is holding N actions" banner + plan‑hidden Focus chat**
  (copilot-workspace3.jsx:89‑90) — this lives in the CENTER chat column, owned by
  `TcChat`'s Focus rendering (#148), not the details rail. **Out of scope** for this
  front (no change to `TcChat`).
- **`chatSide`/right‑vs‑left rail placement** (copilot-v3.css `[data-side="right"]`) — a
  Studio‑only mirror option in the mock; not part of Focus and not requested. **DESCOPE.**

## Test plan

Unit (Vitest, `packages/chat-surface`):

- **T‑1 `RunDetailsRail` expanded** — renders exactly three tabs (Agents, Approvals,
  Sources), no Chat tab; selecting each shows the matching hoisted body; Agents "N live"
  badge derives from `subagents`; Approvals badge from `approvalsQueue.pending`. Guards:
  regression where a Chat tab leaks into Focus, or badges drift from `RunWorkspaceRail`.
- **T‑2 `RunDetailsRail` collapsed strip** — `collapsed` renders the `.sd-strip` with an
  expand button + three 32×32 icon buttons; clicking one calls
  `onToggleCollapsed(false)` and selects that tab; the Approvals button shows the amber
  `.bd` with `aria-label="${n} pending approvals"` only when `pending>0`. Guards: strip
  icon → tab wiring, badge presence.
- **T‑3 scrubbed guard** — with `scrubbed`, the Approvals tab + strip badge are
  suppressed and an Approvals‑active details tab falls back to Agents. Guards: approving
  a past state.
- **T‑4 no‑remount (crux, `RunDestination` integration)** — mount Focus, tag the
  `TcChat` DOM node, then Studio → Focus → toggle collapse → switch a details tab →
  Studio; assert the tagged node identity/`data-testid="tc-chat-slot"` child is never
  torn down (e.g. a mount‑count spy on a `TcChat` effect, or a stable node ref). Guards
  FR‑6/NFR‑2 regression.
- **T‑5 `RunDestination` Focus wiring** — in Focus with seeded `session.events`, the
  details rail renders and its Agents/Approvals/Sources reflect the SAME projections that
  feed `RunWorkspaceRail` (assert the Approvals count equals `toApprovalsQueue(...)`
  pending). In Studio, `tc-details-slot` is absent (FR‑9). Guards: second‑projection
  regression (NFR‑1) and Studio bleed.
- **T‑6 `useRunDetailsCollapsed`** — default open when unset; `"1"` → collapsed;
  set writes `"1"`/`"0"` through the KV port; a fake store asserts no bare
  `localStorage`. Guards FR‑4/NFR‑3.
- **T‑7 ThreadCanvas focus grid** — `detailsRail` present + `detailsCollapsed=false`
  yields `minmax(0, 1fr) 324px` with a `details` area; collapsed yields `46px`; absent
  `detailsRail` keeps `minmax(0,760px)` centered. Guards FR‑5 and the backward‑compat
  path.

## Risks & gotchas

- **Background token mismatch in Focus.** `RunWorkspaceRail`'s chat wrapper uses
  `--color-bg-elevated` (railStyle, RunWorkspaceRail.tsx:351), but the design's Focus
  chat `.fx` is base `--ink` while `.sd` details is elevated `--ink2`. As‑is the Focus
  chat column reads slightly elevated. Low‑risk polish: optionally make
  `RunWorkspaceRail` render the chat panel on `--color-bg` in Focus. Flagged, not
  required for parity of THIS front.
- **Centering.** Removing `justifyContent:center` from the focus grid when details are
  present relies on `TcChat`'s own `focusContainerStyle` centering (max‑width 760). Verify
  the chat column visually centers to ~730 and doesn't left‑align once the details
  column claims the right edge. (Covered indirectly by T‑7 + a manual pass.)
- **Details tab state reset on Studio↔Focus.** `RunDetailsRail` mounts only in Focus, so
  its selected tab resets when leaving/returning to Focus. Acceptable (the bodies carry
  no critical scroll/draft state; they already remount on tab switch inside
  `RunWorkspaceRail`). If a reviewer wants tab persistence, lift the tab into a KV hook
  like collapse — out of scope by default.
- **Collapse scope choice.** Global (mirrors `useRailWidth`) means collapsing in one run
  collapses everywhere. If product wants per‑run, switch the key to the per‑conversation
  `useRunMode` shape — the hook API is unchanged.
- **`ThreadCanvas` prop growth.** Adding `detailsRail`/`detailsCollapsed` keeps the
  single‑mount invariant intact only because the slot is additive; do NOT be tempted to
  move `TcChat` into the details rail or re‑parent it per mode — that reintroduces the
  remount FR‑6 forbids.
- **Barrel discipline.** New exports go inside the delimited Run block in `src/index.ts`;
  hosts consume via the barrel only (no deep import). No binder change is needed since
  `RunDestination` owns the composition — don't accidentally add duplicate wiring in the
  web/desktop binders.
