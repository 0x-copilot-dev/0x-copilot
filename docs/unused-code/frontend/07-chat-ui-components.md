# Cluster: Chat UI components

**Path:** `apps/frontend/src/features/chat/components/`  
**Last reviewed:** 2026-05-06

## Scope

Thread, composer, sidebar, shell/topbar, workspace pane/tabs, activity cards, details panels, tool renderers, markdown plugins, results summaries.

## Candidate dead code

| Symbol / module                                                                                                  | Location                                       | Confidence | Notes                                                                                                                                                                                                                                                                                                         |
| ---------------------------------------------------------------------------------------------------------------- | ---------------------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`LargeToolResultNotice`](../../../apps/frontend/src/features/chat/components/results/LargeToolResultNotice.tsx) | `components/results/LargeToolResultNotice.tsx` | **High**   | No imports anywhere in `apps/frontend`. Large-result UX appears inlined elsewhere (e.g. [`summarize.tsx`](../../../apps/frontend/src/features/chat/components/results/summarize.tsx) `safeMainResultSummary` string path). **Candidate deletion** or wire-up if design intended a dedicated banner component. |

## ts-prune: barrel (`shell/index.ts`)

`ts-prune` lists every symbol re-exported from [`components/shell/index.ts`](../../../apps/frontend/src/features/chat/components/shell/index.ts) as unused. **[`ChatScreen.tsx`](../../../apps/frontend/src/features/chat/ChatScreen.tsx)** imports `{ Topbar, activeConnectorsFromScopes } from "./components/shell"`, so those rows are **false positives** for at least the imports actually used from the barrel.

**Verification:** prefer ripgrep over raw ts-prune output for barrel files:

```bash
rg 'from ["\047].*components/shell' apps/frontend/src
```

Other shell exports (`Crumb`, `UsageMeter`, …) may still be imported **directly** from sibling files (e.g. `./shell/Crumb`) elsewhere — check before deleting exports.

## Citations context

| Export                                                                                                                                                                                                       | Status                                                                                                                                                                                                                        |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`useCitation`](../../../apps/frontend/src/features/chat/components/citations/citationsContext.tsx), [`useRunCitations`](../../../apps/frontend/src/features/chat/components/citations/citationsContext.tsx) | **Used** — [`CitationChip.tsx`](../../../apps/frontend/src/features/chat/components/citations/CitationChip.tsx), [`AssistantMessage.tsx`](../../../apps/frontend/src/features/chat/components/messages/AssistantMessage.tsx). |
| [`useCitations`](../../../apps/frontend/src/features/chat/components/citations/citationsContext.tsx)                                                                                                         | **Unused** — returns full active map; no production importer. **Candidate removal** if no planned consumer (or keep as ergonomic alias with a lint ignore).                                                                   |

## Smells

- **Workspace pane composition** — Tabs (`SourcesTab`, `AgentsTab`, …) are presentational; state lives in [`ChatScreen.tsx`](../../../apps/frontend/src/features/chat/ChatScreen.tsx) + workspace hooks. Good separation, but large props drilling — watch for stale props when refactoring.
- **Results folder growth** — `summarize.tsx`, `McpResultList`, `SearchSourceList`, and optional `LargeToolResultNotice` overlap conceptually; consolidating large-result handling could drop dead components.

## Confidence

**High** on `LargeToolResultNotice` and `useCitations` being unused at this revision; **medium** on barrel false positives.
