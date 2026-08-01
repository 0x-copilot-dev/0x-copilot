# PRD-08 — The timeline reads as a timeline

**Severity:** P1 · **Depends on:** [PRD-00](./PRD-00-overview.md) for the compact half · **Coordinates with:** [PRD-02](./PRD-02-run-header.md) (scrub label) · **Surface:** `thread-canvas/TcMiniTimeline.tsx`

## 1. Problem statement

At the very bottom of the 0xCopilot window sits a strip of twelve small dots —
ten grey, two amber — with no label. I first read it as decorative noise or a
stray pager, and said so.

**That was wrong, and the correction matters.** It is `TcMiniTimeline`: a fully
functional time-travel scrubber. Click a bead to rewind the canvas to that
moment; ⌘←/⌘→ to step; ⌘L or Escape to snap live; a `Live` / `↩ Now` pill; an
expand chevron back to Studio. Every bead is a real `<button>` with an
`aria-label`, a `title` tooltip, and `aria-pressed`.

So the defect is not "meaningless chrome". It is worse in an interesting way:
**a genuinely powerful feature is rendered so quietly that a reader who has been
staring at the app cannot tell it is interactive.** The affordance is invisible
while the pixels are spent anyway.

## 2. Current state

The component is well built. Its problems are presentational and vocabulary-level.

### 2.1 Nothing announces what the strip is

The container is `role="region" aria-label="Run timeline (mini)"` — correct for a
screen reader, invisible to everyone else. There is no visible label, no icon, no
count. The only text is the `Live` pill at the right edge, which reads as a status
badge rather than as the control for the row it sits in.

### 2.2 The lane vocabulary is from the previous product

