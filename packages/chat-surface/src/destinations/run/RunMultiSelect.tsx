// RunMultiSelect — the Run cockpit's multi-run selector (PR-3.11).
//
// Source: docs/plan/desktop-redesign/phase-3/PRD.md — US-3.9 / FR-3.26
//   "The multi-run state MUST render a run selector (goal, status, time) when
//    the conversation has >1 run and rebind the projection/tabs/timeline/surface
//    on selection."
//
// Ownership: RunMultiSelect is presentation only. It renders one selectable
// entry per run (goal · status · time) and calls `onSelectRun(runId)`; the
// RunDestination shell wires that to `useRunSession.selectRun`, which flips the
// session's active run so the event projector, tabs, timeline, and surface all
// rebind (the shell also clears scrub/tabs so mode/scrub reset appropriately —
// FR-3.26). When the conversation has zero or one run there is nothing to pick,
// so the component renders NO chrome (returns `null`) — the selector never
// clutters the single-run cockpit.
//
// The strip is a `role="tablist"` (mirroring the header's mode segmented
// control): `aria-selected`, roving `tabIndex`, and ArrowLeft/ArrowRight cycling
// over the runs — FR-3.29. Tokens only (sky accent; jade=live/success,
// ember=failed), no lime — FR-3.24 / FR-3.30.

import {
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactElement,
} from "react";

import type { AgentRunStatus } from "@0x-copilot/api-types";

import type { RunListItem } from "./useRunSession";

export interface RunMultiSelectProps {
  /** All runs resolved for the conversation (from `useRunSession.runs`). */
  readonly runs: readonly RunListItem[];
  /** The currently-bound run (`useRunSession.runId`), or `null` if none. */
  readonly selectedRunId: string | null;
  /** Fired when the user picks a run; wired to `useRunSession.selectRun`. */
  readonly onSelectRun: (runId: string) => void;
}

/**
 * The multi-run selector. Renders nothing when the conversation has ≤1 run —
 * there is no choice to make, so the single-run cockpit stays chrome-free
 * (US-3.9 / FR-3.26).
 */
export function RunMultiSelect(
  props: RunMultiSelectProps,
): ReactElement | null {
  const { runs, selectedRunId, onSelectRun } = props;

  if (runs.length <= 1) {
    return null;
  }

  const selectedIndex = runs.findIndex((run) => run.runId === selectedRunId);

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>): void => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") {
      return;
    }
    event.preventDefault();
    // Anchor arrow-nav on the selected run (or the first when none is bound).
    const from = selectedIndex < 0 ? 0 : selectedIndex;
    const dir = event.key === "ArrowLeft" ? -1 : 1;
    const next = (from + dir + runs.length) % runs.length;
    onSelectRun(runs[next].runId);
  };

  return (
    <div
      role="tablist"
      aria-label="Run selection"
      data-testid="run-multi-select"
      style={stripStyle}
      onKeyDown={handleKeyDown}
    >
      <span data-testid="run-multi-select-label" style={labelStyle}>
        {runs.length} runs
      </span>
      {runs.map((run) => {
        const selected = run.runId === selectedRunId;
        const tone = statusTone(run.status);
        return (
          <button
            key={run.runId}
            type="button"
            role="tab"
            aria-selected={selected}
            aria-label={`View run: ${runGoalLabel(run.goal)}`}
            tabIndex={selected ? 0 : -1}
            data-testid={`run-select-${run.runId}`}
            data-selected={selected}
            onClick={() => onSelectRun(run.runId)}
            style={chipStyle(selected)}
          >
            <span
              aria-hidden="true"
              data-testid={`run-select-dot-${run.runId}`}
              style={dotStyle(tone.color)}
            />
            <span style={chipGoalStyle}>{runGoalLabel(run.goal)}</span>
            {tone.label !== "" ? (
              <span
                data-testid={`run-select-status-${run.runId}`}
                style={statusStyle(tone.color)}
              >
                {tone.label}
              </span>
            ) : null}
            <span
              data-testid={`run-select-time-${run.runId}`}
              style={timeStyle}
            >
              {formatStartedAt(run.startedAt)}
            </span>
          </button>
        );
      })}
    </div>
  );
}

