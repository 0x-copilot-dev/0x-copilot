// PR 3.2 — Agents tab body for the right-rail workspace pane.
//
// Pure presentational. Receives the SubagentSnapshotMap that
// `useSubagents` (PR 3.2 archive seed) and the live event reducer
// (PR 1.5 `applySubagentEvent`) feed into. Click-to-jump scrolls the
// thread to the matching <SubagentTool> block (existing). The thread
// jump target is identified by `data-task-id={task_id}` on the
// SubagentTool block; a lightweight scroll helper here keeps the
// integration shallow.
//
// PR 3.2.1 — each card body wraps in a native `<details>` disclosure
// that reveals the per-subagent step timeline (the same activities the
// in-thread `SubagentTool` shows, projected from the chat tree by
// `useSubagentActivities`). Reuses `SubagentActivityList` verbatim with
// a pane-narrow class composed on top of `aui-tool-card__timeline`.

import {
  Badge,
  Card,
  IconButton,
  classNames,
} from "@enterprise-search/design-system";
import type { SubagentEntry } from "@enterprise-search/api-types";
import { useEffect, useRef, type ReactElement } from "react";

import {
  isRunningStatus,
  subagentsByRecency,
  type SubagentSnapshotMap,
} from "../../chatModel/subagentReducer";
import { SubagentActivityList } from "../tools/SubagentActivityList";
import type { SubagentActivitiesByTask } from "./useSubagentActivities";

export interface AgentsTabProps {
  subagents: SubagentSnapshotMap;
  loading?: boolean;
  error?: string | null;
  /** Subagent task_id to scroll into focus on next render. */
  focusTaskId?: string | null;
  onJumpToSubagent?: (subagent: SubagentEntry) => void;
  /** PR 3.2.1 — `task_id → activities[]` projected from the chat tree
   *  by `useSubagentActivities`. Hoisted in `ChatScreen` so the pane
   *  and the in-thread `SubagentTool` share one source of truth. */
  activitiesByTask?: SubagentActivitiesByTask;
}

export function AgentsTab({
  subagents,
  loading,
  error,
  focusTaskId,
  onJumpToSubagent,
  activitiesByTask,
}: AgentsTabProps): ReactElement {
  const ordered = subagentsByRecency(subagents);
  const focusRef = useRef<HTMLLIElement | null>(null);

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
        {ordered.map((entry) => {
          const isFocused = entry.task_id === focusTaskId;
          const running = isRunningStatus(entry.status);
          const activities = activitiesByTask?.get(entry.task_id) ?? [];
          const metaText = running
            ? "working…"
            : entry.duration_ms !== null
              ? `Completed in ${formatDuration(entry.duration_ms)}`
              : null;
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
              <Card>
                <div className="atlas-workspace-agent">
                  <div className="atlas-workspace-agent__header">
                    <Badge tone={badgeToneFor(entry.status)}>
                      {statusLabel(entry.status)}
                    </Badge>
                    <span className="atlas-workspace-agent__name">
                      {entry.display_title ?? entry.subagent_name}
                    </span>
                    {onJumpToSubagent !== undefined ? (
                      <IconButton
                        type="button"
                        size="sm"
                        variant="ghost"
                        aria-label={`Open ${entry.subagent_name} in thread`}
                        onClick={() => onJumpToSubagent(entry)}
                      >
                        ↗
                      </IconButton>
                    ) : null}
                  </div>
                  {entry.objective_summary ? (
                    <p className="atlas-workspace-agent__objective">
                      {entry.objective_summary}
                    </p>
                  ) : null}
                  {entry.result_summary ? (
                    <p className="atlas-workspace-agent__result">
                      {entry.result_summary}
                    </p>
                  ) : null}
                  <details
                    className="atlas-workspace-agent__details"
                    open={isFocused || undefined}
                    data-testid={`workspace-agent-details-${entry.task_id}`}
                  >
                    <summary className="atlas-workspace-agent__details-summary">
                      <span
                        className={classNames(
                          "atlas-workspace-agent__meta",
                          running && "atlas-workspace-agent__working",
                        )}
                      >
                        {metaText ?? <span aria-hidden="true">&nbsp;</span>}
                      </span>
                      <span
                        className="atlas-workspace-agent__disclosure-hint"
                        aria-hidden="true"
                      >
                        ▾
                      </span>
                    </summary>
                    <SubagentActivityList
                      className="atlas-workspace-agent__timeline aui-tool-card__timeline"
                      activities={[...activities]}
                    />
                  </details>
                </div>
              </Card>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function statusLabel(status: SubagentEntry["status"]): string {
  switch (status) {
    case "queued":
      return "Queued";
    case "running":
      return "Running";
    case "completed":
      return "Done";
    case "cancelled":
      return "Cancelled";
    case "failed":
      return "Failed";
    case "timed_out":
      return "Timed out";
    default:
      return status;
  }
}

function badgeToneFor(
  status: SubagentEntry["status"],
): "neutral" | "accent" | "success" | "warning" | "danger" {
  switch (status) {
    case "running":
    case "queued":
      return "accent";
    case "completed":
      return "success";
    case "failed":
    case "timed_out":
      return "danger";
    case "cancelled":
      return "warning";
    default:
      return "neutral";
  }
}

function formatDuration(ms: number): string {
  if (ms < 1000) {
    return `${ms}ms`;
  }
  const seconds = ms / 1000;
  if (seconds < 60) {
    return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
  }
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds - minutes * 60);
  return `${minutes}m ${remainder}s`;
}
