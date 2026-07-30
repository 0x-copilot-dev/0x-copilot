# PRD-01 — `board://` lanes

## The gap

`BoardRenderer` exists (registered, in the `SurfaceArchetype` union, 286 lines) and
now carries the `plum` identity hue. What it does **not** have is the design's lane
treatment: it renders lanes as plain flex columns with no sticky header, no divider
rhythm, no card chrome, and no changed-card marking.

The colour work shipped a hue for a surface nobody has ever seen rendered. This PRD
closes that.

## Design source

`0xCopilot Surface Language` → `BoardSurface` (`surface-archetypes2.jsx`) and the
`/* ---- board ---- */` block of `surface-lang.css`. Verbatim:

```css
.sfbd {
  display: grid;
  grid-auto-flow: column;
  grid-auto-columns: minmax(196px, 1fr);
  gap: 1px;
  background: var(--line);
  min-height: 230px;
  overflow-x: auto;
  overscroll-behavior-x: contain;
}
.sfbd-c {
  background: var(--panel);
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 7px;
  min-width: 0;
  max-height: 352px;
  overflow-y: auto;
  overscroll-behavior-y: contain;
}
.sfbd-h {
  position: sticky;
  top: -10px;
  z-index: 1;
  margin: -10px -10px 0;
  padding: 10px 10px 7px;
  background: var(--panel);
  font-family: var(--mono);
  font-size: 9.5px;
  letter-spacing: 0.11em;
  text-transform: uppercase;
  color: var(--mut2);
  display: flex;
  align-items: center;
  gap: 6px;
}
.sfbd-h .n {
  margin-left: auto;
  color: var(--mut2);
}
.sfk {
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel2);
  padding: 8px 9px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}
.sfk .t {
  font-size: 12px;
  color: var(--tx);
  line-height: 1.4;
  text-wrap: pretty;
}
.sfk .f {
  display: flex;
  align-items: center;
  gap: 6px;
  font-family: var(--mono);
  font-size: 9.5px;
  color: var(--mut2);
}
.sfk[data-chg] {
  border-color: var(--accent-line);
  box-shadow: inset 2px 0 0 var(--accent);
}
```

Token map (design → ours): `--line` → `--color-border`, `--panel` →
`--color-surface`, `--panel2` → `--color-surface-elevated`, `--mut2` →
`--color-text-subtle`, `--tx` → `--color-text`, `--mono` → `--font-mono`,
`--accent` / `--accent-line` → `--color-accent` / `--color-accent-line`.

## Two details that look like mistakes and are not

**The 1px gap IS the divider.** `.sfbd` sets `gap:1px` over a `--line` background
and each lane paints its own `--panel`. There is no border anywhere; the grid
background shows through. Reproducing this with borders gives doubled lines at the
ends and a different corner treatment.

**`top:-10px` on a sticky header is deliberate.** The lane has `padding:10px`, and
the header cancels it with `margin:-10px -10px 0` so it spans the full lane width.
The negative `top` is what makes it stick flush to the lane's padding box instead
of floating 10px down. Both values must move together.

## Requirements

1. Lanes render as the design's grid: column auto-flow, `minmax(196px,1fr)` tracks,
   1px hairline gaps over the border colour, horizontal scroll with contained
   overscroll.
2. Each lane scrolls independently at `max-height:352px`, with contained overscroll
   so a lane at its end does not scroll the page.
3. The lane header is sticky within its lane, full-bleed, mono/caps/9.5px, with the
   card count pushed right.
4. Cards get the design's chrome: 8px radius, elevated ground, hairline border,
   12px title at 1.4 with `text-wrap: pretty`, mono 9.5px meta row.
5. A changed card is marked with an accent border plus a 2px inset accent bar —
   the **attention** register, not the identity hue. Board's `plum` stays on the
   kicker dot only; a card that needs a decision must not compete with it.
6. The card render cap (`CARD_RENDER_CAP`, 200) is preserved and, when it truncates,
   states so rather than silently dropping cards.
7. Every existing `data-testid` keeps its current meaning; new elements get testids
   the parity anchors can bind to.

## Non-goals

- Drag-and-drop, lane collapse, or card reordering. The board is a **read** surface;
  moving an issue is a staged effect, which already has its own approval path.
- Changing `BoardDiffRenderer`'s field-diff rows. Only the current-state view is in
  scope.

## Definition of done

- [ ] `board://` renders lanes, sticky headers, and card chrome per the values above
- [ ] A changed card shows accent border + inset bar; unchanged cards do not
- [ ] Lane and card counts are correct, and truncation past the cap is stated
- [ ] `surface-renderers` suite green, including new cases for grouping, the
      ungrouped bucket, an empty board, and cap truncation
- [ ] The renderer still degrades to the generic view with no spec (PRD-02's path)
