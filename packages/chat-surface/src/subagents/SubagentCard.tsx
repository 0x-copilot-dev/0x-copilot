// PR 3.2.2 — shared subagent card primitive.
//
// One component, two callsites: in-thread `SubagentTool` and workspace-pane
// `AgentsTab` both render this. Header (status icon + name + badge), task
// line (clamp 2), finding line (clamp 3, terminal only), meta row, native
// `<details>` disclosure that reveals either:
//   • the timeline (`SubagentActivityList`) when activities is non-empty;
//   • the full result text (truncated) when activities is empty;
//   • a calm "Single-shot response" fallback when both are empty.
//
// No new dep. The disclosure is `<details>` per PR 3.2.1; the timeline is
// `SubagentActivityList` per PR 3.2.1; truncation comes from the adapter.

import { Badge } from "@0x-copilot/design-system";
import type { ReactElement } from "react";
import { ActivityStatusIcon } from "./ActivityStatusIcon";
import { SubagentActivityList } from "./SubagentActivityList";
import type { SubagentActivityRecord } from "./subagentHelpers";
import {
  formatSubagentDuration,
  pauseJumpLabel,
  pauseShortLabel,
} from "./labels";
import type {
  SubagentCardStatus,
  SubagentCardViewModel,
} from "./subagentCardViewModel";

export interface SubagentCardProps {
  view: SubagentCardViewModel;
  /** Inner activities to render in the disclosure (PR 3.2.1 selector or
   *  the in-thread reducer's `args.activities`). */
  activities: readonly SubagentActivityRecord[];
  /** Optional className override for the `SubagentActivityList` container.
   *  Pane callsite passes `"atlas-workspace-agent__timeline aui-tool-card__timeline"`;
   *  thread callsite uses the default. */
  timelineClassName?: string;
  /** Optional jump-to-thread affordance (workspace pane only). */
  onJumpToThread?: () => void;
  /** PR 3.2.7 — optional jump-to-approval affordance (workspace pane).
   *  Visible only when `view.status === "paused"` and the entry carries
   *  `pauseSourceEventId`. */
  onJumpToApproval?: (sourceEventId: string) => void;
  /** Auto-expand the disclosure on first render. Component-local thereafter. */
  defaultOpen?: boolean;
  /** Compact card chrome for narrow workspace pane rendering. */
  compact?: boolean;
}

export function SubagentCard({
  view,
  activities,
  timelineClassName,
  onJumpToThread,
  onJumpToApproval,
  defaultOpen,
  compact,
}: SubagentCardProps): ReactElement {
  const statusLabel = labelForStatus(view);
  const statusTone = toneForStatus(view.status);
  const meta = metaText(view);
  const showFinding = view.terminal && view.finding !== null;
  const showStatusBadge = view.status !== "completed";
  const hasActivities = activities.length > 0;
  const showFullResult =
    !hasActivities && view.terminal && view.fullResult !== null;
  const showJumpToApproval =
    view.status === "paused" &&
    typeof view.pauseSourceEventId === "string" &&
    onJumpToApproval !== undefined;
  return (
    <div
      className="subagent-card"
      data-compact={compact ? "true" : undefined}
      data-status={view.status}
      data-task-id={view.taskId ?? undefined}
    >
      <div className="subagent-card__head">
        <span
          className="subagent-card__icon"
          aria-hidden="true"
          data-status={view.status}
        >
          <ActivityStatusIcon status={iconStatus(view.status)} />
        </span>
        <span className="subagent-card__name" title={view.name}>
          {view.name}
        </span>
        {showStatusBadge ? (
          <Badge tone={statusTone} className="subagent-card__status-badge">
            {statusLabel}
          </Badge>
        ) : null}
        {onJumpToThread !== undefined ? (
          <button
            type="button"
            className="subagent-card__jump"
            aria-label={`Open ${view.name} in thread`}
            onClick={onJumpToThread}
          >
            ↗
          </button>
        ) : null}
      </div>
      {view.task ? <p className="subagent-card__task">{view.task}</p> : null}
      {showFinding ? (
        <p className="subagent-card__finding">{view.finding}</p>
      ) : null}
      {showJumpToApproval ? (
        <button
          type="button"
          className="subagent-card__jump-to-approval"
          onClick={() => onJumpToApproval!(view.pauseSourceEventId!)}
        >
          Review {pauseJumpLabel(view.pauseReason)} →
        </button>
      ) : null}
      <details
        className="subagent-card__details"
        open={defaultOpen || undefined}
        data-testid={
          view.taskId ? `subagent-card-details-${view.taskId}` : undefined
        }
      >
        <summary className="subagent-card__details-summary">
          <span className="subagent-card__meta">{meta}</span>
          <span className="subagent-card__disclosure-hint" aria-hidden="true">
            ▾
          </span>
        </summary>
        <div className="subagent-card__details-body">
          {hasActivities ? (
            <SubagentActivityList
              activities={[...activities]}
              className={timelineClassName ?? "aui-tool-card__timeline"}
            />
          ) : showFullResult ? (
            <pre className="subagent-card__full-result">{view.fullResult}</pre>
          ) : (
            <p className="subagent-card__empty">
              {view.terminal
                ? "Single-shot response — no inner tool calls."
                : "No activity yet."}
            </p>
          )}
        </div>
      </details>
    </div>
  );
}

function labelForStatus(view: SubagentCardViewModel): string {
  switch (view.status) {
    case "queued":
      return "Queued";
    case "running":
      return "Running";
    case "paused":
      // PR 3.2.7 — when the worker handed us a `pauseReason`, surface it
      // in the badge so a fleet user reading the chip knows what kind of
      // gate is open ("Paused · approval" / "Paused · connector" /
      // "Paused · answer").
      return view.pauseReason
        ? `Paused · ${pauseShortLabel(view.pauseReason)}`
        : "Paused";
    case "completed":
      return "Done";
    case "cancelled":
      return "Cancelled";
    case "failed":
      return "Failed";
    case "timed_out":
      return "Timed out";
  }
}

function toneForStatus(
  status: SubagentCardStatus,
): "neutral" | "accent" | "success" | "warning" | "danger" {
  switch (status) {
    case "queued":
    case "running":
      return "accent";
    case "paused":
      return "warning";
    case "completed":
      return "success";
    case "cancelled":
      return "warning";
    case "failed":
    case "timed_out":
      return "danger";
  }
}

/** ActivityStatusIcon expects the kind of strings the reducer projects;
 *  map our normalised lifecycle status into one. */
function iconStatus(status: SubagentCardStatus): string {
  switch (status) {
    case "completed":
      return "completed";
    case "failed":
    case "timed_out":
      return "failed";
    case "cancelled":
      return "cancelled";
    case "queued":
    case "running":
    case "paused":
      return "running";
  }
}

function metaText(view: SubagentCardViewModel): string {
  if (
    view.status === "running" ||
    view.status === "queued" ||
    view.status === "paused"
  ) {
    if (view.status === "paused") {
      return "paused";
    }
    return view.startedAt ? "working…" : "starting…";
  }
  const duration =
    view.durationMs !== null ? formatSubagentDuration(view.durationMs) : null;
  switch (view.status) {
    case "completed":
      return duration ? `Completed in ${duration}` : "Done";
    case "cancelled":
      return duration ? `Cancelled · ${duration}` : "Cancelled";
    case "failed":
      return duration ? `Failed · ${duration}` : "Failed";
    case "timed_out":
      return duration ? `Timed out · ${duration}` : "Timed out";
  }
}
