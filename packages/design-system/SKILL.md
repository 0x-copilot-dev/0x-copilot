---
name: design-system-recipes
description: Which UI-kit recipe/token to use when styling text, headings, labels, pills, and chips in this product. Read BEFORE hand-writing any font-size / font-weight / letter-spacing, or adding a new pill/label/heading class in packages/*, apps/frontend, or apps/desktop.
---

# Design-system recipes — what to use, when, how

This product has **one** UI kit: `packages/design-system`. It owns the tokens
(`--font-size-*`, `--font-weight-*`, `--tracking-*`, colors, space, radii) **and**
the composed **recipes** below. Consumers (`chat-surface`, `surface-renderers`,
`apps/frontend`, `apps/desktop`) reference them — they do **not** re-compose a role
from raw tokens by hand. Doing so is exactly how the same "section label" ended up at
`0.04 / 0.06 / 0.13em` × four weights across the app.

## The rule

> **Never write a raw `font-size` or `letter-spacing` value.** Use a `--font-size-*`
> / `--tracking-*` token, or — better — a recipe class / React wrapper below.
> If you truly need an off-ladder value, add a token first.

**What actually enforces this.** There is **no stylelint gate** in this repo — earlier
versions of this file and of `styles.css` claimed a strict-value rule on `font-size` +
`letter-spacing`, and it does not exist (`find . -name "*stylelint*"` returns nothing; no workflow, pre-commit
hook, or `package.json` references it). The rule above is real; the gate was fiction, and
a rule that claims to be enforced but is not is worse than no rule. The gates that DO
run:

| Gate                                                     | What it pins                                                                                                   |
| -------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `tools/design-parity/lib/render-live-tokens.test.tsx`    | Parses `styles.css`: base size = 13px, the mono micro-ladder rungs, the scrim token, one-writer-per-accent-var |
| `node tools/design-parity/lib/accent-matrix.mjs --check` | All 9 accents × 3 themes resolve distinctly, with contrast floors                                              |
| the per-surface parity harnesses                         | Computed styles of the shipping DOM vs the design mock                                                         |

Raw `font-size` / `letter-spacing` in a consumer is caught by **review**, not by CI.
Standing up stylelint (and baselining the ~400 existing raw values) is its own change.

## Intent → recipe

Pick by the ROLE the text plays, not by how big it looks.

| You are styling…                                                 | Use (CSS class)                                          | Or (React)                  | Resolves to                                                                  |
| ---------------------------------------------------------------- | -------------------------------------------------------- | --------------------------- | ---------------------------------------------------------------------------- |
| An **eyebrow / kicker** above a heading                          | `.ui-eyebrow`                                            | `<Eyebrow as="span">`       | 2xs · bold · `--tracking-eyebrow` · UPPERCASE                                |
| A **section / group label** (heads a group of rows)              | `.ui-section-label`                                      | `<SectionLabel as="div">`   | 2xs · semibold · `--tracking-label` · UPPERCASE                              |
| A **mono caps** section head (indexes a list/surface)            | `.ui-mono-caps`                                          | —                           | `--font-size-mono-9-5` · mono · regular · `--tracking-mono-caps` · UPPERCASE |
| The **quieter** mono caps register (dividers, panel group heads) | `.ui-mono-caps .ui-mono-caps--9`                         | —                           | same, at `--font-size-3xs` (9px)                                             |
| The **wrapper** around a section head (label + count + action)   | `.ui-section-head`                                       | —                           | flex row · `margin: 22px 0 10px` · `:first-child{margin-top:0}`              |
| A **page / section heading**                                     | `.ui-heading .ui-heading--{1,2,3}`                       | `<Heading level={1\|2\|3}>` | 3xl/2xl/xl · semibold · negative tracking                                    |
| An **item / card / row title**                                   | `.ui-item-title`                                         | `<ItemTitle as="div">`      | md · semibold · `--tracking-normal`                                          |
| **Caption / meta** (secondary small text)                        | `.ui-caption`                                            | `<Caption as="span">`       | xs · medium · `--tracking-caption`                                           |
| A **status / selection pill**                                    | `.ui-pill` (+ `.ui-pill--active`, `.ui-pill__dot`)       | `<Pill active dot>`         | rounded-full · hairline · tone + accent-fill states                          |
| A **live/ready status pill with a dot**                          | —                                                        | `<StatusPill tone label>`   | the pre-existing running/ready/idle variant                                  |
| An **accent-tinted chip** (skills, citations)                    | `.ui-chip--accent` (+ `.ui-chip--inline` for prose flow) | —                           | accent 12% fill / 40% border · rounded-full                                  |
| A **bordered mono metadata chip** (design `.chip`)               | `.ui-badge` (+ `--success/--warning/--danger/--accent`)  | —                           | `--font-size-mono-10-5` · mono · medium · hairline border, NO fill           |

## Composer chrome + popovers (v3 parity family)

The composer's control row and every popover it opens (attach · model · tools) are
one pixel-authored family, ported from the design's `.cmp-*` / `.pop*` blocks. They
live in `styles.css` — **not** in `chat-surface/src/composer/composer.css` — because
`apps/frontend` loads `design-system/styles.css` but never `composer.css`; anything
authored there styles desktop only, which is how the two composers drifted apart.

| You are building…                        | Use (CSS class)                                                     | Notes                                                                                         |
| ---------------------------------------- | ------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| The popover **click-out layer**          | `.ui-pop-scrim`                                                     | fixed · inset 0 · z-index **70** · fully transparent. Sibling BEFORE the panel.               |
| The popover **panel**                    | `.ui-pop`                                                           | z-index **71** · 13px/1.5 base · `ui-pop-in` 0.14s. Consumer owns anchoring + width only.     |
| Its **header** / header meta             | `.ui-pop__h` · `.ui-pop__h-meta`                                    | 12px semibold display · right-aligned 9px mono meta                                           |
| A **group heading** inside the list      | `.ui-pop__grp`                                                      | 8.5px mono caps — deliberately NOT `.ui-section-label` (that one is sans/11.2px/600)          |
| The **scrolling list** region            | `.ui-pop__list`                                                     | max-height 264px                                                                              |
| A **row** (+ its parts)                  | `.ui-pop-row` · `__lg` `__m` `__nm` `__txt` `__sb` `__rad`          | `[data-off]` dims · `[data-on]` fills the radio · `.ui-pop-row--pin` for a pinned foot action |
| A **divider** / **footer** / footer link | `.ui-pop__div` · `.ui-pop__f` · `.ui-pop__f-link` · `.ui-pop__f-sp` | footer sits on `--color-bg-elevated`                                                          |
| A composer **icon button** (attach, mic) | `.ui-cicon`                                                         | 26x26 · quiet panel fill on hover / `[data-open]` / `[aria-expanded="true"]`                  |
| A composer **pill** (model, tools)       | `.ui-cpill` · `__dot` `__lb` `__n`                                  | 26px · mono 10px · transparent border until hover/open — **never** an accent ring             |
| The composer **hint** (⌘↵ etc.)          | `.ui-chint`                                                         | owns the `margin-left: auto` that right-aligns the row tail                                   |
| The composer **send** button             | `.ui-csend`                                                         | 28x28 · the ONE accent-filled control in the composer                                         |

Two rulings that are easy to get wrong:

- `.ui-cpill__n` is the ACTIVE count only ("1"), as plain dimmed mono text. Not
  "on/total", not a filled badge.
- The scrim/panel z-index pair (70/71) and the open animation belong to the recipe.
  Do not re-declare either in a consumer stylesheet, and do not add a second
  click-out listener next to the scrim.
- Scrim **or** the `<Menu>` primitive, never both: `Menu` already dismisses on
  outside-pointerdown and writes `position: fixed; z-index: 50` inline, which a class
  cannot beat — a `.ui-pop` inside a `Menu` would sit under the scrim.

## Tokens (when no recipe fits)

Reach for a raw token only when you're building a genuinely new composition.

- **Sizes (sans ladder)** — `--font-size-3xs … --font-size-3xl` (9→32px). The `sm` rung
  is the app's inherited **base**: exactly 13px, the design's `body` size.
- **Sizes (mono micro-ladder)** — the design's mono metadata register is a half-pixel
  ladder the sans t-shirt scale cannot express, so it has its own rungs, named by px:
  `--font-size-mono-8-5` (rail count badge), `--font-size-mono-9-5` (section heads, ⌘K
  rows), `--font-size-mono-10` (small pill metadata, side heads), `--font-size-mono-10-5`
  (status chips, row timestamps). **Do not reach into `--font-size-3xs` / `-2xs` for mono
  metadata** — they are sans rungs that merely sit nearby, and doing so is exactly how the
  section head shipped 18% too large.
- **Sizes (composer/popover)** — two whole-pixel steps `--font-size-13` / `--font-size-12`
  that sit between the rem-ladder rungs. Use them ONLY inside the composer + popover
  recipes — they exist because the design authors that family off-ladder.
- **Weights** — `--font-weight-regular/medium/semibold/bold` (400/500/600/700). Never a
  numeric literal (a `<strong>` 700 next to the app's 600 reads as a different family
  on macOS — the original "+ menu vs pill" bug).
- **Tracking** — `--tracking-tighter/tight/snug/normal/caption/label/eyebrow/mono-caps`
  (-0.03 → 0.12em). There is no other legal letter-spacing value.

## When to add vs reuse

- **Same role as an existing recipe?** Use it. If it looks slightly off, fix the recipe
  (one place) — do not fork a near-copy.
- **New role the table doesn't cover?** Add a recipe here (class in `styles.css` +
  optional wrapper in `index.tsx` + a row above), don't inline it in a consumer.
- **Off-ladder value genuinely required** (rare)? Add the token first, then use it. If
  even a token won't do (e.g. a `clamp()` display size), keep the literal and leave a
  one-line comment saying why — that comment is what review looks for.

## Boundary

Recipes are presentational. Product copy, feature logic, data fetching, and routing do
**not** belong here (see `CLAUDE.md`). A recipe never dictates document semantics — the
`as` / `level` prop picks the tag.
