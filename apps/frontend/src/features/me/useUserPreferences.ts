// PRD 04 — collapsed into the shared `UserPreferencesProvider` cache.
// See docs/architecture/prds/04-appearance-single-writer.md.
//
// `useUserPreferences` used to construct its own `useMutableRecord`
// instance; every caller (Appearance, Shortcuts, Notifications,
// AppearanceProvider) got an independent copy of the same fetch. The
// provider hoists it to one cache so a save in Appearance immediately
// re-renders the other panels.

export {
  useUserPreferencesState as useUserPreferences,
  type UserPreferencesState,
} from "./UserPreferencesContext";
