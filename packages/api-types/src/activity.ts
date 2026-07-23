// Activity destination (desktop redesign, Phase 4) — run-history read model.
//
// Source: docs/plan/desktop-redesign/phase-4/PRD.md §5 (new types) +
// FR-4.14/4.15/4.16/4.19, and docs/plan/desktop-redesign/design-reference/
// DESIGN-SPEC.md §3 (List destinations — Activity) + §8 (data entities:
// `ACTIVITY`).
//
// Activity is the single run-history feed that ABSORBS the former Agents,
// Inbox, and audit-log surfaces (PRD §3 US-4.5): every run the agent has
// done, grouped by day. The run-history data spine is `GET /v1/agent/runs`
// (PRD-05) — a paginated, newest-first, one-row-per-RUN list carrying all
// eight statuses (`RunHistoryEntry` / `RunHistoryResponse` in ./index.ts).
// PRD-04 owns the host-binder cutover from the legacy conversation-spine
// projection to that endpoint and the shared `mapRunStatus` fold that turns
// an `AgentRunStatus` into an `ActivityRunStatus`; this file stays wire-only.
// Day grouping (Today / Yesterday / explicit date) is derived in the shell,
// not on the wire — no `DayGroup` type lives here.
//
// Wire-only file: no business logic, no HTTP client, no view models. The
// server is the source of truth; this package mirrors the public payloads
// exactly as the facade serves them.
//
// Canonical types reused from elsewhere (DO NOT re-declare):
// * `RunId` — branded ID in ./brands.ts (`ItemRef` kind="run" resolves to
//   `RunId` in ./refs.ts; the running-row open target).

import type { ConversationId, RunId } from "./brands";

// ---------------------------------------------------------------------------
// Status taxonomy (DESIGN-SPEC §3 — Activity row status chip)
// ---------------------------------------------------------------------------

/**
 * Canonical status values for an activity run row, as the runtime SSOT
 * (value tuple) the union derives from. Kept as an `as const` tuple so
 * the union is also runtime-enumerable (status→tone mapping, tests) with
 * a single declaration site — no value/type drift.
 *
 * Exactly FOUR values, and every one has a producer — the total fold from
 * the eight-value {@link AgentRunStatus} (PRD-08 D2):
 *
 * | `AgentRunStatus`                   | `ActivityRunStatus` | design chip   |
 * | ---------------------------------- | ------------------- | ------------- |
 * | `queued`, `running`, `cancelling`  | `running`           | `chip--ok`+dot|
 * | `completed`                        | `done`              | `chip--ok`    |
 * | `waiting_for_approval`             | `needs_input`       | `chip--warn`  |
 * | `cancelled`, `failed`, `timed_out` | `stopped`           | `chip--off`   |
 *
 * * `running`     — the run is in flight (live/jade chip); row → Run.
 * * `done`        — the run completed (muted/jade chip).
 * * `stopped`     — the run was cancelled/stopped/failed/timed-out.
 * * `needs_input` — the run is blocked on an approval / clarifying answer;
 *                   this is how the former Inbox items surface in Activity,
 *                   and it is the design's `paused` slot (`chip--warn`). The
 *                   runtime has no distinct `paused` state — the design's own
 *                   copy ("paused — needed your approval on a swap") names
 *                   `waiting_for_approval` — so there is no `paused` member
 *                   (PRD-08 D2). `AgentRunStatus` is deliberately untouched.
 */
export const ACTIVITY_RUN_STATUSES = [
  "running",
  "done",
  "needs_input",
  "stopped",
] as const;

/** Activity run row status. Drift from the server projection is a bug. */
export type ActivityRunStatus = (typeof ACTIVITY_RUN_STATUSES)[number];

// ---------------------------------------------------------------------------
// Activity row
// ---------------------------------------------------------------------------

/**
 * One run in the day-grouped Activity feed. `run_id` is the open target
 * (running rows → Run cockpit; non-running rows → a read-only run detail).
 * `meta` is a one-line summary of the tools / connectors the run touched.
 * `started_at` drives both the day bucketing and the mono relative time
 * (formatted client-side from the ISO string; never pre-formatted on the
 * wire, per FR-4.4).
 */
export interface ActivityRunRow {
  readonly run_id: RunId;
  /**
   * The conversation the run belongs to — the navigable identity. Both host
   * projections already hold this value (the conversation-list spine is keyed
   * by it); it is now carried on the row so activating a row can open the Run
   * cockpit bound to the conversation (the cockpit binds by conversation id,
   * never by run id). Distinct from `run_id` (PRD-04 Seam C).
   */
  readonly conversation_id: ConversationId;
  readonly title: string;
  readonly status: ActivityRunStatus;
  /** One-line summary of the tools / connectors the run touched. */
  readonly meta: string;
  /** ISO-8601 UTC; server-stamped run start. Drives day grouping + time. */
  readonly started_at: string;
}
