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

| Token family                                 | Values           | Use for          |
| -------------------------------------------- | ---------------- | ---------------- |
| `--font-size-2xs/xs/sm/md/lg/xl/2xl/3xl`     | 11.2px → 32px    | All text sizing  |
| `--font-weight-regular/medium/semibold/bold` | 400/500/600/700  | All text weights |
| `--line-height-tight/snug/base/loose`        | 1.2/1.35/1.5/1.7 | Vertical rhythm  |

**Why this is enforced.** A `<strong>` defaults to weight 700 while the rest of the app uses 600 for the same kind of UI heading. That 100-weight gap reads as a _different font family_ on macOS where SF Pro Text and SF Pro Display swap based on weight × size — root cause of the "+ menu vs GPT-5.4 Nano pill" mismatch. Going through the tokens makes it impossible for a future component to drift.

**Exceptions** (keep as literals): the brand call-to-action weight 650 on `.ui-button` (sits intentionally between semibold and bold), responsive `clamp(...)` on display headings, and `font-size: 0` for screen-reader-only patterns. Document the reason inline when you keep a literal.

## Promotion path

Only promote UI from `apps/frontend` into design-system once it is **stable and reusable** — used (or clearly needed) in more than one place, with a settled API. Avoid promoting prematurely; churn here ripples to every consumer.

## Validation

Run design-system typecheck and affected frontend checks when practical.
