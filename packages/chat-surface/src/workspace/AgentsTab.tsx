// PR 3.2 — Agents tab body for the right-rail workspace pane.
// PR-1.7 — hoisted into @0x-copilot/chat-surface with the pane it serves.
//
// Pure presentational. Receives the SubagentSnapshotMap that
// `useSubagents` (PR 3.2 archive seed) and the live event reducer
// (PR 1.5 `applySubagentEvent`) feed into. Click-to-jump scrolls the
// thread to the matching <SubagentCard> block (existing). The thread
// jump target is identified by `data-task-id={task_id}` on the
// SubagentCard block; a lightweight scroll helper here keeps the
// integration shallow.
//
// PR 3.2.1 — the rail preserves the per-subagent step timeline (the same
// activities the in-thread `SubagentTool` shows, projected from the chat tree
// by `useSubagentActivities`) behind an explicit accessible control.
//
// The in-thread surface retains its richer `<SubagentCard>`. Focus uses the
// compact shared `<AgentActivityRow>` instead, with a separate, explicit
// detail control for the accessible timeline.

import { classNames } from "@0x-copilot/design-system";
import type {
  SubagentEntry,
  SubagentLifecycleStatus,
} from "@0x-copilot/api-types";
import { useEffect, useRef, type ReactElement } from "react";

import { scrollChatToEvent } from "../citations/scrollChatToCitation";
import { AgentActivityRow } from "../subagents/AgentActivityRow";
import {
  agentActivityRowFromEntry,
  displayAgentRole,
  type AgentActivityRowViewModel,
} from "../subagents/agentActivityRowViewModel";
import {
  isRunningStatus,
  subagentsByRecency,
  type SubagentSnapshotMap,
} from "./workspaceHelpers";
import type { SubagentActivitiesByTask, SubagentHistoryGroup } from "./types";

export interface AgentsTabProps {
  subagents: SubagentSnapshotMap;
  loading?: boolean;
  error?: string | null;
  /** Subagent task_id to scroll into focus on next render. */
  focusTaskId?: string | null;
  onJumpToSubagent?: (subagent: SubagentEntry) => void;
  /** PR 3.2.7 — fired when the user clicks the "Review approval →" link
   *  on a paused subagent's card. Default behavior (when omitted) uses
   *  the `scrollChatToEvent` helper to scroll the gating card into view
   *  on the chat thread. */
  onJumpToApproval?: (sourceEventId: string) => void;
  /** PR 3.2.1 — `task_id → activities[]` projected from the chat tree
   *  by `useSubagentActivities`. Hoisted in `ChatScreen` so the pane
   *  and the in-thread `SubagentCard` share one source of truth. */
  activitiesByTask?: SubagentActivitiesByTask;
  historyGroups?: readonly SubagentHistoryGroup[];
}

const PANE_TIMELINE_CLASS =
  "atlas-workspace-agent__timeline aui-tool-card__timeline";

