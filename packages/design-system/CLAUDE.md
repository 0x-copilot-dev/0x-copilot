# Design System

React primitives + tokens. Producer of shared UI for `apps/*`.

## Before changing behavior

Read `packages/design-system/README.md` and `TESTING.md` first.

## What belongs here

- Reusable UI primitives (buttons, inputs, layout, dialogs).
- Design tokens (colors, spacing, typography, radii).
- Variant systems for primitives.

## What does NOT belong here

- App-specific workflows.
- Data fetching / API clients.
- Routing.
- Product copy and labels.
- Feature flags or business logic.

If it knows about `backend-facade`, a specific feature, or product copy — it lives in [apps/frontend](../../apps/frontend), not here.

## Tokens & a11y

- Prefer existing CSS tokens and primitive variants over one-off colors, spacing, or typography. If a new token is needed, add the token first.
- Preserve native semantics, keyboard access, labels, and disabled states. A primitive that wraps `<button>` must remain focusable, keyboard-activatable, and announce its disabled state.

### Typography tokens — single source of truth

Type scale, weight, and line-height live on `:root` in `packages/design-system/src/styles.css` alongside the colour tokens. Every component CSS rule MUST go through these tokens — never hard-code rems / pixels for `font-size`, never hard-code numeric `font-weight` literals.

| Token family                                                           | Values           | Use for              |
| ---------------------------------------------------------------------- | ---------------- | -------------------- |
| `--font-size-3xs/2xs/xs/sm/md/lg/xl/2xl/3xl` (sans ladder)             | 9px → 32px       | All sans text sizing |
| `--font-size-mono-8-5/-9-5/-10/-10-5` (mono micro-ladder)              | 8.5px → 10.5px   | Mono metadata only   |
| `--font-weight-regular/medium/semibold/bold`                           | 400/500/600/700  | All text weights     |
| `--tracking-tighter/tight/snug/normal/caption/label/eyebrow/mono-caps` | -0.03em → 0.12em | All letter-spacing   |
| `--line-height-tight/snug/base/loose`                                  | 1.2/1.35/1.5/1.7 | Vertical rhythm      |

**Two size ladders, and they do not mix.** The sans ladder's `sm` rung is the app's
inherited base and is exactly **13px** — the design's `body` size, and what 148 call
sites already assumed in their `var(…, 13px)` fallback. (The rung's declaration in
`src/styles.css` carries the full rationale; it is the single source of truth for the
value — this table maps families, not rung values.) Alongside the sans t-shirt scale
sits a separate **mono micro-ladder** (`--font-size-mono-8-5` / `-9-5` / `-10` / `-10-5`)
because the design's mono metadata register steps in half pixels — 8.5 / 9 / 9.5 / 10 /
10.5 — which a t-shirt scale cannot express. Never style mono metadata with a nearby sans
rung: reaching for `--font-size-2xs` (11.2px) where 9.5px mono was meant is how the
section header shipped 18% too large.

**Prefer a recipe over raw tokens.** For a role that already has one — eyebrow, section
label, heading, item title, caption, pill, accent chip, status/metadata chip — use the
composed recipe (`.ui-*` class or its `index.tsx` wrapper) instead of re-assembling size

- weight + tracking + transform. `SKILL.md` is the intent → recipe map; hand-composing a
  role that a recipe covers is how the same label drifted to three tracking values app-wide.

**The status/metadata chip is `.ui-badge` / `<Badge>` — the design's `.chip`** (mono,
outlined, NO fill; tones recolour text + border only; `dot` only for a live chip). The
older filled `<StatusPill>` / `.ui-status-pill` recipe was **deleted** (PRD-02); do not
reintroduce a filled status pill. Status labels are LOWERCASE at the source, never an
uppercase `text-transform`.
`letter-spacing` never takes a raw `em` — only a `--tracking-*` token. (That rule is
enforced by **review** plus the token-contract and parity gates in `SKILL.md`, not by
stylelint: this repo has no stylelint configuration, despite what older comments claimed.)

**Why this is enforced.** A `<strong>` defaults to weight 700 while the rest of the app uses 600 for the same kind of UI heading. That 100-weight gap reads as a _different font family_ on macOS where SF Pro Text and SF Pro Display swap based on weight × size — root cause of the "+ menu vs GPT-5.4 Nano pill" mismatch. Going through the tokens makes it impossible for a future component to drift.

**Exceptions** (keep as literals): responsive `clamp(...)` on display headings, and `font-size: 0` for screen-reader-only patterns. Document the reason inline when you keep a literal.

