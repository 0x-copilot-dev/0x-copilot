# Cluster: App shell and shared utils

**Paths:** `apps/frontend/src/app/`, `apps/frontend/src/utils/`  
**Last reviewed:** 2026-05-06

## Scope

- Application chrome: [`App.tsx`](../../../apps/frontend/src/app/App.tsx) (auth gate, chat vs settings, OAuth/MCP callback handling), hash routing for settings.
- Keyboard helpers: [`keymap.ts`](../../../apps/frontend/src/app/keymap.ts).
- Cross-feature utilities: [`useViewportOverlay.ts`](../../../apps/frontend/src/utils/useViewportOverlay.ts), [`useLocalStorageState.ts`](../../../apps/frontend/src/utils/useLocalStorageState.ts).

## Unused / ts-prune signals

| Symbol                                             | File            | Notes                                                                                               |
| -------------------------------------------------- | --------------- | --------------------------------------------------------------------------------------------------- |
| `KeymapHandler`, `KeymapBinding`, `KeymapBindings` | `app/keymap.ts` | Exported types; ts-prune marks `(used in module)` — consumers likely import concrete bindings only. |

No standalone **modules** under `app/` or `utils/` were found with **zero** importers from production or tests.

## Smells / maintenance notes

- **`App.tsx` scope growth** — Single file coordinates auth, connectors prefetch, theme/profile/skills hydration, and settings hash routing. Harder refactors may benefit from extracting route shells once stable (observation only; not a functional defect).
- **Hash routing vs section catalog** — [`useSettingsSection.ts`](../../../apps/frontend/src/features/settings/useSettingsSection.ts) owns `SETTINGS_SECTIONS`; [`App.tsx`](../../../apps/frontend/src/app/App.tsx) duplicates a parallel `settingsSections` array for narrowing — risk of drift if one list updates without the other (see [05-settings-and-workspace.md](./05-settings-and-workspace.md)).

## Confidence

**High** for `useViewportOverlay` usage ([`ChatScreen.tsx`](../../../apps/frontend/src/features/chat/ChatScreen.tsx)); **medium** for ts-prune noise on `keymap` type exports.
