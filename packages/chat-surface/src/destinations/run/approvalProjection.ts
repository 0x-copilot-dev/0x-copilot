// PR-3.10 — approval projection off the SINGLE run event stream.
//
// Source: docs/plan/desktop-redesign/phase-3/PRD.md
//   FR-3.22 (in-chat 4-zone `ApprovalCard` + `ApprovalReceipt`; Focus `.conf-card`)
//   FR-3.12 (Approvals tab pending count badge)
//   FR-3.3  (single projection — no second SSE subscription / projector)
//   §2      ("approvals as content" — the pending approval is the conversation)
//
// This is a PURE selector over the canonical `RuntimeEventEnvelope[]` that
// `useRunSession` owns (the same array `ThreadCanvas` feeds to
// `useEventProjector` and `projectSubagents` reads). It opens NO SSE
// subscription and instantiates NO second `useEventProjector`; `RunDestination`
// memoizes it against `session.events` and threads the result into the two
// approval consumers that live OUTSIDE `ThreadCanvas`:
//   (a) the in-chat `ApprovalCard` / `.conf-card` in `TcChat`  → `approvals`
//   (b) the Approvals-tab pending count in `RunWorkspaceRail`  → `approvalsQueue`
//
// The reduction mirrors the host-owned approval reducer in
// `apps/frontend/.../chatModel`: `approval_requested` opens a pending row,
// `approval_resolved` settles it. Optimistic local decisions (the user clicked
// Approve/Reject in the card before the trailing `approval_resolved` SSE frame
// arrives) are overlaid by `overlayApprovalDecisions` so the card flips to its
// receipt immediately without a second projection.

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

import type { ActivityParam } from "../../approvals";
import type {
  ApprovalsQueueItem,
  ApprovalsQueueProjection,
} from "../../workspace";

/** Binary decision the in-chat card resolves an approval to. */
export type RunApprovalDecision = "approved" | "rejected";

/** Approval category, reusing the rail's `ApprovalsQueueItem` union. */
export type RunApprovalKind = ApprovalsQueueItem["approvalKind"];

/**
 * One approval seen on the run stream, projected for BOTH the in-chat
 * `ApprovalCard`/`.conf-card` and the Approvals-tab queue. The presentational
 * subset (`approvalId`/`title`/`reason`/`summary`/`category`/`params`/
 * `resolved`/`decision`/`createdAtMs`) is structurally compatible with
 * `TcChatApproval` so `RunDestination` can hand it straight to `TcChat` without
 * a mapping pass.
 */
export interface RunApproval {
  readonly approvalId: string;
  /** Verb-first card title ("Post to #launch-aurora"). */
  readonly title: string;
  /** The "why" line under the title. */
  readonly reason: string;
  /** Optional sub-line (from `payload.message` / `payload.summary`). */
  readonly summary: string | null;
  readonly approvalKind: RunApprovalKind;
  /**
   * WC-P5a (AD-7): the connector `server_id` from the approval payload, threaded
   * so the in-chat `mcp_auth` Connect card can call `McpAuthPort.beginAuth`/
   * `skipAuth(serverId)`. Present on `mcp_auth` gates + `mcp_discovery:`
   * suggestions (both `mcp_auth_required` events); null on plain tool approvals.
   */
  readonly serverId: string | null;
  /** Vendor·access pill ({ vendor: "SLACK", access: "ACTION" }); null when unknown. */
  readonly category: {
    readonly vendor: string;
    readonly access: string;
  } | null;
  /** Inset key/value frame projected from `payload.arguments` (primitives only). */
  readonly params: readonly ActivityParam[];
  /** Connector / target preview ("#launch-aurora"); null when absent. */
  readonly target: string | null;
  readonly runId: string | null;
  /** Anchor for the rail's jump-to-card (the requesting event's id). */
  readonly messageId: string;
  /** `sequence_no` of the `approval_requested` event — its conversation anchor. */
  readonly sequenceNo: number;
  /** `created_at` of the request in epoch ms (null if unparseable). */
  readonly createdAtMs: number | null;
  readonly resolved: boolean;
  /** Final decision once resolved (server or optimistic); null while pending. */
  readonly decision: RunApprovalDecision | null;
  /** `created_at` of the resolve event in epoch ms (null when pending/local). */
  readonly resolvedAtMs: number | null;
}

export interface ApprovalProjection {
  /** Every approval seen on the stream, in request (`sequence_no`) order. */
  readonly approvals: readonly RunApproval[];
  /** Still awaiting a decision. */
  readonly pending: readonly RunApproval[];
  /** Settled (server-resolved or optimistic). */
  readonly resolved: readonly RunApproval[];
}

const EMPTY_PROJECTION: ApprovalProjection = {
  approvals: [],
  pending: [],
  resolved: [],
};

const EMPTY_QUEUE: ApprovalsQueueProjection = { pending: [], recent: [] };

