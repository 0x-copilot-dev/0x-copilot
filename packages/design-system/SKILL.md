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
> A stylelint gate (`declaration-strict-value`) fails CI on raw `px`/`em` for those
> two properties. If you truly need an off-ladder value, add a token first.

## Intent → recipe

Pick by the ROLE the text plays, not by how big it looks.

| You are styling…                                         | Use (CSS class)                                          | Or (React)                  | Resolves to                                         |
| -------------------------------------------------------- | -------------------------------------------------------- | --------------------------- | --------------------------------------------------- |
| An **eyebrow / kicker** above a heading                  | `.ui-eyebrow`                                            | `<Eyebrow as="span">`       | 2xs · bold · `--tracking-eyebrow` · UPPERCASE       |
| A **section / group label** (heads a group of rows)      | `.ui-section-label`                                      | `<SectionLabel as="div">`   | 2xs · semibold · `--tracking-label` · UPPERCASE     |
| A **mono caps** micro-label (dividers, mono group heads) | `.ui-mono-caps`                                          | —                           | 3xs · mono · `--tracking-mono-caps` · UPPERCASE     |
| A **page / section heading**                             | `.ui-heading .ui-heading--{1,2,3}`                       | `<Heading level={1\|2\|3}>` | 3xl/2xl/xl · semibold · negative tracking           |
| An **item / card / row title**                           | `.ui-item-title`                                         | `<ItemTitle as="div">`      | md · semibold · `--tracking-normal`                 |
| **Caption / meta** (secondary small text)                | `.ui-caption`                                            | `<Caption as="span">`       | xs · medium · `--tracking-caption`                  |
| A **status / selection pill**                            | `.ui-pill` (+ `.ui-pill--active`, `.ui-pill__dot`)       | `<Pill active dot>`         | rounded-full · hairline · tone + accent-fill states |
| A **live/ready status pill with a dot**                  | —                                                        | `<StatusPill tone label>`   | the pre-existing running/ready/idle variant         |
| An **accent-tinted chip** (skills, citations)            | `.ui-chip--accent` (+ `.ui-chip--inline` for prose flow) | —                           | accent 12% fill / 40% border · rounded-full         |

## Tokens (when no recipe fits)

Reach for a raw token only when you're building a genuinely new composition.

- **Sizes** — `--font-size-3xs … --font-size-3xl` (9→32px). Plus `--font-size-mono-10`
  (10px) for small-mono pill metadata.
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
- **Off-ladder value genuinely required** (rare)? Add the token first, then use it, and
  leave a one-line `/* stylelint-disable-next-line … -- why */` only if even a token
  won't do (e.g. a `clamp()` display size).

## Boundary

Recipes are presentational. Product copy, feature logic, data fetching, and routing do
**not** belong here (see `CLAUDE.md`). A recipe never dictates document semantics — the
`as` / `level` prop picks the tag.
