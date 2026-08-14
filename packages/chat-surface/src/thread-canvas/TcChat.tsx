import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

import type {
  AssistantTurnPartBlock,
  CitationSourceRef,
  RunContentPart,
} from "@0x-copilot/api-types";
import type { Transport } from "@0x-copilot/chat-transport";

import { Composer } from "../composer/Composer";
import { MarkdownText, type MarkdownTextProps } from "../messages/MarkdownText";
import { PlainText } from "../messages/PlainText";
import { Reasoning } from "../messages/Reasoning";
import { ThinkingBlock, ThinkingShimmer } from "../messages/ThinkingShimmer";
import type { MessagePartStatus } from "../messages/types";
import { useTransport } from "../providers/TransportProvider";
// PR-3.8 — inline parallel-subagent fleet card. Reuses the hoisted Phase-1D
// presentation family; the fleet state is projected upstream (FR-3.17a).
import {
  FleetSubagentRow,
  SubagentFleetCard,
  subagentCardFromEntry,
  type FleetProjection,
  type SubagentActivityRecord,
} from "../subagents";
// PR-3.10 — in-chat approvals. EVERY pending ask that is decided with
// Approve/Decline — a parked write and an ordinary tool action alike — renders
// as the same compact `TcWriteGateRow`, in both modes. A settled approval
// renders nothing; the `ApprovalReceipt` line it used to collapse to is gone.
// Presentation only — resolution is the injected onApprove/onReject (host owns
// the POST, D28).
//
// What survives from the Phase-1E consent family here is the set of asks that
// are NOT Approve/Decline: a question you answer, a folder you hand over, a
// connector you sign in to. `ConsentCard` is no longer mounted by this file
// (and the 4-zone `ApprovalCard` these comments used to name never was — it was
// an unused import for a long time); both stay on the barrel for the deprecated
// web ChatScreen path, which is their only remaining consumer.
import {
  type ActivityParam,
  ConnectorConsentCard,
  type ConnectorConsentState,
  QuestionCard,
  type QuestionAnswer,
  type QuestionSpec,
  EMPTY_CONNECTOR_TRUST,
  type ApprovalPresentation,
  type ConnectorTrust,
  WorkspaceGrantCard,
  type WorkspaceGrantCardState,
  type WorkspaceGrantRequest,
} from "../approvals";
// WC-P5a (AD-6/AD-7) — the MCP-OAuth launcher port TYPE + the approval-kind
// union. `McpAuthPort` is a pure interface (no runtime code), so this type-only
// import adds no substrate coupling and no module cycle (the value edge runs the
// other way: `RunDestination` → `TcChat`). `ApprovalsQueueItem["approvalKind"]`
// is the SSOT kind union carried through the `RunApproval → TcChatApproval`
// boundary so the card can branch a `mcp_auth` gate off the `/decision` path.
import type { McpAuthPort } from "../destinations/run/mcpAuthPort";
import type { ApprovalsQueueItem } from "../workspace";
// Workstream D — the main-agent tool-call cards, projected off the SINGLE run
// stream (`projectToolCalls(session.events)`) and interleaved into the
// transcript at the point each tool ran. The projection is the single source of
// truth; TcChat never re-derives tool state from raw events.
import type { RunTodosProjection, ToolCallEntry } from "./eventProjector";
import { ToolRunGroup } from "../activity/ToolRunGroup";
// PRD-03 FR-3.10 — reuse the formatter that already exists rather than adding a
// third. PRD-07 renames/consolidates it; this call site moves with it.
import { formatSubagentDuration } from "../subagents/labels";
import {
  groupActivityStream,
  summariseGroup,
  type GroupSummary,
} from "./groupActivity";
import { InlineToolResultCard } from "./InlineToolResultCard";
import { useSwimlaneScrub } from "./SwimlaneScrubContext";
import { TcTodoList } from "./TcTodoList";
import { ToolCallCard } from "./ToolCallCard";
import { TcWriteGateRow, type WriteGateGrantAlways } from "./TcWriteGateRow";
import {
  TcInlineArtifactCard,
  type InlineArtifactEntry,
} from "./TcInlineArtifactCard";
import type { ArtifactDownloadPort } from "../ports/ArtifactDownloadPort";

export type TcChatMode = "studio" | "focus";

/**
 * PR-3.10 — an approval projected off the run stream
 * (`projectApprovals(session.events)`), shaped for the in-chat card. The
 * superset `RunApproval` (destinations/run) is structurally assignable to this,
 * so the host threads its projection straight through with no mapping pass.
 */
export interface TcChatApproval {
  readonly approvalId: string;
  /** Verb-first card title ("Post to #launch-aurora"). */
  readonly title: string;
  /** The "why" line under the title. */
  readonly reason: string;
  /** Optional sub-line. */
  readonly summary: string | null;
  /**
   * WC-P5a (AD-7): the approval category, carried through from `RunApproval` so
   * the card can branch. `mcp_auth` renders the Connect card (→ `McpAuthPort`),
   * distinct from the `mcp_tool` / `tool_action` / `ask_a_question` Approve/Reject
   * card (→ host `/decision` POST). SSOT: the rail's `ApprovalsQueueItem`.
   */
  readonly approvalKind: ApprovalsQueueItem["approvalKind"];
  /**
   * WC-P5a (AD-7): connector `server_id` for the Connect card's
   * `McpAuthPort.beginAuth(serverId)` / `skipAuth(serverId)`. Present on
   * `mcp_auth` gates + `mcp_discovery:` suggestions; null on plain approvals.
   */
  readonly serverId: string | null;
  /**
   * WC-P5a: the catalog slug of an uninstalled suggestion, present only when the
   * discovery lookup fell through to the catalog. Its presence is what makes a
   * card muteable — see `RunApproval.catalogSlug`.
   */
  readonly catalogSlug?: string | null;
  /**
   * WC-P5a: which catalog connector an installed server is. Handed to
   * `McpAuthPort.beginAuth` so a slug-keyed host (desktop) can start the flow
   * its own path is built on. See `RunApproval.connectorSlug`.
   */
  readonly connectorSlug?: string | null;
  /**
   * Vendor·access pill; null when the payload names no connector. `access` is
   * separately nullable because the wire can name the connector without saying
   * what the call does to it (an `mcp_auth` gate carries no `read_only`), and
   * the card renders the vendor alone rather than guessing an axis.
   */
  readonly category: {
    readonly vendor: string;
    readonly access: string | null;
  } | null;
  /**
   * The call cannot be undone from inside the app ⇒ no one-click Approve, and
   * the "can't be undone" chip. Decided by the approval projection, which is
   * the one place that reads the wire; see `isIrreversible`.
   *
   * Optional so a host that projects approvals its own way is unaffected, and
   * absent means REVERSIBLE — the same fail-open the old predicate had. That is
   * the right default for a flag that only ever ADDS friction: defaulting true
   * would put every ordinary approval behind an extra click on no evidence.
   */
  readonly irreversible?: boolean;
  /** Inset key/value frame. */
  readonly params: readonly ActivityParam[];
  /**
   * The design's card shape (rows / preview / params) plus its narrative
   * labels, projected server-side off the real tool-call arguments. Null
   * renders the params frame every approval rendered before shapes existed.
   */
  readonly presentation: ApprovalPresentation | null;
  /** Server-derived trust clauses for an `mcp_auth` card; nulls are omitted. */
  readonly connectorTrust: ConnectorTrust;
  /**
   * Parsed `ask_a_question` payload. Non-null only for that kind, and the
   * reason it routes to a card you ANSWER rather than one you approve.
   */
  readonly question: QuestionSpec | null;
  /**
   * Parsed folder ask (`payload.workspace_grant`). Non-null routes to
   * `WorkspaceGrantCard`, whose Grant hands the decision to the host's
   * `WorkspaceGrantPort` — an OS dialog, not a `/decision` POST. Optional so a
   * fixture or a host that projects no grants keeps its current shape.
   */
  readonly workspaceGrant?: WorkspaceGrantRequest | null;
  /**
   * The folder an "always allow" would attach (`payload.grant_scope`, and only
   * when `payload.grant_options` offered `allow_always`). Non-null adds the
   * durable arm to the ask card's body; null — nearly always — leaves the ask
   * once-only and the card byte-identical.
   *
   * Distinct from `workspaceGrant` above, and the two must not be conflated: a
   * `workspace_grant` block REPLACES the Approve/Decline card with a folder ask,
   * while this rides an ordinary ask that can still be approved just this once.
   * Collapsing them would delete the "once" arm from every ask that merely
   * OFFERS a durable option.
   */
  readonly grantAlways?: WorkspaceGrantRequest | null;
  /** Resolved? Pending → the ask card; resolved → nothing at all. */
  readonly resolved: boolean;
  /** Final decision once resolved; null while pending. */
  readonly decision: "approved" | "rejected" | null;
  /** Dispatch time (epoch ms) — retained for display, no longer the anchor. */
  readonly createdAtMs: number | null;
  /**
   * `sequence_no` of the request event — the conversation anchor. `RunApproval`
   * has always carried this; the chat used `createdAtMs` instead and paid for
   * it (wall-clock across producers is not a total order, and ms collisions are
   * routine at streaming rates).
   */
  readonly sequenceNo?: number;
}

/** OAuth-success receipt retained while the cockpit rebinds to the next run. */
export interface ConnectedConnectorReceipt {
  readonly approvalId: string;
  readonly serverId: string;
  readonly displayName: string;
}

// WC-P5a (AD-7): the `mcp_discovery:` prefix on an approval id marks a catalog
// suggestion — a UI hint from `McpDiscoveryService` that is NEVER persisted as an
// ApprovalRequest row, so a `/decision` POST 404s. Both the blocking `mcp_auth`
// gate and this suggestion arrive as `mcp_auth_required` events carrying
// `approval_kind: "mcp_auth"`, so `approvalKind === "mcp_auth"` already recognises
// the whole family; the prefix check is a defensive belt-and-suspenders in case a
// suggestion ever arrives with a stripped/unknown kind. Either way it routes to
// the Connect card + `McpAuthPort`, never `onApprove`/`onReject`.
const MCP_DISCOVERY_APPROVAL_PREFIX = "mcp_discovery:";

function isMcpAuthApproval(approval: TcChatApproval): boolean {
  return (
    approval.approvalKind === "mcp_auth" ||
    approval.approvalId.startsWith(MCP_DISCOVERY_APPROVAL_PREFIX)
  );
}

// A folder ask is recognised by its PAYLOAD, not by a kind: the backend raises
// it by stamping `payload.workspace_grant` on whichever interrupt it already
// emits, so any kind can carry one. Checked before the `mcp_auth` branch —
// which folder to hand over is a different question from which vendor to sign
// in to, and only one of the two cards names a path.
// A parked WRITE is recognised by its approval id, the same way an mcp_auth gate
// is: `PolicyToolMiddleware` parks on a deterministic `mcp_write:<run>:<call>`
// (policy_tool.py), chosen so the id is stable across LangGraph's node replay.
// Its wire shape is `ask_a_question` — the gate deliberately reuses that resume
// plumbing — so without this it falls into the generic question branch and a
// yes/no about a real side effect renders as a free-text box.
const WRITE_GATE_APPROVAL_PREFIX = "mcp_write:";

function isWriteGateApproval(approval: TcChatApproval): boolean {
  return approval.approvalId.startsWith(WRITE_GATE_APPROVAL_PREFIX);
}

function isWorkspaceGrantApproval(approval: TcChatApproval): boolean {
  return (approval.workspaceGrant ?? null) !== null;
}

export interface TcChatMessagePart {
  readonly type: "text" | "reasoning";
  readonly text: string;
  /**
   * Streaming lifecycle for this part. Absent parts (historical messages
   * fetched via GET) default to `complete`; a part still arriving over the
   * live stream carries `{ type: "running" }`, which routes the incremental
   * blinking cursor onto the markdown renderer (FR-3.19).
   */
  readonly status?: MessagePartStatus;
  /**
   * `sequence_no` of the event that OPENED this part, within its message's
   * `run_id` event space. This is the ordering key that lets tool / fleet /
   * approval cards interleave BETWEEN the parts of one turn — a turn is
   * `text → tools → text`, and before this existed the whole turn carried a
   * single anchor (its first token) so every mid-turn card sorted after it.
   *
   * Optional: user turns and pre-ordering historical messages carry none, and
   * those keep document order (see `mergeStream`).
   */
  readonly seq?: number;
  /** Epoch ms of the first event in this part — drives the reasoning stamp. */
  readonly startedAtMs?: number;
  /** Epoch ms of the latest event applied to this part. */
  readonly updatedAtMs?: number;
}

