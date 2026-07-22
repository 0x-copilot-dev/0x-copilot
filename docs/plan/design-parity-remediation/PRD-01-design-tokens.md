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
5. **Every small primary button renders lighter than every other primary button.**
   `.ui-button--sm` sets a weight and `.ui-button--primary` does not, so size tier beats
   tone: a small primary CTA computes 500 where the design re-asserts 600. Measured on the
   one CTA the Chats surface has (`btn.newChat fontWeight 600 → 500`,
   `surfaces/chats/out/report-default.md:48`). Assigned here by README §Gaps **G9**.

All five are defects in one file — `packages/design-system/src/styles.css`, the declared
single token source of truth — and therefore hit every surface, both hosts, at once.

## Evidence

Every row opened and verified in this working tree.

| Claim                                                                                   | File:line                                                                                                                                          | What the code actually does                                                                                                                                                                                                                                                                                                                            |
| --------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Nine accent blocks define `--color-accent`                                              | `packages/design-system/src/styles.css:245-289`                                                                                                    | CONFIRMED. `:root[data-accent="sky"…"violet"]`, each writing `--color-accent`, `--color-accent-strong`, `--color-accent-contrast`. Header comment at `:243-244` claims "a swatch override wins regardless of theme".                                                                                                                                   |
| Light theme redefines accent at equal specificity, later in source                      | `packages/design-system/src/styles.css:291,307-309`                                                                                                | CONFIRMED. `:root[data-theme="light"]` = `(0,2,0)`, identical to `:root[data-accent="…"]` = `(0,2,0)`, and 46 lines later. `--color-accent:#1f6fb0` at `:307`. Later-equal wins → all nine accents collapse.                                                                                                                                           |
| Slate does the same                                                                     | `packages/design-system/src/styles.css:324,335-337`                                                                                                | CONFIRMED. `--color-accent:#7bb7ff` — i.e. slate silently forces the `blue` swatch on every user regardless of their pick.                                                                                                                                                                                                                             |
| Measured: 9 distinct badge colours in dark, 1 in light, 1 in slate                      | `tools/design-parity/surfaces/rail-badge/out/AUDIT.md:155`; probes `probe4-accent-theme.mjs`, `probe5-design-accent-theme.mjs`                     | CONFIRMED as a prior measurement, and independently re-derived above from the cascade. Design measures 4/4 across both themes.                                                                                                                                                                                                                         |
| The light-accent darkening is deliberate, not accidental                                | `packages/design-system/src/styles.css:306`                                                                                                        | CONFIRMED — comment: "Darkened brand sky for legible accent text/borders on the light ground." **This matters: a naive "delete the theme override" fix ships `#5fb2ec` (~2.0:1) as text on `#ffffff`.** The design has the same latent bug.                                                                                                            |
| Accent _is_ used as foreground text, so the a11y concern is real                        | `packages/design-system/src/styles.css:586-587, 833, 1009`                                                                                         | CONFIRMED. `.ui-chip--accent{color:var(--color-accent)}`, `.ui-badge--accent`, `.ds-*` link colour.                                                                                                                                                                                                                                                    |
| Accent aliases reflow automatically from `--color-accent`                               | `packages/design-system/src/styles.css:214-241`                                                                                                    | CONFIRMED. `--color-accent-soft/-line/--color-bg-accent-subtle` are `color-mix(... var(--color-accent) ...)` on bare `:root`, resolving at use-site. `color-mix()` is already load-bearing here (also `:970, :1160, :1183`).                                                                                                                           |
| `body` inherits 13.6px; design is 13px                                                  | `packages/design-system/src/styles.css:65, 377`; `tools/design-parity/design-kit/app-v3/copilot.css:104`                                           | CONFIRMED. `--font-size-sm: 0.85rem` (13.6px); `body{font-size:var(--font-size-sm)}`. Design `body{…font-size:13px…}`. The comment at `:365-368` admits it is an approximation ("the closest token on the existing scale to the design's 13px").                                                                                                       |
| The rem ladder is anchored at the UA 16px, deliberately                                 | `packages/design-system/src/styles.css:369-376`                                                                                                    | CONFIRMED. Comment: "It deliberately does NOT touch the rem anchor (`html`/`:root` stays at the UA 16px), so every rem-based token … keeps its exact geometry."                                                                                                                                                                                        |
| 148 call sites already believe `--font-size-sm` is 13px                                 | `grep -rn "font-size-sm, *13px" packages apps` → 148 hits (vs 2 for `, 14px`)                                                                      | CONFIRMED. e.g. `packages/chat-surface/src/settings/ProfilePage.tsx:176`, `NotificationsPage.tsx:164`, `WebhookSecurityPage.tsx:102`. 394 references to the token in total.                                                                                                                                                                            |
| 13.6px drift is app-wide, not Chats-local                                               | `tools/design-parity/surfaces/*/out/report-*.md`                                                                                                   | CONFIRMED by count: tools 22, projects 14, chats 7, activity 6, rail-badge 6 = **55 rows**.                                                                                                                                                                                                                                                            |
| `--font-size-3xs` = 9px, `--font-size-2xs` = 11.2px, `--font-size-mono-10` = 10px exist | `packages/design-system/src/styles.css:62, 63, 71`                                                                                                 | CONFIRMED. `:71` comment: "deliberately off the main ladder".                                                                                                                                                                                                                                                                                          |
| `--font-size-mono-10` is UNUSED                                                         | `grep -rn "font-size-mono-10"` → `packages/chat-surface/src/onboarding/onboarding.css:575`                                                         | **DISPUTED.** It has exactly one consumer (the FTUE wallet chip). "Unused" is wrong; "used once, in one subtree, and unknown to every destination component" is right — which is the same discoverability failure, stated honestly.                                                                                                                    |
| A doc comment states a wrong fact and drove the wrong pick                              | `packages/chat-surface/src/destinations/_shared/SectionHeader.tsx:4`                                                                               | CONFIRMED, verbatim: `Mono, ~9.5px (--font-size-2xs)`. `--font-size-2xs` is 11.2px. The component then picks it at `:40`.                                                                                                                                                                                                                              |
| Section heads render 11.2px semibold                                                    | `packages/chat-surface/src/destinations/_shared/SectionHeader.tsx:40-41`                                                                           | CONFIRMED, plus a raw `letterSpacing: "0.12em"` at `:42` where `--tracking-mono-caps` is exactly that value (`styles.css:92`).                                                                                                                                                                                                                         |
| Design section head is **9.5px, not 9px**                                               | `tools/design-parity/design-kit/app-v3/copilot.css:1563-1570`                                                                                      | **PARTIAL DISPUTE of the audit brief.** `.sect-h{font-family:var(--mono);font-size:9.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--mut2)}`. `--font-size-3xs` (9px) is the _closest_ rung but still 0.5px off = MEDIUM band.                                                                                                           |
| Design chip/time register is **10.5px, not 10px**                                       | `tools/design-parity/design-kit/app-v3/copilot.css:575-586, 1655-1660`                                                                             | CONFIRMED. `.chip` and `.lrow__time` are both `10.5px`. `--font-size-mono-10` (10px) is 0.5px under = MEDIUM band. So the rung that "exists" still cannot hit the target.                                                                                                                                                                              |
| An existing recipe already encodes the `.sect-h` role                                   | `packages/design-system/src/styles.css:1097-1104`                                                                                                  | CONFIRMED. `.ui-mono-caps` = mono + `--font-size-3xs` + `--tracking-mono-caps` + uppercase. `SectionHeader` hand-composed it instead, at the wrong size.                                                                                                                                                                                               |
| An existing recipe already encodes the `.chip` role                                     | `packages/design-system/src/styles.css:555-568`                                                                                                    | CONFIRMED. `.ui-badge`, comment: "design `.chip` — mono, bordered, NO fill". Size `--font-size-2xs` (11.2px) at `:563`, weight semibold at `:564` — both wrong vs the design's 10.5px / inherited 500.                                                                                                                                                 |
| A raw off-ladder `font-size` literal survives                                           | `packages/chat-surface/src/shell/AppRail.tsx:155`                                                                                                  | CONFIRMED — `fontSize: 8.5`. It is _design-correct_ (`copilot.css:353` `.rbadge{font-size:8.5px}`) but there is no token for it, so the SKILL rule "never write a raw font-size" is unsatisfiable here.                                                                                                                                                |
| No `--color-scrim` token exists                                                         | `grep -rn "color-scrim" packages apps` → only two _consumers_ with fallbacks                                                                       | CONFIRMED. `packages/chat-surface/src/settings/Modal.tsx:145` `var(--color-scrim, rgb(0 0 0 / 0.54))` and `surfaces/edit/EditOverlay.tsx:437` `var(--color-scrim, rgba(8,10,14,0.6))` — **two different fallback colours** for the same role.                                                                                                          |
| Modal comment explicitly requests this token                                            | `packages/chat-surface/src/settings/Modal.tsx:141-144`                                                                                             | CONFIRMED, verbatim: "Token-first: prefer a `--color-scrim` token if the design system adds one. The design system has no scrim token yet".                                                                                                                                                                                                            |
| Measured scrim delta                                                                    | `tools/design-parity/surfaces/tools/out/report-connect.md:28`                                                                                      | CONFIRMED: `connect.scrim backgroundColor rgba(4,4,6,0.66) → rgba(0,0,0,0.54)`. No blur row because live sets none.                                                                                                                                                                                                                                    |
| A third scrim exists with the design value hard-coded                                   | `packages/chat-surface/src/shell/CommandPalette.tsx:447-449`                                                                                       | CONFIRMED: `backgroundColor:"rgba(4, 4, 6, 0.6)"`, `backdropFilter:"blur(2px)"` — the design's `.cmdk-scrim` (`copilot.css:2461-2465`) copied as literals.                                                                                                                                                                                             |
| A fourth scrim exists in the kit itself                                                 | `packages/design-system/src/styles.css:698-707`                                                                                                    | CONFIRMED: `.ui-dialog-backdrop{background:rgb(0 0 0 / 0.54)}` — the literal Modal's fallback mirrors.                                                                                                                                                                                                                                                 |
| "Enforced by stylelint (declaration-strict-value)"                                      | `packages/design-system/SKILL.md:19-21`; `packages/design-system/src/styles.css:1075-1076`                                                         | **DISPUTED — the gate does not exist.** `find . -name "*stylelint*" -not -path "*/node_modules/*"` returns nothing; no `stylelint` key in root `package.json`, `.pre-commit-config.yaml`, or `.github/workflows/*.yml`. The rule is documented, unenforced.                                                                                            |
| `design-system` has no test runner                                                      | `packages/design-system/package.json:12-14`; `TESTING.md:1-10`                                                                                     | CONFIRMED — the only script is `typecheck`. "The current design system has typecheck coverage only."                                                                                                                                                                                                                                                   |
| Design keeps accent hue across themes by overriding only the ink                        | `tools/design-parity/design-kit/app-v3/copilot.css:70-83`                                                                                          | CONFIRMED. The `[data-theme="light"]` block redefines `--ink/--panel/--line/--tx/--mut` **and `--accent-ink:#f4faff`** — and never touches `--accent`.                                                                                                                                                                                                 |
| `.ui-button--sm` sets a weight, `.ui-button--primary` does not (G9)                     | `packages/design-system/src/styles.css:443-449, 462-466`                                                                                           | CONFIRMED. `.ui-button--sm{font-weight:var(--font-weight-medium)}` (500); `.ui-button--primary` sets only `background` + `color`; base `.ui-button:422` is 650. So `--primary --sm` computes **500**.                                                                                                                                                  |
| The design inverts that precedence deliberately                                         | `tools/design-parity/design-kit/app-v3/copilot.css:465-481, 491-496, 567-570`                                                                      | CONFIRMED. `.cbtn` = 500; `.cbtn--sm` changes **only** `padding` + `font-size`; `.cbtn--pri` **re-asserts `font-weight:600`** so tone beats size tier.                                                                                                                                                                                                 |
| Measured consequence of G9                                                              | `tools/design-parity/surfaces/chats/out/report-default.md:48`                                                                                      | CONFIRMED: `btn.newChat fontWeight 600 → 500`. One row today; PRD-11 adopts `.ui-button` for the Tools CTA and would inherit the same defect.                                                                                                                                                                                                          |
| `sect-h` sits on the **wrapper**, not the label (C13)                                   | `packages/chat-surface/src/destinations/_shared/SectionHeader.tsx:64, 69-75`                                                                       | CONFIRMED. `className="sect-h …"` is on the wrapper `<div>` that also holds the count pill and the `action` slot; the label is the `<h2>` at `:69-75` (`data-testid="section-header-label"`). A type recipe on the wrapper would mono-uppercase the CTA.                                                                                               |
| `sect-h` has no CSS anywhere in the product                                             | `grep -rn "sect-h" packages apps` → 10 hits, none in a stylesheet                                                                                  | CONFIRMED — the class is vestigial. Its **deletion is PRD-13's**, not this PRD's (README C13).                                                                                                                                                                                                                                                         |
| The `.sect-h` block rhythm is measured and missing                                      | `tools/design-parity/surfaces/chats/out/report-default.md:77, 88`; `copilot.css:1569-1573`                                                         | CONFIRMED: `sect.recent` / `sect.archived` `margin 22px 0px 10px 0px → 0px`. `sect.pinned` has no margin row (the design zeroes it inline when the head carries a count/action). PRD-10:184 delegates this to PRD-01; absorbed — see Scope.                                                                                                            |
| **`--color-bg` `#09090b` is NOT the design's `#050506`** (G3)                           | `packages/design-system/src/styles.css:168`; `copilot.css:8, 105, 160-168, 179, 386`                                                               | **DISPUTED — the code wins, no token change.** `--color-bg:#09090b` equals the design's `--ink:#09090b` byte-for-byte, and `--ink` is what the design paints the app window with (`.mw:179`, `.main:386`). `#050506` is the mock's **stage** behind a 1220×840 window (`body:105`, `.stage:160-168`) — a surface a real full-screen app does not have. |
| No parity report actually asserts `#050506`                                             | `grep -rn "050506" tools/design-parity/surfaces/*/out/report-*.md` → 0 rows                                                                        | CONFIRMED. Projects RC-11 is a LOW **inference** from `.pg` being transparent in the design harness (`copilot.css:1552-1555`), not a measured row. Ruling recorded in Architectural decision F.                                                                                                                                                        |
| Migration high-water marks (this PRD claims no id)                                      | `ls services/backend/migrations` → `0045_provider_api_keys_custom_endpoint.sql`; `ls services/ai-backend/migrations` → `0001_runtime_baseline.sql` | CONFIRMED on disk. PRD-01 touches no schema and claims **no id** from README C18's table (`backend` 0046/0047 → PRD-06/07; `ai-backend` 0002/0003/0004 → PRD-05/07/09).                                                                                                                                                                                |

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

