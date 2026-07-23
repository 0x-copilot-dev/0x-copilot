// activityProjection — the SINGLE wire→view-model projection for the Activity
// feed (PRD-04 Seam C, README C7).
//
// Both hosts (apps/frontend `activityApi.ts`, apps/desktop `destinationBinders`)
// previously carried a byte-for-byte copy of this composition — same
// `buildMetaIndex`, same never-ran skip rule, same `"Untitled run"` fallback,
// same sort — so a field added on one host could silently drift from the other.
// It is hoisted here (precedent: `destinations/run/chatProjection.ts`,
// `destinations/run/approvalProjection.ts` — pure projections already live in
// the destination) so the new `conversation_id` field is stamped IDENTICALLY on
// both hosts, and so PRD-08 can later feed real meta strings THROUGH this
// projector by adding a consumer rather than forking the module.
//
// There is no dedicated run-list endpoint yet, so this composes the two
// endpoints that DO exist — `GET /v1/agent/conversations` (the run spine: one
// row per conversation whose latest run exists) and `GET /v1/audit` (meta
// enrichment: the tools/connectors a run touched) — into a flat, newest-first
// `ActivityRunRow[]`. Pure — no I/O — so it is directly unit-testable; the
// hosts keep their own fetch/transport code and call this on the results.

import type {
  ActivityRunRow,
  ActivityRunStatus,
  AgentRunStatus,
  AuditEvent,
  Conversation,
  ConversationId,
  RunId,
} from "@0x-copilot/api-types";

/**
 * Project a runtime run status onto the Activity taxonomy (FR-4.15). Single
 * declaration site so the mapping can't drift between the projection and any
 * future consumer.
 *
 * Notable folds:
 * - `waiting_for_approval` → `needs_input` — how approval-blocked runs (the
 *   former Inbox surface) reappear in Activity (FR-4.18).
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
 * Best-effort tool/connector label for one audit row. Prefers the explicit
 * connector / server / tool identifiers the backend stamps in `metadata`;
 * returns `null` when the row carries nothing nameable.
 */
function auditLabel(row: AuditEvent): string | null {
  const meta: Record<string, unknown> = row.metadata;
  const candidates = [
    meta.connector_id,
    meta.server_id,
    meta.display_name,
    meta.tool_name,
  ];
  for (const candidate of candidates) {
    if (typeof candidate === "string" && candidate.trim().length > 0) {
      return candidate.trim();
    }
  }
  return null;
}

/**
 * Index the audit rows by the resource id they reference (a run id or a
 * conversation id), collecting the distinct tool/connector labels each touched.
 * A run's meta line is later looked up under BOTH its run id and its
 * conversation id, since audit rows reference whichever the emitting stream had
 * to hand.
 */
export function buildMetaIndex(
  auditRows: readonly AuditEvent[],
): Map<string, Set<string>> {
  const index = new Map<string, Set<string>>();
  for (const row of auditRows) {
    const label = auditLabel(row);
    if (label === null) continue;
    const key = row.resource_id;
    if (typeof key !== "string" || key.length === 0) continue;
    let set = index.get(key);
    if (set === undefined) {
      set = new Set<string>();
      index.set(key, set);
    }
    set.add(label);
  }
  return index;
}

// Sort keyed on `started_at`. Unparseable timestamps sort LAST (they never hide
// real runs) — this is the web copy's stricter NaN-guarded behaviour, made
// canonical for both hosts (the desktop copy lacked the guard; PRD-04 Risks).
function startedAtMs(iso: string): number {
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? Number.NEGATIVE_INFINITY : ms;
}

/**
 * Compose conversations + audit into the flat, newest-first `ActivityRunRow[]`
 * the destination renders (FR-4.15/4.19).
 *
 * The conversation list is the run SPINE: one row per conversation whose latest
 * run exists (`latest_run_id` + `latest_run_status` present). A conversation
 * that never ran is a chat, not a run, and is skipped. Audit rows add only the
 * meta line; when audit is absent the rows still render, just without meta.
 *
 * Each row carries BOTH `conversation_id` (the navigable identity — the Run
 * cockpit binds by conversation) and `run_id` (the specific run named), PRD-04
 * Seam C. The two are distinct fields, not aliases.
 */
export function projectActivityRows(
  conversations: readonly Conversation[],
  auditRows: readonly AuditEvent[],
): ActivityRunRow[] {
  const metaIndex = buildMetaIndex(auditRows);
  const rows: ActivityRunRow[] = [];

  for (const conversation of conversations) {
    const runId = conversation.latest_run_id;
    const status = conversation.latest_run_status;
    // Never-ran conversations are chats, not runs — skip them.
    if (
      runId === null ||
      runId === undefined ||
      runId === "" ||
      status === null ||
      status === undefined
    ) {
      continue;
    }

    const labels = new Set<string>();
    for (const label of metaIndex.get(runId) ?? []) labels.add(label);
    for (const label of metaIndex.get(conversation.conversation_id) ?? [])
      labels.add(label);

    const title = conversation.title?.trim();
    rows.push({
      run_id: runId as RunId,
      conversation_id: conversation.conversation_id as ConversationId,
      title: title !== undefined && title.length > 0 ? title : "Untitled run",
      status: mapRunStatus(status),
      meta: [...labels].sort((a, b) => a.localeCompare(b)).join(" · "),
      started_at: conversation.updated_at,
    });
  }

  // Newest-first across the whole flat list (FR-4.19). The component re-buckets
  // by day; a stable newest-first order keeps the feed deterministic and lets a
  // future paginated endpoint drop in unchanged.
  rows.sort((a, b) => startedAtMs(b.started_at) - startedAtMs(a.started_at));
  return rows;
}
