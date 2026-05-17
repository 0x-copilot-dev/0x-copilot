# PRD: Cross-invalidation for connector state

**Status:** Implemented (closed)
**Owner:** Frontend platform
**Related audit:** [05-dry-audit.md §15](../05-dry-audit.md)

## Problem

Two hooks own different views of connector state:

- [`useConnectors`](../../src/features/connectors/useConnectors.ts) — workspace-installed servers (`GET /v1/mcp/servers`).
- [`useConversationConnectors`](../../src/features/connectors/useConversationConnectors.ts) — per-chat enabled subset (`PATCH /v1/agent/conversations/{id}/connectors`).

When an admin disabled connector X via `useConnectors.setEnabled()`,
the in-chat connector popover (`useConversationConnectors`) kept
offering X's tools until the user switched chats or the tab regained
visibility. The two hooks never observed each other's mutations
in-tab.

## Goal

When workspace-connector state changes, the per-chat connector hook
refetches the conversation and re-derives its scopes — within the
same tab, same React tree, no reload.

## Non-goals

- Cross-tab sync (BroadcastChannel / storage events).
- A general workspace event bus (see PRD 07 for the workspace-state
  precedent we deliberately are not building yet).
- Optimistic update across both hooks. The fix is reconciliation, not
  optimism.

## Design

A tiny module-level pub/sub at
[`features/connectors/invalidation.ts`](../../src/features/connectors/invalidation.ts):

```ts
export function notifyWorkspaceConnectorsChanged(): void { … }
export function useWorkspaceConnectorsChanged(listener: () => void): void { … }
```

- `useConnectors` calls `notifyWorkspaceConnectorsChanged()` after any
  successful mutation (`addServer`, `installFromCatalog`,
  `removeServer`, `setEnabled`, `setDisplayName`, `skipAuth`).
- `useConversationConnectors` subscribes via
  `useWorkspaceConnectorsChanged(reconcileFromServer)`. The existing
  `reconcileFromServer` logic (extracted from the visibility-change
  branch) refetches the conversation and applies server scopes when
  `connectors_updated_at` advances.

### Why module-level pub/sub

The publisher and subscriber live in different React subtrees:
workspace settings panel ↔ chat composer popover. Threading a context
through both means a context that spans the entire authenticated app
just to carry one notification. A module-level `Set<Listener>` is
~20 lines and has no provider requirement.

If we later add a second slice (workspace skills, workspace API keys),
we can generalise to a typed slice channel (see PRD 07 sketch). Until
then, one channel for connectors is enough.

### Why we don't lift to a shared cache

The two hooks fetch different endpoints with different lifecycles
(workspace-global vs conversation-scoped). A shared cache would double
the work: each hook would still need its own selector, error handling,
and refresh trigger. Reconciliation is cheaper than re-architecture.

## Validation

- `npm run typecheck`, `npm run build`.
- Manual repro: open chat with connector X scope active → open
  Settings → Connectors in a side tab → toggle X off → return to chat
  → connector popover no longer offers X without a reload.

## Risks

- **Spurious refetches.** Every connector mutation triggers a
  conversation GET in every open chat. Conversation loads are cheap
  and a chat normally has one active hook instance, so the cost is
  one extra GET per mutation per active chat.
- **Race with in-flight PATCH.** `reconcileFromServer` guards on
  `loadingRef.current` so a workspace-level invalidation that lands
  mid-PATCH is dropped. The PATCH success path will replace `scopes`
  from the server response anyway.

## Rollback

Single-file revert: remove `invalidation.ts` imports from both hooks
(or delete the file). Each hook reverts to its previous independent
behaviour — the visibility-change reconciliation already covered the
tab-switch case.
