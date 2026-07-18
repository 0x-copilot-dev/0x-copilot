// activityApi — composition binder for the Activity destination
// (desktop redesign, Phase 4 · PR-4.6).
//
// Source: docs/plan/desktop-redesign/phase-4/PRD.md FR-4.14/4.15/4.18/4.19
// + §11 (High risk: "No run-list endpoint"). Activity is the single
// run-history feed that ABSORBS the former Agents, Inbox, and audit-log
// surfaces. There is no dedicated `GET /v1/activity` run-list endpoint
// yet, so this binder COMPOSES the two endpoints that do exist —
// `/v1/agent/conversations` (the run spine: one row per conversation's
// latest run) and `/v1/audit` (meta enrichment: the tools/connectors a
// run touched) — into a flat, newest-first `ActivityRunRow[]`. The
// `<ActivityDestination>` component stays endpoint-agnostic: projection
// happens here, day grouping happens in-shell (via the injected `now`).
//
// When a paginated `GET /v1/activity` lands in the backend workstream it
// drops in behind `fetchActivity` with NO change to the component or the
// route — only this file swaps its data source.
//
// This lives in `apps/frontend` (a host binder), never in
// `@0x-copilot/chat-surface`: composing product endpoints is host work
// (FR-4.3/4.32). Types come from `@0x-copilot/api-types`.

import type {
  ActivityRunRow,
  ActivityRunStatus,
  AgentRunStatus,
  AuditEvent,
  Conversation,
  RunId,
  SectionResult,
} from "@0x-copilot/api-types";

import { listConversations } from "../../../api/agentApi";
import { listAuditEvents } from "../../../api/auditApi";
import type { RequestIdentity } from "../../../api/config";
import { errorMessage } from "../../../utils/errors";

/** How many conversations to pull for the run spine (server clamps). */
const DEFAULT_CONVERSATION_LIMIT = 50;
/** How many audit rows to scan for tool/connector meta enrichment. */
const DEFAULT_AUDIT_LIMIT = 200;

/**
 * Project a runtime run status onto the Activity taxonomy
 * (FR-4.15). Single declaration site so the mapping can't drift between
 * the projection and any future consumer.
 *
 * Notable folds:
 * - `waiting_for_approval` → `needs_input` — this is how the former Inbox
 *   surface (approval-blocked runs) reappears in Activity (FR-4.18).
 * - `queued` / `cancelling` → `running` — still in flight from the
 *   viewer's perspective; the row remains a live jump-into-Run target.
 * - `failed` / `timed_out` / `cancelled` → `stopped` — terminal without a
 *   clean completion; the Activity taxonomy has one "stopped" bucket.
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
 * Best-effort tool/connector label for one audit row. Prefers the
 * explicit connector / server / tool identifiers the backend stamps in
 * `metadata`; returns `null` when the row carries nothing nameable (those
 * rows still exist for the run, they just add no meta text).
 *
 * Kept permissive on purpose: the audit metadata shape is heterogeneous
 * across streams (mcp / skill / identity / deploy), and this binder is a
 * stopgap until `/v1/activity` projects the touched-tools list itself.
 */
function auditLabel(row: AuditEvent): string | null {
  // `metadata` is a required `Record<string, unknown>` on the wire, so
  // index access yields `unknown` — narrowed to a usable string below.
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
 * conversation id), collecting the distinct tool/connector labels each
 * touched. A run's meta line is later looked up under BOTH its run id and
 * its conversation id, since audit rows reference whichever the emitting
 * stream had to hand.
 */
function buildMetaIndex(
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

function startedAtMs(iso: string): number {
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? Number.NEGATIVE_INFINITY : ms;
}

/**
 * Compose conversations + audit into the flat, newest-first
 * `ActivityRunRow[]` the destination renders (FR-4.15/4.19). Pure — no
 * I/O — so the projection is directly unit-testable.
 *
 * The conversation list is the run SPINE: one row per conversation whose
 * latest run exists (`latest_run_id` + `latest_run_status` present). A
 * conversation that never ran is not a run and is skipped. Audit rows add
 * only the meta line (tools/connectors touched); when audit is absent the
 * rows still render, just without meta (graceful degradation, PRD §11).
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
      title: title !== undefined && title.length > 0 ? title : "Untitled run",
      status: mapRunStatus(status),
      meta: [...labels].sort((a, b) => a.localeCompare(b)).join(" · "),
      started_at: conversation.updated_at,
    });
  }

  // Newest-first across the whole flat list (FR-4.19). The component
  // re-buckets by day, but a stable newest-first order keeps the feed
  // deterministic and lets a future paginated endpoint drop in unchanged.
  rows.sort((a, b) => startedAtMs(b.started_at) - startedAtMs(a.started_at));
  return rows;
}

/**
 * Fetch + compose the Activity feed, returned in the `SectionResult`
 * shape the destination consumes (FR-4.2 states derive from it). The
 * conversation list is required — its failure surfaces as
 * `status:"error"` (+ Retry). Audit is enrichment-only: its failure
 * degrades to conversations-without-meta rather than failing the feed
 * (PRD §11 rollback: "Activity renders conversations-only until audit
 * compose lands").
 */
export async function fetchActivity(
  identity: RequestIdentity,
  options: { conversationLimit?: number; auditLimit?: number } = {},
): Promise<SectionResult<ActivityRunRow[]>> {
  try {
    const [conversationList, auditRows] = await Promise.all([
      listConversations(identity, {
        limit: options.conversationLimit ?? DEFAULT_CONVERSATION_LIMIT,
        includeArchived: true,
      }),
      // Audit is best-effort meta enrichment; a failed / degraded audit
      // read must not sink the whole feed.
      listAuditEvents(identity, {
        limit: options.auditLimit ?? DEFAULT_AUDIT_LIMIT,
      })
        .then((response) => response.rows)
        .catch(() => [] as AuditEvent[]),
    ]);

    return {
      status: "ok",
      data: projectActivityRows(conversationList.conversations, auditRows),
    };
  } catch (error: unknown) {
    return {
      status: "error",
      error: errorMessage(error, "Could not load activity."),
    };
  }
}
