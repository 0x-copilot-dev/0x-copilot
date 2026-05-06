# Knip pass — full unused surface (frontend)

**Tool:** [knip](https://github.com/webpro-nl/knip) (v6.x via `npx knip` from repo root, cwd `apps/frontend`).  
**Last reviewed:** 2026-05-06  
**Git revision:** `a1d79d7a61868a6a9ae774e3a46c875356b29b78`

This document captures output from a **second pass** after the initial cluster notes. Knip finds substantially more than `ts-prune` alone because it also reports **unused npm dependencies**, **files nothing imports**, and **exports that no other file imports** — including symbols that are still **used inside their declaring module** (those are **export hygiene**, not necessarily dead logic).

## How to regenerate

```bash
cd apps/frontend && npx knip
```

Knip exits non-zero when issues exist; that is expected.

---

## 1. Unused files (hard orphans)

These modules are **not reachable** from Knip’s entrypoint analysis (see knip’s default: `package.json` + `tsconfig` + common entry files). **Safe deletion candidates** after a final `rg` for dynamic import / string path references.

| File                                                                                                                                                      | Notes                                                                                                                                                                |
| --------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`src/api/sessionApi.ts`](../../../apps/frontend/src/api/sessionApi.ts)                                                                                   | Legacy `GET /v1/session` client; nothing imports it. Overlaps with [`authApi` + `AuthContext`](../../../apps/frontend/src/features/auth/AuthContext.tsx).            |
| [`src/features/chat/components/results/LargeToolResultNotice.tsx`](../../../apps/frontend/src/features/chat/components/results/LargeToolResultNotice.tsx) | Component never imported.                                                                                                                                            |
| [`src/features/chat/hooks/useConversationSources.ts`](../../../apps/frontend/src/features/chat/hooks/useConversationSources.ts)                           | Hook never imported; workspace path uses [`useArchivedSources`](../../../apps/frontend/src/features/chat/components/workspace/useArchivedSources.ts) + `ChatScreen`. |

**Not listed as unused files** but still production-disconnected: [`useConversationSubagents.ts`](../../../apps/frontend/src/features/chat/hooks/useConversationSubagents.ts) — only its test file imports it; knip still sees the module as “used” via the test. Treat as **test-only / orphan implementation** (see [08](./08-chat-hooks-prompts-utils-markdown.md)).

---

## 2. Unused dependencies (package.json)

| Package                         | Location       | Action                                                                                                                                                                   |
| ------------------------------- | -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `@opentelemetry/sdk-trace-base` | `package.json` | **No `import` in `src/`** at this revision — candidate removal from dependencies, or wire into [`otel.ts`](../../../apps/frontend/src/observability/otel.ts) if planned. |

---

## 3. Unlisted dependencies (import without package.json entry)

Knip reports imports that resolve **transitively** (often via other `@opentelemetry/*` packages) or belong in devDependencies. Treat as **dependency hygiene / CI smell**, not dead application code.

| Package                                       | Example import sites                                                                                                        |
| --------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `@opentelemetry/instrumentation`              | [`otel.ts`](../../../apps/frontend/src/observability/otel.ts)                                                               |
| `unified`, `unist-util-visit`, `mdast`        | [`citationRemarkPlugin.ts`](../../../apps/frontend/src/features/chat/components/markdown/citationRemarkPlugin.ts)           |
| `unified`, `remark-parse`, `remark-stringify` | [`citationRemarkPlugin.test.ts`](../../../apps/frontend/src/features/chat/components/markdown/citationRemarkPlugin.test.ts) |

**Remediation options:** add explicit `dependencies` / `devDependencies`, or configure knip `ignoreDependencies` for known re-exports after locking versions in `package.json`.

---

## 4. Unused exports — values (46)

No other file **imports** these **exported** names. Subclasses:

### 4a. “Strip `export` only” (likely still used **inside** the same file)

Knip does not distinguish “private helper wrongly marked `export`” from “dead function.” Spot-check before deleting **logic**.

Examples strongly suspected to be **internal-only helpers** (grep for same-file usage):

