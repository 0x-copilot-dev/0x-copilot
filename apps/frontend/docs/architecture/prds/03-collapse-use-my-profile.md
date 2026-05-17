# PRD: Collapse `useMyProfile` into `useUserProfile`

**Status:** Draft → In implementation
**Owner:** Frontend platform
**Related audit:** [05-dry-audit.md §12](../05-dry-audit.md)

## Problem

Two hooks fetch the **same endpoint** (`/v1/me/profile`) with **two
separate React caches** and no cross-invalidation:

- [`useMyProfile`](../../src/features/auth/useMyProfile.ts) — lazy
  fetch on first mount, returns minimal `{ display_name, email }`.
  Consumers: sidebar `UserCard`, chat `ThreadBody`.
- [`useUserProfile`](../../src/features/me/useUserProfile.ts) — full
  fetch on first mount, returns full `UserProfile` and a `save()`.
  Consumers: `App`, `SettingsScreen`, settings `Profile`, settings
  `Appearance`.

**Observable bug:** When the user edits their display name in
Settings, `useUserProfile.save()` updates the settings cache but the
sidebar `UserCard` keeps showing the old name until a hard reload.
Two caches, no invalidation.

## Goals

1. One in-memory cache of the user's profile, shared by all consumers.
2. `save()` in Settings → sidebar greeting updates within the same
   tick.
3. Existing call sites keep their current return-shape contract — no
   widespread refactor of `UserCard` / `ThreadBody`.
4. The minimal-shape callers (`useMyProfile`) keep using a minimal
   shape so we don't accidentally widen their API surface.

## Non-goals

- A general-purpose React Query / SWR layer.
- Cross-tab sync (separate concern; storage events).
- Optimistic UI changes beyond what `useMutableRecord` already does.

## Design

Add a `UserProfileProvider` context at the auth boundary so every
consumer reads from one `useMutableRecord` instance.

```
App
└── AuthProvider                       (existing — owns identity/bearer)
    └── UserProfileProvider            (new — owns useMutableRecord)
        └── …all other features
```

### Provider

```tsx
// features/me/UserProfileContext.tsx
const UserProfileContext = createContext<UserProfileState | null>(null);

export function UserProfileProvider({ children }: { children: ReactNode }) {
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

function useUserProfileState(): UserProfileState {
  const ctx = useContext(UserProfileContext);
  if (!ctx) {
    throw new Error(
      "UserProfileProvider missing — wrap the app at the auth boundary.",
    );
  }
  return ctx;
}
```

### Public hooks

```ts
// features/me/useUserProfile.ts
export type UserProfileState = MutableRecordState<
  UserProfile,
  UpdateUserProfileRequest
>;
export const useUserProfile = useUserProfileState;
```

```ts
// features/auth/useMyProfile.ts
export interface ProfileSnapshot {
  display_name: string | null;
  email: string;
}

export function useMyProfile(): ProfileSnapshot | null {
  const { data } = useUserProfileState();
  if (data === null) return null;
  return { display_name: data.display_name, email: data.email };
}
```

Both hooks now read from the same context value. `save()` updates the
shared `data`; every subscriber re-renders.

### Identity gating

The provider mounts inside `AuthContext`'s authenticated branch (per
`App.tsx`), so when there is no session there is no provider, and
`useUserProfileState()` throws if called outside the gate. This matches
the current model — `useUserProfile` already assumes a bearer exists.

For the pre-auth surfaces (LoginScreen, dev IdP fallback), no caller
touches profile state, so no change needed.

### `setData` for non-save callers

`useMutableRecord` returns a `setData` setter — kept on the context
state for the rare consumer that wants to patch fields without a
network round-trip (e.g. an inline rename optimisation later). No
caller needs it today.

## Migration

One PR, three small edits:

1. **Create** `features/me/UserProfileContext.tsx` with the provider +
   hook.
2. **Mount** the provider in `App.tsx` inside the authenticated branch
   (right after `AuthProvider`'s authenticated render).
3. **Replace** the bodies of `useMyProfile` and `useUserProfile` to
   read from the context. Delete the per-hook `useMutableRecord` call.

Call sites do not change. `UserCard` and `ThreadBody` keep importing
`useMyProfile`; `Profile.tsx` and `Appearance.tsx` keep importing
`useUserProfile`. Both now share one cache.

## Validation

- `npm run typecheck`, `npm run build`.
- Manual: open Settings → change display name → save → confirm sidebar
  greeting updates in the same tick without reload.
- Existing tests for `UserCard`, `ThreadBody`, `Profile`, `Appearance`
  pass unchanged.

## Risks

- The provider re-renders every consumer whenever `data` changes. The
  data object is replaced by reference on every `save`, so React's
  default subscription model is fine; consumers re-renders are cheap.
- One context for one record — if we add more shared records (workspace,
  preferences) we should consider whether each gets its own context or
  they share one composite store. Defer.

## Rollback

Single-file revert: restore `useMyProfile` to its own local fetcher and
remove the provider mount in `App.tsx`. The settings hook continues
working because it never lost its `useMutableRecord` call (only the
delegation changes are reverted).