**Button weight precedence — tone beats size tier.**

```css
.cbtn {
  font-size: 12px;
  font-weight: 500;
} /* copilot.css:465-481 */
.cbtn--sm {
  padding: 4px 9px;
  font-size: 11.5px;
} /* copilot.css:567-570 — size ONLY, no weight */
.cbtn--pri {
  background: var(--accent);
  color: var(--accent-ink);
  font-weight: 600;
} /* copilot.css:491-496 */
```

The literal to pin: **600** on the primary tone, asserted after the size tier.

**Section-head block rhythm.**

```css
.sect-h {
  /* copilot.css:1563-1570 */
  font-size: 9.5px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--mut2);
  margin: 22px 0 10px;
}
.sect-h:first-child {
  margin-top: 0;
} /* copilot.css:1571-1573 */
```

`--mut2` is `#64646d` (`copilot.css:17`) = our `--color-text-subtle` (`styles.css:178`);
`--mut` `#98989f` = `--color-text-muted` (`styles.css:177`). **`.ui-mono-caps` ships
`--color-text-muted` (`styles.css:1103`) — one rung too bright for `.sect-h`, and today's
`SectionHeader` already picks the right one (`SectionHeader.tsx:44`), which is why no
`sect.* color` row appears in any report.** The migration therefore keeps `color` as the
single documented per-role override on the label; the recipe governs family, size,
tracking and case. Changing the recipe's colour instead is rejected: its other consumer is
the login divider (`apps/frontend/src/features/auth/LoginScreen.tsx:516`), which this PRD
has no measurement for.

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
   `.ui-mono-caps`, which also retires the raw `letterSpacing: "0.12em"` at `:42` and the
   `--font-weight-semibold` override at `:41` (the design's `.sect-h` sets no weight, so it
   inherits 400 — the measured `fontWeight 400 → 600` rows at
   `surfaces/chats/out/report-default.md:47,76,87`).

   **The recipe goes on the LABEL element, never the wrapper (README C13).** `sect-h` is on
   the wrapper `<div>` (`SectionHeader.tsx:64`) which also holds the count pill and the
   `action` slot — the Chats "＋ New chat" primary lives there — so a type recipe on the
   wrapper would mono-uppercase the CTA. Apply `className="ui-mono-caps"` to the `<h2>` at
   `:69-75` only. This PRD does **not** remove the vestigial `sect-h` class from the
   wrapper; that deletion (and `SectionHeader.test.tsx:18`'s `toHaveClass("sect-h")`) is
   **PRD-13's**.

   **Block rhythm (absorbed from PRD-10:184, which delegates `.sect-h`'s margins here).**
   The design's `.sect-h` carries `margin: 22px 0 10px` with `:first-child{margin-top:0}`
   (`copilot.css:1569-1573`); live computes `0` (`surfaces/chats/out/report-default.md:77,88`).
   Because `:first-child` cannot be expressed inline, ship it as a layout-only recipe on the
   **wrapper** — it is spacing, not type, so it does not collide with C13:

   ```css
   /* Section head — the block rhythm of a `.sect-h` group (copilot.css:1569-1573).
      Layout only: the label's TYPE comes from .ui-mono-caps on the <h2>. */
   .ui-section-head {
     display: flex;
     align-items: center;
     gap: var(--space-sm);
     margin: 22px 0 10px;
   }
   .ui-section-head:first-child {
     margin-top: 0;
   }
   ```

   `SectionHeader`'s `wrapStyle` (`:30-34`) is then deleted in favour of the class.
   **Named hand-off:** the chats/projects anchors resolve `design .sect-h` → live
   `[data-testid="section-header-label"]` (the `<h2>`; `surfaces/chats/anchors.json:41-42`),
   so the margin lands on the wrapper and the `sect.* margin` row will only clear once that
   anchor is retargeted to `[data-testid="section-header"]`. That one-line `anchors.json`
   edit belongs to the PRD that owns the surface's anchors file (**PRD-09** for chats,
   **PRD-10** for projects), not here; this PRD's DoD asserts the computed margin in a unit
   test instead.

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

### E. Make tone beat size tier on `.ui-button` (README §Gaps G9)

One line: `.ui-button--primary, .ui-link-button` (`styles.css:462-466`) gains
`font-weight: var(--font-weight-semibold)`. That is the exact shape of the design's
`.cbtn--pri` (`copilot.css:495`) and it is the reason `.cbtn--sm` can safely omit a weight.

Why this belongs in the token PRD and not in a surface PRD: the defect is a **cascade
precedence** bug inside the kit's own recipe set — the same class of defect as the accent
collision above — and it is inherited by every host that adopts `.ui-button`. PRD-11
adopts `.ui-button` for the Tools connect CTA; fixing it there would fix one call site and
leave the recipe wrong. Alternatives rejected: raising `--sm`'s weight (breaks the
deliberate dense-row register documented at `styles.css:437-442`), and dropping `--sm`'s
weight entirely (returns small ghost/secondary buttons to the 650 CTA weight).