export interface TcChatMessage {
  readonly message_id: string;
  readonly role: "user" | "assistant" | "system" | "tool";
  readonly parts: ReadonlyArray<TcChatMessagePart>;
  readonly created_at_ms?: number;
  /**
   * The run whose `sequence_no` space this message's part `seq` values live in.
   * Only parts belonging to the ACTIVE run may be seq-merged against that run's
   * cards — every run numbers its events from 0, so merging across runs would
   * collide run 2's seq 5 with run 7's seq 5.
   */
  readonly run_id?: string | null;
}

export interface TcChatMessagesResponse {
  readonly messages: ReadonlyArray<TcChatMessage>;
}

// The facade returns messages in the wire shape (`content_text` + `content`
// blocks + `created_at`), NOT the presentational `{ parts }` shape this
// component renders. Normalize each fetched message into a `TcChatMessage` with
// a single text part so `renderMessage` never maps over an undefined `parts`.
// A message that already arrives with `parts` (a test fixture, or a future
// endpoint) is passed through untouched.
interface ApiChatMessage {
  readonly message_id: string;
  readonly role: TcChatMessage["role"];
  readonly content_text?: string | null;
  readonly content?: ReadonlyArray<
    AssistantTurnPartBlock | RunContentPart
  > | null;
  readonly created_at?: string | null;
  readonly parts?: ReadonlyArray<TcChatMessagePart>;
  readonly created_at_ms?: number;
  readonly run_id?: string | null;
}
interface ApiChatMessagesResponse {
  readonly messages?: ReadonlyArray<ApiChatMessage>;
}

/**
 * Read the assistant turn's ORDERED parts off the wire `content` blocks.
 *
 * The worker folds the run's sealed ledger into `MessageRecord.content` at seal
 * time, so a completed turn reloads as what it was — `text → tools → text` —
 * instead of collapsing to its last sentence. `content_text` is still written
 * and still correct; it is the FINAL text, for previews and model context, and
 * remains the fallback for every message written before this existed.
 *
 * Scoped to the assistant: user `content` blocks are composer parts
 * (attachments, quotes) with their own vocabulary, and nothing is gained by
 * reinterpreting them here.
 */
function partsFromContentBlocks(
  message: ApiChatMessage,
): TcChatMessagePart[] | null {
  if (message.role !== "assistant" || !Array.isArray(message.content)) {
    return null;
  }
  const parts: TcChatMessagePart[] = [];
  for (const block of message.content) {
    const type = block.type;
    const text = block.text;
    if ((type !== "text" && type !== "reasoning") || typeof text !== "string") {
      continue;
    }
    const status = block.status;
    const statusType =
      status !== null && typeof status === "object"
        ? (status as Record<string, unknown>).type
        : undefined;
    parts.push({
      type,
      text,
      status:
        statusType === "running" ? { type: "running" } : { type: "complete" },
      ...(typeof block.seq === "number" ? { seq: block.seq } : {}),
      ...(typeof block.startedAtMs === "number"
        ? { startedAtMs: block.startedAtMs }
        : {}),
      ...(typeof block.updatedAtMs === "number"
        ? { updatedAtMs: block.updatedAtMs }
        : {}),
    });
  }
  return parts.length > 0 ? parts : null;
}

function toTcChatMessage(message: ApiChatMessage): TcChatMessage {
  if (Array.isArray(message.parts)) {
    return {
      message_id: message.message_id,
      role: message.role,
      parts: message.parts,
      ...(message.run_id != null ? { run_id: message.run_id } : {}),
      ...(message.created_at_ms != null
        ? { created_at_ms: message.created_at_ms }
        : {}),
    };
  }
  const createdAt =
    message.created_at != null ? Date.parse(message.created_at) : Number.NaN;
  const blockParts = partsFromContentBlocks(message);
  const text = message.content_text ?? "";
  return {
    message_id: message.message_id,
    role: message.role,
    parts: blockParts ?? (text.length > 0 ? [{ type: "text", text }] : []),
    ...(message.run_id != null ? { run_id: message.run_id } : {}),
    ...(Number.isNaN(createdAt) ? {} : { created_at_ms: createdAt }),
  };
}

/**
 * Fetch + normalize the durable conversation transcript. Extracted so BOTH
 * TcChat's default-mount fallback and the Run cockpit's `useRunTranscript`
 * binder resolve messages through ONE wire mapping.
 */
export async function fetchConversationMessages(
  transport: Transport,
  conversationId: string,
): Promise<TcChatMessage[]> {
  const res = await transport.request<ApiChatMessagesResponse>({
    method: "GET",
    path: `/v1/agent/conversations/${conversationId}/messages`,
  });
  return (res.messages ?? []).map(toTcChatMessage);
}

export interface TcChatProps {
  readonly conversationId: string;
  readonly mode: TcChatMode;
  /**
   * Host-provided transcript. When supplied, TcChat is fully presentational and
   * renders exactly these messages — the Run cockpit's `useRunTranscript` binder
   * feeds persisted history ⊕ the live streamed reply off the single event
   * stream (FR-3.3). Omitted → the component falls back to a one-time GET of the
   * conversation (standalone usage + the ThreadCanvas default mount).
   */
  readonly messages?: readonly TcChatMessage[];
  readonly onSend?: (text: string) => void;
  readonly portalTarget?: HTMLElement;
  /**
   * Anchor/chip renderers forwarded to `MarkdownText` (its `components.a`
   * slot routes citation anchors to the host's chip dispatcher). Injected so
   * assistant markdown keeps its citation chips without chat-surface pulling
   * in the host's citation wrappers.
   */
  readonly markdownComponents?: MarkdownTextProps["components"];
  /**
   * PR-3.8 — parallel-subagent fleets projected off the run stream
   * (`projectSubagents(session.events)`). When the agent dispatches a batch,
   * the matching `SubagentFleetCard` renders inline in the conversation,
   * anchored by the dispatch event's timestamp (FR-3.17a). Empty/omitted in
   * standalone usage — linear runs render no fleet card.
   */
  readonly fleets?: readonly FleetProjection[];
  /**
   * Detailed inner work for each fleet child, projected from the same canonical
   * run event array as `fleets`. The chat only renders this injected map; it
   * does not subscribe to or re-project the stream.
   */
  readonly subagentActivitiesByTask?: ReadonlyMap<
    string,
    readonly SubagentActivityRecord[]
  >;
  /**
   * Workstream D — main-agent tool-call cards projected off the run stream
   * (`projectToolCalls(session.events)`). Each entry interleaves into the
   * transcript at the point its tool ran (running spinner → done/error), in
   * BOTH Studio and Focus (shared transcript). Empty/omitted in standalone
   * usage — a run with no tool calls renders no card. Subagent tool calls are
   * excluded upstream (they belong to the subagent views, FR-3.17).
   */
  readonly toolCalls?: readonly ToolCallEntry[];
  /**
   * The agent's working checklist (`projectRunTodos(session.events)`), pinned
   * above the composer in BOTH modes so it never scrolls away mid-run. `null`
   * — the common case — renders nothing: most requests are too small for the
   * agent to open a list, and an empty frame would be worse than no panel.
   */
  readonly todos?: RunTodosProjection | null;
  /**
   * The run's terminal verdict, rendered as the final beat of the stream. The
   * host projects and owns it (`projectRunTerminalBeat` +
   * `RunTerminalBeatCard`); the chat only places it last. It lives here rather
   * than on the canvas so a run has exactly ONE statement about how it ended,
   * in the column the user is already reading.
   */
  readonly terminalBeat?: ReactNode;
  /** PRD-03 D-3.5 — the RUN's terminal failure keeps its group open. */
  readonly runFailed?: boolean;
  /** PRD-03 D-3.6 — narrow surface; shortens the group's summary label. */
  readonly compact?: boolean;
  /**
   * Run-scoped citations supplied by the cockpit's canonical event projection.
   * Inline source cards select only citations whose backend-issued
   * `source_tool_call_id` matches their tool call; no source is inferred.
   */
  readonly toolCallCitations?: readonly CitationSourceRef[];
  /**
   * PR-3.10 — pending + recently-resolved approvals projected off the run
   * stream. A pending one renders as the compact ask card, identically in both
   * modes; a RESOLVED one renders nothing. The host hides them while scrubbed
   * off-now by passing `[]`; as a safeguard the chat also hides them whenever
   * the scrub cursor is off-now.
   */
  readonly approvals?: readonly TcChatApproval[];
  /** Resolve the approval (host owns the POST); fires on Approve / `⌘↵`. */
  readonly onApprove?: (approvalId: string) => void;
  /** Reject the approval (host owns the POST); fires on Reject / `⌘⌫`. */
  readonly onReject?: (approvalId: string) => void;
  /**
   * Answer an `ask_a_question` interrupt (host owns the POST). Separate from
   * `onApprove` because the wire carries the answer text, and because an
   * approval that silently became an answer would resume the run with the
   * wrong payload.
   */
  readonly onAnswer?: (approvalId: string, answer: QuestionAnswer) => void;
  /**
   * WC-P5a (AD-6/AD-7): host launcher for the `mcp_auth` Connect card. When an
   * approval's `approvalKind === "mcp_auth"` (or its id is `mcp_discovery:`-
   * prefixed), the card renders a Connect / Skip pair wired to this port instead
   * of Approve/Reject — Connect → `beginAuth(serverId)`, Skip → `skipAuth(serverId)`
   * — so the connector-auth gate NEVER resolves through `onApprove`/`onReject`'s
   * `/decision` POST (which 404s on discovery and mis-resolves the gate). Omitted
   * → the Connect card still renders (the gate stays visible) but its actions are
   * inert; the host wires this in P5b. Non-`mcp_auth` approvals ignore the port.
   */
  readonly mcpAuthPort?: McpAuthPort;
  /**
   * Per-`server_id` consent state, owned by the host's OAuth machine
   * (`useConnectorConsentStates`). Absent entries render `pending` — the run
   * stream can report that a gate opened, but connecting / connected / denied
   * happen in a popup it cannot observe.
   */
  readonly connectorConsentStates?: Readonly<
    Record<string, ConnectorConsentState>
  >;
  /**
   * Compact OAuth-success receipt. Run events are scoped to the bound run, so
   * the cockpit retains this while the automatic user turn rebinds the stream;
   * when the originating approval is still projected, that card wins and this
   * fallback stays hidden.
   */
  readonly connectedConnectorReceipt?: ConnectedConnectorReceipt | null;
  /**
   * Return a connector to `pending` — the card's Cancel while connecting. The
   * Run cockpit supplies `useConnectorConsentStates().markPending`; absent, the
   * Cancel button renders inert like every other unwired affordance here.
   */
  readonly onConnectorConsentCancel?: (serverId: string) => void;
  /**
   * "Never suggest this again", fired alongside the deny when the card is an
   * uninstalled CATALOG suggestion. Denying a gate for a connector the user
   * already installed is a decision about this run; denying an unsolicited
   * suggestion is a decision about the connector, and the design puts the mute
   * where that intent forms rather than only in Settings.
   */
  readonly onConnectorMute?: (catalogSlug: string) => void;
  /**
   * Per-`approval_id` state of a folder ask, owned by the host's grant machine
   * (`useWorkspaceGrantCardStates`). Absent entries render `pending` for the
   * same reason the connector states do: the run stream can report that a
   * folder was asked for, but the OS dialog that answers it is invisible to the
   * stream.
   */
  readonly workspaceGrantStates?: Readonly<
    Record<string, WorkspaceGrantCardState>
  >;
  /**
   * Per-`approval_id` failure text for a folder ask that could not be granted
   * (OS refusal, broker down, disk gone). Shown verbatim on the `failed` card,
   * because "we couldn't" with no reason is the same dead end as an empty
   * listing — the user cannot tell whether to retry or to go fix something.
   */
  readonly workspaceGrantFailures?: Readonly<Record<string, string>>;
  /**
   * Grant the folder — the host calls `WorkspaceGrantPort.requestGrant` and the
   * OS dialog it opens is the real consent. Omitted → the card renders inert
   * (the ask stays readable, the buttons do nothing), never a `/decision`
   * fallback: `onApprove` would resume a run that still has no grant, which is
   * exactly how a refusal became an empty success.
   */
  readonly onWorkspaceGrant?: (
    approvalId: string,
    request: WorkspaceGrantRequest,
  ) => void;
  /** Decline the folder — the run continues without it (host owns the POST). */
  readonly onWorkspaceGrantDeny?: (approvalId: string) => void;
  /** Abandon the ask while the OS dialog is up (state-only; see the hook). */
  readonly onWorkspaceGrantCancel?: (approvalId: string) => void;
  /** Open a parked write's payload on its detail surface ("Review →"). */
  readonly onReviewWriteGate?: (approvalId: string) => void;
  /**
   * The audit anchor for a gate-bearing approval, joined host-side from the
   * ledger fold (`ledger.gates.get(approvalId)?.ledgerId`). A callback rather
   * than a field on the approval: `ledgerId` is anchored on the `gate.opened`
   * event, which is a DIFFERENT event from the `approval_requested` the
   * approval projection folds, so the two folds must stay separate and the
   * join belongs at the host. Returns undefined when there is no ledger row.
   */
  readonly ledgerIdByApprovalId?: (approvalId: string) => string | undefined;
  /**
   * Composer slot override. When supplied, the cockpit renders the host's
   * composer in place of the bare base `<Composer>` — the seam the desktop
   * host uses to mount the full `AssistantComposer` (attachments, `/`-menu,
   * connectors, model picker) while keeping the Run cockpit's scrub/ghost
   * gating: the ghost `disabled` state and the placeholder are handed to the
   * host so the injected composer disables identically off-live. The host owns
   * submission end-to-end (it wires its own `onSubmit`), so `onSend` is only
   * consulted for the default base composer. Omitted → the base `<Composer>`
   * renders as before (web + tests unchanged).
   */
  readonly renderComposer?: (ctx: {
    readonly disabled: boolean;
    readonly placeholder: string;
  }) => ReactNode;
  /**
   * The active run — the `sequence_no` space shared by the projected cards and
   * the active turn's part `seq` values. Only that run's parts join the
   * seq-ordered interleave in `mergeStream`; a prior run's turn keeps document
   * order, because every run numbers its events from 0 and merging across runs
   * would collide their seqs. Omitted → inferred from the last seq-bearing
   * message, which keeps standalone usage and existing fixtures working.
   */
  readonly activeRunId?: string | null;
  /**
   * The bound run is live and has produced nothing visible yet — drives the
   * "Thinking" shimmer that fills the gap between send and the first token.
   * Host-owned: only the cockpit knows the run's status, and the transcript
   * alone cannot tell "still thinking" from "finished with no output".
   */
  readonly awaitingFirstOutput?: boolean;
  /**
   * Artifacts to interleave into the transcript where they were published.
   * Host-owned (the cockpit holds the projection); omitted ⇒ the transcript is
   * byte-identical to before this existed.
   */
  readonly inlineArtifacts?: readonly InlineArtifactEntry[];
  /** Transport an EXPANDED artifact fetches through. Collapsed cards do not. */
  readonly artifactTransport?: Transport;
  readonly artifactDownloadPort?: ArtifactDownloadPort;
  /** Hands the reader the full Studio workspace for one artifact. */
  readonly onOpenArtifactInStudio?: (subjectKey: string) => void;
}

