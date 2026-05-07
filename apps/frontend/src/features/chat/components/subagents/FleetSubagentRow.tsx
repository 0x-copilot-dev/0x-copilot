// PR 3.2.4 — compact row inside a parallel-fleet card.
// PR 3.2.7 — paused chrome (amber indicator + paused chip + frozen
// progress) when status === "paused", click-to-expand inline timeline
// (independent disclosure per row), and a "Review approval →" link
// that anchors back to the gating interrupt event on the same thread.

import { Badge } from "@enterprise-search/design-system";
import { useCallback, useId, useState, type ReactElement } from "react";
import type { SubagentActivityRecord } from "../../utils/activityDataBuilders";
import { SubagentActivityList } from "../tools/SubagentActivityList";
import { useElapsedSeconds } from "../tools/useElapsedSeconds";
import type {
  SubagentCardViewModel,
  SubagentPauseReason,
} from "./subagentCardViewModel";

export interface FleetSubagentRowProps {
  view: SubagentCardViewModel;
  /** 0..1 advisory progress fed by the worker; null while the worker
   *  hasn't reported a number yet. CSS handles the running animation
   *  when fillFraction is null. */
  progress?: number | null;
  /** PR 3.2.7 — inner activities to render in the inline timeline when
   *  the user clicks the row to expand. Same data source the standalone
   *  `<SubagentCard>` consumes. */
  activities?: readonly SubagentActivityRecord[];
  /** PR 3.2.7 — fired from the inline expansion's "Review approval →"
   *  link when the row is paused and `view.pauseSourceEventId` is
   *  populated. The handler is responsible for scrolling the chat to
   *  the matching interrupt card. */
  onJumpToApproval?: (sourceEventId: string) => void;
}

export function FleetSubagentRow({
  view,
  progress,
  activities,
  onJumpToApproval,
}: FleetSubagentRowProps): ReactElement {
  const [expanded, setExpanded] = useState(false);
  const timelineId = useId();
  const elapsedSeconds = useElapsedSeconds(!view.terminal, view.startedAt);
  const elapsedLabel =
    view.terminal && view.durationMs !== null
      ? formatDuration(view.durationMs)
      : `${elapsedSeconds}s`;
  const isPaused = view.status === "paused";
  const fillFraction = view.terminal
    ? 1
    : typeof progress === "number"
      ? Math.max(0, Math.min(1, progress))
      : null;
  const showStatusWord =
    view.terminal && (view.status === "failed" || view.status === "cancelled");
  const toggle = useCallback(() => setExpanded((s) => !s), []);
  const showJump =
    isPaused &&
    typeof view.pauseSourceEventId === "string" &&
    onJumpToApproval !== undefined;
  return (
    <>
      <div
        className="subagent-fleet-row subagent-fleet-row--clickable"
        data-status={view.status}
        data-paused={isPaused ? "true" : undefined}
        data-task-id={view.taskId ?? undefined}
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        aria-controls={timelineId}
        onClick={toggle}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            toggle();
          }
        }}
      >
        <span
          className="subagent-fleet-row__indicator"
          aria-hidden="true"
          data-status={view.status}
        >
          {indicatorGlyph(view.status)}
        </span>
        <div className="subagent-fleet-row__text">
          <div className="subagent-fleet-row__name" title={view.name}>
            {view.name}
          </div>
          {view.task ? (
            <div className="subagent-fleet-row__task" title={view.task}>
              {view.task}
            </div>
          ) : null}
        </div>
        <span
          className="subagent-fleet-row__progress"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={1}
          aria-valuenow={fillFraction ?? undefined}
          aria-label={`${view.name} progress`}
        >
          <span
            className="subagent-fleet-row__progress-fill"
            style={
              fillFraction !== null
                ? { transform: `scaleX(${fillFraction})` }
                : undefined
            }
          />
        </span>
        {isPaused ? (
          <Badge
            tone="warning"
            className="subagent-fleet-row__paused-chip"
            aria-label={`Paused, ${ariaLabelForPause(view.pauseReason)}`}
          >
            Paused · {labelForPause(view.pauseReason)}
          </Badge>
        ) : null}
        <span className="subagent-fleet-row__elapsed">
          {elapsedLabel}
          {showStatusWord ? ` · ${view.status}` : ""}
        </span>
      </div>
      {expanded ? (
        <div
          id={timelineId}
          className="subagent-fleet-row__inline-timeline"
          role="region"
          aria-label={`${view.name} activity timeline`}
        >
          {activities && activities.length > 0 ? (
            <SubagentActivityList
              activities={[...activities]}
              className="subagent-fleet-row__activity-list"
            />
          ) : (
            <p className="subagent-fleet-row__empty">
              {view.terminal
                ? "Single-shot response — no inner tool calls."
                : "No activity yet."}
            </p>
          )}
          {showJump ? (
            <button
              type="button"
              className="subagent-fleet-row__jump-link"
              onClick={(event) => {
                event.stopPropagation();
                onJumpToApproval!(view.pauseSourceEventId!);
              }}
            >
              Review {jumpLabelForPause(view.pauseReason)} →
            </button>
          ) : null}
        </div>
      ) : null}
    </>
  );
}

function indicatorGlyph(status: SubagentCardViewModel["status"]): string {
  switch (status) {
    case "completed":
      return "✓";
    case "failed":
    case "timed_out":
      return "✕";
    case "cancelled":
      return "−";
    case "queued":
    case "running":
      return "○";
    case "paused":
      return "⏸";
  }
}

function labelForPause(reason: SubagentPauseReason | undefined): string {
  switch (reason) {
    case "approval":
      return "waiting on approval";
    case "mcp_auth":
      return "waiting on connector";
    case "ask_a_question":
      return "waiting for answer";
    default:
      return "waiting";
  }
}

function ariaLabelForPause(reason: SubagentPauseReason | undefined): string {
  switch (reason) {
    case "approval":
      return "waiting on approval";
    case "mcp_auth":
      return "waiting on connector authentication";
    case "ask_a_question":
      return "waiting for user answer";
    default:
      return "waiting";
  }
}

function jumpLabelForPause(reason: SubagentPauseReason | undefined): string {
  switch (reason) {
    case "approval":
      return "approval";
    case "mcp_auth":
      return "connector auth";
    case "ask_a_question":
      return "question";
    default:
      return "approval";
  }
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds - minutes * 60);
  return `${minutes}m ${remainder}s`;
}
