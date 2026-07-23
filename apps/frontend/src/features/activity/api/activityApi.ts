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
  AuditEvent,
  SectionResult,
} from "@0x-copilot/api-types";
// PRD-04 Seam C — the wire→view-model projection (`projectActivityRows`,
// `mapRunStatus`) is hoisted to the shared destination so both hosts stamp
// `conversation_id` identically. This host keeps only its fetch/compose I/O.
import { projectActivityRows, mapRunStatus } from "@0x-copilot/chat-surface";

import { listConversations } from "../../../api/agentApi";
import { listAuditEvents } from "../../../api/auditApi";
import type { RequestIdentity } from "../../../api/config";
import { errorMessage } from "../../../utils/errors";

// Re-exported for callers that imported these from the host binder before the
// hoist (keeps the public import site stable).
export { projectActivityRows, mapRunStatus };

/** How many conversations to pull for the run spine (server clamps). */
const DEFAULT_CONVERSATION_LIMIT = 50;
/** How many audit rows to scan for tool/connector meta enrichment. */
const DEFAULT_AUDIT_LIMIT = 200;

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