Blast radius is every `--primary --sm` button in both apps, 500 → 600. Guarded by DoD 16.

### F. `--color-bg` stays `#09090b` — G3 is resolved as a no-change ruling

README §Gaps **G3** assigns "`--color-bg` `#09090b` vs the design's `#050506`" to this PRD.
Opened and verified: **the code is right and the finding is not actionable as a token
change.** `--color-bg` (`styles.css:168`) equals the design's `--ink` (`copilot.css:8`)
byte-for-byte, and `--ink` is what the design paints the application window with
(`.mw` `copilot.css:179`, `.main` `:386`). `#050506` is the **stage** the mock centres a
1220×840 fake window on (`body` `:105`, `.stage` `:160-168`, radial gradient + `#050506`) —
a surface that does not exist in a full-screen desktop app or a full-viewport web app.
Retargeting `--color-bg` to `#050506` would move the app canvas off `--ink` and break the
nine design rules that paint `var(--ink)`.

Corroboration: no report row anywhere asserts it —
`grep -rn "050506" tools/design-parity/surfaces/*/out/report-*.md` returns zero rows.
Projects RC-11 (`projects/out/AUDIT.md:211-219`, rated LOW) is an inference drawn from the
design's `.pg` being transparent (`copilot.css:1552-1555`) so the harness reads through to
`body`.