const EMPTY_FLEETS: readonly FleetProjection[] = [];
/** Stable identity, so an unwired host never re-runs the merge on every render. */
const EMPTY_INLINE_ARTIFACTS: readonly InlineArtifactEntry[] = [];
const EMPTY_SUBAGENT_ACTIVITIES: ReadonlyMap<
  string,
  readonly SubagentActivityRecord[]
> = new Map();
const EMPTY_TOOL_CALLS: readonly ToolCallEntry[] = [];
const EMPTY_TOOL_CALL_CITATIONS: readonly CitationSourceRef[] = [];
const EMPTY_APPROVALS: readonly TcChatApproval[] = [];
// WC-P5a (AD-7): the Connect card's persistent rule line. Connecting starts an
// OAuth flow in a new tab (the host owns the redirect); nothing is shared until
// you approve on the vendor's consent screen.
const MCP_AUTH_REASSURANCE =
  "Connecting opens the vendor's sign-in — Copilot never sees your password.";
// GONE WITH THE TWO-SHAPE SPLIT, and worth naming so it is not lost silently:
// the mode-specific reassurances ("You're always asked before Copilot acts
// outside this chat." in Studio, "The agent paused here — nothing runs until
// you decide." in Focus) were `ConsentCard`'s ONLY accessible description —
// visually hidden, referenced by `aria-describedby`. The compact ask card has
// no slot for one, so an approval currently announces as its title plus three
// buttons. Restoring it means a prop on the card, not a constant here: the
// claim has to reach the DOM to be worth anything, and there is exactly one
// shape now, so there is one claim to make, not two.

type LoadState =
  | { readonly status: "idle" }
  | { readonly status: "loading" }
  | {
      readonly status: "ready";
      readonly messages: ReadonlyArray<TcChatMessage>;
    }
  | { readonly status: "error" };

