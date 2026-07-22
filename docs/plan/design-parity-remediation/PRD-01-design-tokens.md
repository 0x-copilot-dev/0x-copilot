# PRD-01 — Design token foundation: accent cascade, base size, mono rungs, scrim

## Problem

Four things a user can see today:

1. **The accent picker is a lie outside dark mode.** Settings → Appearance offers nine
   accent swatches. Pick violet, switch the theme to Light, and everything accented turns
   the same blue as everyone else's. Switch to Slate and it turns a _different_ single
   blue. Nine choices collapse to one per theme. The setting still says "Violet", the
   preference still round-trips to the server, and the UI simply ignores it.
2. **Every unstyled line of text in the app is 4.6% too large.** The design's base is a
   literal `13px`; we inherit `13.6px`. It is invisible in isolation and unmissable in
   aggregate — 55 measured rows across the five audited surfaces are this one substitution,
   and it is why the app reads slightly "chunkier" than the mock at identical zoom.
3. **Section headers shout.** The mono micro-labels that index a list ("PINNED", "RECENT",
   "ARCHIVED") render at 11.2px semibold where the design draws them at 9.5px regular — 18%
   too big, on the loudest possible type treatment (uppercase, wide-tracked, mono). They
   compete with the row titles they are supposed to sit quietly above.
4. **The Tools connect dialog dims the wrong way.** Its backdrop is a flat neutral
   `rgba(0,0,0,0.54)` with no blur, where the design uses a hue-matched `rgba(4,4,6,0.66)`
   over a 2px blur. The modal reads as pasted on top of the app instead of lifted out of it.

All four are defects in one file — `packages/design-system/src/styles.css`, the declared
single token source of truth — and therefore hit every surface, both hosts, at once.

## Evidence

Every row opened and verified in this working tree.

