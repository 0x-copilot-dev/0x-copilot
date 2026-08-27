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
  /**
   * The scopes the SERVER says this card may be answered with — `payload
   * .grant_options`, verbatim and unfiltered. `allow_once` is what a plain
   * approve has always meant; `allow_always` is the second scope, and its
   * MEANING IS LANE-SPECIFIC, which is why this is carried as the raw list
   * rather than collapsed into a boolean here:
   *
   * * **the write gate** (`mcp_write:` ids, `ToolAccessGate._grant_options`) —
   *   `always` writes a RUN-SCOPED allow rule over the subjects this call
   *   already carried, and expires with the run. It is offered for every op
   *   class except `destructive`, because the value of pausing on an
   *   irreversible act is that it is decided each time.
   * * **the filesystem lane** (`filesystem_access`,
   *   `runtime_worker/stream_events.py:227-234`) — `always` ATTACHES A FOLDER:
   *   a durable workspace grant, wider than the one path the card named. That
   *   is a different act with a different lifetime, settled by
   *   `WorkspaceGrantPort` and an OS dialog, and the `/decision` POST does not
   *   carry it at all (`ApprovalResumeBuilder` drops `decision_scope` on every
   *   lane but `ask_a_question`).
   *
   * So a consumer must decide WHICH `always` it is looking at before drawing a
   * control for it. `allowsRunScopedGrant` below is that decision, made once.
   */
  readonly grantOptions: readonly string[];
  /**
   * True when an `always` on this card is the run-scoped policy rule the
   * `/decision` POST actually carries — i.e. the write-gate lane, and the
   * server offered `allow_always`.
   *
   * Derived here, at the one place that reads the wire, for the same reason
   * `irreversible` is: the view used to decide `irreversible` by substring-
   * matching an axis that could not produce the word, and the whole safety lane
   * was unreachable in production while every fixture test passed. A control
   * whose POST is dropped server-side is that defect exactly — it would look
   * like a working "always" and quietly do nothing.
   */
  readonly allowsRunScopedGrant: boolean;
  /**
   * The exact command a `run_command` ask will execute, verbatim off
   * `payload.command` (PRD-shell-execution §14.1). Non-null is what makes an
   * ask a COMMAND ask, and it is the string the card is approved OVER —
   * `TcWriteGateRow.commandText`, which also counts as the payload-seen
   * evidence that unlocks Approve for an irreversible write.
   *
   * KEYED ON ITS OWN PAYLOAD BLOCK, not on a kind — the same rule
   * `workspaceGrant` follows, and for the same reason. The command lane rides
   * the write gate's wire shape verbatim (`approval_kind: "ask_a_question"`,
   * see `mapApprovalKind`), so the kind cannot tell a command apart from an MCP
   * write; the presence of this field is the only thing that can.
   *
   * NOT the params frame. `buildParams` keeps primitive top-level arguments and
   * renders them in a `<dd>` grid with no cap — the wrong shape for a
   * multi-line string that must appear as an exact monospace block. A command
   * that arrived only through `params` would be re-flowed, and a re-flowed
   * command is not the command.
   *
   * DARK, but wired end to end. `_CommandApproval` stamps the key and
   * `_ask_a_question_requested_payload` now projects it, so a payload reaching
   * here really can carry a command. What is still missing is the TOOL: nothing
   * raises the `run_command` interrupt, so no live run produces one and this
   * reads null on every ask today — rendering exactly what the card rendered
   * before.
   */
  readonly command: string | null;
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

/**
 * A parked WRITE's approval id prefix. `PolicyToolMiddleware` parks on a
 * deterministic `mcp_write:<run>:<call>` (policy_tool.py), chosen so the id is
 * stable across LangGraph's node replay.
 *
 * The SSOT for the prefix, imported by `TcChat` rather than restated there:
 * this is the one property that says which lane an approval is on, and both the
 * projection (which scope may be offered) and the renderer (which card to draw)
 * have to agree about it exactly.
 *
 * IT MARKS ONE PRODUCER, NOT A FAMILY. Exactly one place mints it —
 * `PolicyToolMiddleware._approval_id` — and that lane is MCP-only, so the
 * `mcp_` is accurate and not a misnomer. A parked `run_command` does NOT wear
 * it: that producer serves LangGraph's native `action_requests` interrupt and
 * mints `<interrupt_id>:<index>`. Read this string as "an MCP write parked by
 * `PolicyToolMiddleware`", never as "anything parked at `ToolAccessGate`" —
 * both gates borrow the `ask_a_question` WIRE SHAPE, and sharing a shape is not
 * sharing an id. `allowsRunScopedGrant` documents what keys on this prefix and
 * what a command ask keys on instead.
 */
export const WRITE_GATE_APPROVAL_PREFIX = "mcp_write:";