**Deliverables:** (a) a comment at `styles.css:168` pinning `--color-bg` to
`copilot.css:8 --ink` with this reasoning, so the next reader does not "fix" it; (b) DoD 17
asserts the value. Nothing else. If a surface PRD wants the harness to stop inferring it,
the mechanism is an `expectDivergence` entry on that surface's `page.container`
(`lib/compare.mjs:172`) — which README §G6 already routes to **PRD-10**.

## Scope

**`packages/design-system`**

| File                                        | Reason                                                                                                                                                                                                                                                                        |
| ------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/styles.css:62-71`                      | Retune `--font-size-sm` → `0.8125rem`; add `--font-size-mono-8-5/-9-5/-10-5`; add the mono-ladder comment block.                                                                                                                                                              |
| `src/styles.css:214-241`                    | Add `--color-scrim` + `--blur-scrim` to the bare-`:root` alias block (ground-independent by placement).                                                                                                                                                                       |
| `src/styles.css:245-289`                    | Rewrite the nine accent blocks to write `--accent-seed/-strong/-ink` only.                                                                                                                                                                                                    |
| `src/styles.css:164-204, 291-322, 324-350`  | Dark / light / slate blocks become the sole writers of `--color-accent`, `-strong`, `-contrast`, derived from the seed.                                                                                                                                                       |
| `src/styles.css:363-377`                    | Update the `body` comment — it currently documents the 13.6px approximation as intentional.                                                                                                                                                                                   |
| `src/styles.css:168`                        | Comment only — pin `--color-bg: #09090b` to the design's `--ink` (`copilot.css:8`). **Value unchanged** (decision F / README G3).                                                                                                                                             |
| `src/styles.css:462-466`                    | `.ui-button--primary, .ui-link-button` gains `font-weight: var(--font-weight-semibold)` — tone beats size tier (decision E / README G9).                                                                                                                                      |
| `src/styles.css:555-568`                    | `.ui-badge` → `--font-size-mono-10-5`, `--font-weight-medium`. **Size + weight only.** Chip-exactness (`gap`, `line-height`, `padding`, dot, tone border alphas, `--muted`) is **PRD-02**, which lands next on this file (README C11/C12; file order 01 → 02 → 08 → 11 → 10). |
| `src/styles.css:1097-1104` (new, adjacent)  | Add the `.ui-section-head` layout recipe (`margin: 22px 0 10px` + `:first-child{margin-top:0}` + the flex row) — decision C, absorbed from PRD-10:184.                                                                                                                        |
| `src/styles.css:698-707`                    | `.ui-dialog-backdrop` background → `var(--color-scrim)`, add `backdrop-filter: blur(var(--blur-scrim))`.                                                                                                                                                                      |
| `src/styles.css:1075-1076, 1098-1104`       | `.ui-mono-caps` → `--font-size-mono-9-5`; correct the recipe-block comment's stylelint claim.                                                                                                                                                                                 |
| `SKILL.md:19-21, 29-31, 42-44`              | Correct the stylelint claim to name the real gate; add the mono micro-ladder to the token list; update the `.ui-mono-caps` / `.ui-badge` rows.                                                                                                                                |
| `CLAUDE.md` (typography table + v2 section) | The table says the size family is "11.2px → 32px" and the accent section says "a swatch override wins regardless of theme" — both now false. Document the seed/derived two-tier.                                                                                              |