_The former `.ui-button` weight-650 exception is gone (PRD-01)._ It predated the v3 design and inverted the button hierarchy once `.ui-button--primary` adopted the design's 600: a primary rendered lighter than a secondary that still inherited 650. The design specifies `.cbtn { 500 }` / `.cbtn--pri { 600 }`, so the base is now `--font-weight-medium` and the TONE variant owns emphasis — size tiers control size, tone controls weight.

## v2 "quiet" tokens — the single token source of truth

`packages/design-system/src/styles.css` `:root` is the **one place** color, type,
space, radius, motion, and density tokens are defined for the whole product. Every
consumer — `chat-surface`, `surface-renderers`, `apps/frontend`, `apps/desktop` —
resolves `var(--color-…)` / `var(--font-…)` against this file. Do not hard-code hex
colors or px/rem type in a consumer; add or reuse a token here.

The v2 "quiet" system (0xCopilot desktop redesign) is deliberately calm, native-
feeling chrome rather than a branded display face:

- **Typography.** `--font-display` and `--font-sans` both resolve to the native
  platform UI stack (`-apple-system`, `SF Pro Text`, `Segoe UI`, `system-ui`) — there
  is no vendored display face. `--font-mono` is **JetBrains Mono** (self-hosted
  variable woff2, latin + latin-ext split by unicode-range, `font-display: swap`) —
  the only vendored face, used for code and metadata/labels. The old Space Grotesk /
  Instrument Sans `@font-face` rules were removed with v2; their woff2 assets remain
  vendored but unreferenced.
- **Neutrals.** Near-black ladder anchored at `--color-bg: #09090b`, with
  `--color-bg-elevated` / `--color-surface` / `--color-surface-muted` stepping up.
- **Hairline borders.** `--color-border: rgba(255,255,255,0.06)` (and
  `--color-border-strong`) — near-invisible strokes over the near-black ground,
  replacing the old solid borders.
- **One-accent discipline.** Sky `--color-accent` `#5fb2ec` is the ONLY accent hue.
  Jade `--color-success` `#57c785` means live/success; ember `--color-danger`
  (`#f0764f`, also aliased `--color-ember`) is the single destructive hue (a locked v2
  shift from the old danger red); amber `--color-warning` is warning. Never introduce
  a second accent for emphasis — use the accent, a neutral, or a semantic tone.
- **Switches.** `:root[data-accent=…]` (Settings → Appearance accent swatches, sky is
  default), `:root[data-density="compact"]` (drops spacing tokens ~20%),
  `:root[data-reduce-motion="always|auto|off"]`, and theme via
  `:root[data-theme="light|dark|slate"]`. All three themes are fully specified; the
  appearance write path stamps these attributes on `document.documentElement`.
- **Accent = two tiers, one writer per variable.** `[data-accent]` blocks write ONLY the
  private seed tier — `--accent-seed`, `--accent-seed-strong`, `--accent-seed-ink` — and
  must never write `--color-accent*`. The three `[data-theme]` blocks are the **sole
  writers** of the public `--color-accent` / `-strong` / `-contrast` tier, deriving it
  from the seed (identity on the dark grounds; darkened in oklab on light, where the
  ink flips near-white as the design does). This exists because both selectors used to
  write `--color-accent` at identical specificity, so source order decided the winner and
  nine swatches collapsed to one colour in light and one in slate. Adding a swatch is one
  seed block; adding a theme is one derivation block — never a 9 × N matrix. Guarded by
  `node tools/design-parity/lib/accent-matrix.mjs --check`.

Semantic aliases (`--color-focus-ring`, `--color-line`, `--color-surface-2`,
`--color-text-danger`, …) live on `:root` and resolve to the canonical tokens at
use-site, so a theme override reflows them automatically.

**Cross-package reconciliation.** `packages/surface-renderers` and the thread-canvas
subtree carried an old lime accent `#c2ff5a`; v2 reconciled it to the accent tokens.
`surface-renderers/src/_shared/palette.ts` now maps `lime: "var(--color-accent)"` and
`limeBgSoft` to an accent color-mix — the names survive for continuity but resolve to
the single sky accent. Do not reintroduce a literal lime.

## Promotion path

Only promote UI from `apps/frontend` into design-system once it is **stable and reusable** — used (or clearly needed) in more than one place, with a settled API. Avoid promoting prematurely; churn here ripples to every consumer.

## Validation

Run design-system typecheck and affected frontend checks when practical.