export function AgentsTab({
  subagents,
  loading,
  error,
  focusTaskId,
  onJumpToSubagent,
  onJumpToApproval,
  activitiesByTask,
  historyGroups,
}: AgentsTabProps): ReactElement {
  const ordered = mergeOrderedSubagents(subagents, historyGroups ?? []);
  const focusRef = useRef<HTMLLIElement | null>(null);
  const handleJumpToApproval = onJumpToApproval ?? scrollChatToEvent;

  useEffect(() => {
    if (focusTaskId && focusRef.current) {
      focusRef.current.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }, [focusTaskId, ordered.length]);

  if (ordered.length === 0) {
    return (
      <div
        className="atlas-workspace-tab atlas-workspace-tab--empty"
        data-testid="workspace-agents-tab-empty"
      >
        {loading ? (
          <p>Loading subagents…</p>
        ) : error ? (
          <p role="alert">Couldn’t load subagents — {error}</p>
        ) : (
          <p>Subagents run here when Copilot dispatches parallel work.</p>
        )}
      </div>
    );
  }

  const runningCount = ordered.filter((entry) =>
    isRunningStatus(entry.status),
  ).length;

  return (
    <div className="atlas-workspace-tab" data-testid="workspace-agents-tab">
      {error ? (
        <p
          className="atlas-workspace-tab__stale"
          role="status"
          data-testid="workspace-agents-tab-stale"
        >
          Showing live results — older history failed to load ({error}).
        </p>
      ) : null}
      <ul
        className="atlas-workspace-tab__list"
        aria-live="polite"
        aria-label={
          runningCount > 0
            ? `Subagents in this conversation — ${runningCount} running`
            : "Subagents in this conversation"
        }
      >
        {renderHistoryGroups({
          ordered,
          groups: historyGroups ?? [],
          focusTaskId,
          focusRef,
          activitiesByTask,
          onJumpToSubagent,
          onJumpToApproval: handleJumpToApproval,
        })}
      </ul>
    </div>
  );
}

function renderHistoryGroups({
  ordered,
  groups,
  focusTaskId,
  focusRef,
  activitiesByTask,
  onJumpToSubagent,
  onJumpToApproval,
}: {
  ordered: readonly SubagentEntry[];
  groups: readonly SubagentHistoryGroup[];
  focusTaskId?: string | null;
  focusRef: React.MutableRefObject<HTMLLIElement | null>;
  activitiesByTask?: SubagentActivitiesByTask;
  onJumpToSubagent?: (subagent: SubagentEntry) => void;
  onJumpToApproval: (sourceEventId: string) => void;
}): ReactElement[] {
  const groupedTaskIds = new Set(
    groups.flatMap((group) => group.entries.map((entry) => entry.task_id)),
  );
  const byTask = new Map(ordered.map((entry) => [entry.task_id, entry]));
  const rendered: ReactElement[] = [];
  for (const group of groups) {
    const entries = group.entries
      .map((entry) => byTask.get(entry.task_id) ?? entry)
      .filter((entry, index, arr) => {
        return (
          arr.findIndex((item) => item.task_id === entry.task_id) === index
        );
      });
    if (entries.length === 0) continue;
    const first = entries[0];
    rendered.push(
      <li key={`group-${group.id}`} className="atlas-workspace-agent-group">
        <button
          type="button"
          className="atlas-workspace-agent-group__header"
          onClick={() => onJumpToSubagent?.(first)}
        >
          <span>{group.label}</span>
          <time>{formatGroupTime(group.timestamp)}</time>
        </button>
        <ul className="atlas-workspace-agent-group__list">
          {renderFocusRows({
            entries,
            focusTaskId,
            focusRef,
            activitiesByTask,
            onJumpToSubagent,
            onJumpToApproval,
          })}
        </ul>
      </li>,
    );
  }
  const ungrouped: SubagentEntry[] = [];
  for (const entry of ordered) {
    if (groupedTaskIds.has(entry.task_id)) continue;
    ungrouped.push(entry);
  }
  rendered.push(
    ...renderFocusRows({
      entries: ungrouped,
      focusTaskId,
      focusRef,
      activitiesByTask,
      onJumpToSubagent,
      onJumpToApproval,
    }),
  );
  return rendered;
}

function renderFocusRows({
  entries,
  focusTaskId,
  focusRef,
  activitiesByTask,
  onJumpToSubagent,
  onJumpToApproval,
}: {
  entries: readonly SubagentEntry[];
  focusTaskId?: string | null;
  focusRef: React.MutableRefObject<HTMLLIElement | null>;
  activitiesByTask?: SubagentActivitiesByTask;
  onJumpToSubagent?: (subagent: SubagentEntry) => void;
  onJumpToApproval: (sourceEventId: string) => void;
}): ReactElement[] {
  return focusRows(entries).map((row) =>
    renderEntry({
      entry: row.entry,
      lead: row.lead,
      depth: row.depth,
      focusTaskId,
      focusRef,
      activitiesByTask,
      onJumpToSubagent,
      onJumpToApproval,
    }),
  );
}

function renderEntry({
  entry,
  lead,
  depth,
  focusTaskId,
  focusRef,
  activitiesByTask,
  onJumpToSubagent,
  onJumpToApproval,
}: {
  entry?: SubagentEntry;
  lead?: AgentActivityRowViewModel;
  depth: number;
  focusTaskId?: string | null;
  focusRef: React.MutableRefObject<HTMLLIElement | null>;
  activitiesByTask?: SubagentActivitiesByTask;
  onJumpToSubagent?: (subagent: SubagentEntry) => void;
  onJumpToApproval: (sourceEventId: string) => void;
}): ReactElement {
  const view = lead ?? agentActivityRowFromEntry(entry!);
  const isLead = lead !== undefined;
  const taskId = entry?.task_id;
  const isFocused = taskId === focusTaskId;
  const activities = taskId ? (activitiesByTask?.get(taskId) ?? []) : [];
  return (
    <li
      key={isLead ? `lead-${view.taskId ?? view.name}` : taskId}
      ref={isFocused ? focusRef : undefined}
      className={classNames(
        "atlas-workspace-tab__item",
        depth > 0 && "atlas-workspace-tab__item--child",
        isFocused && "atlas-workspace-tab__item--focused",
      )}
      id={taskId ? `subagent-task-${taskId}` : undefined}
      data-task-id={taskId}
      data-status={view.status}
    >
      <AgentActivityRow
        view={view}
        activities={activities}
        timelineClassName={PANE_TIMELINE_CLASS}
        onJumpToThread={
          entry && onJumpToSubagent ? () => onJumpToSubagent(entry) : undefined
        }
        onJumpToApproval={onJumpToApproval}
        defaultOpen={isFocused}
        depth={depth}
        lead={isLead}
      />
    </li>
  );
}

interface FocusRow {
  readonly entry?: SubagentEntry;
  readonly lead?: AgentActivityRowViewModel;
  readonly depth: number;
}

/**
 * Preserve stream/archive order while expressing the parent relation when the
 * additive projection fields are present. A real parent entry wins. Synthetic
 * leads are only rendered when the parent signal is explicit (name or non-
 * orchestrator role). Supervisor-only hints are not interpreted as a concrete
 * lead because the backend emits that alias by default.
 */
function focusRows(entries: readonly SubagentEntry[]): readonly FocusRow[] {
  const byTaskId = new Map(entries.map((entry) => [entry.task_id, entry]));
  const childByParent = new Map<string, SubagentEntry[]>();
  const orphanGroups = new Map<string, SubagentEntry[]>();
  const parentLabels = new Map<string, { name: string; role: string | null }>();

  for (const entry of entries) {
    const view = agentActivityRowFromEntry(entry);
    if (view.parentTaskId && byTaskId.has(view.parentTaskId)) {
      const children = childByParent.get(view.parentTaskId) ?? [];
      children.push(entry);
      childByParent.set(view.parentTaskId, children);
      continue;
    }
    const parentRoleLabel = displayAgentRole(view.parentAgentRole);
    const parentName = view.parentAgentName ?? parentRoleLabel;
    const shouldGroupOrphan =
      parentName !== null && parentRoleLabel !== "Orchestrator";

    if (shouldGroupOrphan) {
      const key =
        view.parentTaskId ?? `${view.parentAgentRole ?? "agent"}:${parentName}`;
      const children = orphanGroups.get(key) ?? [];
      children.push(entry);
      orphanGroups.set(key, children);
      parentLabels.set(key, { name: parentName, role: view.parentAgentRole });
    }
  }

  const rendered = new Set<string>();
  const output: FocusRow[] = [];
  const appendEntry = (entry: SubagentEntry, depth: number): void => {
    if (rendered.has(entry.task_id)) return;
    rendered.add(entry.task_id);
    output.push({ entry, depth });
    for (const child of childByParent.get(entry.task_id) ?? []) {
      appendEntry(child, depth + 1);
    }
  };

  for (const entry of entries) {
    if (rendered.has(entry.task_id)) continue;
    const view = agentActivityRowFromEntry(entry);
    const hasRealParent =
      view.parentTaskId !== null && byTaskId.has(view.parentTaskId);
    if (hasRealParent) continue;
    const parentRoleLabel = displayAgentRole(view.parentAgentRole);
    const parentName = view.parentAgentName ?? parentRoleLabel;
    const shouldRenderSyntheticLead =
      parentName !== null && parentRoleLabel !== "Orchestrator";
    const orphanKey = shouldRenderSyntheticLead
      ? (view.parentTaskId ??
        `${view.parentAgentRole ?? "agent"}:${parentName}`)
      : null;
    if (orphanKey && orphanGroups.has(orphanKey)) {
      const children = orphanGroups.get(orphanKey)!;
      output.push({
        lead: syntheticLead(orphanKey, parentLabels.get(orphanKey)!, children),
        depth: 0,
      });
      for (const child of children) appendEntry(child, 1);
      continue;
    }
    appendEntry(entry, 0);
  }
  return output;
}

function syntheticLead(
  key: string,
  parent: { name: string; role: string | null },
  children: readonly SubagentEntry[],
): AgentActivityRowViewModel {
  const status = aggregateLeadStatus(children);
  const activeCount = children.filter((entry) =>
    isRunningStatus(entry.status),
  ).length;
  const childLabel = children.length === 1 ? "agent" : "agents";
  return {
    taskId: `parent-${key}`,
    name: parent.name,
    status,
    terminal: status === "completed",
    task: null,
    finding: null,
    fullResult: null,
    startedAt: null,
    completedAt: null,
    durationMs: null,
    isError: status === "failed",
    parentTaskId: null,
    parentAgentRole: parent.role,
    parentAgentName: parent.name,
    modelDisplayLabel: null,
    currentActivity:
      activeCount > 0
        ? `Coordinating ${activeCount} active ${childLabel}`
        : `Coordinated ${children.length} ${childLabel}`,
  };
}

function aggregateLeadStatus(
  children: readonly SubagentEntry[],
): SubagentLifecycleStatus {
  if (children.some((entry) => isRunningStatus(entry.status))) return "running";
  if (children.some((entry) => entry.status === "paused")) return "paused";
  if (
    children.some(
      (entry) => entry.status === "failed" || entry.status === "timed_out",
    )
  ) {
    return "failed";
  }
  if (children.some((entry) => entry.status === "cancelled"))
    return "cancelled";
  return "completed";
}

function mergeOrderedSubagents(
  subagents: SubagentSnapshotMap,
  groups: readonly SubagentHistoryGroup[],
): readonly SubagentEntry[] {
  const merged = new Map(subagents);
  for (const group of groups) {
    for (const entry of group.entries) {
      if (!merged.has(entry.task_id)) {
        merged.set(entry.task_id, entry);
      }
    }
  }
  return subagentsByRecency(merged);
}

function formatGroupTime(value: string | null): string {
  if (value === null) return "Earlier";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Earlier";
  return new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}