| Claim                                                                                   | File:line                                                                                                                      | What the code actually does                                                                                                                                                                                                                                 |
| --------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Nine accent blocks define `--color-accent`                                              | `packages/design-system/src/styles.css:245-289`                                                                                | CONFIRMED. `:root[data-accent="sky"…"violet"]`, each writing `--color-accent`, `--color-accent-strong`, `--color-accent-contrast`. Header comment at `:243-244` claims "a swatch override wins regardless of theme".                                        |
| Light theme redefines accent at equal specificity, later in source                      | `packages/design-system/src/styles.css:291,307-309`                                                                            | CONFIRMED. `:root[data-theme="light"]` = `(0,2,0)`, identical to `:root[data-accent="…"]` = `(0,2,0)`, and 46 lines later. `--color-accent:#1f6fb0` at `:307`. Later-equal wins → all nine accents collapse.                                                |
| Slate does the same                                                                     | `packages/design-system/src/styles.css:324,335-337`                                                                            | CONFIRMED. `--color-accent:#7bb7ff` — i.e. slate silently forces the `blue` swatch on every user regardless of their pick.                                                                                                                                  |
| Measured: 9 distinct badge colours in dark, 1 in light, 1 in slate                      | `tools/design-parity/surfaces/rail-badge/out/AUDIT.md:155`; probes `probe4-accent-theme.mjs`, `probe5-design-accent-theme.mjs` | CONFIRMED as a prior measurement, and independently re-derived above from the cascade. Design measures 4/4 across both themes.                                                                                                                              |
| The light-accent darkening is deliberate, not accidental                                | `packages/design-system/src/styles.css:306`                                                                                    | CONFIRMED — comment: "Darkened brand sky for legible accent text/borders on the light ground." **This matters: a naive "delete the theme override" fix ships `#5fb2ec` (~2.0:1) as text on `#ffffff`.** The design has the same latent bug.                 |
| Accent _is_ used as foreground text, so the a11y concern is real                        | `packages/design-system/src/styles.css:586-587, 833, 1009`                                                                     | CONFIRMED. `.ui-chip--accent{color:var(--color-accent)}`, `.ui-badge--accent`, `.ds-*` link colour.                                                                                                                                                         |
| Accent aliases reflow automatically from `--color-accent`                               | `packages/design-system/src/styles.css:214-241`                                                                                | CONFIRMED. `--color-accent-soft/-line/--color-bg-accent-subtle` are `color-mix(... var(--color-accent) ...)` on bare `:root`, resolving at use-site. `color-mix()` is already load-bearing here (also `:970, :1160, :1183`).                                |
| `body` inherits 13.6px; design is 13px                                                  | `packages/design-system/src/styles.css:65, 377`; `tools/design-parity/design-kit/app-v3/copilot.css:104`                       | CONFIRMED. `--font-size-sm: 0.85rem` (13.6px); `body{font-size:var(--font-size-sm)}`. Design `body{…font-size:13px…}`. The comment at `:365-368` admits it is an approximation ("the closest token on the existing scale to the design's 13px").            |
| The rem ladder is anchored at the UA 16px, deliberately                                 | `packages/design-system/src/styles.css:369-376`                                                                                | CONFIRMED. Comment: "It deliberately does NOT touch the rem anchor (`html`/`:root` stays at the UA 16px), so every rem-based token … keeps its exact geometry."                                                                                             |
| 148 call sites already believe `--font-size-sm` is 13px                                 | `grep -rn "font-size-sm, *13px" packages apps` → 148 hits (vs 2 for `, 14px`)                                                  | CONFIRMED. e.g. `packages/chat-surface/src/settings/ProfilePage.tsx:176`, `NotificationsPage.tsx:164`, `WebhookSecurityPage.tsx:102`. 394 references to the token in total.                                                                                 |
| 13.6px drift is app-wide, not Chats-local                                               | `tools/design-parity/surfaces/*/out/report-*.md`                                                                               | CONFIRMED by count: tools 22, projects 14, chats 7, activity 6, rail-badge 6 = **55 rows**.                                                                                                                                                                 |
| `--font-size-3xs` = 9px, `--font-size-2xs` = 11.2px, `--font-size-mono-10` = 10px exist | `packages/design-system/src/styles.css:62, 63, 71`                                                                             | CONFIRMED. `:71` comment: "deliberately off the main ladder".                                                                                                                                                                                               |
| `--font-size-mono-10` is UNUSED                                                         | `grep -rn "font-size-mono-10"` → `packages/chat-surface/src/onboarding/onboarding.css:575`                                     | **DISPUTED.** It has exactly one consumer (the FTUE wallet chip). "Unused" is wrong; "used once, in one subtree, and unknown to every destination component" is right — which is the same discoverability failure, stated honestly.                         |
| A doc comment states a wrong fact and drove the wrong pick                              | `packages/chat-surface/src/destinations/_shared/SectionHeader.tsx:4`                                                           | CONFIRMED, verbatim: `Mono, ~9.5px (--font-size-2xs)`. `--font-size-2xs` is 11.2px. The component then picks it at `:40`.                                                                                                                                   |
| Section heads render 11.2px semibold                                                    | `packages/chat-surface/src/destinations/_shared/SectionHeader.tsx:40-41`                                                       | CONFIRMED, plus a raw `letterSpacing: "0.12em"` at `:42` where `--tracking-mono-caps` is exactly that value (`styles.css:92`).                                                                                                                              |
| Design section head is **9.5px, not 9px**                                               | `tools/design-parity/design-kit/app-v3/copilot.css:1563-1570`                                                                  | **PARTIAL DISPUTE of the audit brief.** `.sect-h{font-family:var(--mono);font-size:9.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--mut2)}`. `--font-size-3xs` (9px) is the _closest_ rung but still 0.5px off = MEDIUM band.                |
| Design chip/time register is **10.5px, not 10px**                                       | `tools/design-parity/design-kit/app-v3/copilot.css:575-586, 1655-1660`                                                         | CONFIRMED. `.chip` and `.lrow__time` are both `10.5px`. `--font-size-mono-10` (10px) is 0.5px under = MEDIUM band. So the rung that "exists" still cannot hit the target.                                                                                   |
| An existing recipe already encodes the `.sect-h` role                                   | `packages/design-system/src/styles.css:1097-1104`                                                                              | CONFIRMED. `.ui-mono-caps` = mono + `--font-size-3xs` + `--tracking-mono-caps` + uppercase. `SectionHeader` hand-composed it instead, at the wrong size.                                                                                                    |
| An existing recipe already encodes the `.chip` role                                     | `packages/design-system/src/styles.css:555-568`                                                                                | CONFIRMED. `.ui-badge`, comment: "design `.chip` — mono, bordered, NO fill". Size `--font-size-2xs` (11.2px) at `:563`, weight semibold at `:564` — both wrong vs the design's 10.5px / inherited 500.                                                      |
| A raw off-ladder `font-size` literal survives                                           | `packages/chat-surface/src/shell/AppRail.tsx:155`                                                                              | CONFIRMED — `fontSize: 8.5`. It is _design-correct_ (`copilot.css:353` `.rbadge{font-size:8.5px}`) but there is no token for it, so the SKILL rule "never write a raw font-size" is unsatisfiable here.                                                     |
| No `--color-scrim` token exists                                                         | `grep -rn "color-scrim" packages apps` → only two _consumers_ with fallbacks                                                   | CONFIRMED. `packages/chat-surface/src/settings/Modal.tsx:145` `var(--color-scrim, rgb(0 0 0 / 0.54))` and `surfaces/edit/EditOverlay.tsx:437` `var(--color-scrim, rgba(8,10,14,0.6))` — **two different fallback colours** for the same role.               |
| Modal comment explicitly requests this token                                            | `packages/chat-surface/src/settings/Modal.tsx:141-144`                                                                         | CONFIRMED, verbatim: "Token-first: prefer a `--color-scrim` token if the design system adds one. The design system has no scrim token yet".                                                                                                                 |
| Measured scrim delta                                                                    | `tools/design-parity/surfaces/tools/out/report-connect.md:29`                                                                  | CONFIRMED: `connect.scrim backgroundColor rgba(4,4,6,0.66) → rgba(0,0,0,0.54)`. No blur row because live sets none.                                                                                                                                         |
| A third scrim exists with the design value hard-coded                                   | `packages/chat-surface/src/shell/CommandPalette.tsx:447-449`                                                                   | CONFIRMED: `backgroundColor:"rgba(4, 4, 6, 0.6)"`, `backdropFilter:"blur(2px)"` — the design's `.cmdk-scrim` (`copilot.css:2461-2465`) copied as literals.                                                                                                  |
| A fourth scrim exists in the kit itself                                                 | `packages/design-system/src/styles.css:698-707`                                                                                | CONFIRMED: `.ui-dialog-backdrop{background:rgb(0 0 0 / 0.54)}` — the literal Modal's fallback mirrors.                                                                                                                                                      |
| "Enforced by stylelint (declaration-strict-value)"                                      | `packages/design-system/SKILL.md:19-21`; `packages/design-system/src/styles.css:1075-1076`                                     | **DISPUTED — the gate does not exist.** `find . -name "*stylelint*" -not -path "*/node_modules/*"` returns nothing; no `stylelint` key in root `package.json`, `.pre-commit-config.yaml`, or `.github/workflows/*.yml`. The rule is documented, unenforced. |
| `design-system` has no test runner                                                      | `packages/design-system/package.json:12-14`; `TESTING.md:1-10`                                                                 | CONFIRMED — the only script is `typecheck`. "The current design system has typecheck coverage only."                                                                                                                                                        |
| Design keeps accent hue across themes by overriding only the ink                        | `tools/design-parity/design-kit/app-v3/copilot.css:70-83`                                                                      | CONFIRMED. The `[data-theme="light"]` block redefines `--ink/--panel/--line/--tx/--mut` **and `--accent-ink:#f4faff`** — and never touches `--accent`.                                                                                                      |