/** The wire words. Mirrors `_Values.ALLOW_ONCE` / `ALLOW_ALWAYS` server-side. */
const ALLOW_ALWAYS = "allow_always";

const EMPTY_GRANTS: readonly string[] = [];

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
  grantOptions: readonly string[];
  command: string | null;
  params: ActivityParam[];
  target: string | null;
  presentation: ApprovalPresentation | null;
  connectorTrust: ConnectorTrust;
  question: QuestionSpec | null;
  workspaceGrant: WorkspaceGrantRequest | null;
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
    // Same replay rule as `presentation` / `irreversible`: a redelivered frame
    // that omits the list must not erase it. Sticky in the SAFE direction —
    // toward what the first frame advertised — because losing the option only
    // costs a re-ask, while inventing one widens a decision.
    grantOptions:
      buildGrantOptions(event) ?? existing?.grantOptions ?? EMPTY_GRANTS,
    // Same replay rule as `presentation` / `workspaceGrant`: a redelivered
    // frame that omits the command must not erase it. Sticky here is the SAFE
    // direction and the safety-critical one: the command is what unlocks
    // Approve on an irreversible card, so a frame that could retract it would
    // pull the evidence out from under a decision already being made — and
    // dropping it silently turns a command ask into a bare "Approve this
    // action" with nothing on screen saying what runs.
    command: stringField(payload.command) ?? existing?.command ?? null,
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
    grantOptions: m.grantOptions,
    allowsRunScopedGrant: allowsRunScopedGrant(m),
    command: m.command,
    params: m.params,
    target: m.target,
    presentation: m.presentation,
    connectorTrust: m.connectorTrust,
    question: m.question,
    workspaceGrant: m.workspaceGrant,
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

/**
 * The wire's `approval_kind`, as a CLOSED set — and the closure is the trap.
 *
 * An unlisted kind lands on `"unknown"`, which nothing errors on: the ask still
 * draws, as the generic Approve/Decline card, minus whatever the kind was going
 * to unlock. A lane discovering that from its own blank card is the expensive
 * way to find out, so a new lane decides HERE first.
 *
 * THE COMMAND LANE ADDS NO CASE, deliberately (PRD-shell-execution §18 Phase 0,
 * §14.1). It rides the write gate's wire shape verbatim —
 * `approval_kind: "ask_a_question"` (`surfaces_v2/gate.py::_Values
 * .APPROVAL_KIND_WRITE`) — and that is ONE decision with the server, not two:
 *
 * * **The kind IS the resume shape.** `ApprovalResumeBuilder` forwards
 *   `decision_scope` on exactly one branch, `ask_a_question`
 *   (`runtime_worker/handlers/approval.py`), so §8.3's run-scoped always-grant
 *   is expressible only under this kind. A bespoke `run_command` kind would
 *   draw an "always" whose POST the server drops — the dead-control shape.
 * * **The kind picks the server's allow-list.** `_approval_requested_payload`
 *   early-returns into `_ask_a_question_requested_payload` for this kind, and
 *   only that branch projects `op_class` (`events.py:2700`) alongside
 *   `risk_level` and `grant_options`. The sibling list carries the latter two
 *   but not `op_class`, which is the field §14.1 keeps DECOUPLED from
 *   `irreversible` so a command can be un-one-clickable and still earn a
 *   run-scoped grant.
 *
 * So the discriminator for a command ask is not the kind — it is
 * `RunApproval.command`, keyed on its own payload block.
 */
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
 * The scopes the card may be answered with, read off `payload.grant_options`.
 *
 * Returns `null` — not `[]` — when the key is absent, so `reduceRequested` can
 * tell "this frame said nothing about scope" from "this frame said: once only",
 * and a redelivered frame cannot silently retract an option the first one
 * advertised. Non-string entries are dropped exactly as the server's own
 * projection drops them (`_approval_requested_payload`); a list that is not a
 * list is refused outright rather than coerced.
 */
function buildGrantOptions(
  event: RuntimeEventEnvelope,
): readonly string[] | null {
  const raw = event.payload.grant_options;
  if (!Array.isArray(raw)) {
    return null;
  }
  return raw.filter(
    (option): option is string => typeof option === "string" && option !== "",
  );
}