**`packages/chat-surface`**

| File                                                                | Reason                                                                                                                                                                                                                                                                                                                                                                   |
| ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `src/destinations/_shared/SectionHeader.tsx:4,30-34,40-42,64,69-75` | Correct the false doc comment; put `.ui-mono-caps` on the **`<h2>` label** at `:69-75` (drops the wrong size, the raw `0.12em`, the semibold override; keeps `color: var(--color-text-subtle)` as the one per-role override). Replace `wrapStyle` with `.ui-section-head` on the wrapper. **Leave the wrapper's `sect-h` class alone — PRD-13 deletes it** (README C13). |
| `src/settings/Modal.tsx:141-145`                                    | Drop the fallback + the now-stale comment; add `backdropFilter: blur(var(--blur-scrim))` (+ `WebkitBackdropFilter`).                                                                                                                                                                                                                                                     |
| `src/surfaces/edit/EditOverlay.tsx:437`                             | Drop the divergent `rgba(8,10,14,0.6)` fallback.                                                                                                                                                                                                                                                                                                                         |
| `src/shell/CommandPalette.tsx:447-449`                              | Replace the two hard-coded literals with the tokens.                                                                                                                                                                                                                                                                                                                     |
| `src/shell/AppRail.tsx:155`                                         | `fontSize: 8.5` → `var(--font-size-mono-8-5)` — retires the last raw off-ladder literal in the rail.                                                                                                                                                                                                                                                                     |

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
- **Chip-exactness, `StatusPill`, and `Row`'s chips → PRD-02.** This PRD moves `.ui-badge`'s
  **size and weight only** (10.5px / medium). Everything else the chip needs — `gap: 5px`,
  `line-height`, `padding 1px 8px`, `.ui-badge__dot`, translucent tone border-colours,
  `.ui-badge--muted`, and the rewrite of
  `packages/chat-surface/src/shell/StatusPill.tsx:66-84` / `destinations/_shared/Row.tsx:107,117`
  — is **PRD-02**, which lands immediately after this PRD on the same file (README C12: file
  order 01 → 02 → 08 → 11 → 10). PRD-02 consumes `--font-size-mono-10-5` from here and
  **does not mint `--font-size-mono-105`** (README C11 — this PRD's name wins, for ladder
  consistency with `mono-8-5`/`mono-9-5`).
