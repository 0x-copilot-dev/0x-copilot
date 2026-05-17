# PRD: Workspace state ownership & cross-invalidation

**Status:** Draft (advisory — no implementation required today)
**Owner:** Frontend platform
**Related audit:** [05-dry-audit.md §14](../05-dry-audit.md)

## Background

The audit flagged "workspace state split across three modules with no
shared cache." On closer reading, the split is intentional: each
endpoint owns a **different** slice of workspace-scoped settings.
There is no shared cache to build. What's actually missing is
**cross-invalidation** — when one slice changes, related slices don't
refetch.

## Current slices (each owned by exactly one hook / API call)

| Slice                                   | Endpoint                           | Hook / call site                                                              | Mutator                      |
| --------------------------------------- | ---------------------------------- | ----------------------------------------------------------------------------- | ---------------------------- |
| Branding (display_name, slug, metadata) | `PATCH /v1/workspace`              | [`useWorkspace`](../../src/features/settings/useWorkspace.ts)                 | `save(patch)`                |
| Members directory                       | `GET /v1/workspace/members`        | [`useWorkspaceMembers`](../../src/features/settings/useWorkspace.ts)          | `updateRole`, `removeMember` |
| Invitations                             | `GET /v1/workspace/invitations`    | [`useInvitations`](../../src/features/settings/useWorkspace.ts)               | `invite`, `revoke`           |
| Billing digest                          | `GET /v1/workspace/billing`        | [`useBilling`](../../src/features/settings/useWorkspace.ts)                   | (read-only)                  |
| Agent defaults (model, context limits)  | `GET /v1/agent/workspace/defaults` | [`useWorkspaceDefaults`](../../src/features/settings/useWorkspaceDefaults.ts) | `save(patch)`                |
| MFA policy (enforcement)                | `GET /v1/workspace/mfa-policy`     | [`api/workspaceMfaApi`](../../src/api/workspaceMfaApi.ts)                     | `updateWorkspaceMfaPolicy`   |

Each slice is read by exactly one Settings panel. None of these slices
read the same field as another. There is **no DRY violation** — there
is a fan-out of independent endpoints.

## What the audit actually saw

Three independent fetch paths with no shared invalidation channel.
The example: an admin enables MFA policy via `workspaceMfaApi`, the
workspace branding settings tab keeps its stale `useWorkspace`
snapshot, and a "MFA enforced" banner on the branding tab (if added
later) would lag until manual refresh.

Today the UI doesn't surface cross-slice dependencies, so the latent
bug doesn't manifest. It will the first time a feature reads from one
slice and reacts to a change in another.

## Why we are not bundling into a "workspace context" today

- Each hook owns one endpoint. A shared context that fetches all six
  would either over-fetch on every Settings open (most users open one
  tab) or re-derive the same per-tab gating.
- The DRY value would be **zero**: each sub-selector would still have
  to handle its own fetch, save, error, and refresh.
- Bundling under a context wouldn't fix invalidation either; the
  context would still hold N orthogonal `useMutableRecord` instances.

## What we should add when the first cross-slice consumer ships

A workspace-level invalidation event. Sketch:

```ts
// features/workspace/invalidation.ts
type WorkspaceSlice =
  | "branding"
  | "members"
  | "invitations"
  | "billing"
  | "defaults"
  | "mfa-policy";

const subscribers = new Set<(slice: WorkspaceSlice) => void>();

export function invalidateWorkspace(slice: WorkspaceSlice): void {
  for (const sub of subscribers) sub(slice);
}

export function useWorkspaceInvalidation(
  slices: readonly WorkspaceSlice[],
  refresh: () => Promise<void>,
): void {
  useEffect(() => {
    const onInvalidate = (slice: WorkspaceSlice) => {
      if (slices.includes(slice)) void refresh();
    };
    subscribers.add(onInvalidate);
    return () => {
      subscribers.delete(onInvalidate);
    };
  }, [slices, refresh]);
}
```

Each mutator calls `invalidateWorkspace("...")` after a successful
save, and any consumer that needs to react subscribes to the slices it
cares about. Costs nothing for slices that don't have cross-references
(every today's hook stays as-is).

When the first such consumer appears, ship this in the same PR.

## Invariants to preserve

1. **One endpoint per hook.** No hook fetches more than one slice.
2. **No global "workspace context."** The current six hooks are the
   correct boundary.
3. **Cross-slice reactions go through `invalidateWorkspace`** when we
   add it — never through ad-hoc event listeners on `window` or
   shared module-level state.

## Out of scope

- Multi-tab sync via `BroadcastChannel` or `storage` events.
- Optimistic mutation rollback (the current pattern is server-replaces-
  local on save success; failures leave the local snapshot stale until
  refresh).
- Pagination for members/invitations beyond what `useWorkspaceMembers`
  already does.
