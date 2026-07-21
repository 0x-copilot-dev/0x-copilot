# Studio rail side (left default + left/right toggle) — v3 parity plan

## Problem statement

In the v3 mock (`copilot-workspace3.jsx` + `copilot-v3.css`), Studio mode puts
the workspace rail — the Chat/Agents/Approvals/Sources column — on the **left**
by default, with the work surface (the canvas) filling the space to its right:

```css
.ws3[data-mode="studio"] .ws3-main {
  grid-template-columns: 378px minmax(0, 1fr);
} /* rail LEFT, 378px */
.ws3[data-mode="studio"][data-side="right"] .ws3-main {
  grid-template-columns: minmax(0, 1fr) 378px;
}
.ws3[data-mode="studio"][data-side="right"] .ws3-main > .sd {
  order: 2;
} /* rail RIGHT variant */
```

`Workspace3` renders `<div className="ws3" ... data-side={chatSide}>` — the side is a
first-class, host-controlled preference (`chatSide`), and left is the resting state.

What ships today does the opposite and offers no choice. `ThreadCanvas.gridStyleFor`
(studio branch, `ThreadCanvas.tsx:513-522`) hard-codes:

```ts
gridTemplateColumns: `minmax(0, 1fr) 1px ${railWidthPx}px`,   // surface | handle | rail  → rail RIGHT
gridTemplateAreas: '… "surface handle chat" …',
```

