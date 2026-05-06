# Cluster: Connectors (MCP)

**Path:** `apps/frontend/src/features/connectors/`  
**Last reviewed:** 2026-05-06

## Scope

- [`useConnectors.ts`](../../../apps/frontend/src/features/connectors/useConnectors.ts) — workspace-scoped MCP registry for settings/chat.
- [`useConversationConnectors.ts`](../../../apps/frontend/src/features/connectors/useConversationConnectors.ts) — per-conversation connector scopes for composer UI.
- [`ConnectorPopover.tsx`](../../../apps/frontend/src/features/connectors/ConnectorPopover.tsx), [`projectConnectors.ts`](../../../apps/frontend/src/features/connectors/projectConnectors.ts).

## Unused / ts-prune signals

| Symbol                                               | File                           | Notes                                                                                |
| ---------------------------------------------------- | ------------------------------ | ------------------------------------------------------------------------------------ |
| `ConnectorPopoverPlacement`, `ConnectorPopoverProps` | `ConnectorPopover.tsx`         | Props/placement types — `(used in module)` pattern.                                  |
| `ConnectorRowState`                                  | `projectConnectors.ts`         | Exported type for row state machines; verify external imports if tightening exports. |
| `ConversationConnectorScopeState`                    | `useConversationConnectors.ts` | Same pattern.                                                                        |

No files in this cluster appeared **unreferenced** from `App.tsx`, chat, or settings flows.

## Smells

- **Two connector hooks** — `useConnectors` vs `useConversationConnectors` split is intentional (workspace catalog vs thread scopes); naming is the main onboarding cost, not duplication.
- **OAuth callback coupling** — Completion paths are coordinated from [`App.tsx`](../../../apps/frontend/src/app/App.tsx); ensure new connector flows register cleanup in the same place to avoid orphaned pending actions.

## Confidence

**Low** for dead production code; typical ts-prune noise on component prop types.
