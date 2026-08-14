// PR-3.10 — approval projection off the SINGLE run event stream.
//
// Source: docs/plan/desktop-redesign/phase-3/PRD.md
//   FR-3.22 (the in-chat ask card — ONE shape, Studio and Focus alike)
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
//   (a) the in-chat ask card in `TcChat`                      → `approvals`
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
import { parseQuestion, type QuestionSpec } from "../../approvals/question";
import {
  parseApprovalPresentation,
  parseConnectorTrust,
  parseGrantScope,
  parseWorkspaceGrantRequest,
  type ApprovalPresentation,
  type ApprovalPreview,
  type ConnectorTrust,
  type WorkspaceGrantRequest,
} from "../../approvals/presentation";
import type {
  ApprovalsQueueItem,
  ApprovalsQueueProjection,
} from "../../workspace";

/** Binary decision the in-chat card resolves an approval to. */
export type RunApprovalDecision = "approved" | "rejected";

/** Approval category, reusing the rail's `ApprovalsQueueItem` union. */
export type RunApprovalKind = ApprovalsQueueItem["approvalKind"];

/**
 * One approval seen on the run stream, projected for BOTH the in-chat ask
 * card and the Approvals-tab queue. The presentational
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
  /**
   * The catalog slug an uninstalled suggestion refers to (`payload.catalog_slug`,
   * stamped only when the discovery lookup fell through to the catalog). It is
   * what separates the two things this card does: an `mcp_auth` gate is a
   * connector the user HAS and the run is blocked on, while a suggestion with a
   * slug is a connector they do not have and did not ask for. Only the latter
   * can be muted — "never suggest this again" is meaningless for something
   * already installed — and the mute is keyed by slug, not `server_id`, because
   * no server row exists yet.
   */
  readonly catalogSlug: string | null;
  /**
   * Which catalog connector an INSTALLED server is (`payload.connector_slug`).
   * Distinct from `catalogSlug` above, which is stamped only when the connector
   * is NOT installed — conflating them would let a gate be muted. Both answer
   * "which connector", which is what a slug-keyed host needs to start a
   * connect; `null` on a custom MCP server, which has no catalog identity.
   */
  readonly connectorSlug: string | null;
  /**
   * Vendor·access pill ({ vendor: "SLACK", access: "WRITE" }); null when the
   * payload names no connector at all. `access` is separately nullable, because
   * the wire can name the connector without saying what the call does to it —
   * see `buildCategory`, and note that the card degrades one segment at a time.
   */
  readonly category: {
    readonly vendor: string;
    readonly access: string | null;
  } | null;
  /**
   * The call cannot be undone from inside the app, so the card withholds
   * one-click approval and sends the reviewer to the payload first.
   *
   * Decided HERE rather than in the view because this is the one place that
   * reads the wire, and the wire answers it with two fields that mean different
   * things — see `buildIrreversible`. The view used to decide it by substring-
   * matching the access axis for "destructive", a word that axis cannot
   * produce, which left the entire safety lane unreachable in production while
   * every test of it passed on fixtures.
   */
  readonly irreversible: boolean;
  /** Inset key/value frame projected from `payload.arguments` (primitives only). */
  readonly params: readonly ActivityParam[];
  /** Connector / target preview ("#launch-aurora"); null when absent. */
  readonly target: string | null;
  /**
   * The design's card shape — a batch of decidable rows, the draft about to be
   * sent, or the key/value frame. Null keeps the params frame, which is what
   * every approval rendered before shapes existed.
   */
  readonly presentation: ApprovalPresentation | null;
  /**
   * Connector consent card's server-derived trust clauses. Only meaningful on
   * `mcp_auth` approvals; a null field means the clause has no trustworthy
   * source and the card must omit it rather than guess.
   */
  readonly connectorTrust: ConnectorTrust;
  /**
   * The parsed `ask_a_question` payload. Non-null only for that kind — it is
   * the difference between a card you answer and a card you approve.
   */
  readonly question: QuestionSpec | null;
  /**
   * The parsed folder ask (`payload.workspace_grant`). Non-null makes this
   * approval a filesystem grant request, which routes to `WorkspaceGrantCard`
   * instead of Approve/Reject — the folder is handed over by the host's OS
   * dialog, so there is nothing for a `/decision` POST alone to do.
   */
  readonly workspaceGrant: WorkspaceGrantRequest | null;
  /**
   * The folder an "always allow" would attach — non-null ONLY when this ask
   * genuinely offers the durable choice. Null keeps the ask once-only, which is
   * what nearly every ask is.
   *
   * Decided HERE, from two wire fields at once, for the reason `irreversible`
   * gives above: this is the one place that reads the payload, and a view that
   * re-derived it could drift. Both fields are required, and the producer is why
   * — `stream_events._FilesystemApproval` advertises `allow_always` in
   * `grant_options` and names its subject in `grant_scope`, and its own comment
   * calls "advertising the option without shipping its scope" the silent
   * widening the card must never allow. Requiring both here means a payload that
   * lost one of them offers nothing, rather than offering a durable grant over a
   * folder the client would have had to guess.
   *
   * A WRITE reaches this null by the producer's design, not by a rule invented
   * here: `_grant_scope` returns `None` for a non-read, so a write ships
   * `grant_options: ["allow_once"]` and no scope. That is deliberate — an
   * "always" on a write folder means ATTACH A FOLDER, which a write inside an
   * already-attached folder does not need; what the user wants after the third
   * card is "stop pausing for this run", and that is the composer's bypass pill.
   * Reading the wire rather than branching on `read_only` here is what keeps the
   * two controls from being conflated by a client that forgot the distinction.
   */
  readonly grantAlways: WorkspaceGrantRequest | null;
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
  catalogSlug: string | null;
  connectorSlug: string | null;
  category: { vendor: string; access: string | null } | null;
  irreversible: boolean;
  params: ActivityParam[];
  target: string | null;
  presentation: ApprovalPresentation | null;
  connectorTrust: ConnectorTrust;
  question: QuestionSpec | null;
  workspaceGrant: WorkspaceGrantRequest | null;
  grantAlways: WorkspaceGrantRequest | null;
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
  // Resolved BEFORE the params frame, because the frame is filtered against it.
  // A redelivered event carrying no shape must not erase a shape an earlier
  // frame established — replay would otherwise flatten a rows card to params.
  const presentation =
    parseApprovalPresentation(payload.presentation) ??
    existing?.presentation ??
    null;
  byId.set(approvalId, {
    approvalId,
    title:
      // A parked WRITE carries a line written for the person deciding
      // (`GatePurposeBuilder`: verb + the sanitised primary argument). It leads,
      // because every generic fallback below describes the CARD rather than the
      // effect — a live gate rendered "Approve this action" over a call that was
      // about to file a Linear issue.
      gatePurpose(payload) ??
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
    // Same replay rule as `presentation`: a redelivered frame that omits the
    // slug must not erase it, or a muteable suggestion silently becomes a
    // gate the user can only decline for this one run.
    catalogSlug:
      stringField(payload.catalog_slug) ?? existing?.catalogSlug ?? null,
    connectorSlug:
      stringField(payload.connector_slug) ?? existing?.connectorSlug ?? null,
    category: buildCategory(event),
    // Sticky like `presentation`: a redelivered frame that omits the axis
    // must never DOWNGRADE an approval we already know is irreversible, or a
    // replay would hand back the one-click Approve the first frame withheld.
    irreversible: buildIrreversible(event) || (existing?.irreversible ?? false),
    // Filtered against the preview so the draft is not printed twice — once as
    // the card's preview frame and again as an untruncated `<dd>` in the params
    // grid. See `buildParams`.
    //
    // Keyed on the DECLARED layout, byte-for-byte the same predicate the body
    // renders on (`TcWriteGatePayload`: layout === "preview"). Filtering on
    // `preview !== null` alone looks equivalent and is not: a presentation that
    // carries a preview block under any OTHER layout would have the argument
    // removed here and never drawn there, so the one string the user is
    // approving would vanish from the card entirely. Whatever hides a param must
    // be the same condition that shows it.
    params: buildParams(
      payload.arguments,
      presentation?.layout === "preview" ? presentation.preview : null,
    ),
    target: buildTarget(payload.arguments),
    presentation,
    connectorTrust: mergeConnectorTrust(
      parseConnectorTrust(payload),
      existing?.connectorTrust,
    ),
    // Gated on the KIND, not on the payload's shape. `parseQuestion` falls
    // back to `payload.message` (the tool mirrors the question there), and
    // every approval carries a `message` — so without this gate every
    // approval would render as a question card. Same replay rule as
    // `presentation`: a later frame that omits it must not erase it.
    question:
      resolveApprovalKind(event) === "ask_a_question"
        ? (parseQuestion(payload) ?? existing?.question ?? null)
        : null,
    // Keyed on its own payload block rather than on a kind, so any interrupt a
    // backend already emits becomes a folder ask by stamping one field. Same
    // replay rule as `presentation`: a redelivered frame that omits the block
    // must not erase it, or a card the user was reading turns into an
    // Approve/Reject for an action that was never the question.
    workspaceGrant:
      parseWorkspaceGrantRequest(payload) ?? existing?.workspaceGrant ?? null,
    // Same replay rule as `presentation`, and it matters more here than
    // anywhere: a redelivered frame that omits the option must not retract a
    // choice the user is mid-way through making. It can only ever be RE-offered
    // on the same folder, never widened — `buildGrantAlways` requires the wire
    // to name a scope, so there is nothing for a sticky value to invent.
    grantAlways: buildGrantAlways(payload) ?? existing?.grantAlways ?? null,
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
    catalogSlug: m.catalogSlug,
    connectorSlug: m.connectorSlug,
    category: m.category,
    irreversible: m.irreversible,
    params: m.params,
    target: m.target,
    presentation: m.presentation,
    connectorTrust: m.connectorTrust,
    question: m.question,
    workspaceGrant: m.workspaceGrant,
    grantAlways: m.grantAlways,
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

/**
 * The `linear · write` meta: which connector, and what the call does to it.
 *
 * WHAT THE WIRE ACTUALLY CARRIES. The producer computes a real three-value axis
 * — `ApprovalCategory` = `read` | `write` | `action`
 * (`services/ai-backend/src/runtime_api/schemas/common.py`) — and spreads it
 * onto the approval payload as `category`
 * (`stream_events.McpApprovalMetadata`). It does not reach us: the
 * client-visible allow-list
 * (`runtime_api/schemas/events.py::_approval_requested_payload`) lists neither
 * `category` nor `vendor`. What DOES survive that projection is the boolean the
 * producer derived the axis from — `read_only` — so the axis is derived here
 * exactly as `stream_events._approval_category` derives it:
 * `True → READ`, `False → WRITE`. Nothing is invented and nothing diverges;
 * both sides read one field one way.
 *
 * "ACTION" was what an absent-or-false `read_only` used to print, and it was
 * wrong twice over. `_approval_category` never returns `ACTION` for a boolean
 * at all, so a Linear issue creation — a write, `read_only: false` — rendered
 * `linear · action`, contradicting the design AND the backend. And it printed a
 * word over payloads that say nothing about access whatsoever: an
 * `mcp_auth_required` gate carries a `server_id` and no `read_only`, so a
 * connector the run merely wants to CONNECT to was labelled as taking an
 * action. An unstated axis is now omitted — the card degrades a segment at a
 * time and renders the bare vendor.
 *
 * NOT read here: `payload.category`. It is a real producer field, not an
 * invented one, but the allow-list above deletes it on every path, so a read
 * would be a dead branch whose green test would read as evidence the field
 * arrives. If that allow-list is ever widened, add the read here and prefer the
 * server's word — it is the only thing that could ever say `action`.
 *
 * NOT decided here: whether the call is irreversible. That is a different
 * question from the access axis — see `buildIrreversible` — and conflating the
 * two is what left the card's safety lane unreachable.
 */
function buildCategory(
  event: RuntimeEventEnvelope,
): { vendor: string; access: string | null } | null {
  const payload = event.payload;
  const vendor =
    stringField(payload.server_name) ?? stringField(payload.server_id);
  if (vendor === null) {
    return null;
  }
  return { vendor, access: accessAxis(payload.read_only) };
}

/**
 * Can this be undone from inside the app?
 *
 * The wire answers with two fields that are not the same question, and the card
 * needs both because neither lane emits both:
 *
 * - `op_class` is the PDP's verdict (`McpToolActionClass`: read | write |
 *   destructive). It is the ONLY field that can ever say `destructive`, and it
 *   rides the MCP gate.
 * - `risk_level` says whether a write reaches the user's real files. A
 *   filesystem write is `high` (nothing in this app can put the bytes back); a
 *   connector write is `medium`, because the copy we show the user — "you can
 *   undo this from the connector" — is true there and false for a file.
 *
 * So a connector DELETE is caught by `op_class`, a filesystem WRITE by
 * `risk_level`, and reading either one alone silently under-protects the other
 * lane. `critical` is included because the contract admits it and a client that
 * ignores a level above the one it knows is failing open.
 *
 * Unknown or absent ⇒ false, deliberately. This gates whether the reviewer must
 * open the payload before approving; defaulting it TRUE would park every
 * ordinary approval behind an extra click on no evidence, and the producer
 * already fails closed to `write` when its classifier is missing.
 */
function buildIrreversible(event: RuntimeEventEnvelope): boolean {
  const payload = event.payload;
  const opClass = stringField(payload.op_class)?.toLowerCase() ?? null;
  if (opClass === "destructive") {
    return true;
  }
  const risk = stringField(payload.risk_level)?.toLowerCase() ?? null;
  return risk === "high" || risk === "critical";
}

/**
 * The wire's word for "this once", and for "from now on".
 *
 * Exported so a test can assert against the contract rather than a literal, and
 * spelled exactly as `_FilesystemApproval.ALLOW_ONCE` / `ALLOW_ALWAYS`
 * (`runtime_worker/stream_events.py`) — the two strings the producer puts in
 * `grant_options`.
 */
export const GRANT_OPTION_ALLOW_ONCE = "allow_once";
export const GRANT_OPTION_ALLOW_ALWAYS = "allow_always";

/**
 * The durable option, or null — see `RunApproval.grantAlways` for why both wire
 * fields are required and why a write never reaches a non-null answer.
 *
 * Membership is tested on the ARRAY the producer sent, not on any local notion
 * of which asks deserve the option. `grant_options` is the producer's decision
 * and it is already load-bearing there; re-deciding it here would be a second
 * authority on a consent question that must have exactly one.
 */
function buildGrantAlways(
  payload: Record<string, unknown>,
): WorkspaceGrantRequest | null {
  const options = payload.grant_options;
  if (!Array.isArray(options)) {
    return null;
  }
  if (!options.includes(GRANT_OPTION_ALLOW_ALWAYS)) {
    return null;
  }
  return parseGrantScope(payload);
}

/**
 * READ / WRITE / nothing — the producer's own mapping, plus an honest gap.
 *
 * Strict identity on both booleans rather than a truthiness test, so "absent"
 * and "false" stay distinguishable: they are the difference between "the wire
 * did not say" and "the wire said this writes".
 */
function accessAxis(value: unknown): string | null {
  if (value === true) return "READ";
  if (value === false) return "WRITE";
  return null;
}

const PARAM_LIMIT = 6;

/**
 * The key/value frame, from the raw call arguments — minus the one the card
 * already shows in full.
 *
 * These params are NOT the server's curated allow-list (which deliberately omits
 * `body` / `text` / `description`); they are the first few primitive top-level
 * arguments in object order, with no length cap. So a 2000-character draft that
 * the preview frame renders — scrollable, pre-wrapped, with its own volumetric
 * meta line — would otherwise ALSO land in the grid as an untruncated `<dd>`,
 * printing the same string twice with the second copy in the worse shape.
 *
 * Matched on the VALUE rather than by re-declaring the producer's list of
 * preview keys (`text`/`body`/`message`/…): the question being answered is
 * literally "would this render the same string twice", and duplicating a
 * server-side key list here is how the two drift. `startsWith` rather than
 * equality because the producer trims and caps the preview at 2000 characters,
 * so a longer argument shares only its prefix.
 */
function buildParams(
  value: unknown,
  preview: ApprovalPreview | null,
): ActivityParam[] {
  if (typeof value !== "object" || value === null) {
    return [];
  }
  const shown = preview === null ? null : preview.text;
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
      if (
        shown !== null &&
        typeof raw === "string" &&
        raw.trim().startsWith(shown)
      ) {
        continue;
      }
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

/**
 * Keep any trust clause an earlier frame established.
 *
 * The blocking gate and the discovery suggestion can both touch one approval id,
 * and a later frame that omits `auth_host` means "this frame didn't carry it",
 * not "there is no host". Dropping to null there would silently retract a clause
 * the user already read. Nothing is ever *invented* here — an absent clause on
 * both sides stays absent.
 */
function mergeConnectorTrust(
  next: ConnectorTrust,
  existing: ConnectorTrust | undefined,
): ConnectorTrust {
  if (existing === undefined) {
    return next;
  }
  return {
    accessMode: next.accessMode ?? existing.accessMode,
    authHost: next.authHost ?? existing.authHost,
    sourceTool: next.sourceTool ?? existing.sourceTool,
  };
}

/**
 * The gate block's human purpose line, when this approval is a parked write.
 *
 * Untrusted by origin — it embeds a tool argument — but the emitter already
 * caps its length and strips newlines, markdown and URLs
 * (`GatePurposeBuilder.build`), and it is rendered as a text node. Read
 * defensively anyway: the block is additive and absent on every non-gate
 * approval.
 */
function gatePurpose(payload: Record<string, unknown>): string | null {
  // Presentation lifts the one line out of the additive gate block and ships it
  // as `display_title`; the raw block is read as a fallback so a payload that
  // predates that projection still renders the effect rather than the generic
  // card copy.
  const lifted = stringField(payload.display_title);
  if (lifted !== null) return lifted;
  const gate = payload.gate;
  if (typeof gate !== "object" || gate === null) return null;
  return stringField((gate as Record<string, unknown>).purpose);
}

function stringField(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

function parseMs(iso: string): number | null {
  const parsed = Date.parse(iso);
  return Number.isNaN(parsed) ? null : parsed;
}