export function TcChat(props: TcChatProps): ReactElement {
  const {
    conversationId,
    mode,
    messages: hostMessages,
    onSend,
    portalTarget,
    markdownComponents,
    fleets = EMPTY_FLEETS,
    subagentActivitiesByTask = EMPTY_SUBAGENT_ACTIVITIES,
    toolCalls = EMPTY_TOOL_CALLS,
    todos = null,
    toolCallCitations = EMPTY_TOOL_CALL_CITATIONS,
    approvals = EMPTY_APPROVALS,
    onApprove,
    onReject,
    onAnswer,
    mcpAuthPort,
    connectorConsentStates,
    connectedConnectorReceipt = null,
    onConnectorConsentCancel,
    onConnectorMute,
    workspaceGrantStates,
    workspaceGrantFailures,
    onWorkspaceGrant,
    onWorkspaceGrantDeny,
    onWorkspaceGrantCancel,
    onReviewWriteGate,
    ledgerIdByApprovalId,
    renderComposer,
    terminalBeat,
    runFailed = false,
    compact = false,
    activeRunId = null,
    awaitingFirstOutput = false,
    inlineArtifacts,
    artifactTransport,
    artifactDownloadPort,
    onOpenArtifactInStudio,
  } = props;
  const transport = useTransport();
  const scrub = useSwimlaneScrub();
  const hostFed = hostMessages !== undefined;
  const [fetched, setFetched] = useState<LoadState>({ status: "idle" });

  // Fallback fetch — ONLY when the host does not supply the transcript. The Run
  // cockpit feeds `messages` via useRunTranscript (history ⊕ live stream), so
  // this never runs there; it keeps standalone usage + the ThreadCanvas default
  // mount working with a one-time GET.
  useEffect(() => {
    if (hostFed) {
      return;
    }
    let cancelled = false;
    setFetched({ status: "loading" });
    fetchConversationMessages(transport, conversationId)
      .then((messages) => {
        if (!cancelled) setFetched({ status: "ready", messages });
      })
      .catch(() => {
        if (!cancelled) setFetched({ status: "error" });
      });
    return () => {
      cancelled = true;
    };
  }, [conversationId, transport, hostFed]);

  const state: LoadState =
    hostMessages !== undefined
      ? { status: "ready", messages: hostMessages }
      : fetched;

  // PR-3.10 — approvals are HIDDEN while scrubbed off-now (you cannot approve a
  // past state). The host also drops them from `approvals` when scrubbed, but
  // guarding on the scrub cursor here keeps standalone usage correct too.
  const transcriptRef = useRef<HTMLDivElement | null>(null);
  const scrubbedOffNow = scrub.scrubbedTo !== "now";
  // A RESOLVED approval is history, not a live decision, so it does not belong
  // in the strip directly above the composer. Once the user has approved, the
  // run continues and its result — the listing, the answer — is already in the
  // transcript; a "✓ Approved · <name>" line pinned above the input adds no
  // information and pushes the conversation up. The record is not lost: the
  // Approvals tab still projects every decision from the same event stream.
  //
  // Question cards are exempt because a resolved question still shows the
  // answer the user gave, which the transcript does not repeat.
  const liveApprovals = scrubbedOffNow ? EMPTY_APPROVALS : approvals;
  // Inline, a PENDING approval renders in place, anchored where it was asked.
  // A settled one renders nothing at all — see `renderApprovalItem`. The old
  // strip filtered resolved ones out for the same reason, and the receipt that
  // briefly replaced them was worse: bare text in a transcript of cards.
  const visibleApprovals = liveApprovals;
  // The decision surface can now scroll away, which is exactly what the pinned
  // strip prevented. This is the replacement: one line of chrome, not a card,
  // that says how many are waiting and jumps to the oldest.
  const pendingApprovals = liveApprovals.filter(
    (approval) => !approval.resolved,
  );
  const oldestPending = pendingApprovals.reduce<TcChatApproval | null>(
    (oldest, approval) =>
      oldest === null || approvalAt(approval) < approvalAt(oldest)
        ? approval
        : oldest,
    null,
  );
  const projectedConnectedReceipt =
    connectedConnectorReceipt !== null &&
    visibleApprovals.some(
      (approval) =>
        isMcpAuthApproval(approval) &&
        approval.serverId === connectedConnectorReceipt.serverId,
    );
  const connectedReceipt =
    !scrubbedOffNow &&
    connectedConnectorReceipt !== null &&
    !projectedConnectedReceipt
      ? renderConnectedConnectorReceipt(connectedConnectorReceipt)
      : null;

  const ghost = scrub.scrubbedTo !== "now";
  const ghostLabel =
    typeof scrub.scrubbedTo === "number"
      ? formatGhostTime(scrub.scrubbedTo)
      : null;
  // One placeholder source for both the base composer and an injected host
  // composer, so the off-live copy stays identical across the seam.
  const composerPlaceholder = ghost
    ? "Snap to now to send a message"
    : "Send a message…";

  const filteredMessages = filterByScrub(state, scrub.scrubbedTo);
  // PR-3.8 — fleet cards follow the same scrub cursor as messages so a
  // time-travelled conversation never shows a batch dispatched after the cut.
  const filteredFleets = filterFleetsByScrub(fleets, scrub.scrubbedTo);
  // Workstream D — tool cards follow the SAME scrub cursor so a tool that ran
  // after the cut never appears in a time-travelled transcript.
  const filteredToolCalls = filterToolCallsByScrub(toolCalls, scrub.scrubbedTo);

  // Focus and Studio render the SAME transcript + composer (single-mount,
  // FR-3.9): the streamed reply, the ghost banner, the approvals and the
  // composer are all shared. The only thing left that the mode changes here is
  // the wrapper — Focus centers the column.
  const ghostBanner =
    ghost && ghostLabel !== null ? (
      <div
        role="status"
        data-testid="tc-chat-ghost-banner"
        style={ghostBannerStyle}
      >
        Viewing {ghostLabel}
      </div>
    ) : null;

  // Scoped to the transcript's own node, not `document` — the substrate
  // boundary bans bare globals, and a ref subtree query needs none.
  const jumpToApproval = (approvalId: string): void => {
    transcriptRef.current
      ?.querySelector(`[data-testid="tc-chat-approval-item-${approvalId}"]`)
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  const approvalHandlers: ApprovalHandlers = {
    onApprove,
    onReject,
    onAnswer,
    mcpAuthPort,
    connectorConsentStates,
    onConnectorConsentCancel,
    onConnectorMute,
    workspaceGrantStates,
    workspaceGrantFailures,
    onWorkspaceGrant,
    onWorkspaceGrantDeny,
    onWorkspaceGrantCancel,
    onReviewWriteGate,
    ledgerIdByApprovalId,
  };

  const transcript = (
    <div
      ref={transcriptRef}
      data-testid="tc-chat-messages"
      style={messageListStyle(ghost)}
    >
      <MessageListBody
        state={state}
        messages={filteredMessages}
        fleets={filteredFleets}
        subagentActivitiesByTask={subagentActivitiesByTask}
        toolCalls={filteredToolCalls}
        toolCallCitations={toolCallCitations}
        approvals={visibleApprovals}
        approvalHandlers={approvalHandlers}
        parked={oldestPending !== null}
        mode={mode}
        markdownComponents={markdownComponents}
        terminalBeat={terminalBeat}
        runFailed={runFailed}
        compact={compact}
        activeRunId={activeRunId}
        awaitingFirstOutput={awaitingFirstOutput}
        {...(inlineArtifacts === undefined ? {} : { inlineArtifacts })}
        {...(artifactTransport === undefined ? {} : { artifactTransport })}
        {...(artifactDownloadPort === undefined
          ? {}
          : { artifactDownloadPort })}
        {...(onOpenArtifactInStudio === undefined
          ? {}
          : { onOpenArtifactInStudio })}
      />
    </div>
  );

  // The reachability line + the OAuth-success receipt are the only things left
  // pinned besides the checklist: both are transient chrome with no place in
  // the conversation (the receipt outlives the run it belongs to, by design).
  const pinnedNotices =
    oldestPending !== null || connectedReceipt !== null ? (
      <div data-testid="tc-chat-notices" style={noticesStyle}>
        {oldestPending !== null ? (
          <button
            type="button"
            data-testid="tc-chat-approvals-waiting"
            data-pending-count={pendingApprovals.length}
            style={waitingStyle}
            onClick={() => jumpToApproval(oldestPending.approvalId)}
          >
            <span aria-hidden="true" style={waitingDotStyle} />
            {pendingApprovals.length === 1
              ? "1 approval waiting"
              : `${pendingApprovals.length} approvals waiting`}
            <span aria-hidden="true" style={waitingArrowStyle}>
              ↓
            </span>
          </button>
        ) : null}
        {connectedReceipt}
      </div>
    ) : null;

  // The ONLY pinned element above the composer now that approvals interleave
  // into the transcript — so it never shifts, which is what made pinning it
  // worth doing. Single-mount like the transcript itself, so a Focus↔Studio
  // switch never remounts it. Hidden while
  // scrubbed: the checklist snapshot has no per-row timestamps to rewind to, so
  // showing today's list beside a time-travelled transcript would assert a
  // state that did not hold at the cut.
  // NOTE: no wrapper element here. `TcChat.test.tsx` pins
  // `panel.nextElementSibling === tc-chat-composer-slot` in both modes — the
  // checklist is pinned DIRECTLY above the composer and nothing may come
  // between them. `TcTodoList` therefore carries the shared content rail on
  // its own root (see `railStyle` there) rather than being wrapped in one.
  const todoList =
    todos !== null && !ghost ? (
      <TcTodoList projection={todos} blocked={oldestPending !== null} />
    ) : null;

  const composer = (
    <div data-testid="tc-chat-composer-slot" style={composerSlotStyle}>
      {renderComposer !== undefined ? (
        renderComposer({ disabled: ghost, placeholder: composerPlaceholder })
      ) : (
        <Composer
          onSend={(text) => onSend?.(text)}
          disabled={ghost}
          placeholder={composerPlaceholder}
          portalTarget={portalTarget}
        />
      )}
    </div>
  );

  // Both modes render the SAME tail. The two blocks used to differ only in
  // their approvals strip — one conf-card list, one big-card list — that
  // difference first moved inside `renderApprovalItem` and has now been deleted
  // outright: there is one ask card, and both modes render it.
  return (
    <div
      data-testid="tc-chat"
      data-mode={mode}
      data-ghost={ghost ? "true" : "false"}
      style={mode === "focus" ? focusContainerStyle : chatContainerStyle()}
      aria-live="polite"
    >
      {ghostBanner}
      {transcript}
      {pinnedNotices}
      {todoList}
      {composer}
    </div>
  );
}

// PR-3.10 — in-chat approval renderers. Pure presentation over the injected
// projection: every pending Approve/Decline ask is the SAME compact card, a
// SETTLED approval renders nothing, and resolution is the injected
// onApprove/onReject (D28).

/**
 * The ONE approval renderer, and now genuinely one: what is chosen here is the
 * KIND of ask, never a skin for it. `mode` is not a parameter at all — Studio
 * and Focus render byte-identical approvals.
 *
 * The branches are kinds because each resolves through a DIFFERENT seam, not
 * because each wants a different look:
 *   - a write gate / tool action → Approve or Decline, host `/decision` POST
 *   - a question                 → an ANSWER, whose text the wire carries
 *   - a folder ask               → `WorkspaceGrantPort`, an OS dialog
 *   - a connector ask            → `McpAuthPort`, an OAuth redirect
 * Collapsing any of those into the ask card would post the wrong thing (or,
 * for a `mcp_discovery:` id, post to a row that does not exist and 404).
 *
 * ORDER IS LOAD-BEARING. The write gate goes first because its wire shape is
 * `ask_a_question`, so the question branch would otherwise claim it and render
 * a yes/no about a real side effect as a free-text box.
 *
 * This used to be a 16-call-site conditional written out TWICE, once per mode
 * block. Approvals now interleave into the transcript like every other card
 * family, which leaves one call site and made the duplicate unnecessary.
 */
function renderApprovalItem(
  approval: TcChatApproval,
  handlers: ApprovalHandlers,
): ReactNode {
  if (!approval.resolved && isWriteGateApproval(approval)) {
    return renderAskCard(approval, handlers);
  }
  if (approval.question !== null) {
    return renderQuestionCard(approval, handlers.onAnswer);
  }
  if (approval.resolved) {
    // A settled approval leaves NOTHING behind, either way it went.
    //
    // The receipt was one line of bare text — "✓ Approved · <title>" — in a
    // transcript otherwise made of cards, which is what made it read as debris
    // rather than as a record. Approved, it restated the tool card directly
    // below it; denied, it was the only row in the thread that was not a card
    // at all. Neither loses the decision: it is on the run's event stream,
    // which is what the Approvals tab and the audit views project from.
    //
    // This is only about what survives the click. The decision surface itself —
    // the card that asks and takes Approve/Reject — is a card and stays one.
    return null;
  }
  if (isWorkspaceGrantApproval(approval)) {
    return renderWorkspaceGrantCard(
      approval,
      handlers.workspaceGrantStates,
      handlers.workspaceGrantFailures,
      handlers.onWorkspaceGrant,
      handlers.onWorkspaceGrantDeny,
      handlers.onWorkspaceGrantCancel,
    );
  }
  if (isMcpAuthApproval(approval)) {
    return renderMcpAuthConnectCard(
      approval,
      handlers.mcpAuthPort,
      handlers.connectorConsentStates,
      handlers.onConnectorConsentCancel,
      handlers.onConnectorMute,
    );
  }
  // Everything else that is decided with Approve/Decline — a `tool_action`, an
  // `mcp_tool` call, an unrecognised kind — is the SAME card as a parked write.
  // It was never a different question; it was the same question drawn twice.
  return renderAskCard(approval, handlers);
}

/** Everything `renderApprovalItem` needs, bundled so the interleave pass can carry it. */
interface ApprovalHandlers {
  readonly onApprove?: (approvalId: string) => void;
  readonly onReject?: (approvalId: string) => void;
  readonly onAnswer?: (approvalId: string, answer: QuestionAnswer) => void;
  readonly mcpAuthPort?: McpAuthPort;
  readonly connectorConsentStates?: Readonly<
    Record<string, ConnectorConsentState>
  >;
  readonly onConnectorConsentCancel?: (serverId: string) => void;
  readonly onConnectorMute?: (catalogSlug: string) => void;
  readonly workspaceGrantStates?: Readonly<
    Record<string, WorkspaceGrantCardState>
  >;
  readonly workspaceGrantFailures?: Readonly<Record<string, string>>;
  readonly onWorkspaceGrant?: (
    approvalId: string,
    request: WorkspaceGrantRequest,
  ) => void;
  readonly onWorkspaceGrantDeny?: (approvalId: string) => void;
  readonly onWorkspaceGrantCancel?: (approvalId: string) => void;
  /**
   * Told that the reader opened a parked write's detail. A NOTIFICATION, not a
   * navigation: the card expands IN PLACE and does so whether or not this is
   * wired (no host supplies it today). Passed only when the host actually
   * supplied one — see `renderAskCard`.
   */
  readonly onReviewWriteGate?: (approvalId: string) => void;
  /**
   * The audit anchor for a gate-bearing approval, joined host-side from the
   * ledger fold (`ledger.gates.get(approvalId)?.ledgerId`). A callback rather
   * than a field on the approval: `ledgerId` is anchored on the `gate.opened`
   * event, which is a DIFFERENT event from the `approval_requested` the
   * approval projection folds, so the two folds must stay separate and the
   * join belongs at the host. Returns undefined when there is no ledger row.
   */
  readonly ledgerIdByApprovalId?: (approvalId: string) => string | undefined;
}

// WC-P5a (AD-7) — the `mcp_auth` Connect card. Its own frame
// (`ConnectorConsentCard`), and Connect/Skip rather than Approve/Reject, wired
// to the host `McpAuthPort` and NOT to `onApprove`/`onReject`: a connector-auth
// gate resolves via OAuth (a host `mcp_auth_resolved` decision after the
// redirect returns, P5b) and a `mcp_discovery:` suggestion is never a persisted
// approval row, so a `/decision` POST would 404 (AD-7). Rendered in BOTH modes
// with no difference between them, like every other ask here (the
// connector-auth affordance is mode-agnostic). When no port is wired, or the
// payload carried no `server_id`, the actions render disabled — the gate stays
// visible but inert (never a crash, never a `/decision` fallback).
function renderMcpAuthConnectCard(
  approval: TcChatApproval,
  mcpAuthPort?: McpAuthPort,
  consentStates?: Readonly<Record<string, ConnectorConsentState>>,
  onConsentCancel?: (serverId: string) => void,
  onMute?: (catalogSlug: string) => void,
): ReactNode {
  const serverId = approval.serverId;
  // A gate names its connector with `connector_slug`; an uninstalled suggestion
  // names the same thing with `catalog_slug`. Both answer "which connector",
  // which is all a slug-keyed connect needs — the two fields differ in what
  // they imply about installation, not in what they identify.
  const connectorSlug = approval.connectorSlug ?? approval.catalogSlug ?? null;
  const actionable = mcpAuthPort !== undefined && serverId !== null;
  return (
    <div
      key={`mcp-auth-${approval.approvalId}`}
      data-testid={`tc-chat-mcp-auth-${approval.approvalId}`}
      data-approval-id={approval.approvalId}
      data-server-id={serverId ?? ""}
    >
      <ConnectorConsentCard
        displayName={approval.title}
        // The model's stated reason for wanting the connector — narrative, and
        // the one line on this card it authors.
        purpose={approval.summary ?? approval.reason}
        // `pending` is the only state the run stream can report; the other
        // three happen after the host launches OAuth, in a popup the stream
        // cannot see. The host's `useConnectorConsentStates` owns those and
        // supplies them here — absent still means pending, so a host that has
        // not wired it behaves exactly as before.
        state={
          (serverId !== null ? consentStates?.[serverId] : undefined) ??
          "pending"
        }
        trust={approval.connectorTrust}
        brandKey={serverId}
        actionable={actionable}
        onConnect={() =>
          serverId !== null
            ? mcpAuthPort?.beginAuth(serverId, { connectorSlug })
            : undefined
        }
        onDeny={() => {
          if (serverId === null) return;
          mcpAuthPort?.skipAuth(serverId);
          // Only a catalog suggestion is muteable; a gate carries no slug, so
          // this is a no-op there rather than a branch on approval-id prefixes.
          const slug = approval.catalogSlug ?? null;
          if (slug !== null) {
            onMute?.(slug);
          }
        }}
        // Reconsider re-enters OAuth: a denial is a decision the design
        // deliberately lets the user reverse, so it is the same verb as
        // Connect, not a separate one.
        onReconsider={() =>
          serverId !== null
            ? mcpAuthPort?.beginAuth(serverId, { connectorSlug })
            : undefined
        }
        // Cancel is state-only — see `markPending`. Both handlers went
        // unpassed until now without anyone noticing, because `connecting` and
        // `denied` were states nothing could reach.
        onCancel={() =>
          serverId !== null ? onConsentCancel?.(serverId) : undefined
        }
        connectTestId={`tc-chat-mcp-connect-${approval.approvalId}`}
        denyTestId={`tc-chat-mcp-skip-${approval.approvalId}`}
        testId={`tc-chat-connector-${approval.approvalId}`}
      />
    </div>
  );
}

// The folder-grant ask. Same shape of wiring as the Connect card above and for
// the same reason: what settles it is an OS dialog the run stream cannot see, so
// the state comes from the host's machine (`useWorkspaceGrantCardStates`) and
// the decision goes to `WorkspaceGrantPort`, never to the `/decision` POST that
// `onApprove` owns. Rendered in BOTH modes — being asked for a folder is not a
// Studio-only event. With no handler wired the card renders inert: the ask stays
// readable, which is still strictly better than the defect it replaces (an
// ungranted read answered with an empty listing and a green tick).
function renderWorkspaceGrantCard(
  approval: TcChatApproval,
  states?: Readonly<Record<string, WorkspaceGrantCardState>>,
  failures?: Readonly<Record<string, string>>,
  onGrant?: (approvalId: string, request: WorkspaceGrantRequest) => void,
  onDeny?: (approvalId: string) => void,
  onCancel?: (approvalId: string) => void,
): ReactNode {
  // Non-null by construction — `isWorkspaceGrantApproval` gated this branch.
  const request = approval.workspaceGrant!;
  const actionable = onGrant !== undefined;
  const grant = (): void => onGrant?.(approval.approvalId, request);
  return (
    <div
      key={`workspace-grant-${approval.approvalId}`}
      data-testid={`tc-chat-workspace-grant-${approval.approvalId}`}
      data-approval-id={approval.approvalId}
    >
      <WorkspaceGrantCard
        request={request}
        state={states?.[approval.approvalId] ?? "pending"}
        failureMessage={failures?.[approval.approvalId] ?? null}
        actionable={actionable}
        onGrant={grant}
        onDeny={() => onDeny?.(approval.approvalId)}
        onCancel={() => onCancel?.(approval.approvalId)}
        // Retry and reverse-a-decline are the same verb as Grant — ask again.
        onReconsider={grant}
        grantTestId={`tc-chat-workspace-grant-approve-${approval.approvalId}`}
        denyTestId={`tc-chat-workspace-grant-deny-${approval.approvalId}`}
        testId={`tc-chat-grant-${approval.approvalId}`}
      />
    </div>
  );
}

function renderConnectedConnectorReceipt(
  receipt: ConnectedConnectorReceipt,
): ReactNode {
  return (
    <div
      key={`mcp-connected-${receipt.approvalId}`}
      data-testid={`tc-chat-mcp-auth-${receipt.approvalId}`}
      data-approval-id={receipt.approvalId}
      data-server-id={receipt.serverId}
    >
      <ConnectorConsentCard
        displayName={receipt.displayName}
        purpose={null}
        state="connected"
        trust={EMPTY_CONNECTOR_TRUST}
        brandKey={receipt.serverId}
        testId={`tc-chat-connector-${receipt.approvalId}`}
      />
    </div>
  );
}

/**
 * THE ask card — every approval that is settled with Approve or Decline, in
 * both modes, whether or not a write gate parked it.
 *
 * There used to be three of these: a parked write got the compact row, Studio
 * got a big `ConsentCard`, Focus got the same `ConsentCard` inside a
 * `.conf-card` wrapper that has no CSS rule anywhere in the product. So the
 * "two shapes" the mode chose between were one shape with two sets of testids
 * and two different reassurance strings — while the surface that actually
 * looked different was the one nobody could pick: the write gate.
 *
 * Everything gate-specific degrades to omission rather than to an empty frame,
 * which is what lets one card serve both. An ordinary `tool_action` ask has no
 * vendor, no access axis and no ledger row; it renders as title + reason +
 * params + the decision, and none of the missing pieces leave a hole.
 *
 * The decision controls are APPROVAL-SCOPED here, for every ask, write gates
 * included — one card body, three ids that name the approval they decide. Two
 * asks parked at once is a drawn state, and a global `tc-write-gate-approve`
 * is ambiguous in it: Playwright refuses the selector, so "two cards parked"
 * becomes a decision that never happened. Six live desktop journeys already
 * select these names (five by the `tc-chat-approval-approve-` prefix,
 * `connectors/gate_audit_events.py` by the exact reject id), so this is the
 * scheme the packaged app is driven by — do not add a second, unscoped alias.
 */
function renderAskCard(
  approval: TcChatApproval,
  handlers: ApprovalHandlers,
): ReactNode {
  // Captured so the presence check below narrows it — `handlers.x` is a
  // property read, and TS will not narrow one across a closure boundary.
  const notifyReview = handlers.onReviewWriteGate;
  const grantAlways = buildGrantAlwaysArm(approval, handlers);
  return (
    <div
      key={`approval-${approval.approvalId}`}
      data-testid={`tc-chat-approval-${approval.approvalId}`}
      data-approval-id={approval.approvalId}
    >
      <TcWriteGateRow
        title={approval.title}
        // `linear · write`. Both halves come off the same projected category,
        // so they arrive or vanish together; a non-MCP ask has neither and the
        // meta span is simply not rendered.
        connector={approval.category?.vendor ?? null}
        access={approval.category?.access ?? null}
        irreversible={isIrreversible(approval)}
        // Why the agent is asking. The card shows it in the expanded body only
        // — a compact ask that has to be read before it can be decided is not
        // a compact ask — and omits the paragraph when it is empty.
        reason={approval.reason}
        // The payload was already sitting here — the old cards showed it, the
        // row simply never did, which is why reviewing a write meant leaving
        // the transcript.
        params={approval.params}
        // The server-projected SHAPE, and the verb the approve button promises.
        // Null on the write-gate lane — that wire shape carries no presentation
        // — and the card renders exactly as before when it is. It is the plain
        // `mcp_tool` lane that has one, and dropping it here is what made a
        // batch of twelve payees render as a params frame that did not contain
        // the batch, and a 2000-character draft render as a `<dd>`.
        presentation={approval.presentation}
        // Joined host-side from the ledger fold; `undefined` for an approval
        // with no gate, where the honest answer is that there is no ledger row
        // rather than a guessed id. Absent ⇒ the audit line is omitted.
        ledgerId={handlers.ledgerIdByApprovalId?.(approval.approvalId)}
        approveTestId={`tc-chat-approval-approve-${approval.approvalId}`}
        declineTestId={`tc-chat-approval-reject-${approval.approvalId}`}
        // NOT `…-approve-body-<id>`: that would match the
        // `[data-testid^=tc-chat-approval-approve-]` prefix five journeys press,
        // and an irreversible write's only approve is meant to be unreachable
        // until the payload has rendered. The safety property is enforced by
        // the SHAPE of the name, not only by the branch that renders it.
        bodyApproveTestId={`tc-chat-approval-body-approve-${approval.approvalId}`}
        // The durable arm, on the asks that offer one. Approval-scoped like the
        // other three decision names, and deliberately outside the
        // `tc-chat-approval-approve-` prefix five journeys press: this button
        // hands over a folder that outlives the run, which is strictly more than
        // those journeys mean by "Approve".
        grantAlwaysTestId={`tc-chat-approval-grant-always-${approval.approvalId}`}
        {...(grantAlways === null ? {} : { grantAlways })}
        onApprove={() => handlers.onApprove?.(approval.approvalId)}
        onDecline={() => handlers.onReject?.(approval.approvalId)}
        // Omitted when no host is listening, rather than passed as a function
        // that calls nothing. The card treats it as a notification either way,
        // so the disclosure works regardless — but wrapping "nobody is
        // listening" in a callback is how a dead control hides.
        {...(notifyReview === undefined
          ? {}
          : { onReview: () => notifyReview(approval.approvalId) })}
      />
    </div>
  );
}

/**
 * The ask card's durable arm, or null to leave the ask once-only.
 *
 * THREE THINGS MUST ALL BE TRUE, and each rules out a different way of offering
 * a grant nobody agreed to:
 *
 *   1. the wire offered it AND named its folder — decided upstream in
 *      `buildGrantAlways`, which requires `grant_options` to carry
 *      `allow_always` and `grant_scope` to name the subject. Nothing here
 *      re-derives that; a client that inferred the option from `read_only`
 *      would hand out folders the producer deliberately refused to offer.
 *   2. a host can actually take the decision — `onWorkspaceGrant` is passed only
 *      when a `WorkspaceGrantPort` exists (web has none), so absent means the
 *      arm is omitted rather than drawn as a button that opens no dialog.
 *   3. the ask is still open. A settled approval renders nothing at all, so this
 *      is structural rather than a guard — but it is why the arm never needs a
 *      "you already chose" state.
 *
 * REUSES THE FOLDER-ASK MACHINERY WHOLESALE, and that is the point rather than a
 * shortcut. `useWorkspaceGrantCardStates` already owns exactly this round trip:
 * optimistic `granting`, `granted` → resume the run through the same
 * `/decision` POST, `cancelled` → back to pending, `failed` → keep the run
 * paused and SHOW the host's message. Its states are keyed by `approval_id`, and
 * a folder ask and an ordinary ask can never share one, so the two consumers of
 * that map are disjoint by construction. A second copy of that machine would
 * have been a second place for "grant succeeded but the run never resumed" to
 * live.
 */
function buildGrantAlwaysArm(
  approval: TcChatApproval,
  handlers: ApprovalHandlers,
): WriteGateGrantAlways | null {
  const request = approval.grantAlways ?? null;
  const onGrant = handlers.onWorkspaceGrant;
  if (request === null || onGrant === undefined) {
    return null;
  }
  return {
    request,
    state: handlers.workspaceGrantStates?.[approval.approvalId] ?? "pending",
    failureMessage:
      handlers.workspaceGrantFailures?.[approval.approvalId] ?? null,
    onGrant: () => onGrant(approval.approvalId, request),
    onCancel: () => handlers.onWorkspaceGrantCancel?.(approval.approvalId),
  };
}

/**
 * Whether this ask cannot be undone from inside the app.
 *
 * A pass-through now, and that is the fix. This used to substring-match
 * `category.access` for "destructive" — an axis whose only producers emit
 * `READ` / `WRITE` / nothing — so it was ALWAYS FALSE outside a hand-built
 * fixture, and the card's destructive lane (no one-click Approve, the "can't be
 * undone" chip) could not fire for any payload a user could actually provoke.
 * Every test of that lane passed, on fixtures, over a dead branch.
 *
 * It took two changes, neither of them in this card: the server stopped
 * dropping `op_class` / `risk_level` at `_ask_a_question_requested_payload` (a
 * parked write borrows that wire shape), and `buildIrreversible` in the
 * approval projection now decides it where the wire is read. The reasoning
 * about WHICH field means what lives there, with the payloads.
 *
 * Kept as a function rather than inlining `approval.irreversible` because
 * `RunDestination` calls it for the gate lane too, and one predicate is what
 * stops the two surfaces disagreeing about whether a write can be undone.
 */
export function isIrreversible(approval: TcChatApproval): boolean {
  return approval.irreversible === true;
}

function renderQuestionCard(
  approval: TcChatApproval,
  onAnswer?: (approvalId: string, answer: QuestionAnswer) => void,
): ReactNode {
  if (approval.question === null) {
    return null;
  }
  return (
    <div
      key={`question-${approval.approvalId}`}
      data-testid={`tc-chat-question-${approval.approvalId}`}
      data-approval-id={approval.approvalId}
    >
      <QuestionCard
        spec={approval.question}
        resolved={approval.resolved}
        answer={approval.summary}
        onAnswer={(answer) => onAnswer?.(approval.approvalId, answer)}
        testId={`tc-chat-question-card-${approval.approvalId}`}
      />
    </div>
  );
}

interface MessageListBodyProps {
  readonly state: LoadState;
  readonly messages: ReadonlyArray<TcChatMessage>;
  readonly fleets: readonly FleetProjection[];
  readonly subagentActivitiesByTask: ReadonlyMap<
    string,
    readonly SubagentActivityRecord[]
  >;
  readonly toolCalls: readonly ToolCallEntry[];
  readonly toolCallCitations: readonly CitationSourceRef[];
  /**
   * Approvals interleaved into the transcript at the point they were asked, so
   * the decision sits beside the tool call that provoked it. Nothing survives
   * the decision, whichever way it went — approved, the tool card it released
   * says the same thing better; declined, the run's event stream is the record,
   * and it is the one the Approvals tab and the audit views already read.
   */
  readonly approvals: readonly TcChatApproval[];
  readonly approvalHandlers: ApprovalHandlers;
  /**
   * The run is parked on a pending approval, so every still-`running` tool card
   * reads waiting rather than running. Same flag and same reasoning as
   * `TcTodoList`'s `blocked`; both surfaces were asserting motion that had
   * stopped.
   */
  readonly parked: boolean;
  readonly mode: TcChatMode;
  readonly markdownComponents?: MarkdownTextProps["components"];
  readonly terminalBeat?: ReactNode;
  /** PRD-03 D-3.5 — the RUN's terminal failure keeps its group open. */
  readonly runFailed?: boolean;
  /** PRD-03 D-3.6 — narrow surface; shortens the group's summary label. */
  readonly compact?: boolean;
  /**
   * The run whose `sequence_no` space the cards and the active turn's part
   * `seq` values share. Only that run's parts join the seq-ordered interleave —
   * see `mergeStream`. Omitted → inferred from the last seq-bearing message.
   */
  readonly activeRunId?: string | null;
  /**
   * The bound run is live and has produced nothing visible yet.
   *
   * Between send and the first token the column rendered NOTHING — measured at
   * 5.16s on gpt-5.6-luna and 2.80s on claude-sonnet-5 for a trivial prompt, and
   * longer on anything real. The host owns this because only it knows the run's
   * status; the transcript alone cannot tell "still thinking" from "finished
   * with no output".
   */
  readonly awaitingFirstOutput?: boolean;
  /**
   * Artifacts interleaved into the transcript at the point they were PUBLISHED,
   * on the same rule approvals follow. Focus mode used to answer "an artifact
   * exists" with a pinned card above the transcript whose only move was to
   * leave the mode entirely; inline, reading one is no longer a mode switch.
   *
   * Empty (the default) ⇒ `mergeStream` receives `[]` and the transcript is
   * byte-identical, which is what keeps this prop safe to land unmounted.
   */
  readonly inlineArtifacts?: readonly InlineArtifactEntry[];
  /** Transport for an EXPANDED artifact; collapsed cards never fetch. */
  readonly artifactTransport?: Transport;
  readonly artifactDownloadPort?: ArtifactDownloadPort;
  readonly onOpenArtifactInStudio?: (subjectKey: string) => void;
}

function MessageListBody(props: MessageListBodyProps): ReactNode {
  const {
    state,
    messages,
    fleets,
    subagentActivitiesByTask,
    toolCalls,
    toolCallCitations,
    approvals,
    approvalHandlers,
    parked,
    mode,
    markdownComponents,
    terminalBeat,
    runFailed = false,
    compact = false,
    activeRunId,
    awaitingFirstOutput = false,
    inlineArtifacts = EMPTY_INLINE_ARTIFACTS,
    artifactTransport,
    artifactDownloadPort,
    onOpenArtifactInStudio,
  } = props;
  // The message-load notice never SUPPRESSES the live cards any more. It used
  // to be an early return, which was harmless while approvals lived in a strip
  // outside this component — inline, it meant a slow or failed message fetch
  // hid a pending approval completely, and the run would sit parked with no
  // visible way to unblock it. The notice is now a row above the stream.
  const notice =
    state.status === "loading" || state.status === "idle" ? (
      <div role="status" style={statusStyle} data-testid="tc-chat-loading">
        Loading messages…
      </div>
    ) : state.status === "error" ? (
      <div role="alert" style={statusStyle} data-testid="tc-chat-error">
        Failed to load messages.
      </div>
    ) : null;

  // Artifacts count here for the same reason approvals do: this guard used to
  // early-return past the live cards, and inline that hid a parked run's only
  // way out. An answer that is entirely an artifact — "here is the CSV" with no
  // prose — would otherwise render "No messages yet." over a real result.
  const nothingToShow =
    messages.length === 0 &&
    fleets.length === 0 &&
    toolCalls.length === 0 &&
    approvals.length === 0 &&
    inlineArtifacts.length === 0 &&
    terminalBeat === undefined;
  if (notice !== null && nothingToShow) {
    return notice;
  }
  if (nothingToShow) {
    return (
      <div role="status" style={statusStyle} data-testid="tc-chat-empty">
        No messages yet.
      </div>
    );
  }
  // Messages (GET) plus the three projected-off-the-run-stream card families —
  // fleet cards (PR-3.8), tool-call cards (Workstream D) and approvals — are
  // interleaved by timestamp so each lands where it happened in the flow.
  const items = mergeStream(
    messages,
    fleets,
    toolCalls,
    approvals,
    activeRunId ?? null,
    inlineArtifacts,
  );

  const renderItem = (item: StreamItem): ReactNode => {
    if (item.kind === "fleet") {
      return renderFleetCard(item.fleet, subagentActivitiesByTask);
    }
    if (item.kind === "tool") {
      return renderToolCard(item.toolCall, mode, toolCallCitations, parked);
    }
    if (item.kind === "part") {
      // The absorbed cards are rendered HERE, not inside `renderPart`, because
      // this is the only scope holding what a card needs to draw itself
      // (mode, citations, parked, the subagent activity map). The thinking
      // block frames them; it never learns about any of that.
      const absorbed = item.activity ?? [];
      return renderMessagePartItem(
        item.message,
        item.part,
        item.index,
        markdownComponents,
        absorbed.length === 0
          ? undefined
          : {
              cards: absorbed.map((a) =>
                a.kind === "tool"
                  ? renderToolCard(a.toolCall, mode, toolCallCitations, parked)
                  : renderFleetCard(a.fleet, subagentActivitiesByTask),
              ),
              summary: summariseGroup(
                absorbed.map((a) =>
                  a.kind === "tool"
                    ? {
                        status: a.toolCall.status,
                        createdAtMs: a.toolCall.createdAtMs,
                        durationMs: a.toolCall.durationMs,
                      }
                    : { createdAtMs: a.fleet.createdAtMs },
                ),
                runFailed,
              ),
              total: absorbed.length,
            },
      );
    }
    if (item.kind === "approval") {
      const content = renderApprovalItem(item.approval, approvalHandlers);
      // An approved approval renders nothing at all now. The wrapper has to go
      // with it — an empty <li> still contributes the row's margin, which would
      // leave the gap the receipt used to occupy.
      if (content === null) return null;
      return (
        <li
          key={`approval-item-${item.approval.approvalId}`}
          style={approvalItemStyle}
          data-testid={`tc-chat-approval-item-${item.approval.approvalId}`}
          data-approval-pending={item.approval.resolved ? "false" : "true"}
        >
          {content}
        </li>
      );
    }
    if (item.kind === "artifact") {
      // The transport is what makes an expanded card able to fetch. Without it
      // the card could only ever render its collapsed row, so we render nothing
      // rather than a row whose Expand button silently does nothing.
      if (artifactTransport === undefined) return null;
      return (
        <li
          key={`artifact-item-${item.artifact.artifactId}`}
          style={approvalItemStyle}
          data-testid={`tc-chat-artifact-item-${item.artifact.artifactId}`}
        >
          <TcInlineArtifactCard
            artifact={item.artifact}
            transport={artifactTransport}
            {...(artifactDownloadPort === undefined
              ? {}
              : { downloadPort: artifactDownloadPort })}
            {...(onOpenArtifactInStudio === undefined
              ? {}
              : { onOpenInStudio: onOpenArtifactInStudio })}
          />
        </li>
      );
    }
    return renderMessage(item.message, markdownComponents);
  };

  // PRD-03 — fold consecutive ACTIVITY into one collapsible group. Pure view
  // layer: `items` order is untouched, only its framing changes.
  //
  // Only tool + fleet opt in. Messages and approvals pass through, and so does
  // any kind added later — an approval buried inside a collapsed group would
  // hide a parked run's only way out.
  const grouped = groupActivityStream(items, {
    isGroupable: (item) => item.kind === "tool" || item.kind === "fleet",
    idOf: (item) =>
      item.kind === "fleet"
        ? `fleet-${item.fleet.fleetId}`
        : item.kind === "tool"
          ? item.toolCall.id
          : "group",
  });

  return (
    <>
      {notice}
      <ul style={ulStyle}>
        {grouped.map((entry) => {
          if (entry.kind !== "group") {
            return renderItem(entry.item);
          }
          const summary = summariseGroup(
            entry.members.map((m) =>
              m.kind === "tool"
                ? {
                    status: m.toolCall.status,
                    createdAtMs: m.toolCall.createdAtMs,
                    durationMs: m.toolCall.durationMs,
                  }
                : {
                    createdAtMs:
                      m.kind === "fleet" ? m.fleet.createdAtMs : null,
                  },
            ),
            runFailed,
          );
          return (
            <li key={`group-${entry.id}`} style={fleetItemStyle}>
              <ToolRunGroup
                state={summary.state}
                done={summary.done}
                total={summary.total}
                retried={summary.retried}
                elapsed={
                  summary.elapsedMs === null
                    ? null
                    : formatSubagentDuration(summary.elapsedMs)
                }
                compact={compact}
              >
                {entry.members.map(renderItem)}
              </ToolRunGroup>
            </li>
          );
        })}
        {/* The wait itself. Placed where the first prose part will land, so the
            shimmer is replaced in place and nothing reflows when it arrives.
            Dropped the moment ANY visible output exists — it must never compete
            with the answer, and a second "thinking" under a streaming reply
            would be a lie about what the model is doing. */}
        {awaitingFirstOutput ? (
          <li
            style={messageItemStyle("assistant")}
            data-testid="tc-chat-awaiting"
          >
            <ThinkingShimmer />
          </li>
        ) : null}
        {terminalBeat}
      </ul>
    </>
  );
}

function renderPart(
  part: TcChatMessagePart,
  role: TcChatMessage["role"],
  key: number | string,
  markdownComponents?: MarkdownTextProps["components"],
  activity?: ThoughtActivity,
): ReactNode {
  const status: MessagePartStatus = part.status ?? { type: "complete" };
  if (part.type === "reasoning") {
    // Collapsed disclosure with a shimmering header while the span streams.
    // Bare `<Reasoning>` rendered the raw chain of thought inline with the
    // answer, unlabelled — indistinguishable from the reply on the surface
    // where it matters, because the accordion that would have labelled it is
    // styled from the web app's stylesheet, which desktop never loads.
    const elapsed =
      part.startedAtMs !== undefined && part.updatedAtMs !== undefined
        ? Math.floor((part.updatedAtMs - part.startedAtMs) / 1000)
        : 0;
    return (
      <ThinkingBlock
        key={key}
        text={part.text}
        running={status.type === "running"}
        elapsedSeconds={elapsed}
        {...(activity === undefined
          ? {}
          : {
              activity: activity.cards,
              stepCount: activity.total,
              failedCount: activity.summary.retried,
              activityRunning: activity.summary.state === "running",
            })}
      >
        {/* Omitted rather than rendered empty: a reasoning span that has
            produced no prose yet still carries its tool cards, and an empty
            markdown block above them adds a stray gap inside the body. */}
        {part.text.trim() === "" ? null : (
          <Reasoning type="reasoning" text={part.text} status={status} />
        )}
      </ThinkingBlock>
    );
  }
  // User input stays literal (a typed `| pipe |` is not markdown);
  // agent/tool/system text routes through the citation-safe streaming
  // markdown path so conversational GFM tables render as real tables
  // with the incremental blinking cursor, never as half-parsed raw
  // pipes (FR-3.19).
  if (role === "user") {
    return <PlainText key={key} type="text" text={part.text} status={status} />;
  }
  return (
    <MarkdownText
      key={key}
      type="text"
      text={part.text}
      status={status}
      components={markdownComponents}
    />
  );
}

function renderMessage(
  m: TcChatMessage,
  markdownComponents?: MarkdownTextProps["components"],
): ReactNode {
  return (
    <li
      key={m.message_id}
      style={messageItemStyle(m.role)}
      data-testid={`tc-chat-message-${m.message_id}`}
      data-role={m.role}
    >
      {(m.parts ?? []).map((part, idx) =>
        renderPart(part, m.role, idx, markdownComponents),
      )}
    </li>
  );
}

/**
 * One part of a seq-ordered turn, as its own stream item so cards can sit
 * between the parts. The assistant `<li>` is transparent and full-bleed
 * (`messageItemStyle`), so splitting a turn across several of them is visually
 * identical to the single-`<li>` render — no bubble is being broken up.
 */
function renderMessagePartItem(
  m: TcChatMessage,
  part: TcChatMessagePart,
  index: number,
  markdownComponents?: MarkdownTextProps["components"],
  activity?: ThoughtActivity,
): ReactNode {
  return (
    <li
      key={`${m.message_id}-part-${index}`}
      style={messageItemStyle(m.role)}
      data-testid={`tc-chat-message-${m.message_id}-part-${index}`}
      data-role={m.role}
      data-part-type={part.type}
      data-part-seq={typeof part.seq === "number" ? part.seq : undefined}
    >
      {renderPart(part, m.role, index, markdownComponents, activity)}
    </li>
  );
}

/** The already-rendered work folded into one reasoning span, plus the counts
 *  its header states. Assembled by `renderItem`, which owns the card
 *  renderers; consumed only by `renderPart`. */
interface ThoughtActivity {
  readonly cards: readonly ReactNode[];
  readonly summary: GroupSummary;
  readonly total: number;
}

// PR-3.8 — reuse the hoisted `SubagentFleetCard` (Phase 1D) with the projected
// fleet head + one `FleetSubagentRow` per child. The card + rows are pure
// presentation; the projection is the single source of truth (FR-3.17a).
function renderFleetCard(
  fleet: FleetProjection,
  activitiesByTask: ReadonlyMap<string, readonly SubagentActivityRecord[]>,
): ReactNode {
  return (
    <li
      key={`fleet-${fleet.fleetId}`}
      style={fleetItemStyle}
      data-testid={`tc-chat-fleet-${fleet.fleetId}`}
    >
      <SubagentFleetCard
        fleetId={fleet.fleetId}
        title={fleet.title}
        sub={fleet.sub}
        total={fleet.total}
        running={fleet.running}
        done={fleet.done}
        failed={fleet.failed}
        elapsed={fleet.elapsed}
      >
        {fleet.children.map((child) => (
          <FleetSubagentRow
            key={child.task_id}
            view={subagentCardFromEntry(child)}
            activities={activitiesByTask.get(child.task_id)}
          />
        ))}
      </SubagentFleetCard>
    </li>
  );
}

// Workstream D — the compact inline tool-call card. The reusable card owns the
// visual disclosure target and its bounded payload/detail treatment; TcChat
// only owns transcript ordering and the list-item anchor.
function renderToolCard(
  toolCall: ToolCallEntry,
  mode: TcChatMode,
  citations: readonly CitationSourceRef[],
  parked: boolean,
): ReactNode {
  return (
    <li
      key={`tool-${toolCall.id}`}
      style={toolItemStyle}
      data-testid={`tc-chat-tool-${toolCall.id}`}
      data-tool-status={toolCall.status}
    >
      <ToolCallCard toolCall={toolCall} parked={parked} />
      {mode === "studio" ? (
        <InlineToolResultCard toolCall={toolCall} citations={citations} />
      ) : null}
    </li>
  );
}

/** Activity that can be folded into a reasoning span — the same two kinds
 *  `groupActivityStream` will group, and deliberately no others. */
type ActivityItem =
  | { readonly kind: "fleet"; readonly fleet: FleetProjection }
  | { readonly kind: "tool"; readonly toolCall: ToolCallEntry };

type StreamItem =
  | { readonly kind: "message"; readonly message: TcChatMessage }
  | {
      readonly kind: "part";
      readonly message: TcChatMessage;
      readonly part: TcChatMessagePart;
      readonly index: number;
      /**
       * Tool / fleet cards that ran INSIDE this reasoning span — populated by
       * `absorbThoughtActivity`, empty for every other part.
       */
      readonly activity?: readonly ActivityItem[];
    }
  | ActivityItem
  | { readonly kind: "approval"; readonly approval: TcChatApproval }
  | { readonly kind: "artifact"; readonly artifact: InlineArtifactEntry };

/** An item anchored to a `sequence_no`, for the interleave pass. */
interface AnchoredItem {
  readonly seq: number;
  readonly item: StreamItem;
}

/**
 * The run whose seq space the tail of the transcript lives in, when the host
 * did not name one. The active turn is always the LAST message carrying
 * seq-bearing parts, so its `run_id` is the answer.
 */
function inferActiveRunId(
  messages: ReadonlyArray<TcChatMessage>,
): string | null {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i];
    if (
      message.run_id != null &&
      message.parts.some((part) => typeof part.seq === "number")
    ) {
      return message.run_id;
    }
  }
  return null;
}

