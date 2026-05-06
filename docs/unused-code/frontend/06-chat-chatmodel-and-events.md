# Cluster: Chat model, reducers, and event pipeline

**Paths:** `apps/frontend/src/features/chat/chatModel/`, [`chatRunState.ts`](../../../apps/frontend/src/features/chat/chatRunState.ts), [`chatModel.ts`](../../../apps/frontend/src/features/chat/chatModel.ts), [`mcpAuthAction.ts`](../../../apps/frontend/src/features/chat/mcpAuthAction.ts), [`approval/`](../../../apps/frontend/src/features/chat/approval/)  
**Last reviewed:** 2026-05-06

## Scope

Pure-ish domain logic: event reducers, citations/subagents/sources, MCP auth shaping, approval helpers, payload/metadata helpers, presentation transforms.

## Unused / ts-prune signals

Most rows from `ts-prune` in this tree are **internal helpers** marked `(used in module)` — `replaceToolCallPart`, `upsertActivityRecord`, MCP matchers, etc. Those are normal for a reducer-heavy module.

| Symbol                       | File                    | Assessment                                                                   |
| ---------------------------- | ----------------------- | ---------------------------------------------------------------------------- |
| `resolveQuestionFromPayload` | `chatModel/approval.ts` | Internal to approval flows — verify no external API promise before deleting. |

No entire `.ts` files under `chatModel/` were identified as unimported.

## Smells

- **Reducer surface area** — Multiple parallel reducers (`sourcesReducer`, `subagentReducer`, citation stores) interact with workspace hooks; duplicated “seed from GET + live SSE” patterns appear in both [`useArchivedSources`](../../../apps/frontend/src/features/chat/components/workspace/useArchivedSources.ts) / [`useSubagents`](../../../apps/frontend/src/features/chat/components/workspace/useSubagents.ts) and older hooks under `features/chat/hooks/` (see [08-chat-hooks-prompts-utils-markdown.md](./08-chat-hooks-prompts-utils-markdown.md)).
- **LargeArtifact / MCP auth** — Specialized branches for large results and MCP approval wrapping increase cognitive load; changes should stay paired with integration tests ([`AssistantMessage.integration.test.tsx`](../../../apps/frontend/src/features/chat/AssistantMessage.integration.test.tsx), agent SSE fixtures).

## Confidence

**Low** for dead modules in `chatModel/`; primary findings live in hooks/UI overlap rather than orphan reducers.