## Design intent

Literal values from `tools/design-parity/design-kit/app-v3/`.

**Accent × theme are orthogonal.** `copilot.css:7-43` defines the ground _and_ the accent
on `:root`; `copilot.css:45-68` defines three alternate accents, each writing only accent
variables (`--accent`, `--accent-hi`, `--accent-lo`, `--accent-ink`, `--accent-soft`,
`--accent-line`); `copilot.css:70-83` defines the light ground and writes **exactly one**
accent variable:

```css
[data-theme="light"] {
  --ink: #f4f4f6;
  --panel: #ffffff;
  --line: rgba(10, 10, 14, 0.07);
  --tx: #141419;
  --mut: #5f5f68;
  --accent-ink: #f4faff; /* copilot.css:82 — the ONLY accent write */
}
```

The ink flips polarity with the ground (dark `#08131d` → light `#f4faff`) while the hue is
ground-independent. That is the contract to reproduce. It is **not** the contract to copy
verbatim: the design ships a dark-first mock whose light theme leaves `--accent: #5fb2ec`
as foreground on `--panel: #ffffff` — about 2.0:1, below WCAG AA. We reproduce the
_orthogonality_, and derive light-ground legibility ourselves (see below).

**Base size.**

```css
body {
  background: #050506;
  color: var(--tx);
  font-family: var(--body);
  font-size: 13px;
  line-height: 1.5;
} /* copilot.css:100-107 */
```

**Mono micro register.** The design's mono metadata sizes form a half-pixel ladder that a
sans t-shirt scale cannot express. Occurrence counts across `copilot.css`:

| Design px | Where (representative rule)                                          | Live rung today            | Δ         |
| --------- | -------------------------------------------------------------------- | -------------------------- | --------- |
| `8.5px`   | `.rail-item .rbadge` `copilot.css:353`; `.conf-nm`                   | none (raw `8.5` literal)   | —         |
| `9px`     | `.tl-lbl` `:1386`, `.set-nav__grp`, `.srow`, `.notif-head`           | `--font-size-3xs` 9px      | **0** ✅  |
| `9.5px`   | `.sect-h` `:1565`, `.cmdk__row`, `.sheet-h`, `.tl-lanes`, `.conf`    | none                       | —         |
| `10px`    | `.side-h`, `.act-day`, `.mw-chip`, `.tb-search`, `.fr-wchip`         | `--font-size-mono-10` 10px | **0** ✅  |
| `10.5px`  | `.chip` `:580`, `.lrow__time` `:1657`, `.modal__title`, `.mrow__sub` | none                       | —         |
| `11px`    | `.lrow__sub` `:1644`, `.auto-pill`, `.backlink`                      | `--font-size-2xs` 11.2px   | +0.2 (ok) |

**Scrim.**

```css
.scrim {
  /* copilot.css:2223-2232 */
  position: absolute;
  inset: 0;
  background: rgba(4, 4, 6, 0.66);
  backdrop-filter: blur(2px);
  display: grid;
  place-items: center;
  z-index: 60;
  padding: 22px;
}
.cmdk-scrim {
  background: rgba(4, 4, 6, 0.6);
  backdrop-filter: blur(2px);
} /* :2461-2465 */
```