/**
 * Interleave the projected card families (fleets, tool calls, approvals) with
 * the assistant turn's PARTS, ordered by `sequence_no`.
 *
 * THIS USED TO ANCHOR ON WALL-CLOCK TIMESTAMPS, and that was the render half of
 * the interleaving bug. A turn is `text → tools → text → tools`, but the whole
 * turn arrived as ONE message carrying ONE anchor (`created_at_ms` of its first
 * token). Every card that ran mid-turn therefore compared as "after the
 * message" and drained to the tail — one bubble, then a pile of cards,
 * regardless of what actually happened when. No timestamp fix could have solved
 * it: text-before-tools and text-after-tools were the same object with one
 * anchor. They are separate parts now, and each carries the seq it opened at.
 *
 * `sequence_no` is the runtime's monotonic total order per run and is what the
 * ledger seals; wall-clock is not an ordering key across producers and collides
 * routinely at streaming rates.
 *
 * SCOPE — only parts belonging to `activeRunId` join the seq merge. Every run
 * numbers its events from 0, so seq-merging a prior run's turn would collide
 * its seq 5 with this run's seq 5. Everything else keeps document order and
 * renders ahead of the merged tail, which is where history belongs anyway.
 */
function mergeStream(
  messages: ReadonlyArray<TcChatMessage>,
  fleets: readonly FleetProjection[],
  toolCalls: readonly ToolCallEntry[],
  approvals: readonly TcChatApproval[],
  activeRunId: string | null,
  artifacts: readonly InlineArtifactEntry[] = [],
): readonly StreamItem[] {
  const runId = activeRunId ?? inferActiveRunId(messages);
  const anchored: AnchoredItem[] = [];
  const out: StreamItem[] = [];

  // CARDS BELONG TO A RUN, NOT TO THE TRANSCRIPT.
  //
  // `sequenceNo` is an offset, not an address: every run numbers its events
  // from 0, so run A's seq 3 and run B's seq 3 are different moments that sort
  // as the same one. Merging every card into one seq order on that basis put
  // EVERY run's cards into the active run's block — turn t-1 rendered bare
  // while turn t collected the pile, growing each turn. The message side has
  // always guarded against exactly this (`message.run_id === runId` below); the
  // cards were simply never given the identity to be guarded by.
  //
  // So a card from a settled run is flushed with ITS OWN turn, in message
  // order, and only the active run's cards take part in the seq interleave.
  // A card whose `runId` is null cannot be placed — it is treated as the active
  // run's, which is what the whole stream was assumed to be before this.
  const settledCards = new Map<string, AnchoredItem[]>();
  const cardRun = (id: string | null): string | null =>
    id === null || id === runId ? null : id;
  const stash = (id: string, entry: AnchoredItem): void => {
    const bucket = settledCards.get(id);
    if (bucket === undefined) settledCards.set(id, [entry]);
    else bucket.push(entry);
  };
  let pendingRun: string | null = null;
  const flushRun = (id: string | null | undefined): void => {
    if (id == null) return;
    const bucket = settledCards.get(id);
    if (bucket === undefined) return;
    settledCards.delete(id);
    bucket.sort((a, b) => a.seq - b.seq);
    for (const entry of bucket) out.push(entry.item);
  };

  // Partitioned BEFORE the walk below, because that walk is what drains the
  // buckets — populating them afterwards left every settled card to fall
  // through to the tail, which is the bug this function exists to fix.
  for (const toolCall of toolCalls) {
    const entry: AnchoredItem = {
      seq: cardSeq(toolCall.sequenceNo),
      item: { kind: "tool", toolCall },
    };
    const settled = cardRun(toolCall.runId);
    if (settled !== null) stash(settled, entry);
    else anchored.push(entry);
  }

  for (const message of messages) {
    const seqBearing =
      runId !== null &&
      message.run_id === runId &&
      message.parts.some((part) => typeof part.seq === "number");
    if (!seqBearing) {
      // Flushed when the run CHANGES, not when it first appears: a turn is
      // `user → assistant`, and the cards belong after the answer they were
      // produced for, not between the question and it.
      if (message.run_id !== pendingRun) flushRun(pendingRun);
      pendingRun = message.run_id ?? null;
      out.push({ kind: "message", message });
      continue;
    }
    if (message.run_id !== pendingRun) flushRun(pendingRun);
    pendingRun = message.run_id ?? null;
    message.parts.forEach((part, index) => {
      anchored.push({
        seq: typeof part.seq === "number" ? part.seq : Number.MAX_SAFE_INTEGER,
        item: { kind: "part", message, part, index },
      });
    });
  }

  // Fleets are pushed before tool calls so cards sharing a seq keep a stable
  // fleet-then-tool order (ES sort is stable).
  for (const fleet of fleets) {
    anchored.push({
      seq: cardSeq(fleet.sequenceNo),
      item: { kind: "fleet", fleet },
    });
  }
  for (const approval of approvals) {
    anchored.push({
      seq: cardSeq(approval.sequenceNo),
      item: { kind: "approval", approval },
    });
  }
  // Artifacts last, so a card sharing a seq with an approval keeps the
  // approval first — the decision is the thing the reader must act on, and the
  // artifact it produced reads as its consequence. Same stable-sort convention
  // as fleet-before-tool above.
  //
  // Anchored on `createdSeq`, NOT `lastSeq`: an artifact revised later in the
  // run must stay where it was published. Anchoring on the revision would drag
  // it to the bottom of the thread, which is the same failure mode the
  // wall-clock note above describes for whole turns.
  for (const artifact of artifacts) {
    anchored.push({
      seq: cardSeq(artifact.createdSeq),
      item: { kind: "artifact", artifact },
    });
  }

  // A settled run with no message in this transcript (a turn still loading, or
  // one whose message failed to fetch) would otherwise have its cards silently
  // dropped. They go before the active block, which is where their run ran.
  flushRun(pendingRun);
  for (const id of [...settledCards.keys()]) {
    flushRun(id);
  }
  anchored.sort((a, b) => a.seq - b.seq);
  for (const item of absorbThoughtActivity(anchored.map((e) => e.item))) {
    out.push(item);
  }
  return out;
}

