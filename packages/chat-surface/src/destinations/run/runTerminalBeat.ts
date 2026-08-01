// The run's own verdict, projected once from the same event array everything
// else in the cockpit reads (FR-3.3 — no second subscription, no second fold).
//
// This exists because a run-level verdict used to be rendered on the CANVAS,
// where it contradicted the chat pane whenever the agent recovered, and where
// its only action was wired to an SSE reconnect. The verdict now appears in the
// chat stream — where the user is already reading — exactly once, and only when
// the run actually died.

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

/** Terminal statuses that mean the run stopped without finishing its work. */
const DEAD_STATUSES = new Set(["failed", "timed_out"]);

export interface RunTerminalBeat {
  /** Card heading — the typed cause, never a generic "something failed". */
  readonly title: string;
  /** One sentence saying what happened, in the user's terms. */
  readonly copy: string;
  /** The typed failure code, when the runtime named one. */
  readonly code: string | null;
  /**
   * Whether re-sending the goal could plausibly change the outcome. `false`
   * means NO action is offered — an action the system cannot honour is worse
   * than none.
   */
  readonly retryable: boolean;
  /** Which terminal status produced this beat (`failed` / `timed_out`). */
  readonly status: string;
}

/**
 * Fold the run's terminal verdict, or `null` when there is nothing to say.
 *
 * Returns `null` for a run that completed, was cancelled by the user (their own
 * decision needs no verdict), or is still going. Critically it also returns
 * `null` for a run that hit a failing step and then answered — that is the case
 * the old canvas panel got wrong, and no amount of copy makes a false alarm
 * correct.
 */
export function projectRunTerminalBeat(
  events: readonly RuntimeEventEnvelope[],
): RunTerminalBeat | null {
  let terminal: {
    status: string;
    payload: Record<string, unknown>;
    // `presentation` is a TOP-LEVEL envelope field, not a payload key. Reading
    // it from the payload silently yields nothing and the card falls back to
    // generic copy with no retryability — which is the state this whole change
    // exists to remove, so it is captured explicitly here.
    presentation: Record<string, unknown>;
  } | null = null;
  let hasFinalResponse = false;

  for (const event of [...events].sort(
    (a, b) => (a.sequence_no ?? 0) - (b.sequence_no ?? 0),
  )) {
    const type = String(event.event_type);
    const payload = asRecord(event.payload);
    if (type === "final_response") hasFinalResponse = true;
    // Prefer the envelope's own presentation; fall back to a payload copy so a
    // replayed/synthetic frame that nests it still resolves.
    const presentation = {
      ...asRecord(payload.presentation),
      ...asRecord(event.presentation),
    };
    if (type === "run_completed") {
      terminal = {
        status: text(payload.status) ?? "completed",
        payload,
        presentation,
      };
    } else if (
      type === "run_failed" ||
      type === "run_cancelled" ||
      type === "run_timed_out"
    ) {
      terminal = { status: type.slice("run_".length), payload, presentation };
    }
  }

  if (terminal === null || !DEAD_STATUSES.has(terminal.status)) return null;
  // The run died but still delivered an answer. The answer is the outcome; a
  // verdict on top of it would be the same contradiction in a new location.
  if (hasFinalResponse) return null;

  const { presentation } = terminal;
  const code =
    text(presentation.code) ??
    text(terminal.payload.error_code) ??
    text(terminal.payload.code);
  return {
    title: text(presentation.title) ?? fallbackTitle(terminal.status),
    copy:
      text(presentation.summary) ??
      text(terminal.payload.safe_message) ??
      fallbackCopy(terminal.status),
    code,
    // Absent means unknown, and unknown means no action. Only an explicit
    // `true` from the runtime earns a button.
    retryable: presentation.retryable === true,
    status: terminal.status,
  };
}

function fallbackTitle(status: string): string {
  return status === "timed_out" ? "Run timed out" : "Run interrupted";
}

function fallbackCopy(status: string): string {
  return status === "timed_out"
    ? "This run ran out of time before it finished."
    : "This run stopped before it finished.";
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value.trim() : null;
}