- **`.ui-badge--muted`** for the archived tone (`chats/out/AUDIT.md:36`) — **PRD-02**.
- **`statusTone.ts`** (`needs_input` → `warning`) — **PRD-08**, layered on PRD-02's rewrite;
  re-run the chats + activity harnesses only after PRD-08 (README C12).
- **Modal geometry.** `padding: 22px`, `display: grid`, `z-index: 60`, `position: absolute`
  (`surfaces/tools/out/report-connect.md:92-94`) are Modal-shape defects, not token defects
  — **PRD-11** (README §Gaps G10). This PRD ships only the scrim colour + blur those rows sit
  behind.
- **`--space-grid-gap` and `.ui-grid3`.** This PRD does **not** introduce a grid-gap token;
  **PRD-10** owns it (README, "wrong or unsupportable declared dependencies").
- **`_shared/Row.tsx`.** Not touched here at all — **PRD-08 owns the file** (README C9);
  PRD-09 then PRD-11 stack on it.
- **Standing up stylelint.** This PRD makes the documentation honest and ships a real
  numeric gate; adding a stylelint toolchain + baselining ~400 existing raw values is its own
  PRD.
- **Desktop accent persistence (rail-badge A4).** `rail-badge/out/AUDIT.md:155` reports
  desktop resets to `sky` every launch: `splitAppearancePersistence`
  (`packages/chat-surface/src/settings/AppearancePage.tsx:212`) is exported and unit-tested
  (`AppearancePage.test.tsx:204-209`) with **zero host call sites** — `SettingsMount.tsx`
  applies on change only, never on mount. Real bug, different seam (host bootstrap).
  **README §Gaps G7 records that no PRD owned it and routes it to a new PRD-14 or a fold into
  PRD-12 — it is explicitly not this PRD's.** Note the interaction: until it lands, this
  PRD's headline fix (nine working accents) is observable on web, and on desktop only after
  an in-session accent change.
- **Adding or removing accent swatches.** The design ships 4, we ship 9. Out of scope.

## Risks & rollback

| Risk                                                                                                                           | Guard                                                                                                                                                                                                                                                                                                                                   |
| ------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`color-mix(in oklab, …)` support.** Every accent colour in light mode becomes a computed function.                           | Already load-bearing in this file (`styles.css:970, 1160, 1183` use `in oklab`; `:215-223` use `in srgb`) and the product ships Chromium (Electron) + evergreen browsers only. If it ever regressed, _every_ accent alias regressed with it — one blast radius, not a new one.                                                          |
| **Light-mode accent legibility changes for all 9 swatches.** The hand-tuned `#1f6fb0` is replaced by a derived value.          | DoD 9's matrix gate asserts a contrast floor in all 27 cells and fails the build otherwise. Tune the mix percentages until it passes; add a `:root[data-theme="light"][data-accent="X"]` override (specificity 0,3,0 — one writer, still) **only** for a swatch the gate cannot satisfy, each carrying its measured ratio in a comment. |
| **Slate users see a different colour after upgrade.** Anyone on slate who picked a non-blue accent gets their actual pick.     | Intended. Call it out in the changelog. Reverting is one block.                                                                                                                                                                                                                                                                         |
| **−0.6px shift at 394 `--font-size-sm` sites.** Some may have compensated for 13.6px with padding.                             | Below the harness's 0.5px MEDIUM floor. Guarded by re-running all five surface harnesses (DoD 10) — any site that regresses shows up as a new row.                                                                                                                                                                                      |
| **`.ui-badge` weight 600 → 500 repaints every badge in the app.**                                                              | Deliberate (design inherits 500). Caught by the five harnesses; `packages/chat-surface` vitest suite covers rendering, not weight.                                                                                                                                                                                                      |
| **Scrim gets _darker_ (0.54 → 0.66) and gains blur.** Blur has a GPU cost on large surfaces.                                   | Matches the design and the palette already shipping `blur(2px)` (`CommandPalette.tsx:448`). If it costs, drop `--blur-scrim` to `0` — one token, four consumers, no code change.                                                                                                                                                        |
| **`.ui-button--primary --sm` weight 500 → 600 repaints every small primary CTA in both apps** (decision E).                    | Deliberate and design-literal (`copilot.css:495`). Guarded by DoD 16 (parse assertion + the `btn.newChat` row clearing). Nothing asserts button weight in either app's suite, so the harness is the gate.                                                                                                                               |
| **`SectionHeader` gains `22px/10px` block margins**, which moves every section-headed list (Chats, Projects detail, Activity). | The values are the design's (`copilot.css:1569-1573`) and the current `0` is a measured defect (`chats/out/report-default.md:77,88`). DoD 18 pins the computed margins in a unit test; DoD 10's harness re-run catches any list that regresses.                                                                                         |
| Existing suites that could catch collateral                                                                                    | `npx vitest run --root packages/chat-surface`; `npm run typecheck --workspace @0x-copilot/design-system`; `npm run build --workspace @0x-copilot/frontend`; `packages/chat-surface/src/settings/AppearancePage.test.tsx` (asserts the `data-accent` write path — must stay green, proving we did not touch it).                         |

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
5. This PRD churns no `--font-size-sm` consumer; only the token moved:
   `git diff --name-only $(git merge-base HEAD origin/main)..HEAD -G'font-size-sm' -- packages apps`
   prints exactly one path, `packages/design-system/src/styles.css`.
   (Context, not a gate: `grep -rn 'font-size-sm, *1[34]px' packages apps | grep -cv node_modules`
   prints 150 on the merge base — 148 × `13px`, 2 × `14px`.)
