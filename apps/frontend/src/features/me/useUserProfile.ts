// PR — collapsed into the shared `UserProfileProvider` cache.
// See docs/architecture/prds/03-collapse-use-my-profile.md.
//
// Both `useUserProfile` (settings) and `useMyProfile` (sidebar greeting)
// now read from a single `useMutableRecord` instance held in
// `UserProfileContext` so a save() in Settings immediately re-renders
// the sidebar.

export {
  useUserProfileState as useUserProfile,
  type UserProfileState,
} from "./UserProfileContext";
