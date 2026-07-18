import type { SubagentPauseReason } from "./subagentCardViewModel";

// Shared label helpers for subagent row + card chrome. Before this file
// existed, SubagentCard and FleetSubagentRow each defined their own copy
// of `jumpLabelForPause` and `formatDuration` (byte-identical), plus a
// constellation of pause-reason → string helpers with subtly different
// outputs.

/** Short single-word label used inside the "Paused · X" badge. */
export function pauseShortLabel(reason: SubagentPauseReason): string {
  switch (reason) {
    case "approval":
      return "approval";
    case "mcp_auth":
      return "connector";
    case "ask_a_question":
      return "answer";
  }
}

/** Phrase used in the expanded timeline disclosure and visible "waiting" text. */
export function pauseFullLabel(
  reason: SubagentPauseReason | undefined,
): string {
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

/** Slightly more verbose form for aria-label / screen-reader text. */
export function pauseAriaLabel(
  reason: SubagentPauseReason | undefined,
): string {
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

/** Action-label text used inside the "Review {X} →" button. */
export function pauseJumpLabel(
  reason: SubagentPauseReason | undefined,
): string {
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

/** Format a millisecond duration as "Xms" / "X.Xs" / "Xm Ys". */
export function formatSubagentDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds - minutes * 60);
  return `${minutes}m ${remainder}s`;
}
