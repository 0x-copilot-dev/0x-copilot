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

## Promotion path

Only promote UI from `apps/frontend` into design-system once it is **stable and reusable** — used (or clearly needed) in more than one place, with a settled API. Avoid promoting prematurely; churn here ripples to every consumer.

## Validation

Run design-system typecheck and affected frontend checks when practical.
