# Cluster: Chat hooks, prompts, utils, markdown

**Paths:** `apps/frontend/src/features/chat/hooks/`, `prompts/`, `utils/`, [`markdownLinks.ts`](../../../apps/frontend/src/features/chat/markdownLinks.ts)  
**Last reviewed:** 2026-05-06

## Hooks

| Hook                                                                                                     | Production usage                                                                                                                         | Notes                                                                                                                                                                                                                                                                                                                                          |
| -------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`useConversationSources`](../../../apps/frontend/src/features/chat/hooks/useConversationSources.ts)     | **None found**                                                                                                                           | Implements seed + live `source_ingested` aggregation per comments (“Workspace pane Sources tab”). **Superseded in practice** by [`useArchivedSources`](../../../apps/frontend/src/features/chat/components/workspace/useArchivedSources.ts) + reducer wiring from [`ChatScreen.tsx`](../../../apps/frontend/src/features/chat/ChatScreen.tsx). |
| [`useConversationSubagents`](../../../apps/frontend/src/features/chat/hooks/useConversationSubagents.ts) | **Tests only** ([`useConversationSubagents.test.tsx`](../../../apps/frontend/src/features/chat/hooks/useConversationSubagents.test.tsx)) | Production uses [`useSubagents`](../../../apps/frontend/src/features/chat/components/workspace/useSubagents.ts) from `ChatScreen`.                                                                                                                                                                                                             |

**Recommendation:** Either delete the two `hooks/useConversation*.ts` files and migrate tests to the workspace hooks, or clearly mark as experimental and exclude from coverage targets until wired.

## Prompts (`prompts/index.ts`)

| Export                                                           | Usage                                                                                                         |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `CHAT_PROMPT_SUGGESTIONS`, `REGENERATE_PREVIOUS_RESPONSE_PROMPT` | [`ChatScreen.tsx`](../../../apps/frontend/src/features/chat/ChatScreen.tsx)                                   |
| `mcpServerInstructionPrompt`, `skillInstructionPrompt`           | [`AssistantComposer.tsx`](../../../apps/frontend/src/features/chat/components/composer/AssistantComposer.tsx) |

ts-prune may still flag prompt helpers as unused because of analysis quirks — **verify with ripgrep** before removing.

## Utils — unused exports (high confidence)

| Symbol                                                                                                   | File                      | Notes                                                                                               |
| -------------------------------------------------------------------------------------------------------- | ------------------------- | --------------------------------------------------------------------------------------------------- |
| [`hasImportantSubagentActivity`](../../../apps/frontend/src/features/chat/utils/activityDataBuilders.ts) | `activityDataBuilders.ts` | No importers; helper for filtering noisy subagent rows — candidate delete or wire into activity UI. |
| [`mcpToolTitle`](../../../apps/frontend/src/features/chat/utils/toolLabels.ts)                           | `toolLabels.ts`           | No references outside definition file.                                                              |
| [`toolActivityTitle`](../../../apps/frontend/src/features/chat/utils/toolLabels.ts)                      | `toolLabels.ts`           | Same.                                                                                               |

Other exports in `toolLabels.ts` **are** used (e.g. `safeVisibleText`, `toolDisplayName` from [`summarize.tsx`](../../../apps/frontend/src/features/chat/components/results/summarize.tsx)).

## Markdown

[`markdownLinks.ts`](../../../apps/frontend/src/features/chat/markdownLinks.ts) — exercised by [`markdownLinks.test.ts`](../../../apps/frontend/src/features/chat/markdownLinks.test.ts); trace callers from markdown plugins before declaring unused.

## Smells

- **Parallel hook implementations** — `hooks/useConversation*` vs `components/workspace/use*` duplicates responsibility for “conversation opened → GET seed → SSE merge”. Pick one architecture and delete the other to reduce drift.
- **Export sprawl in `toolLabels.ts`** — Large single module; unused exports suggest incomplete refactors or abandoned UI paths.

## Confidence

**High** for unused utils exports and orphaned `useConversationSources`; **high** for `useConversationSubagents` being test-only relative to production wiring.
