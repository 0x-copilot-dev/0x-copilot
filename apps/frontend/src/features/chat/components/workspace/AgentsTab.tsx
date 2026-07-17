// PR 3.2 — Agents tab body for the right-rail workspace pane.
//
// Pure presentational. Receives the SubagentSnapshotMap that
// `useSubagents` (PR 3.2 archive seed) and the live event reducer
// (PR 1.5 `applySubagentEvent`) feed into. Click-to-jump scrolls the
// thread to the matching <SubagentCard> block (existing). The thread
// jump target is identified by `data-task-id={task_id}` on the
// SubagentCard block; a lightweight scroll helper here keeps the
// integration shallow.
//
// PR 3.2.1 — each card body wraps in a native `<details>` disclosure
// that reveals the per-subagent step timeline (the same activities the
// in-thread `SubagentTool` shows, projected from the chat tree by
// `useSubagentActivities`).
//
// PR 3.2.2 — both surfaces (in-thread + this pane) now render via the
// shared `<SubagentCard>` primitive. The pane composes the narrow
// timeline variant on top of the in-thread base styling.

import { classNames } from "@0x-copilot/design-system";
import type { SubagentEntry } from "@0x-copilot/api-types";
import { useEffect, useRef, type ReactElement } from "react";

import {
  isRunningStatus,
  subagentsByRecency,
  type SubagentSnapshotMap,
} from "../../chatModel/subagentReducer";
import { scrollChatToEvent } from "@0x-copilot/chat-surface";
import { SubagentCard } from "../subagents/SubagentCard";
import { subagentCardFromEntry } from "../subagents/subagentCardViewModel";
import type {
  SubagentActivitiesByTask,
  SubagentHistoryGroup,
} from "./useSubagentActivities";

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
          <p>Subagents run here when Atlas dispatches parallel work.</p>
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
          {entries.map((entry) =>
            renderEntry({
              entry,
              focusTaskId,
              focusRef,
              activitiesByTask,
              onJumpToSubagent,
              onJumpToApproval,
            }),
          )}
        </ul>
      </li>,
    );
  }
  for (const entry of ordered) {
    if (groupedTaskIds.has(entry.task_id)) continue;
    rendered.push(
      renderEntry({
        entry,
        focusTaskId,
        focusRef,
        activitiesByTask,
        onJumpToSubagent,
        onJumpToApproval,
      }),
    );
  }
  return rendered;
}

function renderEntry({
  entry,
  focusTaskId,
  focusRef,
  activitiesByTask,
  onJumpToSubagent,
  onJumpToApproval,
}: {
  entry: SubagentEntry;
  focusTaskId?: string | null;
  focusRef: React.MutableRefObject<HTMLLIElement | null>;
  activitiesByTask?: SubagentActivitiesByTask;
  onJumpToSubagent?: (subagent: SubagentEntry) => void;
  onJumpToApproval: (sourceEventId: string) => void;
}): ReactElement {
  const isFocused = entry.task_id === focusTaskId;
  const view = subagentCardFromEntry(entry);
  const activities = activitiesByTask?.get(entry.task_id) ?? [];
  return (
    <li
      key={entry.task_id}
      ref={isFocused ? focusRef : undefined}
      className={classNames(
        "atlas-workspace-tab__item",
        isFocused && "atlas-workspace-tab__item--focused",
      )}
      data-task-id={entry.task_id}
      data-status={entry.status}
    >
      <SubagentCard
        view={view}
        activities={activities}
        timelineClassName={PANE_TIMELINE_CLASS}
        onJumpToThread={
          onJumpToSubagent ? () => onJumpToSubagent(entry) : undefined
        }
        onJumpToApproval={onJumpToApproval}
        defaultOpen={isFocused}
        compact
      />
    </li>
  );
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
