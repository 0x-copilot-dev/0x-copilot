# PRD: `useResourceWithMutation` hook

**Status:** Draft → In implementation
**Owner:** Frontend platform
**Related audit:** [05-dry-audit.md §6](../05-dry-audit.md)

## Problem

`useResource<T>(identity, fetcher, fallback)` already gives us
`{ data, loading, error, refresh }` for read-only collections. But
when a hook needs `save(patch)` on top, or needs to fetch a single
record instead of a collection, callers bypass `useResource` entirely
and hand-roll the same 30 lines:

```ts
const [data, setData] = useState<T | null>(null);
const [loading, setLoading] = useState(true);
const [error, setError] = useState<string | null>(null);
const cancelledRef = useRef(false);
// ... fetchOnce + useEffect + save ...
```

This pattern is duplicated **9 times** (see audit §6). Each copy
re-derives StrictMode cancellation, error mapping, and save semantics,
which means each can subtly differ.

## Goals

1. One hook that handles the "fetch one record, then optionally
   mutate it" shape.
2. Replace inline copies in 9 hooks with a single import.
3. No behaviour change visible to callers.
4. Compose cleanly with `useResource` for the collection case (the
   new hook is **strictly an addition**, not a replacement).

## Non-goals

- Building a generic data-fetching cache (this is not React Query).
- Cross-hook invalidation / event bus (separate PRD if needed).
- Pagination / cursoring.
- Optimistic updates with rollback (current code is fire-and-forget
  with server snapshot replacement; keep that).

## Design

Two additions to `apps/frontend/src/api/useResource.ts`:

### 1. `useRecord<T>` — single-record loader (no mutation)

```ts
export interface RecordState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

/**
 * Loads a single record once on mount; exposes `refresh()` for tab
 * focus / manual reload. Cancellation-safe under React StrictMode.
 */
export function useRecord<T>(
  fetcher: () => Promise<T>,
  errorFallback: string,
): RecordState<T>;
```

### 2. `useMutableRecord<T, P>` — single-record loader + saver

```ts
export interface MutableRecordState<T, P> extends RecordState<T> {
  /** Apply a partial update; returns the saved snapshot or throws. */
  save: (patch: P) => Promise<T>;
}

/**
 * Same as `useRecord` plus a `save(patch)` that updates the local
 * snapshot from the server's response. Error fallback for save is
 * separate so call sites keep their copy ("Could not save policy"
 * vs "Could not load policy").
 */
export function useMutableRecord<T, P>(
  fetcher: () => Promise<T>,
  saver: (patch: P) => Promise<T>,
  fallbacks: { load: string; save: string },
): MutableRecordState<T, P>;
```

### Why two hooks, not one

- `useArchivedSources` only needs `{ data, loading, error, refresh,
restore }` — no `save`. Forcing it through `useMutableRecord` means
  passing a useless `saver`. Two hooks keep each call site honest.
- `useWorkspaceMembers` / `useInvitations` have multiple mutators
  (`invite`, `revoke`, `updateRole`) — they layer their own actions on
  top of `useRecord`, which returns `setData` via a stable callback
  (see below).

### Composition for hooks with multiple mutators

`useRecord` exposes a `setData` updater so a feature hook can build
its own actions:

```ts
export function useWorkspaceMembers(identity: RequestIdentity | null) {
  const { data, loading, error, refresh, setData } = useRecord(
    () => listMembers(identity),
    "Could not load members.",
  );

  const removeMember = useCallback(
    async (id: string) => {
      await deleteMember(identity, id);
      await refresh();
    },
    [identity, refresh],
  );

  // ...
  return { data, loading, error, refresh, removeMember, updateRole };
}
```

That gives us the "compose your own actions" path without baking
every mutator name into the generic hook.

### Identity gating

`useResource` accepts `identity: RequestIdentity | null` and short-
circuits when null. `useRecord` does **not** take identity — callers
that need it close over the identity in their fetcher and the hook
becomes "fetch when fetcher's deps change." This mirrors React's
own data-fetch idioms and avoids the "what does null mean?" trap
that complicates `useResource`. We may revisit later.

For hooks like `useArchivedSources` that need identity gating, they
pass a no-op fetcher when identity is null, or guard at the call
site. The two existing call sites
([useUserProfile](../../src/features/me/useUserProfile.ts) /
[useUserPreferences](../../src/features/me/useUserPreferences.ts))
don't need identity; the workspace hooks already accept identity and
gate before calling.

### StrictMode safety

The current copies use `cancelledRef` to drop late responses from
double-invoked effects. The new hook owns the ref so callers don't
write it. Tests pin this behaviour with a "double-mount drops first
result" case.

## Migration plan

Per file, one PR per group. All eight (counted in audit) plus
`MfaPanel`'s inline copy:

1. `useUserProfile.ts` — `useMutableRecord` (textbook fit).
2. `useUserPreferences.ts` — `useMutableRecord` (textbook fit).
3. `useArchivedSources.ts` — `useRecord` + `restore()` action.
4. `useWorkspace.ts` `useWorkspace` — `useMutableRecord`.
5. `useWorkspace.ts` `useWorkspaceMembers` — `useRecord` + actions.
6. `useWorkspace.ts` `useInvitations` — `useRecord` + actions.
7. `useWorkspace.ts` `useBilling` — `useRecord`.
8. `useMcpCatalog.ts` — `useRecord` (renames internal `entries` →
   `data`; consumers updated in same PR).
9. `MfaPanel.tsx` — inline fetcher → `useRecord`. Component-local but
   the same shape; lift to a hook if a second consumer appears.

Each migration is one file. Cancellation tests added once on the hook
itself; per-call-site tests stay focused on domain behaviour.

## Validation

- `npm run typecheck`, `npm run build`.
- New unit tests on the hooks: load happy-path, load error, save
  happy-path, save error, StrictMode double-mount cancellation,
  refresh after save.
- Run existing tests for each migrated hook unchanged.

## Risks

- The current copies all call their fetcher with **no arguments** in
  `useEffect`, captured via closure. The new hook does the same, but
  any caller that needed `useEffect`-deps-driven refetch (e.g. on
  identity change) must switch to `useResource` (collections) or rely
  on `refresh()` from the parent on identity change. Audit each call
  site for this — `useUserProfile` and friends fetch a global record,
  so they're fine.
- Error fallback strings change shape (one string → `{ load, save }`).
  Trivial mechanical edit.

## Rollback

Single-file revert per migration. The two new hooks are additive — old
behaviour is reachable by reverting the per-file change without
reverting the hook itself.