/**
 * Fold the work the model did WHILE thinking into the thought itself.
 *
 * A turn is `reasoning → tools → text`, and the three used to render as three
 * peers: a "Thought for 6s" disclosure, then a "Worked for 140ms · 2 steps"
 * disclosure, then the answer — two collapsed rows, in two different visual
 * languages, saying one thing between them. The tool calls are not a sibling of
 * the thought; they are what the thought DID.
 *
 * Absorbs only the run of tool/fleet items IMMEDIATELY following a reasoning
 * part, which is what makes this a description of the model's behaviour rather
 * than a bucket. `reasoning → text → tool` — thought, spoke, then acted — stops
 * at the text, and that tool stays a peer, because it happened after the
 * thought ended.
 *
 * Two kinds are deliberately NOT absorbable:
 * - **approvals**, for the reason the group predicate already documents — an
 *   approval buried inside a collapsed row hides a parked run's only way out;
 * - **artifacts**, which are the run's output, not its working.
 *
 * Operates on the ACTIVE run's sorted items only. Cards flushed from settled
 * runs are already in `out` by the time this runs, and they must stay peers:
 * their seq numbers index a different run's event space, so "immediately
 * following" is not a statement about them.
 */
function absorbThoughtActivity(
  items: readonly StreamItem[],
): readonly StreamItem[] {
  const isActivity = (item: StreamItem): item is ActivityItem =>
    item.kind === "tool" || item.kind === "fleet";
  const out: StreamItem[] = [];
  for (let i = 0; i < items.length; i += 1) {
    const item = items[i];
    if (item.kind !== "part" || item.part.type !== "reasoning") {
      out.push(item);
      continue;
    }
    let j = i + 1;
    const activity: ActivityItem[] = [];
    while (j < items.length) {
      const next = items[j];
      if (!isActivity(next)) break;
      activity.push(next);
      j += 1;
    }
    out.push(activity.length === 0 ? item : { ...item, activity });
    i = j - 1;
  }
  return out;
}