So the rail is pinned to the **right** at a `DEFAULT_RAIL_WIDTH = 360` (`ThreadCanvas.tsx:75`),
now resizable (PR #136) but not moveable. A user who wants chat on the left — the mock's
default, and the natural reading order for a left-to-right transcript beside a wide
canvas — cannot get there.

Why it matters now: rail _width_ just became a persisted, user-owned layout preference
(`useRailWidth`). Side is the sibling preference the mock treats as equally first-class;
shipping width-without-side leaves the cockpit visibly diverged from the approved v3
layout and silently locks every user into a placement the design does not default to.

This is a pure-layout change (no backend contract). The one genuine product call it
forces: **the shipped product defaults right; the mock defaults left.** We recommend
matching the mock (left default) but flag it as an intentional flip, not a silent one.

## Functional requirements

- **FR-1** — Studio mode MUST support a `side` preference of `"left"` or `"right"`.
  `"left"` places the rail (chat/tabs column) at the start of the row and the work
  surface after it; `"right"` places the surface first and the rail last — matching the
  two `copilot-v3.css` grids above.
- **FR-2** — The default side MUST be `"left"`, matching the v3 mock
  (`.ws3[data-mode="studio"] .ws3-main` with no `data-side`). This is a deliberate change
  from the shipped right-default (see Descopes / product-decision note).
- **FR-3** — The chosen side MUST persist across sessions and across runs (a global
  layout preference, exactly like rail width — one value the user sets once), stored via
  the KeyValueStore port under a stable `chats.*` key.
- **FR-4** — A user-visible control MUST toggle the side while in Studio mode. Focus mode
  (single centered column, no surface/rail split) MUST NOT show the toggle — it is a
  no-op there.
- **FR-5** — The resize handle MUST keep working on whichever edge the rail occupies:
  when the rail is on the left, dragging its **right** edge resizes it; when on the right,
  dragging its **left** edge resizes it. Drag math, the dynamic max (`rect.width -
MIN_SURFACE_WIDTH`), and clamping MUST stay correct on both sides.
- **FR-6** — Keyboard resize MUST stay direction-correct: the arrow key that _grows_ the
  rail depends on side (rail-right: `ArrowLeft` grows; rail-left: `ArrowRight` grows).
  `aria-valuenow/min/max` on the separator MUST remain accurate on both sides.
- **FR-7** — Switching side MUST NOT remount any inner slot (`TcSurfaceMount`, `TcChat`,
  `TcSwimlanes`, `TcMiniTimeline`, Composer). It is a `grid-template-columns` +
  `grid-template-areas` reassignment on the same single-mount `ThreadCanvas`, never a tree
  move — chat scroll, active surface tab, scrub cursor, and composer draft all survive.
- **FR-8** — Side MUST have no effect in Focus mode: `gridStyleFor("focus", …)` is
  unchanged (single `minmax(0, 760px)` centered column).
- **FR-9** — The default studio rail width SHOULD be reconciled to the mock's `378px`
  (from `360`). This is a separable one-line change (`DEFAULT_RAIL_WIDTH`) and MUST be
  called out, not smuggled in — it moves the resting width for users with no persisted
  value. (Ship it in its own commit; see plan.)

## Non-functional requirements

- **NFR-1 (one event projection, FR-3.3)** — This change touches layout only. It MUST NOT
  read, subscribe to, or re-project run events. No new `useEventProjector`, no second SSE.
  The `useRailSide` hook reads/writes KeyValueStore exclusively; it never sees `events`.
- **NFR-2 (single mount, FR-3.9)** — Side is expressed purely through
  `gridStyleFor(mode, side, railWidthPx)` output (column template + area names). The JSX
  element shape in `ThreadCanvas`'s return MUST stay invariant across side values so React
  reconciliation preserves every mounted child. No conditional that adds/removes/reorders
  a persistent slot in the tree. (Grid `order`/area reassignment is CSS-visual only.)
- **NFR-3 (substrate boundary)** — `useRailSide` MUST go through the `KeyValueStore` port
  via `useKeyValueStore()` — no `window`/`document`/`localStorage`/`fetch`. It mirrors
  `useRailWidth` byte-for-byte in shape (a pure `readRailSide(store)` + a stateful hook),
  so the eslint `no-restricted-globals` boundary holds.
- **NFR-4 (host-fed / presentational)** — `ThreadCanvas` stays controlled: it receives
  `side` + `onSideChange` as props (like `railWidth`/`onRailWidthChange`), owns no
  persistence. `RunDestination` (the host binder) wires `useRailSide` into those props.
  The toggle control is presentational; its click handler is a host callback.
- **NFR-5 (design tokens, light+dark)** — The toggle control MUST use design-system tokens
  only (`--color-*`), themed for light and dark, matching the existing `RunHeader`
  segmented control / `modeButtonStyle` idiom. No literal colors beyond the `var(--…, #…)`
  fallbacks already used in this file.
- **NFR-6 (a11y)** — The toggle is a labelled control (`aria-label` naming the action and
  its state, e.g. "Move workspace panel to the right"). The resize separator keeps
  `role="separator"`, `aria-orientation="vertical"`, and accurate `aria-valuenow/min/max`
  on both sides. Keyboard resize stays operable from either edge.
- **NFR-7 (perf)** — Side changes reuse the existing `300ms grid-template` transition; no
  new listeners, timers, or reflow-forcing reads beyond the existing
  `getBoundingClientRect()` in the drag path. `readRailSide` runs once on mount.
- **NFR-8 (tests-required)** — Every FR above ships with a test (see Test plan). No FR is
  "done" until its regression is guarded.

## Architecture & plan

### Components / hooks introduced

- **`RailSide` type** — `"left" | "right"`, declared in `ThreadCanvas.tsx` next to
  `ThreadMode` (the layout owner), aliased into the hook (mirrors how `useRunMode` aliases
  `ThreadMode` as `RunMode`, `useRunMode.ts:36`), so the union never drifts.
- **`useRailSide` hook** — new file
  `packages/chat-surface/src/destinations/run/useRailSide.ts`, a near-clone of
  `useRailWidth.ts`. Exports:
  - `RAIL_SIDE_KEY = "chats.rail_side"` (shares the `chats.*` app-preference namespace
    used by `RAIL_WIDTH_KEY = "chats.rail_width"`, `useRailWidth.ts:16`).
  - `readRailSide(store): RailSide` — `store.get(RAIL_SIDE_KEY) === "right" ? "right" : "left"`.
    Unknown/`null`/legacy ⇒ default `"left"` (same "unknown ⇒ default degrades safely"
    shape as `readRunMode`, `useRunMode.ts:60-67`).
  - `useRailSide(): { side, setSide, toggle }` — `useState(() => readRailSide(store))`,
    `setSide` persists via `store.set(RAIL_SIDE_KEY, next)`, `toggle` flips.
- **Toggle control** — a small icon/segmented button rendered in **`RunHeader`**, gated to
  Studio mode. `RunHeader` already owns the Studio/Focus segmented control and receives
  `mode`; adding a `side`/`onToggleSide` prop pair keeps mode + layout affordances in one
  place (the "single mode control" seam RunDestination already relies on,
  `RunDestination.tsx:556-561`). Style follows `modeButtonStyle`
  (`ThreadCanvas.tsx:616-631`) — tokens only.

### Data flow

```
KeyValueStore port ──useRailSide()──▶ RunDestination
   (chats.rail_side)                    │ side, setSide/toggle
                                        ├─▶ RunHeader   (Studio-only toggle button → onToggleSide)
                                        └─▶ ThreadCanvas (side prop) ──▶ gridStyleFor(mode, side, railWidthPx)
                                                                          → grid-template-columns + -areas
```

Exactly parallel to the existing width path:
`useRailWidth → RunDestination → ThreadCanvas.railWidth/onRailWidthChange`
(`RunDestination.tsx:207, 632-633`).

### Exact edit points (verified line numbers)

1. **`packages/chat-surface/src/thread-canvas/ThreadCanvas.tsx`**
   - **:70** — after `export type ThreadMode = "studio" | "focus";` add
     `export type RailSide = "left" | "right";`.
   - **:148-153** — in `ThreadCanvasProps`, after `railWidth` / `onRailWidthChange`, add
     `readonly side?: RailSide;` (default `"left"`). (Side change is host-driven, so no
     `onSideChange` needed on the canvas — the toggle lives in `RunHeader`; canvas only
     _renders_ the side.)
   - **:162-185** — destructure `side = "left"` in the component body.
   - **:197** — `railWidthPx` unchanged.
   - **:199-208 `widthFromPointer`** — branch on side:
     rail-right (current) `Math.round(rect.right - clientX)`;
     rail-left `Math.round(clientX - rect.left)`. `dynamicMax`/clamp unchanged. Add `side`
     to the `useCallback` dep array.
   - **:244-259 `handleResizeKey`** — branch the grow direction on side:
     rail-right `ArrowLeft → +16, ArrowRight → -16` (current);
     rail-left `ArrowRight → +16, ArrowLeft → -16`. Add `side` to deps. Update the inline
     comment ("The rail is on the right, so ArrowLeft widens it") to state the side rule.
   - **:324-329** — pass `side` into `gridStyleFor(mode, side, railWidthPx)`.
   - **:509-532 `gridStyleFor`** — add `side: RailSide` param; in the `studio` branch
     return, when `side === "left"`:
     `gridTemplateColumns: \`${railWidthPx}px 1px minmax(0, 1fr)\``,
`gridTemplateAreas: '"switcher switcher switcher" "tabs tabs tabs" "chat handle surface" "swimlanes swimlanes swimlanes" "mini mini mini"'`;
when `side === "right"` keep the current template (`:518-521`). `focus` branch
(`:525-531`) unchanged — `side` ignored (FR-8).
   - The separator JSX (`:367-386`), surface slot (`:346-361`), chat slot (`:388-407`),
     and `railHandleHitStyle` (`:586-593`, symmetric `left:-4/right:-4`) need **no**
     structural change — they resolve by `gridArea` name, which the area map reassigns.
     This is the single-mount guarantee (NFR-2).

2. **`packages/chat-surface/src/destinations/run/useRailSide.ts`** — new file (clone of
   `useRailWidth.ts`, structure per above).

3. **`packages/chat-surface/src/destinations/run/RunDestination.tsx`**
   - **:86** — add `import { useRailSide } from "./useRailSide";` beside the `useRailWidth`
     import.
   - **:207** — after `const { width: railWidth, setWidth: setRailWidth } = useRailWidth();`
     add `const { side: railSide, toggle: toggleRailSide } = useRailSide();`.
   - **:556-561 `<RunHeader …>`** — pass `mode`, `side={railSide}`,
     `onToggleSide={toggleRailSide}`.
   - **:609-634 `<ThreadCanvas …>`** — add `side={railSide}` beside `railWidth` /
     `onRailWidthChange` (`:632-633`).

4. **`packages/chat-surface/src/destinations/run/RunHeader.tsx`**
   - Add `side?: RailSide` + `onToggleSide?: () => void` to `RunHeaderProps`.
   - Render a Studio-only toggle button (hidden when `mode === "focus"`), tokens-only,
     `aria-label` = "Move workspace panel to the {opposite side}", `aria-pressed` reflecting
     current side. Style per `modeButtonStyle`.

5. **Barrels** — `useRailWidth` is **not** exported from `destinations/run/index.ts` or the
   package `index.ts` (consumed internally by `RunDestination` via relative import,
   `RunDestination.tsx:86`). `useRailSide` follows the same rule: **no barrel export
   needed** unless a host must read side directly. `RailSide` is exported from the
   `thread-canvas` module barrel alongside `ThreadMode` for `RunHeader`/`useRailSide` to
   import as a type (mirror the existing `ThreadMode` re-export path).

### api-types / service-contract changes

**None.** Pure client layout. `CONTRACT: none`. No `packages/api-types`, no facade, no
backend change.

### Ordered, independently-shippable commits

1. **`feat(chat-surface): side-aware Studio grid in ThreadCanvas`** — add `RailSide`,
   `side` prop (default `"left"`), branch `gridStyleFor` + `widthFromPointer` +
   `handleResizeKey`. Ships behind the default; because default flips to `"left"` this is
   the visible parity change. Unit tests: grid template per side, resize math per side,
   focus-mode side-invariance, no-remount on side change.
2. **`feat(chat-surface): useRailSide (KeyValueStore-backed) persistence`** — new hook +
   `readRailSide`. Unit tests: default `"left"`, round-trip persist, unknown ⇒ default.
3. **`feat(chat-surface): rail-side toggle in RunHeader + wire useRailSide in RunDestination`**
   — the visible control + host binding. Tests: toggle hidden in Focus, click flips side,
   RunDestination passes persisted side to canvas.
4. **`chore(chat-surface): reconcile default Studio rail width 360→378 (v3 parity)`** —
   FR-9, isolated so the width delta is a reviewable, separately-revertable decision.

Commits 1–3 are the feature; 4 is the cosmetic width reconciliation. Each is green on its
own.

## Descopes & rationale

- **Default-side flip (LEFT) is a product decision, not a silent change.** The shipped
  product defaults **right**; the v3 mock defaults **left**
  (`copilot-v3.css` `.ws3[data-mode="studio"] .ws3-main{grid-template-columns:378px
minmax(0,1fr)}` — rail column first). We **recommend matching the mock** (FR-2), but this
  is flagged for explicit sign-off, not smuggled in. If the decision is to preserve the
  shipped placement, set `readRailSide`'s default to `"right"` and leave FR-1/3/4/5/6
  intact — the toggle still ships; only the resting value differs. **NOT a descope — a
  decision to confirm.**

- **378 vs 360 default width — NEW nothing, cosmetic reconciliation.** The mock's rail is
  `378px`; ours defaults `360` (`ThreadCanvas.tsx:75 DEFAULT_RAIL_WIDTH`). Bump to `378`
  (FR-9, commit 4) for pixel parity. Separable and low-risk (only affects users with no
  persisted width). Cited: `copilot-v3.css` `378px` in both studio grids. **RECONCILE, not
  descope.**

- **Focus-mode rail width (`324px`) and collapsed strip (`46px`).** `copilot-v3.css`
  defines `.ws3[data-mode="focus"] .ws3-main{grid-template-columns:minmax(0,1fr) 324px}`
  and a `[data-cl="1"]` 46px collapsed strip. Our Focus mode is a single centered
  `minmax(0,760px)` chat column (`ThreadCanvas.tsx:527`) with the rail recomposed
  elsewhere — a different Focus layout entirely. **OUT OF SCOPE for this front** (it is a
  Focus-layout parity item, not rail-side); do not touch `gridStyleFor("focus", …)`.
  **DESCOPE (separate front).**

- **The mock's `gap:1px;background:var(--line)` seam vs our 1px handle column.** The mock
  draws the surface|rail divider as a grid `gap` with no drag handle; our impl uses a
  dedicated 1px `handle` grid column that carries the resize affordance (PR #136). We keep
  the handle (resize is a shipped feature the mock's static prototype lacks). **No parity
  gap — deliberate superset.** No change.

- **`data-side` attribute name / `order:2` mechanism.** The mock reorders via CSS
  `order:2` on `.sd` under `[data-side="right"]`. We reorder via
  `grid-template-columns` + named `grid-template-areas` instead (our slots are
  area-placed, not source-ordered). Same visual result, honors single-mount. **Mechanism
  substitution, not a descope.**

## Test plan

Unit (vitest, `packages/chat-surface`):

- **`ThreadCanvas.railSide.test.tsx`** (new / extend `ThreadCanvas.test.tsx`):
  - Studio + `side="left"` → `gridTemplateColumns` starts with `${width}px 1px` and
    `gridTemplateAreas` row is `"chat handle surface"`. Guards FR-1 layout.
  - Studio + `side="right"` (and default) → `"…surface handle chat"`, columns end with
    `1px ${width}px`. Guards the right variant + FR-2 default assertion.
  - Focus + either side → identical `minmax(0, 760px)` template. Guards FR-8.
  - **No-remount:** render Studio `side="right"`, capture a child instance handle (data
    attribute / ref sentinel on `tc-chat-slot`), rerender `side="left"`, assert the same
    DOM node persists (chat scroll / composer survive). Guards FR-7 / NFR-2.
  - Resize math: with a stubbed `getBoundingClientRect`, `side="left"` pointer at
    `clientX` yields width `clientX - rect.left`; `side="right"` yields `rect.right -
clientX`. Guards FR-5.
  - Keyboard: `side="left"` `ArrowRight` grows, `ArrowLeft` shrinks; `side="right"` the
    reverse; `aria-valuenow` updates accordingly. Guards FR-6 / NFR-6.

- **`useRailSide.test.ts`** (new, mirror `useRailWidth.test.ts`):
  - `readRailSide` → `"left"` when key absent / `null` / unknown string; `"right"` only for
    `"right"`. Guards FR-3 default + safe-degrade.
  - `setSide("right")` persists `"right"` under `chats.rail_side` and re-reads it. Round
    trip. Guards FR-3.
  - `toggle` flips left↔right and persists.

- **`RunHeader.test.tsx`** (extend):
  - Toggle rendered in Studio, absent in Focus. Guards FR-4.
  - Clicking toggle fires `onToggleSide`. `aria-pressed`/`aria-label` reflect side. Guards
    FR-4 / NFR-6.

- **`RunDestination.test.tsx`** (extend):
  - Given a KeyValueStore seeded `chats.rail_side="right"`, `ThreadCanvas` receives
    `side="right"`; toggling via the header flips the canvas `side` and persists. Guards
    the host wiring (NFR-4) end-to-end.

Regression each guards: FR-7/NFR-2 test is the load-bearing one — it fails loudly if
anyone re-introduces a side-conditional that swaps the tree instead of the grid areas
(the single-mount invariant this whole cockpit is built on).

## Risks & gotchas

- **R1 — silent default flip.** Changing `readRailSide` default to `"left"` moves every
  existing user's rail on next load (no persisted value ⇒ default). This is intended
  (FR-2) but is a visible product change; it needs the sign-off called out in Descopes.
  Mitigation: land as its own commit; the toggle lets any user restore right in one click.
- **R2 — resize-direction inversion.** The drag math (`rect.right - clientX`) and the
  keyboard grow-direction are both encoded for a right-side rail. Flip _one_ and not the
  other and resize feels inverted on the left. The per-side unit tests (FR-5/FR-6) exist
  precisely to catch a half-applied branch. Keep the `side` dep in both `useCallback`s or
  the handler closes over a stale side.
- **R3 — accidental remount.** The tempting-but-wrong implementation is
  `{side === "left" ? <chatFirst/> : <surfaceFirst/>}`. That remounts and kills scroll /
  draft / scrub. The ONLY sanctioned mechanism is reassigning `grid-template-areas` +
  `grid-template-columns` on the unchanged element tree (NFR-2). The no-remount test is the
  guardrail.
- **R4 — handle hit-area on the left edge.** `railHandleHitStyle` is symmetric
  (`left:-4;right:-4`, `ThreadCanvas.tsx:586-593`), so the grab zone is fine on either
  edge — but confirm the handle `gridArea:"handle"` sits between `chat` and `surface` in
  the left-side area map (`"chat handle surface"`), not orphaned. A typo in the area string
  silently collapses the handle to `1fr`/auto and breaks resize with no error.
- **R5 — width default coupling.** If commit 4 (360→378) lands, update any
  `DEFAULT_RAIL_WIDTH`-asserting test in the same commit; the clamp range
  (`MIN 300 / MAX 760`) already contains 378, so no clamp fallout.
- **R6 — toggle discoverability.** The mock shows no explicit toggle button in
  `copilot-workspace3.jsx` (side comes in as the `chatSide` prop from the parent app), so
  there is no pixel-exact reference for the control. RunHeader placement + `modeButtonStyle`
  idiom is our reasoned choice; a design reviewer may want it as an icon-only affordance
  near the resizer instead — cheap to move since it is presentational and host-wired.
