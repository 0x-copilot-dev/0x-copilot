// statusTone — the SINGLE source of truth for run-status → chip presentation.
//
// Before this, each destination re-implemented its own status→tone map and they
// disagreed: Activity rendered `done → muted (grey)` and `stopped → danger
// (red)`; Chats rendered `done → muted`. The v3 design is explicit — a finished
// run reads "done" in JADE (success), a user-stopped run reads muted/off, and
// only a LIVE run carries the pulsing dot. Route every status chip through here.
//
// PRD: docs/plan/frontend-parity-v3/PRD-B-tokens-and-status-tone.md (FR-B.2).

import type { StatusTone } from "./StatusPill";

export interface RunStatusPresentation {
  /** The `StatusPill` tone token. */
  readonly tone: StatusTone;
  /** Human label for the chip + a11y. */
  readonly label: string;
  /** Whether to show the pulsing status dot — LIVE (running) states only. */
  readonly showDot: boolean;
}

interface Entry {
  readonly tone: StatusTone;
  readonly label: string;
  /** live states carry the dot. */
  readonly live?: boolean;
}

// Covers every projected status string the destinations use (Activity's
// ActivityRunStatus, Chats' conversation status, and the ai-backend run status
// union). Design semantics:
//   running/queued/streaming → success + dot (LIVE)
//   done/completed           → success (jade — NOT grey)
//   paused/waiting_for_approval → warning (amber)
//   needs_input              → info/accent (folded-inbox CTA; design has no such
//                              state, current-build addition)
//   stopped/cancelled/archived → muted/off (NOT red)
//   failed/error             → error (a genuine failure, distinct from a
//                              user-initiated stop)
const STATUS_MAP: Readonly<Record<string, Entry>> = {
  running: { tone: "ok", label: "Running", live: true },
  queued: { tone: "ok", label: "Queued", live: true },
  streaming: { tone: "ok", label: "Streaming", live: true },
  cancelling: { tone: "warning", label: "Stopping" },
  done: { tone: "ok", label: "Done" },
  completed: { tone: "ok", label: "Done" },
  paused: { tone: "warning", label: "Paused" },
  waiting_for_approval: { tone: "warning", label: "Needs approval" },
  needs_input: { tone: "info", label: "Needs you" },
  stopped: { tone: "muted", label: "Stopped" },
  cancelled: { tone: "muted", label: "Cancelled" },
  canceled: { tone: "muted", label: "Cancelled" },
  archived: { tone: "muted", label: "Archived" },
  failed: { tone: "error", label: "Failed" },
  error: { tone: "error", label: "Error" },
};

function titleCase(s: string): string {
  const cleaned = s.replace(/[_-]+/g, " ").trim();
  return cleaned ? cleaned[0].toUpperCase() + cleaned.slice(1) : "Unknown";
}

/**
 * Map any run/conversation status string to its chip presentation.
 * Unknown statuses fall back to a muted chip with a title-cased label — so a
 * new backend status renders quietly instead of miscolouring.
 */
export function statusTone(status: string): RunStatusPresentation {
  const entry = STATUS_MAP[status];
  if (entry === undefined) {
    return { tone: "muted", label: titleCase(status), showDot: false };
  }
  return { tone: entry.tone, label: entry.label, showDot: entry.live ?? false };
}
