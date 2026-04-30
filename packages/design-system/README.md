# Design System

`@enterprise-search/design-system` contains shared web UI primitives, theme
state, and CSS tokens for Enterprise Search.

The package is intentionally small. Keep app-specific workflows in
`apps/frontend` until a primitive has proven reusable.

## Exports

- `.`: React components and hooks from `src/index.tsx`.
- `./styles.css`: design tokens, theme CSS variables, and primitive styles.

Current primitives include theme provider/hooks, buttons, cards, badges, form
controls, dialogs, layout helpers, and navigation-style components.

## Usage

Import the CSS once from the consuming app:

```tsx
import "@enterprise-search/design-system/styles.css";
```

Use primitives through the package entry point:

```tsx
import { Button, Card, ThemeProvider } from "@enterprise-search/design-system";
```

## Contribution Rules

- Keep primitives generic and composable.
- Do not import from `apps/*` or `services/*`.
- Prefer CSS tokens in `styles.css` over one-off hard-coded colors, spacing, or
  typography in components.
- Preserve accessibility labels and native element semantics.
- Avoid adding product-specific data fetching, routing, or feature state.
- Add new exports deliberately; the package is consumed as a shared API.

## Checks

```bash
npm run typecheck --workspace @enterprise-search/design-system
```

See `TESTING.md` for the current quality bar and future visual testing plan.
