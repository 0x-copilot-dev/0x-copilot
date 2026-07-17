# Design System Testing

The current design system has typecheck coverage only. Visual and component test
tooling has not been added yet.

## Current Check

```bash
npm run typecheck --workspace @0x-copilot/design-system
```

Run frontend typecheck and build when a primitive change affects app usage:

```bash
npm run typecheck --workspace @0x-copilot/frontend
npm run build --workspace @0x-copilot/frontend
```

## Expected Test Shape

As the package grows, add tests in this order:

- Component tests for interaction behavior, controlled inputs, dialogs, and
  keyboard support.
- Accessibility checks for labels, focus order, ARIA attributes, and disabled
  states.
- Visual regression coverage for shared primitives and theme variants.
- App-level smoke tests for screens that consume changed primitives.

## Manual QA

For every primitive or token change, manually verify:

1. Dark, light, and slate themes still render readable text and contrast.
2. Buttons, inputs, selects, textareas, and switches remain keyboard-accessible.
3. Dialogs expose meaningful labels and close behavior.
4. Responsive layouts do not rely on app-specific container assumptions.
5. Existing frontend screens using the changed primitive still render cleanly.

## Ownership Boundary

If a test needs app data, app routes, or feature-specific copy, it belongs in
`apps/frontend`, not this package. Design-system tests should validate reusable
primitive behavior.
