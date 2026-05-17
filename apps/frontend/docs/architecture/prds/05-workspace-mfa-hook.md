# PRD 05: Workspace MFA policy — hook for symmetry

**Status:** Draft → In implementation
**Owner:** Frontend platform
**Related audit:** [05-dry-audit.md §14](../05-dry-audit.md)

## Problem (re-scoped)

The audit flagged the three-way split between `useWorkspace`,
`useWorkspaceDefaults`, and `workspaceMfaApi` as a multi-source-of-truth
risk. On close reading there is **no shared state** between the three —
each owns a disjoint slice of workspace configuration (branding /
defaults / MFA policy). Merging them would conflate three independent
admin surfaces under one cache for no benefit.

What **is** a real defect is the **asymmetry**: `useWorkspace` and
`useWorkspaceDefaults` are hook-shaped (loading / error / refresh /
save), but the MFA policy is consumed by `WorkspaceMfaSettings.tsx`
through bare `useState` + `useEffect` + direct `workspaceMfaApi` calls.
That asymmetry guarantees that any pattern improvement we make to the
hook layer (cancellation, refresh-on-focus, optimistic updates) lands
in 2/3 of the surface and skips MFA.

## Goals

1. Bring MFA policy to the same hook shape as the other two workspace
   slices: `{ policy, loading, error, save, refresh }`.
2. Eliminate the hand-rolled `useState` + `useEffect` + fetch dance in
   `WorkspaceMfaSettings.tsx`.
3. Use the canonical `useMutableRecord` from
   [api/useResource.ts](../../src/api/useResource.ts), the same hook
   `useUserProfile` / `useUserPreferences` / `useWorkspace` already
   delegate to.

## Non-goals

- Merging the three workspace slices. They cover disjoint fields.
- Adding cross-slice invalidation. No two slices share a field; no
  cross-slice drift is possible by construction.
- Changing the wire shape or admin permission model.

## Design

Add `features/settings/useWorkspaceMfaPolicy.ts`:

```ts
export type UseWorkspaceMfaPolicyResult = MutableRecordState<
  WorkspaceMfaPolicy,
  UpdateWorkspaceMfaPolicyRequest
>;

export function useWorkspaceMfaPolicy(): UseWorkspaceMfaPolicyResult {
  return useMutableRecord(getWorkspaceMfaPolicy, updateWorkspaceMfaPolicy, {
    load: "Could not load MFA policy.",
    save: "Could not save MFA policy.",
  });
}
```

`WorkspaceMfaSettings.tsx` becomes a thin form bound to that hook:
the optimistic update + error rollback are the hook's job, not the
component's.

After this PR, all three workspace surfaces are hook-shaped:

| Slice      | Endpoint                       | Hook                    |
| ---------- | ------------------------------ | ----------------------- |
| Branding   | `/v1/workspace`                | `useWorkspace`          |
| Defaults   | `/v1/agent/workspace/defaults` | `useWorkspaceDefaults`  |
| MFA policy | `/v1/workspace/mfa-policy`     | `useWorkspaceMfaPolicy` |

## Migration

1. Add `features/settings/useWorkspaceMfaPolicy.ts` (~10 lines).
2. Update `WorkspaceMfaSettings.tsx`:
   - Drop the local `useState` for `mfaRequired` / `stepUp` /
     `loading` / `busy` / `error` / `savedAt`.
   - Read `{ data, loading, error, save }` from
     `useWorkspaceMfaPolicy()`.
   - Local form-draft state remains (the user types into Step-Up
     before saving), but loading / hydration / save-error / save-busy
     come from the hook.

## Validation

- `npm run typecheck`
- `npx vitest run` from `apps/frontend/`
- Manual: load Settings → Workspace → MFA as an admin and as a
  non-admin (403 path).

## Risks

- WorkspaceMfaSettings's `savedAt` confetti / toast — if any —
  triggers off local state, not the hook. Preserve that behaviour by
  setting a local `savedAt` after a successful `save()` resolves.
- Non-admin 403 path: today the section catches the 403 and shows a
  read-only message. `useMutableRecord` surfaces the error in
  `result.error`; the component renders the same read-only state when
  `error` is non-null AND `data` is null.

## Rollback

Single-file revert.
