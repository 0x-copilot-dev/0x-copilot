// PR — collapsed into the shared `UserProfileProvider` cache.
// See docs/architecture/prds/03-collapse-use-my-profile.md.
//
// `useMyProfile` was a second, separate /v1/me/profile fetcher whose
// cache never observed updates from `useUserProfile.save()`. It now
// projects from the shared context so a profile rename in Settings
// instantly re-renders the sidebar greeting.

import { useUserProfileState } from "../me/UserProfileContext";

export interface ProfileSnapshot {
  display_name: string | null;
  email: string;
}

export function useMyProfile(): ProfileSnapshot | null {
  const { data } = useUserProfileState();
  if (data === null) return null;
  return { display_name: data.display_name, email: data.email };
}