| Symbol                                                                                                                   | Module                                                                                                                                                                                                             |
| ------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `UnauthorizedError`, `newRequestId`                                                                                      | [`http.ts`](../../../apps/frontend/src/api/http.ts)                                                                                                                                                                |
| `resolveQuestionFromPayload`                                                                                             | [`approval.ts`](../../../apps/frontend/src/features/chat/chatModel/approval.ts)                                                                                                                                    |
| `replaceToolCallPart`, `replaceFirstMatchingToolPart`, `upsertPart`, `upsertActivityRecord`                              | [`contentBuilders.ts`](../../../apps/frontend/src/features/chat/chatModel/contentBuilders.ts)                                                                                                                      |
| `isLargeResultArtifactToolName`, `hasLargeResultPath`, `hasLargeResultReference`                                         | [`largeArtifact.ts`](../../../apps/frontend/src/features/chat/chatModel/largeArtifact.ts)                                                                                                                          |
| `removeRedundantMcpAuthWrappers`, `resolveAuthenticatedMcpPart`, `mcpAuthPartMatchesServer`, `mcpAuthPayloadMatchesArgs` | [`mcpAuth.ts`](../../../apps/frontend/src/features/chat/chatModel/mcpAuth.ts)                                                                                                                                      |
| `metadataFromCustom`, `performanceMetricsFromRecord`, `timingFromPerformanceMetrics`                                     | [`metadata.ts`](../../../apps/frontend/src/features/chat/chatModel/metadata.ts)                                                                                                                                    |
| `inlineSummary`                                                                                                          | [`recordHelpers.ts`](../../../apps/frontend/src/features/chat/chatModel/recordHelpers.ts)                                                                                                                          |
| `meaningfulSubagentName`, `truncateText`                                                                                 | [`subagentText.ts`](../../../apps/frontend/src/features/chat/chatModel/subagentText.ts)                                                                                                                            |
| `subagentActivityRecord`                                                                                                 | [`activityDataBuilders.ts`](../../../apps/frontend/src/features/chat/utils/activityDataBuilders.ts)                                                                                                                |
| `largeToolResultText`, `largeToolResultPath`                                                                             | [`jsonUtils.ts`](../../../apps/frontend/src/features/chat/utils/jsonUtils.ts) (used by other functions in same module)                                                                                             |
| `isProjectSearchTool`                                                                                                    | [`toolLabels.ts`](../../../apps/frontend/src/features/chat/utils/toolLabels.ts)                                                                                                                                    |
| `hasComplexToolArgs`, `hasComplexToolResult`, `hasRichToolResult`, `summarizeArgs`                                       | [`toolResultAnalysis.ts`](../../../apps/frontend/src/features/chat/utils/toolResultAnalysis.ts)                                                                                                                    |
| `runIdFromMcpAuthApprovalId`                                                                                             | [`mcpAuthAction.ts`](../../../apps/frontend/src/features/chat/mcpAuthAction.ts)                                                                                                                                    |
| `passthroughMemberLoader`                                                                                                | [`WorkspaceMemberPicker.tsx`](../../../apps/frontend/src/features/chat/components/tools/WorkspaceMemberPicker.tsx)                                                                                                 |
| `CITATION_HREF_PREFIX`                                                                                                   | [`CitationChip.tsx`](../../../apps/frontend/src/features/chat/components/citations/CitationChip.tsx)                                                                                                               |
| `ACCENT_FILL`                                                                                                            | [`UsageWorkspaceChart.tsx`](../../../apps/frontend/src/features/chat/components/details/usage/UsageWorkspaceChart.tsx)                                                                                             |
| `asUserRow`                                                                                                              | [`usageWorkspaceData.ts`](../../../apps/frontend/src/features/chat/components/details/usage/usageWorkspaceData.ts)                                                                                                 |
| `SAFE_ATTRIBUTE_KEYS`                                                                                                    | [`otel.ts`](../../../apps/frontend/src/observability/otel.ts)                                                                                                                                                      |
| `Crumb`, `ConversationTitle`, `ConnectorsPill`, `UsageMeter`, `ModelPill`, `ThinkingDepthControl` re-exports             | [`shell/index.ts`](../../../apps/frontend/src/features/chat/components/shell/index.ts) — other files may import from **concrete files** (e.g. `./shell/Crumb`) instead of the barrel; knip still flags re-exports. |

### 4b. “True external unused API” (no importers; consider delete or use)

| Symbol                              | Module                                                                                                       | Notes                                                                                                                                                                                                                                                         |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `useCitations`                      | [`citationsContext.tsx`](../../../apps/frontend/src/features/chat/components/citations/citationsContext.tsx) | `useCitation` / `useRunCitations` are the live hooks.                                                                                                                                                                                                         |
| `hasImportantSubagentActivity`      | [`activityDataBuilders.ts`](../../../apps/frontend/src/features/chat/utils/activityDataBuilders.ts)          | No callers.                                                                                                                                                                                                                                                   |
| `mcpToolTitle`, `toolActivityTitle` | [`toolLabels.ts`](../../../apps/frontend/src/features/chat/utils/toolLabels.ts)                              | No callers.                                                                                                                                                                                                                                                   |
| `_resetForTests`                    | [`otel.ts`](../../../apps/frontend/src/observability/otel.ts)                                                | No test imports.                                                                                                                                                                                                                                              |
| `SETTINGS_SECTIONS`                 | [`useSettingsSection.ts`](../../../apps/frontend/src/features/settings/useSettingsSection.ts)                | **Exported** but no other file imports the constant; [`App.tsx`](../../../apps/frontend/src/app/App.tsx) duplicates a parallel `settingsSections` array in comments only. **Drift risk** — better: import `SETTINGS_SECTIONS` in one place or stop exporting. |

---

## 5. Unused exports — types (85)

Knip flags **exported** `interface` / `type` names that no other file imports **by name**. In a closed app (no published component library), this is often:

- **React `*Props` interfaces** on components (useful for refactors; not “delete” unless unused in the same file).
- **Hook return types** (`UseWorkspaceResult`, etc.) never imported for typing.
- **Context value types** (`AuthContextValue`, …) consumed through `useAuth()` inference.

**Interpretation:** these are **low-severity** for product behavior. Triage: stop exporting, or add `// @public` / knip ignore rules for intentional public API.

---

## 6. Why the first audit felt “smaller”

- **`ts-prune`** + manual triage under-counted **file-level** orphans and **dependency** issues.
- **Barrel re-exports** and **over-exported** `chatModel` helpers dominate the knip list; most are **not** deletions, they are **removing `export`**.
- **Type exports** inflate the count without meaning 85 types are dead code.

For a **deletion-oriented** cleanup: start with **§1 unused files** + **§2 unused dependency** + **§4b** symbols, then do a **mechanical un-export** pass for **§4a** after verifying same-file use.
