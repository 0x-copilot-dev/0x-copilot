# Single UI kit + enforcement — design

**Goal.** Kill the recurring typographic/pill drift by finishing (not replacing) the
`packages/design-system` kit: add the missing _recipes_ and _tracking tokens_, delete
the duplicated component CSS, and make future drift fail CI.

**Why it drifted despite having a design system** (inventory evidence):

- The kit standardized **tokens** but shipped **zero** composed text recipes — every
  consumer hand-assembled roles from raw tokens.
- **No tracking scale existed** — 17 letter-spacing magic numbers (11 positive, 6
  negative) for the same roles. One uppercase 2xs "section label" rendered at
  `0.04 / 0.06 / 0.13em` × four weights.
- ~1,900 lines of composer/workspace CSS were **copy-pasted** into
  `apps/frontend/src/styles.css` (73 duplicated class tokens, 27 drifted values) — the
  source of the missing web `.atlas-model-pill__group-head`.
- **Nothing enforced it** — no stylelint; CSS values were never inspected in CI.

## PR sequence

| PR                      | Scope                                                                                                                                                                                                                                                  | Risk                                       |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------ |
| **A · Tokens**          | `--tracking-*` scale (8 steps fold all 17 magic numbers) + `--font-size-mono-10`. No consumers change.                                                                                                                                                 | trivial                                    |
| **B · Recipes + guide** | `.ui-eyebrow / .ui-section-label / .ui-mono-caps / .ui-item-title / .ui-caption / .ui-heading / .ui-pill / .ui-chip--accent` + `Eyebrow/SectionLabel/Caption/ItemTitle/Heading/Pill` React wrappers + `SKILL.md` intent→recipe guide. Additive.        | low                                        |
| **C · Kill-the-copy**   | `chat-surface` exports its CSS; web imports it once; reconcile 27 drifted values into the package; delete ~1,900 dup lines; fix desktop `bootstrap.tsx` deep-imports. Fixes web `group-head`.                                                          | **high — checkpoint + design-parity diff** |
| **D · Migrate**         | Move ~40 hand-rolled selectors onto the recipes (independent clusters: eyebrows+headings ∥ section-labels ∥ pills+captions).                                                                                                                           | med                                        |
| **E · Enforce**         | stylelint `declaration-strict-value` on `font-size`+`letter-spacing` as a path-filtered **required** `css-lint` job in `ci-repo.yml`; sweep `design-exact` disable-comments; wire eslint `no-restricted-syntax` for inline-style drift. Land **last**. | med                                        |

Critical path: **A → B → (C ∥ D-eyebrows ∥ D-labels) → D-pills → E.**

## Token additions (PR A — DONE)

`--tracking-tighter -0.03 / tight -0.02 / snug -0.01 / normal 0 / caption 0.01 /
label 0.05 / eyebrow 0.1 / mono-caps 0.12em`, plus `--font-size-mono-10: 0.625rem`.

## Recipes (PR B — DONE)

Composed roles in `styles.css` (`.ui-*`) + thin React wrappers in `index.tsx`. See
`packages/design-system/SKILL.md` for the intent→recipe map. Each wrapper is
element + recipe class; `as`/`level` picks the tag so a recipe never dictates semantics.

## Enforcement (PR E)

- **stylelint** `scale-unlimited/declaration-strict-value` over `font-size` +
  `letter-spacing` — allows `var(--*)`, `inherit`, `normal`, `0`; rejects raw `px`/`em`.
  Scope to those two properties only (not `line-height` — unitless literals are legit).
- CI: dedicated `css-lint` job in `.github/workflows/ci-repo.yml` with a `'**/*.css'`
  path filter (NOT `ci-frontend.yml`, whose filter never fires on `chat-surface`).
  Required check.
- The `tools/design-parity` harness **cannot run headless** (needs a live server +
  `getComputedStyle` + per-surface `anchors.json`); it stays an **opt-in PR-comment
  punch-list**, not a required gate.

## Judgment calls

- Tracking folds cause tiny visual shifts (a `0.04em` label → `0.05em`) — accepted for
  one-role-one-value consistency.
- **stylelint** over eslint for CSS-value drift (eslint can't see CSS); eslint covers
  only the inline-style TSX hole.
- Pill canonical = the v3 `26px` mono-radius-md spec (drop web's `28px` / `999px`).