const APPROVAL_REQUESTED = "approval_requested";
const APPROVAL_RESOLVED = "approval_resolved";
// WC-P5a (AD-7): the mid-run connector-auth gate + catalog suggestion both ride
// the backend `mcp_auth_required` event (never `approval_requested`), carrying
// `approval_kind: "mcp_auth"` and an `approval_id` that is either the blocking
// `mcp_auth:<run_id>:<server_id>` or the suggestion `mcp_discovery:<run_id>:…`.
// We reduce it exactly like `approval_requested` (open a pending row) so the
// in-chat `mcp_auth` Connect card can render off the SAME single projection
// (FR-3.3); its resolution is a host `mcp_auth_resolved` decision after OAuth
// returns (P5b), never the `/decision` POST this projection's consumers own.
const MCP_AUTH_REQUIRED = "mcp_auth_required";

const DEFAULT_REASON =
  "The agent paused here — it won't sign until you approve.";

interface MutableApproval {
  approvalId: string;
  title: string;
  reason: string;
  summary: string | null;
  approvalKind: RunApprovalKind;
  serverId: string | null;
  category: { vendor: string; access: string } | null;
  params: ActivityParam[];
  target: string | null;
  runId: string | null;
  messageId: string;
  sequenceNo: number;
  createdAtMs: number | null;
  resolved: boolean;
  decision: RunApprovalDecision | null;
  resolvedAtMs: number | null;
}

/**
 * Reduce the ordered run event list into approval state.
 *
 * Idempotent on replay (deduplicates by `event_id`). Callers pass events in
 * ascending `sequence_no` order — the same append-only array `useRunSession`
 * exposes — so a single `useMemo(() => projectApprovals(events), [events])`
 * recomputes only when the stream grows.
 */
export function projectApprovals(
  events: readonly RuntimeEventEnvelope[],
): ApprovalProjection {
  if (events.length === 0) {
    return EMPTY_PROJECTION;
  }

  const seen = new Set<string>();
  const byId = new Map<string, MutableApproval>();
  const order: string[] = [];

  for (const event of events) {
    if (seen.has(event.event_id)) {
      continue;
    }
    seen.add(event.event_id);

    if (
      event.event_type === APPROVAL_REQUESTED ||
      event.event_type === MCP_AUTH_REQUIRED
    ) {
      reduceRequested(event, byId, order);
    } else if (event.event_type === APPROVAL_RESOLVED) {
      reduceResolved(event, byId);
    }
  }

  return finalize(order.map((id) => freeze(byId.get(id)!)));
}

/**
 * Overlay optimistic local decisions onto a server projection. Pending
 * approvals the user has already decided (before the trailing
 * `approval_resolved` frame arrives) flip to resolved so the card renders its
 * receipt immediately. A server-resolved approval is never overwritten.
 */
export function overlayApprovalDecisions(
  projection: ApprovalProjection,
  local: ReadonlyMap<string, RunApprovalDecision>,
): ApprovalProjection {
  if (local.size === 0) {
    return projection;
  }
  const approvals = projection.approvals.map((approval) => {
    if (approval.resolved) {
      return approval;
    }
    const decision = local.get(approval.approvalId);
    if (decision === undefined) {
      return approval;
    }
    return { ...approval, resolved: true, decision, resolvedAtMs: null };
  });
  return finalize(approvals);
}

/** Map the projection into the rail's `[pending, recent]` queue shape. */
export function toApprovalsQueue(
  projection: ApprovalProjection,
): ApprovalsQueueProjection {
  if (projection.approvals.length === 0) {
    return EMPTY_QUEUE;
  }
  return {
    pending: projection.pending.map(toQueueItem),
    recent: projection.resolved.map(toQueueItem),
  };
}

// --- reducers --------------------------------------------------------------

function reduceRequested(
  event: RuntimeEventEnvelope,
  byId: Map<string, MutableApproval>,
  order: string[],
): void {
  const payload = event.payload;
  const approvalId = stringField(payload.approval_id);
  if (approvalId === null) {
    return;
  }
  if (!byId.has(approvalId)) {
    order.push(approvalId);
  }
  const existing = byId.get(approvalId);
  byId.set(approvalId, {
    approvalId,
    title:
      stringField(payload.display_name) ??
      stringField(payload.tool_name) ??
      event.display_title ??
      existing?.title ??
      "Approve this action",
    reason:
      stringField(payload.reason) ??
      stringField(payload.message) ??
      existing?.reason ??
      DEFAULT_REASON,
    summary:
      stringField(payload.message) ??
      stringField(payload.summary) ??
      event.summary ??
      existing?.summary ??
      null,
    approvalKind: resolveApprovalKind(event),
    // WC-P5a (AD-7): the connector target of an `mcp_auth` gate / `mcp_discovery`
    // suggestion — the arg the Connect card hands to `McpAuthPort.beginAuth`.
    serverId: stringField(payload.server_id) ?? existing?.serverId ?? null,
    category: buildCategory(event),
    params: buildParams(payload.arguments),
    target: buildTarget(payload.arguments),
    runId: event.run_id,
    messageId: event.event_id,
    sequenceNo: existing?.sequenceNo ?? event.sequence_no,
    createdAtMs: existing?.createdAtMs ?? parseMs(event.created_at),
    resolved: existing?.resolved ?? false,
    decision: existing?.decision ?? null,
    resolvedAtMs: existing?.resolvedAtMs ?? null,
  });
}

