# PR 4.1 — Gap fix · "You" group wiring

> **Status:** Addendum to [`pr-4.1-settings-you-group.md`](pr-4.1-settings-you-group.md).
> **Size:** XS. Three wiring edits, no new files.

## Problem

The PR 4.1 landing left the "You" group inert: backend + components + hooks shipped, but the UI surface that consumes them was not wired. Specifically:

| Gap                                                                      | Acceptance criterion violated                                                                                                                     |
| ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `SettingsScreen` rail does not include the "You" group entries.          | "`<Profile />`, `<Appearance />`, `<Shortcuts />`, `<Notifications />` mount … under the 'You' group rail."                                       |
| `SettingsScreen` JSX has no render branches for the four "You" sections. | Same — the components are dead exports.                                                                                                           |
| `useThemeSync` has zero consumers.                                       | "ThemeProvider mirrors `preferences.appearance`; density and reduce-motion are reflected as `<html data-density>` / `<html data-reduce-motion>`." |

Density + reduce-motion must apply across the whole app, not just the Settings page — so the consumer lives at app shell level, not inside `SettingsScreen`.

## Fix

Three edits, all in `apps/frontend/src`:

1. **`app/App.tsx` — lift hydration to the shell.** Call `useUserProfile()` + `useUserPreferences()` once inside `EnterpriseSearchApp`, then `useThemeSync(preferences.data)` so the `<html>` attributes apply on chat too. Thread `profile` + `preferences` into `SettingsScreen` as props.
2. **`features/settings/SettingsScreen.tsx` — surface the "You" group.** Add the group label + four section rail entries above "Workspace". Render the four section components in the content switch.
3. **No backend, no migration, no api-types churn.** This is FE wiring only.

## Why "You" goes above "Workspace"

The Atlas design doc places "You" first (it's the user's own surface). The ordering in the rail array drives the visual order, so "You" lives at the top.

## Acceptance

- ✅ Navigate to `/settings#profile` — Profile section mounts; form hydrates from `GET /v1/me/profile`.
- ✅ `/settings#appearance` — accent picker, density radios, reduce-motion radios all visible and persist.
- ✅ Set accent to `gold` in Appearance, refresh on the chat surface (not Settings) — the chat repaints in gold (server-side preference applied at app shell).
- ✅ Set density to `compact` — the chat sidebar tightens (HTML attribute applied globally).
- ✅ Set reduce-motion to `always` — pulse animations stop everywhere, not just in Settings.
- ✅ `npm run build --workspace @enterprise-search/frontend` green; no new dep.