6. `grep -rn "color-scrim, \|rgba(4, 4, 6\|rgb(0 0 0 / 0.54)" packages apps --include='*.tsx' --include='*.css' | grep -v node_modules`
   returns zero rows outside `packages/design-system/src/styles.css` — no scrim fallback and
   no scrim literal survives in `Modal.tsx`, `EditOverlay.tsx`, `CommandPalette.tsx`, or
   `.ui-dialog-backdrop`.
7. `grep -c 'fontSize: 8.5' packages/chat-surface/src/shell/AppRail.tsx` prints `0` and
   `grep -c 'font-size-mono-8-5' packages/chat-surface/src/shell/AppRail.tsx` prints ≥ `1`.
   `grep -c 'font-size-2xs' packages/chat-surface/src/destinations/_shared/SectionHeader.tsx`
   prints `0`; `grep -c 'ui-mono-caps' …/SectionHeader.tsx` prints ≥ `1`;
   `grep -c 'sect-h' …/SectionHeader.tsx` prints ≥ `1` (the wrapper class survives this PRD —
   PRD-13 removes it, README C13).
   `packages/chat-surface/src/destinations/_shared/SectionHeader.test.tsx` asserts
   `screen.getByTestId("section-header-label")` carries class `ui-mono-caps` and
   `screen.getByTestId("section-header")` does **not** — the C13 regression guard, and it
   fails on `main` (today neither element carries the class).
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
   `tools/design-parity/out/accent-matrix.expected.json`.
   **Regression guard:** running the same command with the worktree checked out at
   `$(git merge-base HEAD origin/main)` exits **non-zero** on assertion (a) — it resolves 9
   distinct accents under `dark` but 1 under `light` and 1 under `slate`. If it exits 0 on
   the merge base, the gate is not testing the bug.
10. Re-running the five surface harnesses (procedure: `tools/design-parity/SKILL.md`) and
    committing the regenerated reports:
    - `grep -c '13.6px' tools/design-parity/surfaces/*/out/report-*.md | awk -F: '{s+=$2} END{print s}'`
      prints **0**. (Same command on this PR's merge base prints the pre-change total —
      55 at the time of writing; re-derive it rather than trusting the number, per README
      §Cross-cutting note 6.)
    - `grep -E '^\| `sect\.(pinned|recent|archived)`' tools/design-parity/surfaces/chats/out/report-default.md | grep -cE 'fontSize|fontWeight'`
      prints **0** (was 6 rows: three `9.5px → 11.2px`, three `400 → 600`).
11. `grep -c 'connect.scrim.*backgroundColor' tools/design-parity/surfaces/tools/out/report-connect.md`
    prints `0` (on the merge base it prints `1`:
    `rgba(4, 4, 6, 0.66) → rgba(0, 0, 0, 0.54)`, `report-connect.md:28`).
