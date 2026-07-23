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

import type { RunId } from "./brands";

// ---------------------------------------------------------------------------
// Status taxonomy (DESIGN-SPEC §3 — Activity row status chip)
// ---------------------------------------------------------------------------

/**
 * Canonical status values for an activity run row, as the runtime SSOT
 * (value tuple) the union derives from. Kept as an `as const` tuple so
 * the union is also runtime-enumerable (status→tone mapping, tests) with
 * a single declaration site — no value/type drift.
 *
 * * `running`     — the run is in flight (live/jade chip); row → Run.
 * * `done`        — the run completed (muted chip).
 * * `paused`      — the run is paused (amber chip).
 * * `stopped`     — the run was cancelled/stopped by the user or system.
 * * `needs_input` — the run is blocked on an approval / clarifying answer;
 *                   this is how the former Inbox items surface in Activity
 *                   (PRD FR-4.18).
 */
export const ACTIVITY_RUN_STATUSES = [
  "running",
  "done",
  "paused",
  "stopped",
  "needs_input",
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
  readonly title: string;
  readonly status: ActivityRunStatus;
  /** One-line summary of the tools / connectors the run touched. */
  readonly meta: string;
  /** ISO-8601 UTC; server-stamped run start. Drives day grouping + time. */
  readonly started_at: string;
}