[TcMiniTimeline.tsx:48](../../../packages/chat-surface/src/thread-canvas/TcMiniTimeline.tsx#L48):

```ts
const LANE_COLORS = new Map<string, string>([
  ["email", "var(--color-accent, #d97757)"],
  ["sheet", "var(--color-success, #6ab04c)"],
  ["sf-opp", "var(--color-warning, #f0a330)"],
  ["slide", "var(--color-info, #7a9bd9)"],
  ["system", "var(--color-text-subtle, #7e7e84)"],
]);
```

`email` / `sheet` / `sf-opp` / `slide` are SaaS-surface lanes from the Atlas
product model. Lanes are actually derived as the **URI scheme** of a surface
([eventProjector.ts:487](../../../packages/chat-surface/src/thread-canvas/eventProjector.ts#L487)):

```ts
lane: surfaceUri ? schemeOf(surfaceUri) : "system",
```

For the captured filesystem run, no surface URI has scheme `email` or `sf-opp`, so
essentially every bead falls through `colorForLane` to the muted default. **The
colour coding conveys nothing for the workloads the product actually runs**, which
is exactly why the strip reads as undifferentiated grey dots.

### 2.3 The hex fallbacks are the wrong brand

`#d97757` is Claude's terracotta, not 0xCopilot's sky `#5fb2ec`. `#f0a330` is
amber, which the brand palette deprecated. `#6ab04c` and `#7a9bd9` are likewise
not the jade/info values. These only paint on a token-resolution miss — but a
token miss is exactly when you least want the previous product's palette to
appear. This is the same class the settings-parity program flagged as **T-3 —
purge stale fallbacks**.

### 2.4 The hit target is 8×8

[beadStyle](../../../packages/chat-surface/src/thread-canvas/TcMiniTimeline.tsx#L220):
`width/height: selected ? 12 : 8`, `gap: 4`, `padding: 0`. An 8×8 pointer target
is far below any usable minimum (24×24 is the common floor), and the 4px gap means
a miss lands on the neighbouring bead — scrubbing you to the wrong moment rather
than doing nothing. In a narrow window with more beads packed in, this gets worse.

## 3. Goals & non-goals

**Goals**

- The strip is legible as an interactive timeline without a tooltip.
- Colour encodes something true about the current product.
- Beads are clickable without precision aiming.
- Stale-brand fallbacks are gone.

**Non-goals**

- Redesigning scrubbing semantics, the keyboard chords, or `projectAt`. The
  mechanics are right.
- Replacing the mini timeline with the Studio swimlanes. The compact strip is the
  correct Focus-mode affordance.
- Adding a second scrub surface.

## 4. Design decisions

**D-8.1 — Give the strip a leading label.** A small mono `TIMELINE` kicker, or a
bead-count (`6 steps`), at the left of the row. It costs ~60px horizontally and
converts an ambiguous dot row into an obviously-labelled control. The right edge
already has the `Live` pill; a left label balances it and frames the beads as
content between two controls.

**D-8.2 — Re-derive lanes from what runs today.** Replace the SaaS-surface
vocabulary with the transcript's actual entry kinds, which the projector already
distinguishes: `tool`, `subagent`, `approval`, `error`, `answer`. That is a
colour coding that means something in every run the product performs, rather than
one that means something in none of them.

Keep `schemeOf(surfaceUri)` as a **secondary** lane for genuine surface work —
it is not wrong, it is just not the primary axis any more.

**D-8.3 — Delete the hex fallbacks.** Use bare `var(--color-*)`. If a token fails
to resolve, an unstyled bead is a better outcome than a Claude-terracotta bead,
because the former is a visible bug and the latter is a silent brand regression.

**D-8.4 — Grow the hit target without growing the strip.** Keep the 8px visual
bead; wrap it in a transparent ≥24×24 button with negative margins so the click
area overlaps without changing layout height. This is the standard fix and costs
zero vertical pixels — which matters, because vertical pixels are the whole
premise of this program.

**D-8.5 — At `compact`, cap the bead count.** A 640px strip minus the label and
pill leaves ~450px; at 12px per target that is ~37 beads. Beyond that, sample and
render a `+N` affordance rather than shrinking beads below the hit floor. Never
degrade the target size to fit more.

**D-8.6 — Scrubbed state must be loud.** Today, being in the past is signalled by
the pill flipping to `↩ Now` and one bead growing 8→12px. That is too quiet for a
mode as consequential as viewing stale state. PRD-02 promotes the header scrub
label; this PRD adds a tint to the strip container via the existing
`data-state="scrubbed"` attribute, which is already emitted and currently unstyled.

## 5. UX specification

```
live
┌─────────────────────────────────────────────────────────────┐
│ TIMELINE  ● ● ● ● ● ● ● ● ● ● ● ●            [ Live ]  [↑]  │
└─────────────────────────────────────────────────────────────┘

scrubbed  (container tinted, header shows the scrub label per PRD-02)
┌─────────────────────────────────────────────────────────────┐
│ TIMELINE  ● ● ● ◉ ○ ○ ○ ○ ○ ○ ○ ○            [ ↩ Now ] [↑]  │
└─────────────────────────────────────────────────────────────┘
             past  ↑cursor   future (dimmed)
```

- **Beads after the cursor dim** to `opacity: .4` while scrubbed. Right now
  nothing distinguishes "already happened" from "hasn't happened at the cursor",
  which is the single most useful thing a scrubber can show.
- **Lane colours** map to the D-8.2 kinds using existing semantic tokens:
  tool → `--color-text-subtle`, subagent → `--color-accent`,
  approval → `--color-warning`, error → `--color-danger`,
  answer → `--color-success`.
- **Strip height stays ~20px.** No vertical growth. The label and larger hit
  targets are horizontal and overlay changes only.

**Accessibility.** `role="region"` + `aria-label` are kept. The new visible label
is `aria-hidden` (the region label already announces it — do not double it). Bead
buttons keep `aria-label={bead.title}`, `aria-pressed`, and `title`. The `+N`
affordance is a real button announcing `Show N earlier steps`.

## 6. User journeys

**J-8.1 — Sarah notices the timeline for the first time.**
The row now reads `TIMELINE ● ● ● …` with a `Live` pill. She recognises it as a
scrubber, hovers a bead, sees the tooltip, clicks. The canvas rewinds.
_Today: she reads twelve unlabelled dots as decoration — as I did._

**J-8.2 — Marcus rewinds to inspect a step.**
He clicks a bead a third of the way along. The strip tints, later beads dim, the
cursor bead is enlarged, the pill flips to `↩ Now`, and the header shows the scrub
label. Four independent signals that he is looking at the past. He hits ⌘L and
everything snaps back.

**J-8.3 — A long run in a narrow window.**
Sixty beads, 640px. The strip samples to fit at full hit-target size and shows
`+24`. Clicking it expands or pages. No bead is ever smaller than the touch floor.

**J-8.4 — A run with a failed step.**
The failing step's bead is `--color-danger` and stands out along the strip. He
clicks straight to it. Colour finally carries information, because the lanes
describe what the product does.

**J-8.5 — A fresh run with zero beads.**
The strip shows `No activity yet` and a permanent `Live` pill with
`aria-disabled` — unchanged from today. The existing comment explains exactly why
the pill is permanent; that behaviour is correct and stays.

## 7. Functional requirements

| ID      | Requirement                                                                                                               |
| ------- | ------------------------------------------------------------------------------------------------------------------------- |
| FR-8.1  | A visible leading label frames the strip. `aria-hidden`, since the region already carries an accessible name.             |
| FR-8.2  | Lane vocabulary is `tool` \| `subagent` \| `approval` \| `error` \| `answer`, derived from the existing projection.       |
| FR-8.3  | `schemeOf(surfaceUri)` lanes remain supported as a secondary axis; unknown lanes fall back to the muted default.          |
| FR-8.4  | All hard-coded hex fallbacks are removed from `LANE_COLORS`. Bare `var(--color-*)` only.                                  |
| FR-8.5  | Bead hit targets are `>= 24×24` via a transparent wrapper. The visual bead stays 8px (12px selected).                     |
| FR-8.6  | Strip container height is unchanged (~20px) at every width class.                                                         |
| FR-8.7  | While scrubbed, beads after the cursor render at reduced opacity and the container is tinted via `data-state="scrubbed"`. |
| FR-8.8  | At `compact`, beads sample with a `+N` control rather than shrinking below the hit floor.                                 |
| FR-8.9  | Keyboard behaviour (⌘L, Escape, ArrowLeft/Right, the end-of-strip snap-to-now) is unchanged.                              |
| FR-8.10 | Empty-state behaviour and the permanent-pill rationale are unchanged.                                                     |
| FR-8.11 | All existing `data-testid`s are preserved.                                                                                |

## 8. Non-functional requirements

- **NFR-8.1** No new state. The component is a stateless projection renderer and
  must stay one — every change exits via the existing callbacks.
- **NFR-8.2** Sampling is deterministic: same beads in, same sample out. A strip
  that reshuffles on re-render is unusable.
- **NFR-8.3** No animation on scrub. Instant is correct for a scrubber.

## 9. Acceptance criteria

- [ ] The strip renders a visible label; `aria-hidden="true"` on it, and the
      region's accessible name is unchanged.
- [ ] `rg "#[0-9a-fA-F]{6}" TcMiniTimeline.tsx` returns zero hits.
- [ ] Every bead button's `getBoundingClientRect()` is `>= 24×24`.
- [ ] Strip container `offsetHeight` is unchanged from `main` at all width classes.
- [ ] With `scrubbedTo` set, beads after the cursor have reduced opacity and the
      container carries the scrubbed tint.
- [ ] 60 beads at a 640px container: no bead below the hit floor, `+N` present, and
      the sample is stable across two renders with identical input.
- [ ] Lane colours resolve to the five semantic tokens; an unknown lane resolves to
      the muted default.
- [ ] Keyboard suite (⌘L, Escape, arrows, end-of-strip snap) passes unmodified.
- [ ] Packaged desktop build: tokens resolve (no unstyled beads), guarding D-8.3's
      deliberate removal of the fallbacks.

## 10. Open decisions

| ID    | Question                                           | Recommendation                                                                                                                                                      |
| ----- | -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| OD-81 | Label text — `TIMELINE`, a step count, or an icon? | Step count (`6 steps`). It is information rather than a label, and it collapses to `6` at `compact`.                                                                |
| OD-82 | Should the strip be hideable?                      | No. It is ~20px and it is the only rewind affordance in Focus mode. Hiding it re-creates this bug.                                                                  |
| OD-83 | Should `+N` expand inline or switch to Studio?     | Inline paging. Switching mode to see history is too big a jump for a `+N`; `↑` already offers Studio.                                                               |
| OD-84 | Do the stale hex fallbacks exist elsewhere?        | Almost certainly — settings-parity T-3 found the same pattern in `ProfilePage`/`NotificationsPage`. Worth one repo-wide sweep as a separate chore, not in this PRD. |