The light block never overrides either — a scrim darkens what is _behind_ it, so it is
ground-independent by construction.

## Architectural decision

### A. Split the accent token tier into a private **seed** tier and a public **derived** tier

The bug is not "wrong order". It is **two writers for one variable**: `[data-accent]` and
`[data-theme]` both write `--color-accent` at identical specificity, so which one wins is a
property of source order — an invariant no reviewer can see and no test currently guards.
Any fix that keeps two writers is a bandaid that the next theme re-breaks.

**The seam: accent blocks stop writing the public tier.**

```css
/* ACCENT SEED TIER — hue only. One block per swatch. These write PRIVATE seed
   variables and NEVER --color-accent*. Adding a swatch touches this tier only. */
:root,
:root[data-accent="sky"] {
  --accent-seed: #5fb2ec;
  --accent-seed-strong: #8cc8f4;
  --accent-seed-ink: #08131d;
}
/* … atlas-orange, gold, amber, red, lime, teal, blue, violet — same three vars … */

/* THEME TIER — the SOLE writer of the public --color-accent* tier. Derives a
   ground-appropriate value from the seed, so hue and legibility are orthogonal
   axes that cannot collide in the cascade. */
:root,
:root[data-theme="dark"] {
  --color-accent: var(--accent-seed);
  --color-accent-strong: var(--accent-seed-strong);
  --color-accent-contrast: var(--accent-seed-ink);
}
:root[data-theme="slate"] {
  /* identical to dark — slate is a dark ground */
}
:root[data-theme="light"] {
  /* Dark-ground seeds sit ~2:1 on #f4f4f6/#ffffff. Darken toward near-black in
     oklab (perceptual lightness) so every swatch keeps its hue and clears the
     text floor; the ink on the fill flips to near-white, exactly as the design
     does (copilot.css:82). PERCENTAGES ARE PLACEHOLDERS — the implementer tunes
     them against the matrix gate in DoD 9, not against this document. */
  --color-accent: color-mix(in oklab, var(--accent-seed) 62%, #0a0a0e);
  --color-accent-strong: color-mix(in oklab, var(--accent-seed) 48%, #0a0a0e);
  --color-accent-contrast: #f4faff;
}
```

Why this seam and not another:

- Two tiers, **one writer per variable**. The collision is structurally impossible, not
  order-dependent. Reviewing "does an accent block write `--color-accent`?" is a grep.
- It extends the pattern already in the file. `styles.css:214-241` already derives
  `--color-accent-soft/-line` from `--color-accent` via `color-mix` on bare `:root`,
  relying on use-site resolution; this is the same trick one level up. Those aliases keep
  reflowing for free — **no change needed at any of their consumers**.
- Cost is O(A + T), not O(A × T). Adding a tenth swatch is one seed block; adding a fourth
  theme is one derivation block.
- Behaviour change worth naming: **slate stops forcing blue.** Today `styles.css:335` pins
  `#7bb7ff` for every slate user. After this, slate honours the user's pick. That is the
  fix, not a regression.

Rejected:

| Alternative                                                          | Why not                                                                                                                                                                          |
| -------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Move the nine accent blocks below the theme blocks                   | Restores nine hues but discards the deliberate light-ground darkening (`styles.css:306`) → ~2:1 accent text on white. And it leaves two writers, so the next theme re-breaks it. |
| Bump accent specificity (`:root:root[data-accent=…]`)                | Same a11y loss, plus a specificity arms race. Still two writers.                                                                                                                 |
| Enumerate 9 × 3 = 27 explicit blocks                                 | Expresses orthogonality by brute force. 81 hand-tuned hexes guaranteed to drift; every new theme multiplies.                                                                     |
| Copy the design literally (drop the light `--color-accent` entirely) | Reproduces the design's own latent a11y bug. **The code wins here**: `styles.css:306` documents a real reason the design never had to face.                                      |
| Compute accents in `ThemeProvider` and set inline custom properties  | Moves a pure-CSS concern into React, breaks "styles.css is the one token source", and fails before hydration — `apps/desktop/renderer/index.html` stamps no `data-theme`.        |

No API, schema, or migration is required. `AccentScheme` (`packages/design-system/src/index.tsx:34-43`),
`ACCENT_SCHEMES` (`:45-59`), the api-types union (`packages/api-types/src/index.ts:3460`), the
`data-*` write path (`ThemeProvider` `index.tsx:91-99`, `SettingsMount.tsx:257`), and the
`/v1/me/preferences` persistence are all unchanged — they were already correct. The stored
preference was being honoured; the stylesheet was throwing it away.

### B. Retune `--font-size-sm` to `0.8125rem` (13px). Do not move the rem anchor; do not add a rung.