function reduceResolved(
  event: RuntimeEventEnvelope,
  byId: Map<string, MutableApproval>,
): void {
  const approvalId = stringField(event.payload.approval_id);
  if (approvalId === null) {
    return;
  }
  const existing = byId.get(approvalId);
  if (existing === undefined) {
    return;
  }
  existing.resolved = true;
  existing.decision = decisionFromResolve(event);
  existing.resolvedAtMs = parseMs(event.created_at);
}

// --- shaping ---------------------------------------------------------------

function freeze(m: MutableApproval): RunApproval {
  return {
    approvalId: m.approvalId,
    title: m.title,
    reason: m.reason,
    summary: m.summary,
    approvalKind: m.approvalKind,
    serverId: m.serverId,
    category: m.category,
    params: m.params,
    target: m.target,
    runId: m.runId,
    messageId: m.messageId,
    sequenceNo: m.sequenceNo,
    createdAtMs: m.createdAtMs,
    resolved: m.resolved,
    decision: m.decision,
    resolvedAtMs: m.resolvedAtMs,
  };
}

function finalize(approvals: readonly RunApproval[]): ApprovalProjection {
  const pending = approvals.filter((approval) => !approval.resolved);
  const resolved = approvals.filter((approval) => approval.resolved);
  return { approvals, pending, resolved };
}

function toQueueItem(approval: RunApproval): ApprovalsQueueItem {
  return {
    approvalId: approval.approvalId,
    title: approval.title,
    summary: approval.summary,
    approvalKind: approval.approvalKind,
    runId: approval.runId,
    messageId: approval.messageId,
    resolved: approval.resolved,
    resolvedAt:
      approval.resolvedAtMs !== null
        ? new Date(approval.resolvedAtMs).toISOString()
        : null,
    target: approval.target,
  };
}

// --- payload readers -------------------------------------------------------

function decisionFromResolve(event: RuntimeEventEnvelope): RunApprovalDecision {
  const decision = stringField(event.payload.decision);
  const status = stringField(event.payload.status);
  const value = decision ?? status;
  if (value === "approved" || value === "answered") {
    return "approved";
  }
  return "rejected";
}

function mapApprovalKind(value: unknown): RunApprovalKind {
  switch (stringField(value)) {
    case "mcp_tool":
      return "mcp_tool";
    case "mcp_auth":
      return "mcp_auth";
    case "ask_a_question":
      return "ask_a_question";
    case "tool_action":
      return "tool_action";
    default:
      return "unknown";
  }
}

/**
 * WC-P5a (AD-7): the approval's kind, defaulting an `mcp_auth_required` event to
 * `mcp_auth` when its payload omits `approval_kind`. The backend already stamps
 * `approval_kind: "mcp_auth"` on these events (`stream_events.payload_with_action_id`),
 * so this is a belt-and-suspenders default that keeps the Connect card's
 * recognition (`approvalKind === "mcp_auth"`) robust to a stripped payload rather
 * than mis-rendering the auth gate as a `/decision` Approve/Reject card.
 */
function resolveApprovalKind(event: RuntimeEventEnvelope): RunApprovalKind {
  const kind = mapApprovalKind(event.payload.approval_kind);
  if (kind === "unknown" && event.event_type === MCP_AUTH_REQUIRED) {
    return "mcp_auth";
  }
  return kind;
}

function buildCategory(
  event: RuntimeEventEnvelope,
): { vendor: string; access: string } | null {
  const payload = event.payload;
  const vendor =
    stringField(payload.server_name) ?? stringField(payload.server_id);
  if (vendor === null) {
    return null;
  }
  const access = payload.read_only === true ? "READ" : "ACTION";
  return { vendor, access };
}

const PARAM_LIMIT = 6;

function buildParams(value: unknown): ActivityParam[] {
  if (typeof value !== "object" || value === null) {
    return [];
  }
  const out: ActivityParam[] = [];
  for (const [label, raw] of Object.entries(value as Record<string, unknown>)) {
    if (out.length >= PARAM_LIMIT) {
      break;
    }
    if (
      typeof raw === "string" ||
      typeof raw === "number" ||
      typeof raw === "boolean"
    ) {
      out.push({ label, value: String(raw) });
    }
  }
  return out;
}

function buildTarget(value: unknown): string | null {
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const args = value as Record<string, unknown>;
  return (
    stringField(args.channel) ??
    stringField(args.target) ??
    stringField(args.recipient) ??
    null
  );
}

function stringField(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

function parseMs(iso: string): number | null {
  const parsed = Date.parse(iso);
  return Number.isNaN(parsed) ? null : parsed;
}