/** A card with no seq sorts to the tail rather than to the head. */
function cardSeq(sequenceNo: number | undefined): number {
  return typeof sequenceNo === "number" ? sequenceNo : Number.MAX_SAFE_INTEGER;
}

/**
 * Ordering anchor for "which pending approval is oldest". Prefers `sequenceNo`
 * for the same reason the interleave does; falls back to `createdAtMs` only for
 * a projection that predates the field. The two are never mixed in one
 * comparison in practice — a run's approvals all come from one projection.
 */
function approvalAt(approval: TcChatApproval): number {
  return typeof approval.sequenceNo === "number"
    ? approval.sequenceNo
    : (approval.createdAtMs ?? Number.MAX_SAFE_INTEGER);
}

function filterFleetsByScrub(
  fleets: readonly FleetProjection[],
  scrubbedTo: number | "now",
): readonly FleetProjection[] {
  if (scrubbedTo === "now") {
    return fleets;
  }
  return fleets.filter(
    (fleet) => fleet.createdAtMs === null || fleet.createdAtMs <= scrubbedTo,
  );
}

function filterToolCallsByScrub(
  toolCalls: readonly ToolCallEntry[],
  scrubbedTo: number | "now",
): readonly ToolCallEntry[] {
  if (scrubbedTo === "now") {
    return toolCalls;
  }
  return toolCalls.filter(
    (toolCall) =>
      toolCall.createdAtMs === null || toolCall.createdAtMs <= scrubbedTo,
  );
}