- **Not the root anchor.** `:root{font-size:13px}` would scale every rem token in the
  product — the whole size ladder, `--space-*`, `--radius-*` — by 0.8125×. `styles.css:369-376`
  already documents why that line was not crossed. Correct call; keep it.
- **Not a new rung.** The ladder currently has nothing at 13px and _nothing in the design at
  13.6px_ — 13.6 is a value with no referent. Adding `--font-size-base: 13px` next to a
  13.6px `sm` would institutionalise the phantom step and force 394 call sites to choose
  between two near-identical tokens.
- **Retune the value.** `--font-size-sm: 0.85rem → 0.8125rem`. Decisive evidence that this
  is what the codebase already believes: **148 call sites literally write
  `var(--font-size-sm, 13px)`** (versus 2 writing `14px`). The token's authors documented
  its intent as 13px and only the number was wrong. Every consumer moves −0.6px, which the
  parity harness scores below its 0.5px MEDIUM floor everywhere except where it _closes_ a
  gap.

### C. Complete the mono micro-ladder (3 rungs), point the two recipes at it, fix the wrong comment

Answer to "missing rungs, wrong picks, or both": **both, and precisely which is now known.**
Rungs exist at 9px and 10px and are correct. The design's two _most-used_ mono steps —
10.5px (15 rules) and 9.5px (7 rules) — have no rung within 0.2px, and 8.5px has none at
all, so `AppRail.tsx:155` had to write a raw literal. The wrong picks (`--font-size-2xs`
where mono metadata was meant) were caused by a wrong doc comment, and cannot be fully
corrected without the missing rungs.

Extend the tier `styles.css:71` already opened ("deliberately off the main ladder") rather
than starting a parallel one:

```css
/* MONO MICRO-LADDER — the design's mono metadata register is a HALF-PIXEL ladder
   the sans t-shirt scale cannot express (8.5/9/9.5/10/10.5/11). Named by px so a
   call site cannot mis-pick. Do NOT reach into --font-size-3xs/2xs for mono. */
--font-size-mono-8-5: 0.53125rem; /* 8.5px — .rbadge (copilot.css:353) */
--font-size-mono-9-5: 0.59375rem; /* 9.5px — .sect-h (copilot.css:1565) */
--font-size-mono-10: 0.625rem; /* 10px  — .fr-wchip, .side-h (exists) */
--font-size-mono-10-5: 0.65625rem; /* 10.5px — .chip, .lrow__time (copilot.css:580,1657) */
```

`--font-size-3xs` (9px) and `--font-size-2xs` (11.2px) keep their current values and roles.

Discoverability is the other half of the fix, and it is what makes this architectural
rather than three token swaps:

1. **The recipes become the right answer.** `.ui-mono-caps` (`styles.css:1098-1104`) is
   already the `.sect-h` role — repoint its `font-size` to `--font-size-mono-9-5`.
   `.ui-badge` (`styles.css:555-568`) is already the `.chip` role (its own comment says so)
   — repoint to `--font-size-mono-10-5` and `--font-weight-medium` (the design inherits 500;
   `styles.css:564` hard-codes semibold). Consumers then migrate onto a recipe, not a token.
2. **Fix the comment that caused the bug.** `SectionHeader.tsx:4` says
   `Mono, ~9.5px (--font-size-2xs)`. Correct it and migrate the component onto
   `.ui-mono-caps`, which also retires the raw `letterSpacing: "0.12em"` at `:42`.
3. **Make the claimed gate real.** `SKILL.md:19-21` and `styles.css:1075-1076` both assert a
   stylelint `declaration-strict-value` gate. It does not exist anywhere in the repo. Rather
   than stand up a stylelint toolchain in a token PRD, this PRD ships a **token-contract
   test** (below) that pins the numbers, and **corrects both documents** to describe the gate
   that actually exists. An unenforced rule that claims to be enforced is worse than no rule.

### D. Add `--color-scrim` + `--blur-scrim` on `:root`, outside every theme block

```css
/* Scrim — the veil behind a modal / palette. Deliberately on :root and NOT in a
   theme block: a scrim darkens whatever is BEHIND it, so it is ground-independent
   (the design never overrides it either — copilot.css:70-83). */
--color-scrim: rgba(4, 4, 6, 0.66); /* copilot.css:2226 `.scrim` */
--blur-scrim: 2px; /* copilot.css:2227 */
```

One token, four consumers — this is the "fix the seam, not N call sites" clause. Today there
are four independent scrims with three different colours: `Modal.tsx:145` (`rgb(0 0 0/.54)`),
`EditOverlay.tsx:437` (`rgba(8,10,14,.6)`), `CommandPalette.tsx:447-449` (`rgba(4,4,6,.6)`
hard-coded), `.ui-dialog-backdrop` `styles.css:700` (`rgb(0 0 0/.54)`). All four collapse
onto the token; the design's 0.66 vs 0.60 split is folded to one value (0.06 alpha,
imperceptible, and worth less than a second token). Fallbacks are dropped at every site — a
`var(--x, fallback)` on a token that now exists is dead code that hides the next regression.

