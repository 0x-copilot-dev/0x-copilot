// Compact, horizontal Focus-row for the Agents right rail.
//
// This intentionally does not share chrome with `SubagentCard`: that card is
// the rich in-thread/detail primitive. The rail needs a calm scan surface, but
// keeps its detailed timeline behind an explicit, keyboard-accessible control.

import type { ReactElement } from "react";

import { SubagentActivityList } from "./SubagentActivityList";
import type { SubagentActivityRecord } from "./subagentHelpers";
import type { AgentActivityRowViewModel } from "./agentActivityRowViewModel";
import { pauseJumpLabel } from "./labels";

export interface AgentActivityRowProps {
  view: AgentActivityRowViewModel;
  activities: readonly SubagentActivityRecord[];
  timelineClassName?: string;
  onJumpToThread?: () => void;
  onJumpToApproval?: (sourceEventId: string) => void;
  defaultOpen?: boolean;
  /** One level equals the measured 18px child indent. */
  depth?: number;
  /** Parent/orchestrator rows are scan-only until they have their own task. */
  lead?: boolean;
}

export function AgentActivityRow({
  view,
  activities,
  timelineClassName,
  onJumpToThread,
  onJumpToApproval,
  defaultOpen = false,
  depth = 0,
  lead = false,
}: AgentActivityRowProps): ReactElement {
  const hasDetail = !lead && view.taskId !== null;
  const hasActivities = activities.length > 0;
  const showFullResult = !hasActivities && view.terminal && view.fullResult;
  const showApprovalJump =
    view.status === "paused" &&
    view.pauseSourceEventId !== undefined &&
    onJumpToApproval !== undefined;

  return (
    <div
      className="agent-activity-row"
      data-depth={depth > 0 ? String(depth) : undefined}
      data-has-detail={hasDetail ? "true" : undefined}
      data-lead={lead ? "true" : undefined}
      data-status={view.status}
    >
      <span
        className="agent-activity-row__lifecycle"
        role="img"
        aria-label={lifecycleLabel(view.status)}
        data-status={view.status}
      >
        {lifecycleGlyph(view.status)}
      </span>
      <div className="agent-activity-row__content">
        <div className="agent-activity-row__identity">
          <span className="agent-activity-row__name" title={view.name}>
            {view.name}
          </span>
          {view.modelDisplayLabel ? (
            <span className="agent-activity-row__model">
              {view.modelDisplayLabel}
            </span>
          ) : null}
        </div>
        {view.currentActivity ? (
          <p
            className="agent-activity-row__activity"
            title={view.currentActivity}
          >
            {view.currentActivity}
          </p>
        ) : null}
      </div>
      <div className="agent-activity-row__actions">
        {onJumpToThread ? (
          <button
            type="button"
            className="agent-activity-row__jump"
            aria-label={`Open ${view.name} in thread`}
            onClick={onJumpToThread}
          >
            ↗
          </button>
        ) : null}
      </div>
      {hasDetail ? (
        <details
          className="agent-activity-row__details"
          open={defaultOpen || undefined}
          data-testid={`agent-activity-row-details-${view.taskId}`}
        >
          <summary
            className="agent-activity-row__detail-toggle"
            aria-label={`Toggle ${view.name} activity details`}
          >
            <span aria-hidden="true">⌄</span>
          </summary>
          <section
            className="agent-activity-row__details-body"
            role="region"
            aria-label={`${view.name} activity details`}
          >
            {hasActivities ? (
              <SubagentActivityList
                activities={[...activities]}
                className={timelineClassName ?? "aui-tool-card__timeline"}
              />
            ) : showFullResult ? (
              <pre className="agent-activity-row__full-result">
                {view.fullResult}
              </pre>
            ) : (
              <p className="agent-activity-row__empty">
                {view.terminal
                  ? "Single-shot response — no inner tool calls."
                  : "No activity yet."}
              </p>
            )}
            {showApprovalJump ? (
              <button
                type="button"
                className="agent-activity-row__approval-jump"
                onClick={() => onJumpToApproval(view.pauseSourceEventId!)}
              >
                Review {pauseJumpLabel(view.pauseReason)} →
              </button>
            ) : null}
          </section>
        </details>
      ) : null}
    </div>
  );
}

function lifecycleLabel(status: AgentActivityRowViewModel["status"]): string {
  switch (status) {
    case "queued":
      return "Queued";
    case "running":
      return "Running";
    case "paused":
      return "Paused";
    case "completed":
      return "Completed";
    case "cancelled":
      return "Cancelled";
    case "failed":
      return "Failed";
    case "timed_out":
      return "Timed out";
  }
}

function lifecycleGlyph(
  status: AgentActivityRowViewModel["status"],
): ReactElement | string {
  switch (status) {
    case "queued":
    case "running":
      return (
        <span className="agent-activity-row__spinner" aria-hidden="true" />
      );
    case "paused":
      return "Ⅱ";
    case "completed":
      return "✓";
    case "cancelled":
      return "−";
    case "failed":
    case "timed_out":
      return "!";
  }
}
