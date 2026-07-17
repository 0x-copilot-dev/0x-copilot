import {
  createContext,
  useContext,
  type ReactElement,
  type ReactNode,
} from "react";

import type {
  UpdateUserPreferencesRequest,
  UserPreferences,
} from "@0x-copilot/api-types";

import { getMyPreferences, updateMyPreferences } from "../../api/meApi";
import {
  useMutableRecord,
  type MutableRecordState,
} from "../../api/useResource";

// PRD: docs/architecture/prds/04-appearance-single-writer.md
//
// One in-memory cache of the user's preferences, shared across every
// consumer (Appearance, Shortcuts, Notifications, AppearanceProvider).
// Before this provider, `useUserPreferences` was called once in
// `CopilotApp` and threaded as a prop through SettingsScreen
// → each section. The provider lift removes the prop drilling and
// guarantees `AppearanceProvider` sees the same data the sections do.

export type UserPreferencesState = MutableRecordState<
  UserPreferences,
  UpdateUserPreferencesRequest
>;

const UserPreferencesContext = createContext<UserPreferencesState | null>(null);

export function UserPreferencesProvider({
  children,
}: {
  children: ReactNode;
}): ReactElement {
  const state = useMutableRecord(getMyPreferences, updateMyPreferences, {
    load: "Could not load preferences.",
    save: "Could not save.",
  });
  return (
    <UserPreferencesContext.Provider value={state}>
      {children}
    </UserPreferencesContext.Provider>
  );
}

export function useUserPreferencesState(): UserPreferencesState {
  const ctx = useContext(UserPreferencesContext);
  if (ctx === null) {
    throw new Error(
      "UserPreferencesProvider missing — wrap the authenticated app tree.",
    );
  }
  return ctx;
}