## Scope

**`packages/design-system`**

| File                                        | Reason                                                                                                                                                                           |
| ------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/styles.css:62-71`                      | Retune `--font-size-sm` → `0.8125rem`; add `--font-size-mono-8-5/-9-5/-10-5`; add the mono-ladder comment block.                                                                 |
| `src/styles.css:214-241`                    | Add `--color-scrim` + `--blur-scrim` to the bare-`:root` alias block (ground-independent by placement).                                                                          |
| `src/styles.css:245-289`                    | Rewrite the nine accent blocks to write `--accent-seed/-strong/-ink` only.                                                                                                       |
| `src/styles.css:164-204, 291-322, 324-350`  | Dark / light / slate blocks become the sole writers of `--color-accent`, `-strong`, `-contrast`, derived from the seed.                                                          |
| `src/styles.css:363-377`                    | Update the `body` comment — it currently documents the 13.6px approximation as intentional.                                                                                      |
| `src/styles.css:555-568`                    | `.ui-badge` → `--font-size-mono-10-5`, `--font-weight-medium`.                                                                                                                   |
| `src/styles.css:698-707`                    | `.ui-dialog-backdrop` background → `var(--color-scrim)`, add `backdrop-filter: blur(var(--blur-scrim))`.                                                                         |
| `src/styles.css:1075-1076, 1098-1104`       | `.ui-mono-caps` → `--font-size-mono-9-5`; correct the recipe-block comment's stylelint claim.                                                                                    |
| `SKILL.md:19-21, 29-31, 42-44`              | Correct the stylelint claim to name the real gate; add the mono micro-ladder to the token list; update the `.ui-mono-caps` / `.ui-badge` rows.                                   |
| `CLAUDE.md` (typography table + v2 section) | The table says the size family is "11.2px → 32px" and the accent section says "a swatch override wins regardless of theme" — both now false. Document the seed/derived two-tier. |

**`packages/chat-surface`**

| File                                                 | Reason                                                                                                                           |
| ---------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `src/destinations/_shared/SectionHeader.tsx:4,40-42` | Correct the false doc comment; migrate onto `.ui-mono-caps` (drops the wrong size, the raw `0.12em`, and the semibold override). |
| `src/settings/Modal.tsx:141-145`                     | Drop the fallback + the now-stale comment; add `backdropFilter: blur(var(--blur-scrim))` (+ `WebkitBackdropFilter`).             |
| `src/surfaces/edit/EditOverlay.tsx:437`              | Drop the divergent `rgba(8,10,14,0.6)` fallback.                                                                                 |
| `src/shell/CommandPalette.tsx:447-449`               | Replace the two hard-coded literals with the tokens.                                                                             |
| `src/shell/AppRail.tsx:155`                          | `fontSize: 8.5` → `var(--font-size-mono-8-5)` — retires the last raw off-ladder literal in the rail.                             |

**`tools/design-parity`**

| File                                    | Reason                                                                                                                                                                                                    |
| --------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `lib/render-live-tokens.test.tsx` (new) | Token-contract test. Named to match the config's existing `include: ["lib/render-live*.test.tsx"]` glob (`vitest.config.mjs:31`) so **no config edit** — that file is a merge point across parallel PRDs. |
| `lib/accent-matrix.mjs` (new)           | Generalise `surfaces/rail-badge/probe4-accent-theme.mjs` into a reusable 9 × 3 matrix gate (it is a token-tier concern, not a rail concern). Reuse its chromium resolver verbatim.                        |
| `out/accent-matrix.expected.json` (new) | Checked-in expected matrix: 27 cells, per-cell resolved `--color-accent` + two contrast ratios.                                                                                                           |

**Not touched:** `packages/api-types`, any service, any host binder, `ThemeProvider`,
`SettingsMount`, `AppearancePage`. The accent write path is already correct.

## Non-goals

- **Retuning the rest of the ladder.** `--font-size-xs` is 12.48px against the design's
  12px (20 rules) — the same class of defect, deliberately deferred: it moves hundreds of
  sites with no measured HIGH behind it, and it belongs with the surface PRDs that can
  re-measure. Same for `--font-size-md` (14px, correct) and above.
- **Migrating `StatusPill` and `Row` onto `.ui-badge`.** This PRD makes `.ui-badge`
  _correct_ (10.5px / medium); the migration of `packages/chat-surface/src/shell/StatusPill.tsx:66-84`
  and `destinations/_shared/Row.tsx:107,117` — which also changes fill, border alpha, case
  and tone palette — is the StatusPill PRD's, because it repaints Chats, Activity, Projects
  and Connectors and needs all five harnesses re-run. If no sibling PRD claims it, fold it in
  and re-measure.
- **Adding `.ui-badge--muted`** for the archived tone (`chats/out/AUDIT.md:36`) — same owner.
- **Modal geometry.** `padding: 22px`, `display: grid`, `z-index: 60`, `position: absolute`
  (`tools/out/report-connect.md:94-97`) are Modal-shape defects, not token defects.
- **Standing up stylelint.** This PRD makes the documentation honest and ships a real
  numeric gate; adding a stylelint toolchain + baselining ~400 existing raw values is its own
  PRD.
- **Desktop accent persistence.** `rail-badge/out/AUDIT.md:155` reports desktop resets to
  `sky` every launch (`SettingsMount.tsx` applies on change only, never on mount). Real bug,
  different seam (host bootstrap), different PRD. **Note the interaction: until it lands, the
  accent×theme fix is only observable on web and after an in-session accent change on desktop.**
- **Adding or removing accent swatches.** The design ships 4, we ship 9. Out of scope.

## Risks & rollback

| Risk                                                                                                                       | Guard                                                                                                                                                                                                                                                                                                                                   |
| -------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`color-mix(in oklab, …)` support.** Every accent colour in light mode becomes a computed function.                       | Already load-bearing in this file (`styles.css:970, 1160, 1183` use `in oklab`; `:215-223` use `in srgb`) and the product ships Chromium (Electron) + evergreen browsers only. If it ever regressed, _every_ accent alias regressed with it — one blast radius, not a new one.                                                          |
| **Light-mode accent legibility changes for all 9 swatches.** The hand-tuned `#1f6fb0` is replaced by a derived value.      | DoD 9's matrix gate asserts a contrast floor in all 27 cells and fails the build otherwise. Tune the mix percentages until it passes; add a `:root[data-theme="light"][data-accent="X"]` override (specificity 0,3,0 — one writer, still) **only** for a swatch the gate cannot satisfy, each carrying its measured ratio in a comment. |
| **Slate users see a different colour after upgrade.** Anyone on slate who picked a non-blue accent gets their actual pick. | Intended. Call it out in the changelog. Reverting is one block.                                                                                                                                                                                                                                                                         |
| **−0.6px shift at 394 `--font-size-sm` sites.** Some may have compensated for 13.6px with padding.                         | Below the harness's 0.5px MEDIUM floor. Guarded by re-running all five surface harnesses (DoD 10) — any site that regresses shows up as a new row.                                                                                                                                                                                      |
| **`.ui-badge` weight 600 → 500 repaints every badge in the app.**                                                          | Deliberate (design inherits 500). Caught by the five harnesses; `packages/chat-surface` vitest suite covers rendering, not weight.                                                                                                                                                                                                      |
| **Scrim gets _darker_ (0.54 → 0.66) and gains blur.** Blur has a GPU cost on large surfaces.                               | Matches the design and the palette already shipping `blur(2px)` (`CommandPalette.tsx:448`). If it costs, drop `--blur-scrim` to `0` — one token, four consumers, no code change.                                                                                                                                                        |
| Existing suites that could catch collateral                                                                                | `npx vitest run --root packages/chat-surface`; `npm run typecheck --workspace @0x-copilot/design-system`; `npm run build --workspace @0x-copilot/frontend`; `packages/chat-surface/src/settings/AppearancePage.test.tsx` (asserts the `data-accent` write path — must stay green, proving we did not touch it).                         |

