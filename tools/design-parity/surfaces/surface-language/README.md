# surface-language — board lanes (PRD-01) · the no-spec view (PRD-02)

The parity surface for [`docs/plan/surface-language/`](../../../../docs/plan/surface-language/README.md).
Both PRDs close on a **computed-style report, not a green unit suite** — deliberately,
because the colour work that preceded them shipped three defects that unit tests passed
straight through, every one of them a test asserting a proxy instead of the rendered
result. This folder is what makes the real check runnable.

## Run it

```bash
node tools/design-parity/lib/run-surface-language-parity.mjs
```

That is the whole thing: it builds the design bundle, renders the live surfaces,
extracts both sides in headless chromium, and writes `out/report-<state>.md` plus a
summary at `out/report.md`. No dev server, no network, no browser download — it reuses
whichever chromium is already in the Playwright cache (override with `PARITY_CHROMIUM`).

The three steps also run standalone when you are iterating on one of them:

```bash
# design side only — rebuild the bundle, then serve and open it
node tools/design-parity/lib/prepare-surface-language-design.mjs
cd tools/design-parity && python3 -m http.server 8099
#   http://127.0.0.1:8099/surfaces/surface-language/design/index.html?state=board
#   …?state=board-changed   …?state=no-spec   …?state=table
#   add &color=quiet to hold the neutral ladder constant (see below)

# live side only
node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs \
  lib/render-live-surface-language.test.tsx
#   → surfaces/surface-language/live/{board,board-changed,board-capped,no-spec,no-spec-board}.html
```

## The five states

| State           | PRD    | Design                       | Live                                                   |
| --------------- | ------ | ---------------------------- | ------------------------------------------------------ |
| `board`         | PRD-01 | `BoardSurface` `st=current`  | `BoardRenderer` + spec, 4 lanes / 7 cards              |
| `board-changed` | PRD-01 | `BoardSurface` `st=proposed` | same + one `SurfaceFieldChange` marking `issues.2`     |
| `board-capped`  | PRD-01 | `TableSurface` (see below)   | `BoardRenderer` with 260 cards → the cap line          |
| `no-spec`       | PRD-02 | `GenericSurface`             | `TableRenderer` with **no spec**                       |
| `no-spec-board` | PRD-02 | `GenericSurface`             | `BoardRenderer` with **no spec** — same target, proven |

`board-capped`'s design side is the **table** surface on purpose: the mock has no board
cap line, and `.sft-cap` is the only place it states a truncation. That report is a
register comparison and `anchors/board-capped.json` says so on the tin.

## What is design source and what is harness

`design/` is vendored from DesignSync project `73f810d9-…` (Copilot), page
_0xCopilot Surface Language_ — hashes in `design/PROVENANCE.json`, refresh per
[`design-kit/REFRESH.md`](../../design-kit/REFRESH.md).

| File                      |                                                                               |
| ------------------------- | ----------------------------------------------------------------------------- |
| `surface-lang.css`        | **design source** — `.sl` / `.sfc` / `.sfbd` / `.sfk` / `.sfr` / `.sf-note`   |
| `surface-kit.jsx`         | **design source** — `SfCard` / `SfNote` / `SfFieldRows` / `SfBar` / `SfTable` |
| `surface-specs.jsx`       | **design source** — `BOARD_COLS`, `GENERIC_PAYLOAD`, `SURFACES`, `TIER_LABEL` |
| `surface-archetypes2.jsx` | **design source** — `BoardSurface`, `GenericSurface`, the rest                |
| `index.html`              | harness — the page shell + pinned geometry                                    |
| `_globals.js`             | harness — puts `React` on the global before the classic scripts evaluate      |
| `_mount.jsx`              | harness — the `?state=` / `?color=` driver and the `.sl → .sf` ancestor chain |
| `copilot-v3.css`          | harness — a one-line shim so the vendored `@import` reaches `design-kit/`     |
| `build/`                  | generated, gitignored — the esbuild bundle                                    |

This surface compiles its JSX with **esbuild from `node_modules`** rather than pulling
React + Babel from unpkg the way the older design harnesses do. Same rendered DOM, no
network inside headless chromium, ~30 ms.

## The measured property set is widened here, deliberately

Both PRDs are specified in properties the extractor's curated default set does **not**
carry: `position` / `top` / `z-index` (the sticky lane header), `grid-auto-flow` /
`grid-auto-columns` (the lane track), `min-height` / `max-height`, `overflow-x/y` and
`overscroll-behavior-x/y` (independent, contained lane scroll), `text-wrap` (the card
title) and `grid-template-columns` / `font-variant-numeric` (PRD-02's field rows and
its numeric register).

An uncaptured property cannot produce a row, so a harness measuring only the defaults
would have reported these as parity **without ever looking at them** — the precise
failure mode `docs/plan/surface-language/README.md` says this program exists to end.
Each `anchors/*.json` therefore declares an `extraProps` array, which
`extract-playwright.mjs` appends to the defaults for this surface only. Adding them
moved the aggregate by 42 MEDIUM rows.

It cuts both ways, and both are worth having: the widened set is also what lets the
board reports state positively that the lane grid, the `352px` lane clamp, the
contained overscroll, `text-wrap: pretty`, and the `top:-10px` sticky offset **match
the design exactly** — a confirmation the narrow set could not have made.

## Three things to read past in every report

1. **Width is noise.** The live card is `max-width: 820px` inside the same 1040px frame
   the design fills edge to edge, so live lanes clamp to the 196px minimum while the
   design's get ~249px. Typography, colour, spacing, border and shadow are
   container-independent; width and height are not.
2. **One systemic ground delta repeats everywhere.** The design is measured in its own
   default `data-color="functional"`, which re-declares the whole neutral ladder in
   oklch (`--panel` → `oklch(0.212 0.010 276)`). The live app adopted the identity hues
   but not that ladder. Re-run the design side with `?color=quiet` to hold the ladder
   constant and see what is left — at the cost of the identity question, whose rules are
   scoped to functional mode.
3. **A decorative dot inherits type.** `card.kicker-dot` paints no text, so its
   font/line-height/letter-spacing/color rows are its parent kicker's register showing
   through. Read `backgroundColor` / `boxShadow` / size on the dot; the register itself
   is measured on `card.kicker`.

## Two anchors are bound structurally, and why

`nospec.note` and `nospec.footer` (in `anchors/no-spec.json`) do not name a testid.
PRD-02's `NoSpecView` had not landed when this was authored — `primitives.tsx` still
exports `PreparingHint` and every archetype's spec-less path still calls it — so naming
a testid would have been a guess. Instead:

- `nospec.note` → `[data-testid="surface-header"] + *`, i.e. _whatever occupies the slot
  right after the header_. Today that is the "Preparing view…" pill, which is exactly
  what PRD-02 requirement 6 replaces; after PRD-02 it is the honest note.
- `nospec.footer` → the card's last child, excluding the generic field list. Matches
  nothing today — which is the correct finding, there is no read-only footer yet — and
  will match whatever PRD-02 lands without an edit here.

## Deliberately not mapped

Each anchors file carries an `unmapped` array with the reason. In short:
`board-diff-rows` (a different render, `data-mode="diff"`, with no design counterpart),
`surface-empty` (the mock never renders `.sf-empty` on the board), the mock's proposed
note + approval bar (approval chrome is host-owned, and PRD-01 scopes itself to the
current-state view), the mock's "Open in PagerDuty" button (PRD-02 declines a generic
destination as a non-goal), and `surface-generic-field-cap` (the mock's payload has 8
fields, so it never truncates).