function filterByScrub(
  state: LoadState,
  scrubbedTo: number | "now",
): ReadonlyArray<TcChatMessage> {
  if (state.status !== "ready") {
    return [];
  }
  if (scrubbedTo === "now") {
    return state.messages;
  }
  return state.messages.filter((m) => {
    if (m.created_at_ms === undefined) {
      return true;
    }
    return m.created_at_ms <= scrubbedTo;
  });
}

function formatGhostTime(epochMs: number): string {
  const fmt = new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
  return fmt.format(new Date(epochMs));
}

// v3 "quiet" system colors resolved through design-system tokens so the chat
// canvas themes correctly (light / dark / accent). The previous hardcoded
// near-black hex was locked to the dark theme and drifted warm of the tokens.
const PALETTE = {
  cardBg: "var(--color-surface)",
  cardBorder: "var(--color-border)",
  textHi: "var(--color-text)",
  textLo: "var(--color-text-muted)",
  ghostBg: "var(--color-surface-muted)",
  ghostBorder: "var(--color-border-strong)",
  ghostAccent: "var(--color-accent)",
} as const;

// Flush pane, not a card: the chat column already sits inside the workspace
// rail (whose tab strip provides the separation), and the composer carries its
// own bordered shell — a third bordered box around both read as visual noise
// (design review: three nested borders within ~25px at the composer corner).
// One gap for the canvas stack (transcript · pinned notices · todo panel ·
// composer), shared by both modes so the two cannot drift apart again.
//
// 20, not the old 10: at 13px body text a 10px gutter reads as "stuck
// together" next to Claude Desktop / Codex, which give the composer real air.
// The todo panel used to add its own 8px bottom margin on top of this, so the
// stack had two different gaps depending on which pair you measured; that
// margin is gone and this constant is now the only spacing authority.
const CANVAS_STACK_GAP = 20;

// The canvas' own inset from the rail and the window edge. 12 left the
// composer nearly flush against the app rail in a narrow window.
const CANVAS_PADDING = 16;

const chatContainerStyle = (): CSSProperties => ({
  display: "flex",
  flexDirection: "column",
  height: "100%",
  background: "transparent",
  padding: CANVAS_PADDING,
  gap: CANVAS_STACK_GAP,
  color: PALETTE.textHi,
  // v3 anchors chat body text at 12.5–13px (copilot.css `body{font-size:13px}`,
  // `.msg{font-size:12.5px}`). Without this the message text inherited the UA
  // 16px default — the single largest text in the cockpit ("too big").
  fontSize: 13,
  fontFamily: "var(--font-sans)",
});

const messageListStyle = (ghost: boolean): CSSProperties => ({
  flex: 1,
  // Both axes are named on purpose: `overflow-y: auto` alone makes the CSS
  // `visible` on the other axis compute to `auto`, so the transcript silently
  // became a horizontal scroller too — one long token or a wide tool payload
  // and the messages pan sideways under a stationary composer. Wide blocks
  // (code, tables) scroll inside their own box; the column itself never does.
  overflowX: "hidden",
  overflowY: "auto",
  display: "flex",
  flexDirection: "column",
  gap: 8,
  padding: 8,
  opacity: ghost ? 0.55 : 1,
  pointerEvents: ghost ? "none" : "auto",
});

/** One shared reading rail for every conversation-owned surface. The outer
 * chat remains full-width so scrolling and the Focus side panel keep working;
 * only readable content is centered and capped. */
const conversationRailStyle: CSSProperties = {
  boxSizing: "border-box",
  marginLeft: "auto",
  marginRight: "auto",
  maxWidth: "var(--chat-content-width, 68rem)",
  width: "100%",
};

const ghostBannerStyle: CSSProperties = {
  ...conversationRailStyle,
  background: PALETTE.ghostBg,
  border: `1px solid ${PALETTE.ghostBorder}`,
  borderRadius: 8,
  color: PALETTE.ghostAccent,
  padding: "6px 10px",
  fontSize: "var(--font-size-xs)",
  letterSpacing: 0.4,
  textTransform: "uppercase",
};

const composerSlotStyle: CSSProperties = {
  ...conversationRailStyle,
  flexShrink: 0,
};

const statusStyle: CSSProperties = {
  ...conversationRailStyle,
  color: PALETTE.textLo,
  fontSize: "var(--font-size-xs)",
  padding: 12,
};

const ulStyle: CSSProperties = {
  ...conversationRailStyle,
  listStyle: "none",
  marginBottom: 0,
  marginTop: 0,
  padding: 0,
  display: "flex",
  flexDirection: "column",
  gap: 12,
};

const messageItemStyle = (role: TcChatMessage["role"]): CSSProperties => ({
  // v3 `.msg.you` — a right-aligned speech bubble (muted surface, asymmetric
  // radius, 88% cap) for the user; the assistant message renders flush.
  background: role === "user" ? "var(--color-surface-muted)" : "transparent",
  border: role === "user" ? `1px solid ${PALETTE.cardBorder}` : "none",
  borderRadius: role === "user" ? "10px 10px 3px 10px" : 0,
  padding: role === "user" ? "8px 11px" : "0",
  color: PALETTE.textHi,
  alignSelf: role === "user" ? "flex-end" : "stretch",
  maxWidth: role === "user" ? "88%" : "100%",
  // THE QUESTION AND THE ANSWER ARE THE SAME SIZE — and now they finally look
  // it. Both inherit the container's 13px, but the bubble was leaving
  // `line-height` at the UA `normal` (~1.2) while `.assistant-markdown` sets
  // 1.58, so identical type sat in a 15px line box on one side and a 20.5px
  // box on the other. Tight leading reads as bigger, loose leading as smaller;
  // measured they matched, and every reader saw the user's words as the larger
  // of the two. Matching the leading is what makes the measurement true.
  lineHeight: 1.58,
  // Parts of ONE turn — a thought and the answer under it — are siblings in
  // this item. Without a gap they butt together, which is what glued the
  // answer to the bottom of the thinking row. Matches `ulStyle`'s gap so the
  // boundary is the same 12px whether the turn's parts were seq-split into
  // their own items or not.
  display: "flex",
  flexDirection: "column",
  gap: 12,
  alignItems: "stretch",
});

// PR-3.8 — the fleet card carries its own chrome (`.aui-fleet-card`), so the
// list item is a bare positioning slot.
const fleetItemStyle: CSSProperties = {
  listStyle: "none",
  padding: 0,
};

const toolItemStyle: CSSProperties = {
  listStyle: "none",
  padding: 0,
};

// Focus mode: the SAME transcript + composer as Studio. The shell fills its
// workspace column; conversationRailStyle centers the readable content inside
// it so the transcript scroll and optional side panel remain structurally sound.
const focusContainerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  height: "100%",
  minHeight: 0,
  width: "100%",
  maxWidth: "none",
  margin: 0,
  background: "transparent",
  padding: CANVAS_PADDING,
  // Focus carried NO gap while Studio had one, so the transcript, the pinned
  // todo panel and the composer butted together with zero breathing room —
  // the last transcript line read as if the todo panel were sitting on top of
  // it. The panel is meant to be "identical in Focus and Studio"; its spacing
  // has to be too.
  gap: CANVAS_STACK_GAP,
  color: PALETTE.textHi,
  fontSize: 13,
  fontFamily: "var(--font-sans)",
};

// PR-3.10 — in-chat approvals (design-system tokens only; sky accent, jade
// success, ember danger — no lime, no hardcoded hex).

/** One transcript row holding an approval card, anchored where it was asked. */
const approvalItemStyle: CSSProperties = {
  listStyle: "none",
  margin: "8px 0",
  padding: 0,
};

/** The pinned strip's replacement: chrome, not cards. */
const noticesStyle: CSSProperties = {
  ...conversationRailStyle,
  flexShrink: 0,
  display: "flex",
  flexDirection: "column",
  gap: 6,
  padding: "0 8px 8px",
};

const waitingStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 8,
  alignSelf: "flex-start",
  padding: "5px 10px",
  border: "1px solid var(--color-border-strong)",
  borderRadius: "var(--radius-full, 999px)",
  background: "var(--color-surface)",
  color: "var(--color-text-muted)",
  fontFamily: "var(--font-mono)",
  fontSize: 11,
  cursor: "pointer",
};

const waitingDotStyle: CSSProperties = {
  width: 6,
  height: 6,
  borderRadius: "50%",
  background: "var(--color-warning, #e8b45e)",
};

const waitingArrowStyle: CSSProperties = {
  color: "var(--color-text-subtle)",
};

const approvalApproveButtonStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  background: "var(--color-accent)",
  color: "var(--color-accent-contrast, #101113)",
  border: "none",
  borderRadius: 8,
  padding: "8px 14px",
  fontSize: "var(--font-size-xs)",
  fontWeight: 600,
  cursor: "pointer",
  fontFamily: "inherit",
};

const approvalRejectButtonStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  background: "transparent",
  color: "var(--color-text, #f4f5f6)",
  border: "1px solid var(--color-border, #2a2d31)",
  borderRadius: 8,
  padding: "8px 14px",
  fontSize: "var(--font-size-xs)",
  fontWeight: 600,
  cursor: "pointer",
  fontFamily: "inherit",
};

const confCardStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  padding: 14,
  borderRadius: 12,
  background: "var(--color-accent-soft, rgba(95,178,236,.12))",
  border: "1px solid var(--color-accent, #5fb2ec)",
  color: "var(--color-text, #f4f5f6)",
};

const confHeadStyle: CSSProperties = {
  fontSize: "var(--font-size-sm)",
  fontWeight: 600,
  color: "var(--color-text, #f4f5f6)",
};

const confSummaryStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-xs)",
  lineHeight: 1.5,
  color: "var(--color-text-muted, #9aa0a6)",
};

const confActionsStyle: CSSProperties = {
  display: "flex",
  gap: 8,
  justifyContent: "flex-end",
};

const confApproveButtonStyle: CSSProperties = {
  background: "var(--color-accent)",
  color: "var(--color-accent-contrast, #101113)",
  border: "none",
  borderRadius: 8,
  padding: "6px 12px",
  fontSize: "var(--font-size-xs)",
  fontWeight: 600,
  cursor: "pointer",
  fontFamily: "inherit",
};

const confRejectButtonStyle: CSSProperties = {
  background: "transparent",
  color: "var(--color-text, #f4f5f6)",
  border: "1px solid var(--color-border, #2a2d31)",
  borderRadius: 8,
  padding: "6px 12px",
  fontSize: "var(--font-size-xs)",
  fontWeight: 600,
  cursor: "pointer",
  fontFamily: "inherit",
};

const confFootStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-2xs)",
  color: "var(--color-text-muted, #9aa0a6)",
};
