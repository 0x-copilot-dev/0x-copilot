// activityProjection — the SINGLE wire→view-model projection for the Activity
// feed (PRD-04 Seam C / PRD-08 D1, README C7).
//
// Both hosts (apps/frontend `activityApi.ts`, apps/desktop `destinationBinders`)
// call this on the run-list response so a field added on one host can never
// drift from the other.
//
// DATA SOURCE (PRD-08 D1 — cut over from the legacy conversation+audit compose):
// this projects `GET /v1/agent/runs` (PRD-05's org-scoped, all-status,
// one-row-per-RUN `RunHistoryEntry[]`) into the flat `ActivityRunRow[]` the
// destination renders. The meta line ("4 apps · 7 steps · awaiting 1 approval")
// is composed by `formatActivityMeta` from the three server-projected counters
// on each entry — NOT scanned out of an audit feed whose rows don't join to
// runs (the defect this PRD removes). The endpoint is already newest-first
// (`created_at DESC, run_id DESC`); the destination re-buckets by day, so this
// preserves the server order without re-sorting.
//
// NOTE (deviation from PRD-08's dependency assumption): PRD-08 attributed this
// run-list cut-over to PRD-04. PRD-04 as merged hoisted the conversation+audit
// projection into this module but did NOT cut over to `GET /v1/agent/runs`, so
// PRD-08 performs the cut-over here — it is the only way the counter fields
// (which live on `RunHistoryEntry`, not on `Conversation`/`AuditEvent`) can
// reach the meta line, and the only way the audit fan-out and its swallowed 403
// can be deleted (DoD 12/13). Reported loudly in the PR return value.
//
// Pure — no I/O — so it is directly unit-testable; the hosts keep their own
// fetch/transport code and call this on the results.

import type {
  ActivityRunRow,
  ActivityRunStatus,
  AgentRunStatus,
  ConversationId,
  RunHistoryEntry,
  RunId,
} from "@0x-copilot/api-types";

import { formatActivityMeta } from "./meta";

/**
 * Project a runtime run status onto the Activity taxonomy (FR-4.15). Single
 * declaration site so the mapping can't drift between the projection and any
 * future consumer.
 *
 * Notable folds:
 * - `waiting_for_approval` → `needs_input` — how approval-blocked runs (the
 *   former Inbox surface) reappear in Activity (FR-4.18); the design's `paused`
 *   slot (PRD-08 D2).
 * - `queued` / `cancelling` → `running` — still in flight; the row stays a live
 *   jump-into-Run target.
 * - `failed` / `timed_out` / `cancelled` → `stopped` — terminal without a clean
 *   completion; Activity has one "stopped" bucket.
 */
export function mapRunStatus(status: AgentRunStatus): ActivityRunStatus {
  switch (status) {
    case "running":
    case "queued":
    case "cancelling":
      return "running";
    case "waiting_for_approval":
      return "needs_input";
    case "completed":
      return "done";
    case "cancelled":
    case "failed":
    case "timed_out":
      return "stopped";
  }
}

/**
 * Project the run-history page (`RunHistoryEntry[]` from `GET /v1/agent/runs`)
 * into the flat `ActivityRunRow[]` the destination renders (PRD-08 D1).
 *
 * Each row carries BOTH `conversation_id` (the navigable identity — the Run
 * cockpit binds by conversation) and `run_id` (the specific run named), PRD-04
 * Seam C. Row time is `started_at ?? created_at`: a queued run has no
 * `started_at`, so `created_at` (NOT NULL, the keyset key) stands in. The meta
 * string comes from `formatActivityMeta` over the three counters; when they are
 * all unknown/zero it is `""` and the row renders no sub-line.
 */
export function projectActivityRows(
  entries: readonly RunHistoryEntry[],
): ActivityRunRow[] {
  return entries.map((entry) => {
    const title = entry.conversation_title?.trim();
    return {
      run_id: entry.run_id as RunId,
      conversation_id: entry.conversation_id as ConversationId,
      title: title !== undefined && title.length > 0 ? title : "Untitled run",
      status: mapRunStatus(entry.status),
      meta: formatActivityMeta({
        connector_count: entry.connector_count,
        step_count: entry.step_count,
        pending_approval_count: entry.pending_approval_count,
      }),
      // A queued run has no `started_at`; fall back to `created_at` (NOT NULL).
      started_at: entry.started_at ?? entry.created_at,
    };
  });
}