/**
 * Whether an `always` on this card is the run-scoped rule the wire carries.
 *
 * TWO CONDITIONS, and each is load-bearing:
 *
 * 1. **The server offered it.** `ToolAccessGate._grant_options` withholds
 *    `allow_always` for a `destructive` op, on the same reasoning that puts the
 *    destructive rung above BYPASS in the PDP: the value of pausing on an
 *    irreversible act is that it is decided *each time it is about to happen*.
 * 2. **The lane's resume shape carries `decision_scope`.** Only
 *    `approval_kind == "ask_a_question"` does — the shape the write gate
 *    deliberately borrows — and `mcp_write:` is the id `PolicyToolMiddleware`
 *    parks on. Every other lane's resume builder drops the field, so a control
 *    posting `decision_scope: "always"` from, say, a `filesystem_access` card
 *    would look like a working "always" and change nothing.
 *
 * The filesystem lane's `allow_always` is a DIFFERENT act wearing the same wire
 * word: it attaches a folder — a durable workspace grant, wider than the path
 * the card named — and is settled by `WorkspaceGrantPort` and an OS dialog, not
 * by the `/decision` POST. `runtime_worker/stream_events.py:227-234` records
 * that split and names the composer's bypass pill as the intended control for
 * repeated writes. Nothing here overturns that: this predicate is how the
 * client honours it.
 *
 * THE COMMAND LANE DOES NOT TAKE THIS PREFIX. That is not a design option that
 * was weighed and chosen — it is a fact about the tree, and this comment used to
 * assert the opposite (PRD-shell-execution §18 Phase 0 asks for the decision by
 * name; this is it, recorded against the code rather than against the intent).
 *
 * `mcp_write:<run_id>:<tool_call_id>` is minted in exactly ONE place,
 * `PolicyToolMiddleware._approval_id`, and that lane is MCP-only. The command
 * producer is somewhere else entirely: `runtime_worker/stream_events.py`'s
 * `_CommandApproval` serves LangGraph's own native `action_requests` interrupt,
 * so it mints `<interrupt_id>:<index>` — uniform with its two sibling branches
 * and carrying no prefix at all. Nothing in this tree mints an `mcp_write:` id
 * AND stamps `command`, so the two lanes never shared an id shape; they share
 * the `ask_a_question` WIRE SHAPE, which is a different thing.
 *
 * So this predicate returns FALSE for every command ask — and today that is the
 * right answer rather than a gap to route around, because `_CommandApproval
 * .GRANT_OPTIONS` is `("allow_once",)`: the server never offers `always` on a
 * command in the first place. Both conditions above fail, and they fail
 * AGREEING with each other. §8.3's run-scoped grant is therefore not one client
 * edit away — it needs the server to earn `allow_always` from a tokeniser
 * (`ToolAccessGate._grant_options(simple_command=)`) AND this predicate to stop
 * keying on the prefix, together. Shipping either half alone draws a control
 * whose POST changes nothing, or withholds one the server already offered.
 *
 * WHAT KEYS ON THE PREFIX, AND WHY HALF-TEACHING IT IS THE DANGEROUS PART.
 * Three predicates read it and each fails a different silent way — the warning
 * this comment carried before, kept because it is the one that came true:
 *
 * 1. `TcChat.isWriteGateApproval` — WHICH CARD TO DRAW. This is the one that
 *    already bit. A command ask fell past it into `renderApprovalItem`'s
 *    question branch and rendered a yes/no about a shell command as a FREE-TEXT
 *    ANSWER BOX: the exact failure that branch order exists to prevent. It was
 *    fixed WITHOUT teaching a second prefix — `TcChat.isCommandApproval` keys on
 *    the PAYLOAD (`RunApproval.command`, non-blank), which is a fact the
 *    producer really stamps.
 * 2. This one — WHETHER "ALWAYS" MAY BE OFFERED. Correct today for the reason
 *    above. Revisit it only together with the server's grant options.
 * 3. `eventProjector.writeGateCallId` — WHICH TOOL CALL THE ASK IS BLOCKING.
 *    ⚠️ **NAMED PHASE 1 ITEM, DELIBERATELY LEFT UNTAUGHT.** It returns null for
 *    a command id, so the EXACT `tool_call_id` join is unavailable and
 *    `matchAskToCall` falls through to its second join: the single RUNNING call
 *    whose `tool_name` matches (which is why `tool_name` is on the payload and
 *    on the server's `ask_a_question` allow-list). That fallback is right for
 *    one command and returns `undefined` for two in flight — so with two parked
 *    commands neither card shows "Needs you" and both keep spinning on the
 *    run-wide signal. Nothing is broken TODAY: no tool raises the interrupt, so
 *    no command ask exists. And a prefix could not honestly be taught here
 *    anyway — the id's second half is an ACTION INDEX, not a call id, so a
 *    lookalike `mcp_write:` id would hand this exact join the wrong key. Phase 1
 *    owns it as "carry a real `tool_call_id`", never as "add a prefix".
 *
 * The rule those three add up to is unchanged: one discriminator cannot be
 * half-taught. What changed is WHICH discriminator — the payload block, not the
 * id.
 */
function allowsRunScopedGrant(approval: {
  readonly approvalId: string;
  readonly grantOptions: readonly string[];
}): boolean {
  return (
    approval.approvalId.startsWith(WRITE_GATE_APPROVAL_PREFIX) &&
    approval.grantOptions.includes(ALLOW_ALWAYS)
  );
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
