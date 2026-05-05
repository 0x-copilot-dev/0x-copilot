// PR 3.2 — Workspace pane host (right rail).
//
// Composition shell only. Owns no fetches, no event subscriptions. The
// pane is open/closed via `useWorkspacePaneState`; data flows in via
// props (citations / subagents / drafts / approvalsQueue / skills) the
// parent (ChatScreen) lifts from the upstream PR 1.x hooks.
//
// The right column lives inside `aui-workspace` and is collapsible. On
// viewports < 1100px the pane switches to overlay mode (CSS handles
// the position-fixed anchoring; this component just sets a data attr).

import { IconButton } from "@enterprise-search/design-system";
import type {
  Skill,
  SourceEntry,
  SubagentEntry,
} from "@enterprise-search/api-types";
import { useId, type ReactElement } from "react";

import type { SourceEntryMap } from "../../chatModel/sourcesReducer";
import type { SubagentSnapshotMap } from "../../chatModel/subagentReducer";
import { ApprovalsTab } from "./ApprovalsTab";
import { AgentsTab } from "./AgentsTab";
import { DraftTab, type DraftTabProps } from "./DraftTab";
import { SkillsTab } from "./SkillsTab";
import { SourcesTab } from "./SourcesTab";
import {
  WorkspaceTabs,
  workspaceTabPanelId,
  type WorkspaceTabsItem,
} from "./WorkspaceTabs";
import type { WorkspacePaneTabId } from "./useWorkspacePaneAutoOpen";
import type { WorkspacePaneState } from "./useWorkspacePaneState";
import type { ApprovalsQueueProjection } from "./useApprovalsQueue";

export interface WorkspacePaneProps {
  state: WorkspacePaneState;
  /** Sources tab inputs (PR 1.5 reducer + PR 3.1 archive). */
  sources: SourceEntryMap;
  sourcesLoading?: boolean;
  sourcesError?: string | null;
  onSelectSource?: (source: SourceEntry) => void;
  /** Agents tab inputs (PR 1.5 reducer + PR 3.2 archive). */
  subagents: SubagentSnapshotMap;
  subagentsLoading?: boolean;
  subagentsError?: string | null;
  onJumpToSubagent?: (subagent: SubagentEntry) => void;
  /** Draft tab inputs (PR 1.3 + PR 3.2 mutations). */
  draft: DraftTabProps["draft"];
  draftLoading?: boolean;
  draftError?: string | null;
  onPatchDraft?: DraftTabProps["onPatch"];
  onSendDraft?: DraftTabProps["onSend"];
  onDiscardDraft?: DraftTabProps["onDiscard"];
  /** Approvals tab inputs (pure projection). */
  approvalsQueue: ApprovalsQueueProjection;
  onJumpToApproval?: (approvalId: string, messageId: string) => void;
  /** Skills tab inputs. */
  skills: readonly Skill[];
  skillsLoading?: boolean;
  skillsError?: string | null;
  onPickSkill?: (skill: Skill) => void;
  onOpenSkillSettings?: () => void;
  /** Read-only chrome (e.g. shared-conversation view). */
  disabled?: boolean;
  /** Force overlay mode regardless of viewport (used by ChatScreen below 1100px). */
  overlay?: boolean;
}

export function WorkspacePane({
  state,
  sources,
  sourcesLoading,
  sourcesError,
  onSelectSource,
  subagents,
  subagentsLoading,
  subagentsError,
  onJumpToSubagent,
  draft,
  draftLoading,
  draftError,
  onPatchDraft,
  onSendDraft,
  onDiscardDraft,
  approvalsQueue,
  onJumpToApproval,
  skills,
  skillsLoading,
  skillsError,
  onPickSkill,
  onOpenSkillSettings,
  disabled,
  overlay,
}: WorkspacePaneProps): ReactElement | null {
  const tablistId = useId();
  if (!state.open) {
    return null;
  }

  const tabs: readonly WorkspaceTabsItem<WorkspacePaneTabId>[] = [
    {
      id: "sources",
      label: "Sources",
      badge: sources.size > 0 ? <span>{sources.size}</span> : undefined,
    },
    {
      id: "agents",
      label: "Agents",
      badge: agentsBadge(subagents),
    },
    {
      id: "draft",
      label: "Draft",
      badge: draft !== null ? <span>1</span> : undefined,
    },
    {
      id: "approvals",
      label: "Approvals",
      badge:
        approvalsQueue.pending.length > 0 ? (
          <span>{approvalsQueue.pending.length}</span>
        ) : undefined,
    },
    {
      id: "skills",
      label: "Skills",
      badge: skills.length > 0 ? <span>{skills.length}</span> : undefined,
    },
  ];

  const panelId = workspaceTabPanelId(tablistId, state.activeTab);

  return (
    <aside
      className="atlas-workspace-pane"
      aria-label="Workspace pane"
      data-testid="workspace-pane"
      data-overlay={overlay ? "true" : "false"}
      data-active-tab={state.activeTab}
    >
      <header className="atlas-workspace-pane__header">
        <WorkspaceTabs
          items={tabs}
          active={state.activeTab}
          onSelect={(id) => state.setActiveTab(id)}
          ariaLabel="Workspace pane tabs"
        />
        <IconButton
          type="button"
          size="sm"
          variant="ghost"
          aria-label="Close workspace pane"
          data-tooltip="Close pane"
          data-tooltip-placement="bottom"
          onClick={() => state.close("manual")}
          data-testid="workspace-pane-close"
        >
          ✕
        </IconButton>
      </header>
      <section
        role="tabpanel"
        id={panelId}
        className="atlas-workspace-pane__body"
        aria-label={tabs.find((tab) => tab.id === state.activeTab)?.label}
      >
        {state.activeTab === "sources" ? (
          <SourcesTab
            sources={sources}
            loading={sourcesLoading}
            error={sourcesError ?? null}
            focusCitationId={state.focus.citationId ?? null}
            onSelect={onSelectSource}
          />
        ) : null}
        {state.activeTab === "agents" ? (
          <AgentsTab
            subagents={subagents}
            loading={subagentsLoading}
            error={subagentsError ?? null}
            focusTaskId={state.focus.subagentTaskId ?? null}
            onJumpToSubagent={onJumpToSubagent}
          />
        ) : null}
        {state.activeTab === "draft" ? (
          <DraftTab
            draft={draft}
            loading={draftLoading}
            error={draftError ?? null}
            disabled={disabled}
            onPatch={onPatchDraft}
            onSend={onSendDraft}
            onDiscard={onDiscardDraft}
          />
        ) : null}
        {state.activeTab === "approvals" ? (
          <ApprovalsTab
            queue={approvalsQueue}
            onJumpToApproval={onJumpToApproval}
          />
        ) : null}
        {state.activeTab === "skills" ? (
          <SkillsTab
            skills={skills}
            loading={skillsLoading}
            error={skillsError ?? null}
            onPick={onPickSkill}
            onOpenSettings={onOpenSkillSettings}
          />
        ) : null}
      </section>
    </aside>
  );
}

function agentsBadge(subagents: SubagentSnapshotMap): ReactElement | undefined {
  if (subagents.size === 0) {
    return undefined;
  }
  let running = 0;
  for (const entry of subagents.values()) {
    if (entry.status === "running" || entry.status === "queued") {
      running += 1;
    }
  }
  if (running === 0) {
    return <span>{subagents.size}</span>;
  }
  return (
    <span aria-label={`${running} running subagents`}>{running} live</span>
  );
}
