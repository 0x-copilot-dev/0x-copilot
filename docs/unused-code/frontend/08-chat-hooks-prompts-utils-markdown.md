# Cluster: Chat hooks, prompts, utils, markdown

**Paths:** `apps/frontend/src/features/chat/hooks/`, `prompts/`, `utils/`, [`markdownLinks.ts`](../../../apps/frontend/src/features/chat/markdownLinks.ts)  
**Last reviewed:** 2026-05-06

## Hooks

_**RESOLVED at `a78bfc0`.**_ `useConversationSources.ts` and `useConversationSubagents.ts` (plus its test) were deleted. Production paths continue to use [`useArchivedSources`](../../../apps/frontend/src/features/chat/components/workspace/useArchivedSources.ts) and [`useSubagents`](../../../apps/frontend/src/features/chat/components/workspace/useSubagents.ts).

## Prompts (`prompts/index.ts`)

| Export                                                           | Usage                                                                                                         |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `CHAT_PROMPT_SUGGESTIONS`, `REGENERATE_PREVIOUS_RESPONSE_PROMPT` | [`ChatScreen.tsx`](../../../apps/frontend/src/features/chat/ChatScreen.tsx)                                   |
| `mcpServerInstructionPrompt`, `skillInstructionPrompt`           | [`AssistantComposer.tsx`](../../../apps/frontend/src/features/chat/components/composer/AssistantComposer.tsx) |

ts-prune may still flag prompt helpers as unused because of analysis quirks — **verify with ripgrep** before removing.

## Utils — unused exports

_**RESOLVED at `a78bfc0`.**_ `hasImportantSubagentActivity`, `mcpToolTitle`, and `toolActivityTitle` were deleted; nothing imported them.

Other exports in `toolLabels.ts` remain in use (e.g. `safeVisibleText`, `toolDisplayName` from [`summarize.tsx`](../../../apps/frontend/src/features/chat/components/results/summarize.tsx)).

## Markdown

[`markdownLinks.ts`](../../../apps/frontend/src/features/chat/markdownLinks.ts) — exercised by [`markdownLinks.test.ts`](../../../apps/frontend/src/features/chat/markdownLinks.test.ts); trace callers from markdown plugins before declaring unused.

## Smells

- **Export sprawl in `toolLabels.ts`** — Large single module; some exports remain only because they're consumed by sibling files. Worth a focused trim if the module grows further.

## Confidence

**High** at the audited revision; the orphan implementations and unused utility exports were removed at `a78bfc0`.
