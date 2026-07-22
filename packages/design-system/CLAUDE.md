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

| Token family                                                           | Values           | Use for            |
| ---------------------------------------------------------------------- | ---------------- | ------------------ |
| `--font-size-2xs/xs/sm/md/lg/xl/2xl/3xl` (+ `--font-size-mono-10`)     | 11.2px → 32px    | All text sizing    |
| `--font-weight-regular/medium/semibold/bold`                           | 400/500/600/700  | All text weights   |
| `--tracking-tighter/tight/snug/normal/caption/label/eyebrow/mono-caps` | -0.03em → 0.12em | All letter-spacing |
| `--line-height-tight/snug/base/loose`                                  | 1.2/1.35/1.5/1.7 | Vertical rhythm    |

**Prefer a recipe over raw tokens.** For a role that already has one — eyebrow, section
label, heading, item title, caption, pill, accent chip — use the composed recipe
(`.ui-*` class or its `index.tsx` wrapper) instead of re-assembling size + weight +
tracking + transform. `SKILL.md` is the intent → recipe map; hand-composing a role that
a recipe covers is how the same label drifted to three tracking values app-wide.
`letter-spacing` never takes a raw `em` — only a `--tracking-*` token (stylelint-enforced).

**Why this is enforced.** A `<strong>` defaults to weight 700 while the rest of the app uses 600 for the same kind of UI heading. That 100-weight gap reads as a _different font family_ on macOS where SF Pro Text and SF Pro Display swap based on weight × size — root cause of the "+ menu vs GPT-5.4 Nano pill" mismatch. Going through the tokens makes it impossible for a future component to drift.

**Exceptions** (keep as literals): the brand call-to-action weight 650 on `.ui-button` (sits intentionally between semibold and bold), responsive `clamp(...)` on display headings, and `font-size: 0` for screen-reader-only patterns. Document the reason inline when you keep a literal.

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
  `:root[data-theme="light|dark|slate"]`. Both light and dark are fully specified;
  the appearance write path stamps these attributes on `document.documentElement`.

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
