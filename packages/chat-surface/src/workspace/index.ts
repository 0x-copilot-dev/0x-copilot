// Phase 1 (PR-1.7) workspace pane presentation family.
//
// Hoisted from apps/frontend so web and desktop render the right-rail pane
// identically. Composition shell + five tab bodies + the tablist. The host
// keeps every data-binding hook (`useWorkspacePaneState`, `useApprovalsQueue`,
// `useSubagents`, `useSubagentActivities`, `useDrafts`, `useArchivedSources`,
// `useWorkspacePaneAutoOpen`) and passes their normalised outputs in as props
// (FR-1.25). The `chatModel`-typed prop shapes are re-typed chat-surface-local
// here (FR-1.27); the tab bodies consume the already-hoisted citations
// (`SourceRow`), subagents (`SubagentCard`) and, via the host wiring,
// approvals families.

export { WorkspacePane, type WorkspacePaneProps } from "./WorkspacePane";
export {
  WorkspaceTabs,
  workspaceTabPanelId,
  type WorkspaceTabsItem,
  type WorkspaceTabsProps,
} from "./WorkspaceTabs";
export {
  SourcesTab,
  type SourcesTabProps,
  type SourceRowSlot,
} from "./SourcesTab";
export { AgentsTab, type AgentsTabProps } from "./AgentsTab";
export { DraftTab, type DraftTabProps } from "./DraftTab";
export { ApprovalsTab, type ApprovalsTabProps } from "./ApprovalsTab";
export { SkillsTab, type SkillsTabProps } from "./SkillsTab";
export { pluralize, tabLabel, TAB_LABELS, type LabelForms } from "./pluralize";

// Boundary types the host / shims reference (FR-1.27).
export type {
  WorkspacePaneState,
  WorkspacePaneTabId,
  WorkspacePaneCloseReason,
  WorkspacePaneOpenOptions,
  WorkspacePaneFocus,
  ApprovalsQueueItem,
  ApprovalsQueueProjection,
  SubagentActivitiesByTask,
  SubagentHistoryGroup,
} from "./types";
export type {
  SourceEntryMap,
  SubagentSnapshotMap,
  SourceConnectorGroup,
} from "./workspaceHelpers";