**Rollback.** Every change is additive-or-value-only inside `styles.css` plus five one-line
consumer edits. `git revert` of the single commit restores the previous cascade exactly; no
data, schema, or stored preference is touched, so a revert needs no migration and no cache
bust.

## Definition of Done

Run from the repo root unless stated.

1. `packages/design-system/src/styles.css` declares `--font-size-sm: 0.8125rem` and the test
   in DoD 8 asserts `0.8125 * 16 === 13`.
2. `packages/design-system/src/styles.css` declares `--font-size-mono-8-5: 0.53125rem`
   (8.5px), `--font-size-mono-9-5: 0.59375rem` (9.5px), `--font-size-mono-10-5: 0.65625rem`
   (10.5px); `--font-size-mono-10` is unchanged at `0.625rem`; `--font-size-3xs` is unchanged
   at `0.5625rem`; `--font-size-2xs` is unchanged at `0.7rem`.
3. `packages/design-system/src/styles.css` declares `--color-scrim: rgba(4, 4, 6, 0.66)` and
   `--blur-scrim: 2px`, and
   `awk '/^:root\[data-theme/,/^}/' packages/design-system/src/styles.css | grep -c 'color-scrim'`
   prints `0` (the scrim is never theme-scoped).
4. `grep -nE '^\s*--color-accent(-strong|-contrast)?\s*:' packages/design-system/src/styles.css`
   returns matches **only** inside the three `[data-theme]` blocks — zero matches inside any
   `:root[data-accent="…"]` block. This is the regression guard for the specific bug: it fails
   the moment a second writer reappears.
5. `grep -rn 'font-size-sm, *13px\|font-size-sm, *14px' packages apps | wc -l` is unchanged
   (150) — i.e. this PRD does not churn consumers; only the token moved.
