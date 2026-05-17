import type { SubagentLifecycleStatus } from "@enterprise-search/api-types";

// Canonical normalisation + classification for SubagentLifecycleStatus.
//
// Before this file existed, three separate sites projected the wire
// status string into a SubagentLifecycleStatus, and they disagreed:
//   - subagentReducer.terminalStatus dropped "timed_out" silently and
//     rewrote it to "completed", which made the projected entry
//     disagree with what the live-event UI rendered.
//   - subagentCardViewModel.normaliseStatus preserved "timed_out" but
//     missed some aliases ("success", "succeeded", "error", "canceled",
//     "timeout").
//   - useSubagentActivities.statusFromArgs accepted every alias but
//     re-implemented the table.
// All callers now go through normaliseLifecycleStatus so the set of
// accepted aliases and the projected output are one decision.
//
// Predicate helpers (isRunningStatus / isTerminalStatus / isPausedStatus
// / isResumableStatus) replace inline `=== "running" || === "queued"`
// chains across the UI and workspace pane.

// Backend wire-format aliases. Everything maps to the canonical
// SubagentLifecycleStatus literal on the right.
const STATUS_ALIAS: Record<string, SubagentLifecycleStatus> = {
  queued: "queued",
  running: "running",
  started: "running",
  progress: "running",
  paused: "paused",
  completed: "completed",
  succeeded: "completed",
  success: "completed",
  complete: "completed",
  cancelled: "cancelled",
  canceled: "cancelled",
  failed: "failed",
  error: "failed",
  timed_out: "timed_out",
  timeout: "timed_out",
};

// `paused` is intentionally NOT a running state — fleet-row "is anything
// running" checks classify a paused subagent as not running so the
// progress bar freezes and the chrome flips to amber.
const RUNNING_STATES: ReadonlySet<SubagentLifecycleStatus> = new Set([
  "queued",
  "running",
]);

// Statuses that a `subagent_resumed` event is allowed to flip back to
// `running`. Terminal states win — a completed/cancelled/failed/timed_out
// subagent stays terminal even if a stray resume arrives.
const RESUMABLE_STATES: ReadonlySet<SubagentLifecycleStatus> = new Set([
  "queued",
  "paused",
]);

const TERMINAL_STATES: ReadonlySet<SubagentLifecycleStatus> = new Set([
  "completed",
  "cancelled",
  "failed",
  "timed_out",
]);

/**
 * Map a raw wire-format status string to a canonical
 * SubagentLifecycleStatus.
 *
 * @param raw The raw status string (may be unknown casing, an alias, or null).
 * @param isError When true, the result is always "failed" regardless of `raw`.
 *   (Mirrors the historical UI contract: a tool-part marked `isError` is
 *   treated as failed even if the args still carry "running" or
 *   "completed".)
 * @param fallback The canonical status to return when `raw` is null,
 *   empty, or not in the alias table. Defaults to "running" — the
 *   reducer is initialised optimistically, and unknown statuses are
 *   treated as in-flight.
 */
export function normaliseLifecycleStatus(
  raw: string | null | undefined,
  isError: boolean = false,
  fallback: SubagentLifecycleStatus = "running",
): SubagentLifecycleStatus {
  if (isError) return "failed";
  const lc = raw?.trim().toLowerCase() ?? "";
  if (lc.length === 0) return fallback;
  return STATUS_ALIAS[lc] ?? fallback;
}

/**
 * Same as normaliseLifecycleStatus but with a "completed" fallback —
 * for `subagent_completed` events where the wire-format status is
 * sometimes omitted but the lifecycle is known terminal.
 */
export function normaliseTerminalStatus(
  raw: string | null | undefined,
  isError: boolean = false,
): SubagentLifecycleStatus {
  return normaliseLifecycleStatus(raw, isError, "completed");
}

export function isRunningStatus(status: SubagentLifecycleStatus): boolean {
  return RUNNING_STATES.has(status);
}

export function isTerminalStatus(status: SubagentLifecycleStatus): boolean {
  return TERMINAL_STATES.has(status);
}

export function isPausedStatus(status: SubagentLifecycleStatus): boolean {
  return status === "paused";
}

export function isResumableStatus(status: SubagentLifecycleStatus): boolean {
  return RESUMABLE_STATES.has(status);
}
