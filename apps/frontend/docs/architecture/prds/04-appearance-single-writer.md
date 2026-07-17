# PRD 04: Appearance — single writer

**Status:** Draft → In implementation
**Owner:** Frontend platform
**Related audit:** [05-dry-audit.md §13](../05-dry-audit.md)

## Problem

The user's appearance (theme + accent + density + reduce-motion) is
currently written by **three independent layers**:

1. **`useThemeSync`** ([features/me/useThemeSync.ts](../../src/features/me/useThemeSync.ts)) — mounted once at `App.tsx`. Reads server preferences via `useUserPreferences`, calls `setScheme` / `setAccent` on the design-system `ThemeProvider`, and writes `data-density` + `data-reduce-motion` directly onto `document.documentElement`.
2. **Design-system `ThemeProvider`** ([packages/design-system/src/index.tsx:73](../../../../packages/design-system/src/index.tsx#L73)) — owns the painted scheme + accent in React state and mirrors them to `data-theme` / `data-accent` + localStorage on every change.
3. **`Appearance.tsx`** ([features/settings/sections/Appearance.tsx](../../src/features/settings/sections/Appearance.tsx)) — on every swatch click, **independently** calls `setScheme` / `setAccent` for the instant-repaint, writes `data-density` / `data-reduce-motion` to `documentElement`, **and** queues a debounced `useUserPreferences.save()` to persist.

This means:

- The `system → dark` scheme mapping (`toProviderScheme`) is duplicated in both `useThemeSync` and `Appearance.applyAppearanceLocally`. Drift here would split the user-visible theme between settings UI and the rest of the app.
- The `data-density` and `data-reduce-motion` `documentElement` writes happen in two places, with no coordination — if a future caller forgets one, the page chrome silently desyncs from the saved preference.
- ThemeProvider does its own localStorage write on every `setScheme` / `setAccent` call, including the ones triggered by `useThemeSync` mirroring a server fetch — wasted writes on every cold load.
- The "what happens on a swatch click" logic is **split across two files** that don't reference each other: optimistic repaint in `Appearance.tsx`, server save in `Appearance.tsx`, server hydration in `useThemeSync`.

## Goals

1. One module owns the entire write path for appearance: server save +
   provider write + document attribute write.
2. Click handlers in UI call **one function** (`set(patch)`); they do
   not call `setScheme`/`setAccent` directly and do not touch
   `documentElement`.
3. The design-system `ThemeProvider` keeps its zero-flash localStorage
   role for cold paint — but its `setScheme`/`setAccent` are only ever
   driven by the new single owner.
4. No drift between "what was saved" and "what the page shows" at any
   point in the flow: optimistic update is the same write path that the
   server confirmation reaches.

## Non-goals

- Replacing the design-system `ThemeProvider`. It stays as-is — it
  owns the painted state + the cold-paint cache.
- Migrating the `RegionAndLanguage` (locale) section. Locale lives on
  the user profile, not preferences; it's correctly separate.
- Cross-tab live sync. Out of scope (today the active tab wins;
  bringing a stale tab to foreground triggers `useUserPreferences`
  refresh).

## Design

Add `AppearanceProvider` at the app layer. It is the **sole** writer
to:

- `useUserPreferences.save({ appearance: ... })`
- `ThemeProvider.setScheme` / `setAccent`
- `document.documentElement.dataset.density` / `dataset.reduceMotion`

The provider mounts inside the existing `UserProfileProvider` slot in
[App.tsx](../../src/app/App.tsx). It exposes one hook:

```ts
export interface AppearanceController {
  /** Current server snapshot (null while preferences load). */
  appearance: AppearancePreferences | null;
  loading: boolean;
  error: string | null;
  /**
   * Apply a partial update. Visual change is instant (optimistic);
   * server save is debounced 300ms and coalesces consecutive calls.
   * Failure rolls back to the last server snapshot via the regular
   * useUserPreferences re-hydration path.
   */
  set: (patch: Partial<AppearancePreferences>) => void;
}

export function useAppearance(): AppearanceController;
```

### What `set()` does, atomically

1. Mirror `patch.theme` → `setScheme()` (using the one `system → dark`
   mapping, defined once inside the provider).
2. Mirror `patch.accent` → `setAccent()`.
3. Write `patch.density` and `patch.reduce_motion` to
   `document.documentElement.dataset.*`.
4. Reset the 300ms debounce timer; on fire, call
   `useUserPreferences.save({ appearance: { ...current, ...patch } })`.

If the user clicks the swatch six times in 600ms, the provider does
six optimistic repaints and **one** server save with the final value.

### What dies

- `useThemeSync.ts` — its responsibility moves entirely into the
  provider. The "system → dark" mapping function lives once inside the
  provider.
- `Appearance.tsx`'s `applyAppearanceLocally` and `scheduleSave` — both
  collapse into `useAppearance().set(...)`. The component loses ~50
  lines and stops importing `useTheme`.
- The duplicated `toProviderScheme` mapping (currently in
  `useThemeSync` AND `Appearance.tsx`) becomes one private helper.

### Why a provider, not a hook?

Two consumers in the tree need the same state and the same debounce
timer:

- `App.tsx` (or a top-level effect host) needs to mirror server-loaded
  preferences into the provider on hydrate.
- `Appearance.tsx` needs to drive optimistic updates and queue saves.

A hook would create two independent debounce timers and two independent
mirror effects. A context-backed provider keeps the writer singleton
even though there are multiple readers.

The provider's state is **derived** from `useUserPreferences`'s data —
it doesn't fork the source of truth. The optimistic apply just makes
the provider call `setScheme` etc. ahead of the server round-trip.

## Migration

1. Add `features/appearance/AppearanceContext.tsx` with
   `AppearanceProvider` + `useAppearance`.
2. Wrap `<CopilotApp />` in `<AppearanceProvider>` in
   [App.tsx](../../src/app/App.tsx), inside the existing
   `UserProfileProvider`.
3. Delete `features/me/useThemeSync.ts` and its call site in
   `App.tsx`.
4. Simplify `Appearance.tsx`: take `appearance` from `useAppearance()`,
   call `useAppearance().set(...)` on every click handler. Drop the
   `useUserPreferences`/`useTheme` props/imports and the debounce
   plumbing.
5. Update tests: `App.test.tsx` / any consumer of `useThemeSync` / the
   `Appearance` story to wrap in `<AppearanceProvider>`.

## Validation

- `npm run typecheck`
- `npx vitest run` from `apps/frontend/` — must stay green
- Manual click-through: open Settings → Appearance, click each swatch.
  Repaint is instant; server save fires once after 300ms; reload
  preserves choice; opening Appearance in a fresh tab paints saved
  theme on first render (no flicker).

## Risks

- **Existing localStorage scheme/accent might disagree with server
  state at first paint.** Today the user sees the localStorage value
  briefly, then `useThemeSync` reconciles. Behaviour stays identical
  after this PR — the provider does the same reconcile.
- **The debounce timer lifecycle is the only piece of mutable state
  inside the provider.** On unmount we clear it; otherwise a stray
  save could fire after the app tree is gone. Pin with a test.
- **Test fallout:** any test that mounts `Appearance` standalone needs
  `<AppearanceProvider>`. Mirrors the `<UserProfileProvider>` pattern
  added in PR6.

## Rollback

Single-file revert of `App.tsx` + restore `useThemeSync.ts` and the
two functions in `Appearance.tsx`. No schema or storage migration.