12. `npx vitest run --root packages/chat-surface` exits 0, **or** the failing test ids are
    byte-identical to `docs/plan/design-parity-remediation/baseline-failures.txt`, which this
    PR does not modify (README DoD-Q2's form). The run must include
    `src/settings/AppearancePage.test.tsx` (asserts `data-accent="violet"` is written — proof
    the write path is untouched) and `src/destinations/_shared/SectionHeader.test.tsx` green.
13. `npm run typecheck --workspace @0x-copilot/design-system`,
    `npm run typecheck --workspace @0x-copilot/frontend`, and
    `npm run build --workspace @0x-copilot/frontend` all pass.
14. `grep -rc 'declaration-strict-value' packages/design-system/SKILL.md packages/design-system/src/styles.css`
    prints `0` for both files, and
    `grep -c 'render-live-tokens.test.tsx\|accent-matrix.mjs' packages/design-system/SKILL.md`
    prints ≥ `2`. `grep -c 'font-size-mono-9-5' packages/design-system/SKILL.md` and
    `grep -c 'font-size-mono-10-5' packages/design-system/SKILL.md` each print ≥ `1` (the
    recipe table rows for `.ui-mono-caps` / `.ui-badge`).
15. `grep -c 'accent-seed' packages/design-system/CLAUDE.md` prints ≥ `1` and
    `grep -c 'font-size-mono-8-5' packages/design-system/CLAUDE.md` prints ≥ `1` — the
    seed/derived two-tier contract ("accent blocks write `--accent-seed*`; theme blocks are
    the sole writer of `--color-accent*`") and the mono micro-ladder are both documented.
    `grep -c 'a swatch override wins regardless of theme' packages/design-system/CLAUDE.md`
    prints `0` (the now-false claim is gone).
16. **Decision E / README G9.** `packages/design-system/src/styles.css`'s
    `.ui-button--primary, .ui-link-button` block declares
    `font-weight: var(--font-weight-semibold)`, asserted as a named `it()` in
    `tools/design-parity/lib/render-live-tokens.test.tsx` (parse the block, assert the
    declaration is present and resolves to `600`). After the DoD 10 harness re-run,
    `grep -c 'btn.newChat.*fontWeight' tools/design-parity/surfaces/chats/out/report-default.md`
    prints `0` (on the merge base it prints `1`: `600 → 500`, `report-default.md:48`).
17. **Decision F / README G3.** `grep -c -- '--color-bg: #09090b;' packages/design-system/src/styles.css`
    prints `1` — the value is deliberately **unchanged** — and the declaration is preceded by
    a comment containing the string `copilot.css:8`. Asserted as a named `it()` in
    `render-live-tokens.test.tsx` so a future "fix to `#050506`" fails the gate.
18. **Decision C block rhythm.** `packages/design-system/src/styles.css` declares
    `.ui-section-head { … margin: 22px 0 10px }` and `.ui-section-head:first-child { margin-top: 0 }`
    (named `it()` in `render-live-tokens.test.tsx`, values cited to `copilot.css:1569-1573`),
    and `SectionHeader.test.tsx` asserts `screen.getByTestId("section-header")` carries class
    `ui-section-head`. `grep -c 'wrapStyle' packages/chat-surface/src/destinations/_shared/SectionHeader.tsx`
    prints `0`.

## Dependencies

**Blocked by:** nothing. **Wave 0**, alongside PRD-05 and PRD-06 (disjoint file sets — this
PRD is `packages/design-system` + five `chat-surface` leaf consumers + `tools/design-parity`;
it touches no service, no host binder, no `api-types`). It can land first and should.
It claims **no migration id** (on disk: `services/backend` high-water `0045`,
`services/ai-backend` `0001`; README C18 assigns `0046`/`0047` to PRD-06/PRD-07 and
`0002`/`0003`/`0004` to PRD-05/PRD-07/PRD-09).

**Serialisation this PRD is first in** (README, "must be serialised" table):

- `packages/design-system/src/styles.css` — **01 → 02 → 08 → 11 → 10**.
- `packages/chat-surface/src/shell/AppRail.tsx` — **01 → 03 → 12** (PRD-12 owns the file).

**Unblocks (all of them measure against these tokens, so landing them first would force a
re-measure):**

- **PRD-02 (status chip recipe)** — needs `--font-size-mono-10-5` (README C11: this PRD's
  name, not `--font-size-mono-105`) and the size/weight-corrected `.ui-badge` to exist before
  `StatusPill.tsx:66-84` can migrate onto it. PRD-02 carries 12 of the 17 Chats HIGH rows.
- **PRD-08 (activity surface)** — owns `_shared/Row.tsx` (README C9); its metadata sizing
  (`Row.tsx:107,117`) and icon-tile fill consume `--font-size-mono-10-5` and
  `--color-surface-elevated`. It also layers `statusTone.ts` on PRD-02 (README C12).
- **PRD-09 (chats), PRD-10 (projects), PRD-11 (tools)** — every one of their reports is
  contaminated by the 13.6px substitution until DoD 1 lands; re-measure after. PRD-09 and
  PRD-10 also own the `anchors.json` retarget for `sect.*` described in decision C.
- **PRD-11 (Tools connect modal)** — the modal-shell geometry rows (`padding: 22px`,
  `display: grid`, `z-index: 60`, `position: absolute`; README G10) are only readable once
  the scrim colour row clears, and PRD-11's `.ui-button` CTA inherits decision E's fix.
- **PRD-13 (dead code)** — deletes the vestigial `sect-h` class this PRD deliberately leaves
  on the `SectionHeader` wrapper (README C13).
- **The desktop accent-persistence work** (`SettingsMount.tsx` apply-on-mount) — unassigned;
  README §Gaps **G7** routes it to a new PRD-14 or a fold into **PRD-12**. Its user-visible
  payoff is nine working accents, which does not exist until this PRD lands.
