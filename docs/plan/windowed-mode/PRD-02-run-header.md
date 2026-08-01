# PRD-02 — The run header earns its row

**Severity:** P0 · **Depends on:** [PRD-00](./PRD-00-overview.md) · **Coordinates with:** [PRD-01](./PRD-01-thread-switching.md) (shares the left slot) · **Surface:** `destinations/run/RunHeader.tsx`

## 1. Problem statement

The cockpit's header bar says **"0xCopilot — Focus"**. That is the app's name —
which the user knows, because they launched it — and the mode, which the
segmented control on the same row already shows, selected, three inches to the
right.

Claude Desktop's equivalent bar carries: sidebar toggle, search, **the thread
title**, folder, terminal, export, globe, overflow. Codex's carries: panel
toggle, back/forward, **the thread title**, overflow, "Open in", three layout
toggles. 0xCopilot's carries the product wordmark.

In a full-screen window this is merely wasteful. In a 900px window it is a row
of permanent chrome that answers no question the user has.

## 2. Current state

This is the sharpest finding in the set, because **the header already computes
everything worth showing and then deliberately hides it.**

[RunHeader.tsx:134–161](../../../packages/chat-surface/src/destinations/run/RunHeader.tsx#L134):

```tsx
<header data-testid="run-header" style={headerStyle}>
  <div data-testid="run-header-title" style={titleLayerStyle}>
    <b style={productNameStyle}><span style={productMarkStyle}>0x</span>Copilot</b>
    <span aria-hidden="true">—</span>
    <span>{modeLabel}</span>
  </div>
  <div style={visuallyHiddenStyle}>          {/* ← everything useful */}
    <span data-testid="run-header-kicker">{resolvedKicker}</span>
    <h2 data-testid="run-header-goal">{goalText}</h2>
    <RunStatusPulse runStatus={runStatus} />
    {status !== null ? <span data-testid="run-header-status">{status}</span> : null}
  </div>
  <ModeSegmentedControl … />
</header>
```

with ([RunHeader.tsx:335](../../../packages/chat-surface/src/destinations/run/RunHeader.tsx#L335)):

```ts
const visuallyHiddenStyle: CSSProperties = {
  position: "absolute",
  width: 1,
  height: 1,
  padding: 0,
  margin: -1,
  overflow: "hidden",
  clip: "rect(0, 0, 0, 0)",
  whiteSpace: "nowrap",
  border: 0,
};
```

**The run's goal, its ACTIVE RUN / STANDBY kicker, its live status pulse, and the
scrub label are all in the DOM, correctly derived, and clipped to a 1×1 box.**
The code comment is explicit that this was a choice:

> Preserve the run's useful semantic summary without competing with the
> authoritative compact window-bar composition. The visually rendered identity is
> the product + selected workspace mode.

So the header is accessible to a screen reader and useless to everyone else. The
`titleLayerStyle` is `position: absolute; inset: 0` with
`pointerEvents: "none"` — it is a centred overlay across the whole bar, which is
also why the left of the bar is free real estate (PRD-01 wants it).

Two smaller consequences fall out of the same decision:

- `RunStatusPulse` — a per-state `● working / queued / waiting / cancelling` chip
  with a reduced-motion-gated pulse ring — is fully implemented and **never
  visible**. Whatever it cost to build, it currently returns nothing.
- The `status` seam (the `VIEWING 11:43` scrub label, PR-3.7/3.9) is likewise
  invisible, so a user who scrubs the timeline gets no header confirmation that
  they are looking at the past.

## 3. Goals & non-goals

**Goals**

- The bar answers "what is this run doing, right now" at a glance.
- Reuse what is already computed. This PRD deletes a `clip()`, it does not build
  a feature.
- Stay within 38px and stay legible at `compact`.

**Non-goals**

- Growing the bar. 38px is correct and the program's whole premise is that
  vertical chrome is expensive.
- Removing the mode control or the accessible summary.
- Native window controls — those belong to the desktop host, never to the shared
  surface (`RunHeader` header comment).

## 4. Design decisions

**D-2.1 — The goal is the title.** Replace the centred product/mode layer with
the run goal. Both references put the thread/run identity here and nothing else
competes for it.

**D-2.2 — Keep the wordmark, demote it.** The product identity does have a job on
desktop: it is the window's title when the app is in the background, and it
anchors the traffic-light row. Decision: the wordmark survives **only at `wide`**,
left-of-centre and muted; at `regular` and `compact` the goal takes the whole
centre. This is the "chrome yields first" rule from PRD-00 applied concretely.

**D-2.3 — Drop the mode word from the title.** `"0xCopilot — Focus"` duplicates
the segmented control's selected tab, on the same 38px row. One source of truth
per row.

**D-2.4 — Promote the pulse chip.** `RunStatusPulse` moves out of the hidden div
and sits immediately left of the mode control. It is the single highest-value
thing the bar can show: whether the agent is working. It self-hides on terminal
status, so a settled run costs nothing.

**D-2.5 — Promote the scrub label.** The `status` seam renders next to the goal
when scrubbing. A user in the past must be told, in chrome, that they are in the
past — the mini-timeline's `↩ Now` pill is not sufficient signal on its own
(PRD-08).

**D-2.6 — The accessible summary stays, deduplicated.** Do not simply delete the
hidden div: the kicker (`ACTIVE RUN` / `STANDBY`) is genuinely useful to a screen
reader and has no visible equivalent once the goal becomes the title. Keep the
hidden node for the kicker; drop hidden copies of anything now rendered visibly,
so assistive tech does not read the goal twice.

## 5. UX specification

**Layout (single row, 38px, `flex`, `gap: 12`, `padding: 0 13px`):**

```
wide      [▤]  0xCopilot   Create a file named manual-156ea66f.txt…   ● working  [ Focus │ Studio ]
regular   [▤]  Create a file named manual-156ea66f.txt…               ● working  [ Focus │ Studio ]
compact   [▤]  Create a file named manual-…                           ●          [ F │ S ]
```

| Slot   | Content                              | Behaviour                                                                            |
| ------ | ------------------------------------ | ------------------------------------------------------------------------------------ |
| Left   | Threads toggle (PRD-01)              | Always. `flex: none`.                                                                |
| Brand  | `0x`**Copilot**, muted               | `wide` only.                                                                         |
| Title  | Run goal, or `Standing by` when idle | `flex: 1`, `minWidth: 0`, single line, ellipsis. Left-aligned.                       |
| Status | `RunStatusPulse` + scrub label       | Rendered when non-terminal / scrubbing. At `compact`, dot only, label dropped.       |
| Right  | `ModeSegmentedControl`               | Always. `flex: none`. At `compact`, single-letter labels with `aria-label` retained. |

- **Title is left-aligned, not centred.** The current centred absolute layer
  cannot coexist with a variable-length goal and two flanking clusters without
  overlap. Both references left-align. Delete `titleLayerStyle`'s
  `position: absolute` / `inset: 0` / `pointerEvents: none` and make it a normal
  flex child.
- **Idle** keeps the existing `IDLE_GOAL_COPY` (`Standing by`) — already written,
  already honest, do not mint new copy.
- **Truncation** is `overflow: hidden; text-overflow: ellipsis; white-space: nowrap`
  on the title only. The status and mode clusters never shrink.
- **Tooltip** — the full goal on `title` attribute when truncated.

**Accessibility.**

- Title becomes the visible `<h2 data-testid="run-header-goal">`. Keep the testid;
  existing tests assert on it and should keep passing with the node now visible.
- Hidden node retains only `run-header-kicker`.
- `RunStatusPulse` keeps `data-run-status` and its per-state label; at `compact`
  the label is visually hidden rather than removed, so it stays announced.
- Segmented control keeps `role="tablist"`, `aria-selected`, roving `tabIndex`,
  and arrow cycling unchanged. `aria-label={`${label} mode`}` already exists and
  covers the single-letter compact case.

## 6. User journeys

**J-2.1 — Sarah glances at a windowed run.**
Mid-task, she flicks to the app. The bar reads
`Create a file named manual-156ea66f.txt…` with a pulsing `● working`. She knows
what is running and that it is alive, without reading a single transcript line.
_Today: the bar reads "0xCopilot — Focus" and she must read the transcript._

**J-2.2 — The run finishes while she is away.**
`runStatus` goes terminal. `RunStatusPulse` returns `null` — the chip vanishes,
the pulse stops. The goal remains as the record of what the run was. She can tell
"done" from "working" at a glance, from chrome.

**J-2.3 — Marcus scrubs back through a long run.**
He clicks a bead in the mini-timeline. The header shows the scrub label beside
the goal. He is in no doubt that the canvas is showing the past. He hits ⌘L; the
label clears.
_Today: the label is computed and clipped to 1×1._

**J-2.4 — Idle cockpit.**
No active run. Title reads `Standing by`; no pulse chip; the accessible kicker
reads `STANDBY`. The header does not claim a run it does not have — the existing
`IDLE_KICKER` contract is preserved exactly.

**J-2.5 — A very long goal in a 640px window.**
Goal truncates with an ellipsis at the width left over after the toggle, the dot,
and the two-letter mode control. Hovering shows the full goal. The mode control
never gets pushed off the row.

**J-2.6 — Screen-reader user.**
Focus enters the header. They hear the kicker (`ACTIVE RUN`), the goal once (not
twice), the run status, and the mode tablist. No change in information, one
fewer duplicate.

## 7. Functional requirements

| ID      | Requirement                                                                                                                            |
| ------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| FR-2.1  | The run goal renders **visibly** as the header title at all width classes.                                                             |
| FR-2.2  | `visuallyHiddenStyle` retains only the kicker. Nothing rendered visibly is also rendered hidden.                                       |
| FR-2.3  | `RunStatusPulse` renders visibly, left of the mode control, for non-terminal statuses only.                                            |
| FR-2.4  | The `status` (scrub) seam renders visibly beside the goal when supplied.                                                               |
| FR-2.5  | The wordmark renders at `wide` only.                                                                                                   |
| FR-2.6  | The mode word is removed from the title; the segmented control is the only place mode is stated.                                       |
| FR-2.7  | Title truncates with ellipsis; status and mode clusters have `flex: none` and never shrink.                                            |
| FR-2.8  | Header height stays 38px at every width class.                                                                                         |
| FR-2.9  | At `compact` the mode control uses single-letter labels, retaining `aria-label`.                                                       |
| FR-2.10 | The left slot is reserved for PRD-01's toggle; when PRD-01 has not landed, the slot renders nothing and costs no width.                |
| FR-2.11 | Existing `data-testid`s (`run-header`, `run-header-goal`, `run-header-kicker`, `run-header-status-pulse`, `run-mode-*`) are preserved. |
| FR-2.12 | Reduced-motion gates on the pulse remain intact (`[data-reduce-motion]` + `prefers-reduced-motion`).                                   |

## 8. Non-functional requirements

- **NFR-2.1** `RunHeader` stays presentation-only. Mode remains owned by
  `useRunMode`; `runStatus` remains derived upstream from the single event
  projection (FR-3.3). No new subscription.
- **NFR-2.2** Tokens only. Note `pulseChipStyle` / `pulseDotStyle` /
  `PULSE_STYLE` currently embed `#5fb2ec` and `#9aa0a6` as `var()` fallbacks;
  since these become _visible_, verify they resolve to the sky accent in the
  packaged build rather than repainting a fallback.
- **NFR-2.3** No layout thrash on status transitions — the chip appearing must
  not reflow the goal's truncation point in a visible jump (reserve its width or
  transition it).

## 9. Acceptance criteria

- [ ] A run with goal `"Create a file…"` renders that text visibly in `run-header-goal`.
- [ ] `getComputedStyle(goalNode).clip` is not `rect(0px, 0px, 0px, 0px)`.
- [ ] Querying the accessible name of the header yields the goal exactly once.
- [ ] `runStatus: "running"` renders `run-header-status-pulse` visibly;
      `"succeeded"` renders nothing.
- [ ] At `wide` the wordmark is present; at `regular` and `compact` it is absent.
- [ ] The string `"— Focus"` / `"— Studio"` no longer appears in the header title.
- [ ] With a 400-character goal at 640px, the mode control is still fully rendered
      and the goal is ellipsised.
- [ ] Header `offsetHeight === 38` at all three classes.
- [ ] Reduced-motion: `animation: none` on the pulse dot under both gates.
- [ ] Existing `RunHeader.test.tsx` passes, updated only where it asserted the
      product-identity title.
- [ ] Verified in the packaged desktop build (accent resolves, no fallback grey).

## 10. Open decisions

| ID    | Question                                                           | Recommendation                                                                                                                                                                                                                                                            |
| ----- | ------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| OD-21 | Does the desktop host still need the wordmark for window identity? | The OS window title is set by the host and is the right place for product identity. If that is already correct, D-2.2 could drop the wordmark entirely. **Check `apps/desktop/main` before implementing** — if the title is set there, delete the wordmark at all widths. |
| OD-22 | Should the goal be editable inline (rename the run)?               | Out of scope. Both references make the title a menu target; note it as a follow-up, do not build it here.                                                                                                                                                                 |
| OD-23 | Should the header show elapsed time?                               | No. `RunStatusPulse` already distinguishes queued/working/waiting, and per-tool durations live in the cards (PRD-07).                                                                                                                                                     |
