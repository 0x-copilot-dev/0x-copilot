# PRD: Theme & appearance ownership

**Status:** Implemented (closed, documenting current design)
**Owner:** Frontend platform
**Related audit:** [05-dry-audit.md §13](../05-dry-audit.md)

## Background

The audit flagged "three writers" of theme/appearance state and a risk
of drift. Re-reading the code in context shows the ownership is
actually clean — each attribute has exactly one writer. This PRD
captures the boundary so it doesn't drift in future PRs.

## Attribute ownership

| Attribute        | DOM target                      | Owner                                                                               | Cache                                              |
| ---------------- | ------------------------------- | ----------------------------------------------------------------------------------- | -------------------------------------------------- |
| `scheme` (theme) | `<html data-theme="…">`         | [`ThemeProvider`](../../../../packages/design-system/src/index.tsx) (design-system) | `localStorage["0x-copilot-theme"]`                 |
| `accent`         | `<html data-accent="…">`        | `ThemeProvider`                                                                     | `localStorage["0x-copilot-accent"]`                |
| `density`        | `<html data-density="…">`       | [`useThemeSync`](../../src/features/me/useThemeSync.ts)                             | server only (`UserPreferences.appearance.density`) |
| `reduce_motion`  | `<html data-reduce-motion="…">` | `useThemeSync`                                                                      | server only                                        |

### Why this is not a duplication

The audit's "three writers" framing conflated three distinct surfaces
that write to **different** DOM attributes:

- `ThemeProvider` writes `data-theme` and `data-accent` only.
- `useThemeSync` writes `data-density` and `data-reduce-motion` only.
- `Appearance.tsx` is a **caller** of `useTheme().setScheme/setAccent`
  — it doesn't write the DOM directly. The provider does.

There is one writer per attribute. No two writers touch the same
`dataset` field.

### Why scheme/accent have a localStorage cache and density doesn't

- Scheme + accent need to paint **before** the server response lands —
  otherwise a fresh tab flashes the default dark theme for ~200ms while
  preferences fetch. Hence the cache, hence
  [`readPersisted`](../../../../packages/design-system/src/index.tsx#L122)
  during `useState` init.
- Density + reduce_motion are visually-subtle attributes (line spacing,
  animation toggle). Their first-paint default is "comfortable" / "on"
  and matches the most common case, so a brief flash before the
  server hydrates isn't perceptually noticeable. No cache, no extra
  storage key.

If a future change makes density visually startling at first paint,
add `data-density` to ThemeProvider's cache the same way scheme/accent
work and migrate `useThemeSync` to call `setDensity()` on the provider.

## Invariants

1. **No component writes `<html data-theme>` or `<html data-accent>`
   except `ThemeProvider`.**
2. **No component writes `<html data-density>` or
   `<html data-reduce-motion>` except `useThemeSync`.**
3. **`Appearance.tsx` (and any future settings UI) writes through
   `useTheme().setScheme/setAccent`** — never `document.documentElement`
   directly.
4. **Preferences fetch lives in `useUserPreferences` only.**
   `useThemeSync` is a pure projector — it never refetches.

## How this PRD enforces itself

Reviewers should fail a PR that:

- Writes `document.documentElement.dataset.theme` or `.accent` outside
  `ThemeProvider`.
- Writes `.density` or `.reduceMotion` outside `useThemeSync`.
- Adds a third localStorage key for theme/appearance state.
- Re-derives preferences inside a component instead of consuming
  `useUserPreferences()`.

## Out of scope

- OS-following `system` theme (currently `system` maps to `dark` — see
  the comment in `toProviderScheme`). Pull request when ThemeProvider
  grows an explicit `system` scheme.
- Cross-tab sync via `storage` events. Separate PRD if needed.
