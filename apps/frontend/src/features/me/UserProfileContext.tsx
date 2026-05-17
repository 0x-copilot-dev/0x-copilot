import {
  createContext,
  useContext,
  type ReactElement,
  type ReactNode,
} from "react";

import type {
  UpdateUserProfileRequest,
  UserProfile,
} from "@enterprise-search/api-types";

import { getMyProfile, updateMyProfile } from "../../api/meApi";
import {
  useMutableRecord,
  type MutableRecordState,
} from "../../api/useResource";

// PRD: docs/architecture/prds/03-collapse-use-my-profile.md
//
// One in-memory cache of the user's profile, shared across every
// consumer. Before this provider, `useUserProfile` and `useMyProfile`
// each held their own `useMutableRecord` instance — saving in Settings
// updated one cache and left the sidebar greeting stale until reload.

export type UserProfileState = MutableRecordState<
  UserProfile,
  UpdateUserProfileRequest
>;

const UserProfileContext = createContext<UserProfileState | null>(null);

export function UserProfileProvider({
  children,
}: {
  children: ReactNode;
}): ReactElement {
  const state = useMutableRecord(getMyProfile, updateMyProfile, {
    load: "Could not load profile.",
    save: "Could not save.",
  });
  return (
    <UserProfileContext.Provider value={state}>
      {children}
    </UserProfileContext.Provider>
  );
}

export function useUserProfileState(): UserProfileState {
  const ctx = useContext(UserProfileContext);
  if (ctx === null) {
    throw new Error(
      "UserProfileProvider missing — wrap the authenticated app tree.",
    );
  }
  return ctx;
}