6. `grep -rn "color-scrim, \|rgba(4, 4, 6\|rgb(0 0 0 / 0.54)" packages apps --include='*.tsx' --include='*.css' | grep -v node_modules`
   returns zero rows outside `packages/design-system/src/styles.css` — no scrim fallback and
   no scrim literal survives in `Modal.tsx`, `EditOverlay.tsx`, `CommandPalette.tsx`, or
   `.ui-dialog-backdrop`.
7. `grep -n 'fontSize: 8.5' packages/chat-surface/src/shell/AppRail.tsx` returns nothing, and
   `AppRail.tsx` references `--font-size-mono-8-5`.
   `packages/chat-surface/src/destinations/_shared/SectionHeader.tsx:4` no longer contains the
   string `--font-size-2xs`, and the component's heading carries `className` containing
   `ui-mono-caps`.
8. **New** `tools/design-parity/lib/render-live-tokens.test.tsx` passes under
   `npx vitest run --config tools/design-parity/vitest.config.mjs` (it matches the existing
   `include` glob, so `vitest.config.mjs` is byte-unchanged). It asserts, by parsing
   `packages/design-system/src/styles.css`: DoD 1, 2, 3, and 4 above, each as a named `it()`.
9. **New** `node tools/design-parity/lib/accent-matrix.mjs --check` exits 0. It stamps all
   9 `data-accent` × 3 `data-theme` pairs, and asserts for all 27 cells: (a) the resolved
   `--color-accent` values are **9 distinct colours within each theme** (27 distinct overall
   is not required — dark and slate legitimately coincide); (b) contrast(`--color-accent`,
   `--color-bg`) ≥ 3.0 (non-text UI floor, WCAG 1.4.11); (c) contrast(`--color-accent-contrast`,
   `--color-accent`) ≥ 4.5 (text on the accent fill). The expected matrix is checked in at
   `tools/design-parity/out/accent-matrix.expected.json`. Before this PRD the same command
   reports 9/1/1 distinct — that number is the regression guard.
10. Re-running the five surface harnesses (procedure: `tools/design-parity/SKILL.md`)
    produces reports where `grep -c '13.6px' surfaces/*/out/report-*.md` totals **0** (was 55:
    tools 22, projects 14, chats 7, activity 6, rail-badge 6), and
    `grep -c '→ 11.2px' surfaces/*/out/report-*.md` for the anchors
    `sect.pinned`, `sect.recent`, `sect.archived` (chats) totals **0** (was 3 MEDIUM at
    `9.5px → 11.2px`).
11. `tools/design-parity/surfaces/tools/out/report-connect.md` shows **no** `connect.scrim`
    `backgroundColor` row (was `rgba(4, 4, 6, 0.66) → rgba(0, 0, 0, 0.54)` at line 29).
12. `npx vitest run --root packages/chat-surface` passes, including
    `src/settings/AppearancePage.test.tsx` (which asserts `data-accent="violet"` is written —
    proof the write path is untouched).
13. `npm run typecheck --workspace @0x-copilot/design-system`,
    `npm run typecheck --workspace @0x-copilot/frontend`, and
    `npm run build --workspace @0x-copilot/frontend` all pass.
14. `packages/design-system/SKILL.md` no longer claims a stylelint `declaration-strict-value`
    gate; it names `tools/design-parity/lib/render-live-tokens.test.tsx` +
    `lib/accent-matrix.mjs` as the enforcement, lists the mono micro-ladder in the token
    section, and its recipe table shows `.ui-mono-caps` = mono-9-5 and `.ui-badge` = mono-10-5.
    The same claim is removed from `packages/design-system/src/styles.css:1075-1076`.
15. `packages/design-system/CLAUDE.md` documents the seed/derived two-tier accent contract
    ("accent blocks write `--accent-seed*`; theme blocks are the sole writer of
    `--color-accent*`") and its typography table lists the mono micro-ladder.

## Dependencies

**Blocked by:** nothing. This PRD touches only the token tier and five leaf consumers; it can
land first and should.

**Unblocks (all of them measure against these tokens, so landing them first would force a
re-measure):**

- The **StatusPill / `.ui-badge` migration PRD** — needs `--font-size-mono-10-5` and the
  corrected `.ui-badge` recipe to exist before `StatusPill.tsx:66-84` can migrate onto it.
  That PRD carries 12 of the 17 Chats HIGH rows.
- The **Row / destination-parity PRD** (`Row.tsx:107,117` metadata sizing, icon-tile fill) —
  needs `--font-size-mono-10-5`.
- The **Chats / Activity / Projects / Connectors surface PRDs** — every one of their reports
  is contaminated by the 13.6px substitution until DoD 1 lands; re-measure after.
- The **Tools connect-modal PRD** — the geometry rows (`padding`, `display`, `z-index`) are
  only readable once the scrim colour row clears.
- The **desktop accent-persistence PRD** (`SettingsMount.tsx` apply-on-mount) — its user-visible
  payoff is nine working accents, which does not exist until this lands.
