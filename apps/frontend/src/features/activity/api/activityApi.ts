// activityApi — composition binder for the Activity destination
// (desktop redesign, Phase 4 · PR-4.6 · PRD-08 D1/D1c).
//
// Activity is the single run-history feed that ABSORBS the former Agents,
// Inbox, and audit-log surfaces. It reads PRD-05's run-history spine
// (`GET /v1/agent/runs`) — a paginated, newest-first, one-row-per-RUN list
// carrying all eight statuses AND the three meta counters (connector_count /
// step_count / pending_approval_count, PRD-08 D1). The projection
// (`projectActivityRows`) is the SHARED one from `@0x-copilot/chat-surface`, so
// web and desktop compose byte-identical rows + meta strings.
//
// PRD-08 D1c — the legacy audit fan-out is GONE. Activity previously composed
// `/v1/agent/conversations` + `/v1/audit`, derived the meta line by scanning an
// audit feed whose rows don't join to runs, and swallowed a 401/403 on the
// audit half into `[]` (`.catch(() => [])`) — so an RBAC-gated workspace saw a
// silent degrade to a list of titles with no error. Reading your own activity
// never needed an admin audit-export scope. Now Activity issues ONE request;
// a 401/403/500 surfaces through `status:"error"` → the error state + Retry.
//
// This lives in `apps/frontend` (a host binder), never in
// `@0x-copilot/chat-surface`: composing product endpoints is host work
// (FR-4.3/4.32). Types come from `@0x-copilot/api-types`.

import type { ActivityRunRow, SectionResult } from "@0x-copilot/api-types";
// PRD-04 Seam C / PRD-08 D1 — the wire→view-model projection
// (`projectActivityRows`, `mapRunStatus`) is hoisted to the shared destination
// so both hosts stamp `conversation_id` + the meta line identically. This host
// keeps only its fetch/compose I/O.
import { projectActivityRows, mapRunStatus } from "@0x-copilot/chat-surface";

import { listRunHistory } from "../../../api/agentApi";
import type { RequestIdentity } from "../../../api/config";
import { errorMessage } from "../../../utils/errors";

// Re-exported for callers that imported these from the host binder before the
// hoist (keeps the public import site stable).
export { projectActivityRows, mapRunStatus };

/** How many runs to pull for the history feed (server clamps). */
const DEFAULT_RUN_LIMIT = 50;

/**
 * Fetch + project the Activity feed, returned in the `SectionResult`
 * shape the destination consumes (FR-4.2 states derive from it). A failure to
 * read the run list surfaces as `status:"error"` (+ Retry) — there is no longer
 * a second, best-effort endpoint whose failure could be swallowed (D1c).
 */
export async function fetchActivity(
  identity: RequestIdentity,
  options: { runLimit?: number } = {},
): Promise<SectionResult<ActivityRunRow[]>> {
  try {
    const history = await listRunHistory(identity, {
      limit: options.runLimit ?? DEFAULT_RUN_LIMIT,
    });
    return {
      status: "ok",
      data: projectActivityRows(history.runs),
    };
  } catch (error: unknown) {
    return {
      status: "error",
      error: errorMessage(error, "Could not load activity."),
    };
  }
}