// ============================================================
// Helpers
// ============================================================

/** The run's goal, or an honest fallback so a chip is never blank. */
function runGoalLabel(goal: string | null): string {
  return goal !== null && goal.trim() !== "" ? goal : "Untitled run";
}

interface StatusTone {
  readonly label: string;
  /** A `var(--color-*)` token for the status dot + label. */
  readonly color: string;
}

// Map the run's own status onto a short label + a single-accent-safe token.
// jade (--color-success) = live/success; ember (--color-danger) = failed;
// muted for terminal-neutral; unknown → no label (dot uses the muted tone).
function statusTone(status: AgentRunStatus | null): StatusTone {
  switch (status) {
    case "running":
    case "queued":
    case "cancelling":
      return { label: "Live", color: "var(--color-success, #6ab04c)" };
    case "waiting_for_approval":
      return { label: "Needs you", color: "var(--color-warning, #f0a330)" };
    case "completed":
      return { label: "Done", color: "var(--color-text-muted, #9aa0a6)" };
    case "failed":
    case "timed_out":
      return { label: "Failed", color: "var(--color-danger, #f0764f)" };
    case "cancelled":
      return { label: "Stopped", color: "var(--color-text-muted, #9aa0a6)" };
    default:
      return { label: "", color: "var(--color-text-subtle, #7e7e84)" };
  }
}

/** Format the run's start as `HH:MM` (24h); a dash when there is no time. */
function formatStartedAt(startedAt: string | null): string {
  if (startedAt === null) {
    return "—";
  }
  const parsed = Date.parse(startedAt);
  if (Number.isNaN(parsed)) {
    return "—";
  }
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(parsed));
}

// ============================================================
// Styles (design-system tokens only — sky accent, no lime)
// ============================================================

const stripStyle: CSSProperties = {
  flexShrink: 0,
  display: "flex",
  alignItems: "center",
  gap: 8,
  padding: "8px 16px",
  overflowX: "auto",
  borderBottom: "1px solid var(--color-border, #22252e)",
  background: "var(--color-bg-elevated, #16181f)",
  fontFamily: "var(--font-sans)",
};

const labelStyle: CSSProperties = {
  flexShrink: 0,
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-2xs, 11px)",
  letterSpacing: "0.06em",
  textTransform: "uppercase",
  color: "var(--color-text-muted, #9aa0a6)",
};

const chipStyle = (selected: boolean): CSSProperties => ({
  flexShrink: 0,
  display: "inline-flex",
  alignItems: "center",
  gap: 8,
  maxWidth: 240,
  padding: "5px 12px",
  borderRadius: 999,
  cursor: "pointer",
  fontFamily: "inherit",
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: 500,
  outline: "none",
  color: selected
    ? "var(--color-text, #f4f5f6)"
    : "var(--color-text-muted, #9aa0a6)",
  background: selected
    ? "var(--color-accent-soft, rgba(95,178,236,.12))"
    : "transparent",
  border: selected
    ? "1px solid var(--color-accent, #5fb2ec)"
    : "1px solid var(--color-border, #2a2d31)",
});

const dotStyle = (color: string): CSSProperties => ({
  flexShrink: 0,
  width: 7,
  height: 7,
  borderRadius: "50%",
  background: color,
});

const chipGoalStyle: CSSProperties = {
  minWidth: 0,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const statusStyle = (color: string): CSSProperties => ({
  flexShrink: 0,
  color,
  fontSize: "var(--font-size-2xs, 11px)",
  fontWeight: 600,
  textTransform: "uppercase",
  letterSpacing: "0.04em",
});

const timeStyle: CSSProperties = {
  flexShrink: 0,
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-2xs, 11px)",
  color: "var(--color-text-subtle, #7e7e84)",
};
