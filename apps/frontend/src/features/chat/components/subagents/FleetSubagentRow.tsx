// PR 3.2.4 — compact row inside a parallel-fleet card.
//
// Single line per subagent: status indicator · name · 1-line task ·
// progress bar · elapsed. No disclosure, no badge, no jump button —
// the fleet card's footer "View in workspace →" is the affordance for
// digging into a single subagent. The full <SubagentCard> remains for
// standalone subagents (no fleet wrapper).

import type { ReactElement } from "react";
import { useElapsedSeconds } from "../tools/useElapsedSeconds";
import type { SubagentCardViewModel } from "./subagentCardViewModel";

export interface FleetSubagentRowProps {
  view: SubagentCardViewModel;
  /** 0..1 advisory progress fed by the worker; null while the worker
   *  hasn't reported a number yet. CSS handles the running animation
   *  when fillFraction is null. */
  progress?: number | null;
}

export function FleetSubagentRow({
  view,
  progress,
}: FleetSubagentRowProps): ReactElement {
  const elapsedSeconds = useElapsedSeconds(!view.terminal, view.startedAt);
  const elapsedLabel =
    view.terminal && view.durationMs !== null
      ? formatDuration(view.durationMs)
      : `${elapsedSeconds}s`;
  const fillFraction = view.terminal
    ? 1
    : typeof progress === "number"
      ? Math.max(0, Math.min(1, progress))
      : null;
  const showStatusWord =
    view.terminal && (view.status === "failed" || view.status === "cancelled");
  return (
    <div
      className="subagent-fleet-row"
      data-status={view.status}
      data-task-id={view.taskId ?? undefined}
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
      <span className="subagent-fleet-row__elapsed">
        {elapsedLabel}
        {showStatusWord ? ` · ${view.status}` : ""}
      </span>
    </div>
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
