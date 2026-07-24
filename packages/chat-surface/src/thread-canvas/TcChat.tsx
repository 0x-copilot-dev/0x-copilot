import {
  useEffect,
  useState,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

import type { Transport } from "@0x-copilot/chat-transport";

import { Composer } from "../composer/Composer";
import { MarkdownText, type MarkdownTextProps } from "../messages/MarkdownText";
import { PlainText } from "../messages/PlainText";
import { Reasoning } from "../messages/Reasoning";
import type { MessagePartStatus } from "../messages/types";
import { useTransport } from "../providers/TransportProvider";
// PR-3.8 — inline parallel-subagent fleet card. Reuses the hoisted Phase-1D
// presentation family; the fleet state is projected upstream (FR-3.17a).
import {
  FleetSubagentRow,
  SubagentFleetCard,
  subagentCardFromEntry,
  type FleetProjection,
} from "../subagents";
// PR-3.10 — in-chat approvals. Reuses the hoisted Phase-1E consent family: the
// 4-zone `ApprovalCard` (pending, Studio) and the collapsed `ApprovalReceipt`
// (resolved). Presentation only — resolution is the injected onApprove/onReject
// (host owns the POST, D28). Focus mode renders a local `.conf-card` variant.
import {
  ApprovalCard,
  ApprovalReceipt,
  type ActivityParam,
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
import type { ToolCallEntry } from "./eventProjector";
import { useSwimlaneScrub } from "./SwimlaneScrubContext";

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
  /** Vendor·access pill; null when unknown. */
  readonly category: {
    readonly vendor: string;
    readonly access: string;
  } | null;
  /** Inset key/value frame. */
  readonly params: readonly ActivityParam[];
  /** Resolved? Pending → card / conf-card; resolved → receipt. */
  readonly resolved: boolean;
  /** Final decision once resolved; null while pending. */
  readonly decision: "approved" | "rejected" | null;
  /** Dispatch time (epoch ms) — the conversation anchor. */
  readonly createdAtMs: number | null;
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
}

export interface TcChatMessage {
  readonly message_id: string;
  readonly role: "user" | "assistant" | "system" | "tool";
  readonly parts: ReadonlyArray<TcChatMessagePart>;
  readonly created_at_ms?: number;
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
  readonly created_at?: string | null;
  readonly parts?: ReadonlyArray<TcChatMessagePart>;
  readonly created_at_ms?: number;
}
interface ApiChatMessagesResponse {
  readonly messages?: ReadonlyArray<ApiChatMessage>;
}
function toTcChatMessage(message: ApiChatMessage): TcChatMessage {
  if (Array.isArray(message.parts)) {
    return {
      message_id: message.message_id,
      role: message.role,
      parts: message.parts,
      ...(message.created_at_ms != null
        ? { created_at_ms: message.created_at_ms }
        : {}),
    };
  }
  const text = message.content_text ?? "";
  const createdAt =
    message.created_at != null ? Date.parse(message.created_at) : Number.NaN;
  return {
    message_id: message.message_id,
    role: message.role,
    parts: text.length > 0 ? [{ type: "text", text }] : [],
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
   * Workstream D — main-agent tool-call cards projected off the run stream
   * (`projectToolCalls(session.events)`). Each entry interleaves into the
   * transcript at the point its tool ran (running spinner → done/error), in
   * BOTH Studio and Focus (shared transcript). Empty/omitted in standalone
   * usage — a run with no tool calls renders no card. Subagent tool calls are
   * excluded upstream (they belong to the subagent views, FR-3.17).
   */
  readonly toolCalls?: readonly ToolCallEntry[];
  /**
   * PR-3.10 — pending + recently-resolved approvals projected off the run
   * stream. Studio renders each pending one as the hoisted 4-zone
   * `ApprovalCard` (Approve ⌘↵ / Reject ⌘⌫) and each resolved one as an
   * `ApprovalReceipt`; Focus renders pending ones as `.conf-card` confirmation
   * cards (FR-3.22). The host hides them while scrubbed off-now by passing `[]`;
   * as a safeguard the chat also hides them whenever the scrub cursor is off-now.
   */
  readonly approvals?: readonly TcChatApproval[];
  /** Resolve the approval (host owns the POST); fires on Approve / `⌘↵`. */
  readonly onApprove?: (approvalId: string) => void;
  /** Reject the approval (host owns the POST); fires on Reject / `⌘⌫`. */
  readonly onReject?: (approvalId: string) => void;
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
}

const EMPTY_FLEETS: readonly FleetProjection[] = [];
const EMPTY_TOOL_CALLS: readonly ToolCallEntry[] = [];
const EMPTY_APPROVALS: readonly TcChatApproval[] = [];
const APPROVAL_REASSURANCE =
  "You're always asked before Copilot acts outside this chat.";
// WC-P5a (AD-7): the Connect card's persistent rule line. Connecting starts an
// OAuth flow in a new tab (the host owns the redirect); nothing is shared until
// you approve on the vendor's consent screen.
const MCP_AUTH_REASSURANCE =
  "Connecting opens the vendor's sign-in — Copilot never sees your password.";

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
    toolCalls = EMPTY_TOOL_CALLS,
    approvals = EMPTY_APPROVALS,
    onApprove,
    onReject,
    mcpAuthPort,
    renderComposer,
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
  const scrubbedOffNow = scrub.scrubbedTo !== "now";
  const visibleApprovals = scrubbedOffNow ? EMPTY_APPROVALS : approvals;

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
  // FR-3.9): the streamed reply, the ghost banner, and the composer are shared.
  // They differ only in the wrapper (Focus centers the column) and the approval
  // affordance (Focus `.conf-card`, Studio the 4-zone `ApprovalCard`).
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

  const transcript = (
    <div data-testid="tc-chat-messages" style={messageListStyle(ghost)}>
      <MessageListBody
        state={state}
        messages={filteredMessages}
        fleets={filteredFleets}
        toolCalls={filteredToolCalls}
        markdownComponents={markdownComponents}
      />
    </div>
  );

  const composer = (
    <div style={composerSlotStyle}>
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

  if (mode === "focus") {
    return (
      <div
        data-testid="tc-chat"
        data-mode="focus"
        data-ghost={ghost ? "true" : "false"}
        style={focusContainerStyle}
        aria-live="polite"
      >
        {ghostBanner}
        {transcript}
        {/* Focus surfaces a pending approval as a `.conf-card` (resolved → its
            receipt), between the transcript and the composer. */}
        {visibleApprovals.length > 0 ? (
          <div data-testid="tc-chat-conf-cards" style={confCardsWrapStyle}>
            {visibleApprovals.map((approval) =>
              approval.resolved
                ? renderApprovalReceipt(approval)
                : isMcpAuthApproval(approval)
                  ? renderMcpAuthConnectCard(approval, mcpAuthPort)
                  : renderConfCard(approval, onApprove, onReject),
            )}
          </div>
        ) : null}
        {composer}
      </div>
    );
  }

  return (
    <div
      data-testid="tc-chat"
      data-mode={mode}
      data-ghost={ghost ? "true" : "false"}
      style={chatContainerStyle()}
      aria-live="polite"
    >
      {ghostBanner}
      {transcript}
      {/* PR-3.10 (FR-3.22) — in-chat approvals sit between the transcript and
          the composer: pending ones render the 4-zone ApprovalCard, resolved
          ones their receipt. Outside the ghost-dimmed message list so they stay
          interactive. */}
      {visibleApprovals.length > 0 ? (
        <div data-testid="tc-chat-approvals" style={approvalsWrapStyle}>
          {visibleApprovals.map((approval) =>
            approval.resolved
              ? renderApprovalReceipt(approval)
              : isMcpAuthApproval(approval)
                ? renderMcpAuthConnectCard(approval, mcpAuthPort)
                : renderStudioApprovalCard(approval, onApprove, onReject),
          )}
        </div>
      ) : null}
      {composer}
    </div>
  );
}

// PR-3.10 — in-chat approval renderers. Pure presentation over the injected
// projection: Studio uses the hoisted 4-zone `ApprovalCard`, Focus a local
// `.conf-card` confirmation card; a resolved approval collapses to the hoisted
// `ApprovalReceipt`. Resolution is the injected onApprove/onReject (D28).

function renderStudioApprovalCard(
  approval: TcChatApproval,
  onApprove?: (approvalId: string) => void,
  onReject?: (approvalId: string) => void,
): ReactNode {
  return (
    <div
      key={`approval-${approval.approvalId}`}
      data-testid={`tc-chat-approval-${approval.approvalId}`}
      data-approval-id={approval.approvalId}
    >
      <ApprovalCard
        title={approval.title}
        reason={approval.reason}
        category={approval.category}
        params={[...approval.params]}
        reassurance={APPROVAL_REASSURANCE}
        actions={
          <>
            <button
              type="button"
              data-testid={`tc-chat-approval-reject-${approval.approvalId}`}
              onClick={() => onReject?.(approval.approvalId)}
              style={approvalRejectButtonStyle}
            >
              Reject <span aria-hidden="true">⌘⌫</span>
            </button>
            <button
              type="button"
              data-testid={`tc-chat-approval-approve-${approval.approvalId}`}
              onClick={() => onApprove?.(approval.approvalId)}
              style={approvalApproveButtonStyle}
            >
              Approve <span aria-hidden="true">⌘↵</span>
            </button>
          </>
        }
      />
    </div>
  );
}

// WC-P5a (AD-7) — the `mcp_auth` Connect card. Reuses the hoisted 4-zone
// `ApprovalCard` frame but swaps Approve/Reject for Connect/Skip wired to the
// host `McpAuthPort`, NOT `onApprove`/`onReject`: a connector-auth gate resolves
// via OAuth (a host `mcp_auth_resolved` decision after the redirect returns, P5b)
// and a `mcp_discovery:` suggestion is never a persisted approval row, so a
// `/decision` POST would 404 (AD-7). Rendered in BOTH Studio and Focus (the
// connector-auth affordance is mode-agnostic). When no port is wired, or the
// payload carried no `server_id`, the actions render disabled — the gate stays
// visible but inert (never a crash, never a `/decision` fallback).
function renderMcpAuthConnectCard(
  approval: TcChatApproval,
  mcpAuthPort?: McpAuthPort,
): ReactNode {
  const serverId = approval.serverId;
  const actionable = mcpAuthPort !== undefined && serverId !== null;
  return (
    <div
      key={`mcp-auth-${approval.approvalId}`}
      data-testid={`tc-chat-mcp-auth-${approval.approvalId}`}
      data-approval-id={approval.approvalId}
      data-server-id={serverId ?? ""}
    >
      <ApprovalCard
        title={approval.title}
        reason={approval.reason}
        category={approval.category}
        params={[...approval.params]}
        reassurance={MCP_AUTH_REASSURANCE}
        actions={
          <>
            <button
              type="button"
              data-testid={`tc-chat-mcp-skip-${approval.approvalId}`}
              disabled={!actionable}
              onClick={() =>
                serverId !== null ? mcpAuthPort?.skipAuth(serverId) : undefined
              }
              style={approvalRejectButtonStyle}
            >
              Skip
            </button>
            <button
              type="button"
              data-testid={`tc-chat-mcp-connect-${approval.approvalId}`}
              disabled={!actionable}
              onClick={() =>
                serverId !== null ? mcpAuthPort?.beginAuth(serverId) : undefined
              }
              style={approvalApproveButtonStyle}
            >
              Connect
            </button>
          </>
        }
      />
    </div>
  );
}

function renderConfCard(
  approval: TcChatApproval,
  onApprove?: (approvalId: string) => void,
  onReject?: (approvalId: string) => void,
): ReactNode {
  return (
    <div
      key={`conf-${approval.approvalId}`}
      className="conf-card"
      role="group"
      aria-label={`Approval: ${approval.title}`}
      data-testid={`tc-chat-conf-card-${approval.approvalId}`}
      data-approval-id={approval.approvalId}
      style={confCardStyle}
    >
      <div className="conf-card__head" style={confHeadStyle}>
        {approval.title}
      </div>
      {approval.summary !== null ? (
        <p className="conf-card__summary" style={confSummaryStyle}>
          {approval.summary}
        </p>
      ) : null}
      <div className="conf-card__actions" style={confActionsStyle}>
        <button
          type="button"
          data-testid={`tc-chat-conf-reject-${approval.approvalId}`}
          onClick={() => onReject?.(approval.approvalId)}
          style={confRejectButtonStyle}
        >
          Reject
        </button>
        <button
          type="button"
          data-testid={`tc-chat-conf-approve-${approval.approvalId}`}
          onClick={() => onApprove?.(approval.approvalId)}
          style={confApproveButtonStyle}
        >
          Approve &amp; sign
        </button>
      </div>
      <p className="conf-card__foot" style={confFootStyle}>
        The agent paused here — it won&apos;t sign until you approve
      </p>
    </div>
  );
}

function renderApprovalReceipt(approval: TcChatApproval): ReactNode {
  return (
    <div
      key={`receipt-${approval.approvalId}`}
      data-testid={`tc-chat-approval-receipt-${approval.approvalId}`}
      data-decision={approval.decision ?? "approved"}
    >
      <ApprovalReceipt
        kind={approval.decision === "rejected" ? "rejected" : "approved"}
        title={approval.title}
      />
    </div>
  );
}

interface MessageListBodyProps {
  readonly state: LoadState;
  readonly messages: ReadonlyArray<TcChatMessage>;
  readonly fleets: readonly FleetProjection[];
  readonly toolCalls: readonly ToolCallEntry[];
  readonly markdownComponents?: MarkdownTextProps["components"];
}

function MessageListBody(props: MessageListBodyProps): ReactNode {
  const { state, messages, fleets, toolCalls, markdownComponents } = props;
  if (state.status === "loading" || state.status === "idle") {
    return (
      <div role="status" style={statusStyle} data-testid="tc-chat-loading">
        Loading messages…
      </div>
    );
  }
  if (state.status === "error") {
    return (
      <div role="alert" style={statusStyle} data-testid="tc-chat-error">
        Failed to load messages.
      </div>
    );
  }
  if (messages.length === 0 && fleets.length === 0 && toolCalls.length === 0) {
    return (
      <div role="status" style={statusStyle} data-testid="tc-chat-empty">
        No messages yet.
      </div>
    );
  }
  // Messages (GET) plus the two projected-off-the-run-stream card families —
  // fleet cards (PR-3.8) and tool-call cards (Workstream D) — are interleaved by
  // timestamp so each lands where it happened in the flow.
  const items = mergeStream(messages, fleets, toolCalls);
  return (
    <ul style={ulStyle}>
      {/* Scoped spinner keyframes — one style node per mount is enough. */}
      <style>{TOOL_SPINNER_CSS}</style>
      {items.map((item) => {
        if (item.kind === "fleet") {
          return renderFleetCard(item.fleet);
        }
        if (item.kind === "tool") {
          return renderToolCard(item.toolCall);
        }
        return renderMessage(item.message, markdownComponents);
      })}
    </ul>
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
      {(m.parts ?? []).map((part, idx) => {
        const status: MessagePartStatus = part.status ?? {
          type: "complete",
        };
        if (part.type === "reasoning") {
          return (
            <Reasoning
              key={idx}
              type="reasoning"
              text={part.text}
              status={status}
            />
          );
        }
        // User input stays literal (a typed `| pipe |` is not markdown);
        // agent/tool/system text routes through the citation-safe streaming
        // markdown path so conversational GFM tables render as real tables
        // with the incremental blinking cursor, never as half-parsed raw
        // pipes (FR-3.19).
        if (m.role === "user") {
          return (
            <PlainText key={idx} type="text" text={part.text} status={status} />
          );
        }
        return (
          <MarkdownText
            key={idx}
            type="text"
            text={part.text}
            status={status}
            components={markdownComponents}
          />
        );
      })}
    </li>
  );
}

// PR-3.8 — reuse the hoisted `SubagentFleetCard` (Phase 1D) with the projected
// fleet head + one `FleetSubagentRow` per child. The card + rows are pure
// presentation; the projection is the single source of truth (FR-3.17a).
function renderFleetCard(fleet: FleetProjection): ReactNode {
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
        elapsed={fleet.elapsed}
      >
        {fleet.children.map((child) => (
          <FleetSubagentRow
            key={child.task_id}
            view={subagentCardFromEntry(child)}
          />
        ))}
      </SubagentFleetCard>
    </li>
  );
}

// Workstream D — the compact inline tool-call card. Renders in the transcript
// flow at the point the tool ran: a mono tool name, a running spinner that
// resolves to ✓ (done) or ! (failed), and the args/result behind a lightweight
// `<details>` expand (blobs are truncated, never dumped whole). Pure
// presentation over the injected `projectToolCalls` entry — the projection is
// the single source of truth (FR-3.3). Rendered identically in Studio + Focus
// (the transcript is shared).
function renderToolCard(toolCall: ToolCallEntry): ReactNode {
  const running = toolCall.status === "running";
  const error = toolCall.status === "error";
  const hasDetails =
    toolCall.args !== undefined ||
    toolCall.result !== undefined ||
    toolCall.errorMessage !== undefined;
  const statusLabel = running ? "running…" : error ? "failed" : "done";
  return (
    <li
      key={`tool-${toolCall.id}`}
      style={toolItemStyle}
      data-testid={`tc-chat-tool-${toolCall.id}`}
      data-tool-status={toolCall.status}
    >
      <div
        style={toolCardStyle}
        role="group"
        aria-label={`Tool: ${toolCall.title}`}
      >
        <div style={toolHeadStyle}>
          <span style={toolMarkStyle(toolCall.status)} aria-hidden="true">
            {running ? (
              <span className="tc-tool-spinner" style={toolSpinnerStyle} />
            ) : error ? (
              "!"
            ) : (
              "✓"
            )}
          </span>
          <span style={toolNameStyle}>{toolCall.toolName}</span>
          <span style={toolStatusStyle}>{statusLabel}</span>
        </div>
        {toolCall.summary !== undefined ? (
          <p style={toolSummaryStyle}>{toolCall.summary}</p>
        ) : null}
        {hasDetails ? (
          <details style={toolDetailsStyle}>
            <summary style={toolDetailsSummaryStyle}>Details</summary>
            {toolCall.args !== undefined ? (
              <pre
                style={toolPreStyle}
                data-testid={`tc-chat-tool-${toolCall.id}-args`}
              >
                {formatBlob(toolCall.args)}
              </pre>
            ) : null}
            {toolCall.result !== undefined ? (
              <pre
                style={toolPreStyle}
                data-testid={`tc-chat-tool-${toolCall.id}-result`}
              >
                {formatBlob(toolCall.result)}
              </pre>
            ) : null}
            {toolCall.errorMessage !== undefined ? (
              <p style={toolErrorStyle}>{toolCall.errorMessage}</p>
            ) : null}
          </details>
        ) : null}
      </div>
    </li>
  );
}

// Keep blobs compact — pretty-print then hard-cap so a huge tool payload never
// blows out the transcript. Non-serialisable values degrade to a placeholder.
const TOOL_BLOB_CAP = 600;
function formatBlob(value: Record<string, unknown>): string {
  let text: string;
  try {
    text = JSON.stringify(value, null, 2);
  } catch {
    return "[unserialisable]";
  }
  if (text.length <= TOOL_BLOB_CAP) {
    return text;
  }
  return `${text.slice(0, TOOL_BLOB_CAP)}…`;
}

type StreamItem =
  | { readonly kind: "message"; readonly message: TcChatMessage }
  | { readonly kind: "fleet"; readonly fleet: FleetProjection }
  | { readonly kind: "tool"; readonly toolCall: ToolCallEntry };

/** A non-message card anchored to a timestamp, for the interleave pass. */
interface AnchoredItem {
  readonly at: number;
  readonly item: StreamItem;
}

/**
 * Interleave the projected card families (fleets, tool calls) into the message
 * stream WITHOUT reordering messages: messages keep their exact GET order (they
 * may lack timestamps), and each card slots in just before the first message
 * dated after its anchor. Any card with no earlier-dated message anchor falls
 * to the end. Fleets are pushed before tool calls, so cards sharing a timestamp
 * keep a stable fleet-then-tool order (ES sort is stable).
 */
function mergeStream(
  messages: ReadonlyArray<TcChatMessage>,
  fleets: readonly FleetProjection[],
  toolCalls: readonly ToolCallEntry[],
): readonly StreamItem[] {
  if (fleets.length === 0 && toolCalls.length === 0) {
    return messages.map((message) => ({ kind: "message", message }));
  }
  const anchored: AnchoredItem[] = [];
  for (const fleet of fleets) {
    anchored.push({ at: fleetAt(fleet), item: { kind: "fleet", fleet } });
  }
  for (const toolCall of toolCalls) {
    anchored.push({ at: toolAt(toolCall), item: { kind: "tool", toolCall } });
  }
  anchored.sort((a, b) => a.at - b.at);
  const out: StreamItem[] = [];
  let ai = 0;
  for (const message of messages) {
    const at =
      typeof message.created_at_ms === "number" ? message.created_at_ms : null;
    while (ai < anchored.length && at !== null && anchored[ai].at <= at) {
      out.push(anchored[ai].item);
      ai += 1;
    }
    out.push({ kind: "message", message });
  }
  while (ai < anchored.length) {
    out.push(anchored[ai].item);
    ai += 1;
  }
  return out;
}

function fleetAt(fleet: FleetProjection): number {
  return fleet.createdAtMs ?? Number.MAX_SAFE_INTEGER;
}

function toolAt(toolCall: ToolCallEntry): number {
  return toolCall.createdAtMs ?? Number.MAX_SAFE_INTEGER;
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
const chatContainerStyle = (): CSSProperties => ({
  display: "flex",
  flexDirection: "column",
  height: "100%",
  background: "transparent",
  padding: 12,
  gap: 10,
  color: PALETTE.textHi,
  // v3 anchors chat body text at 12.5–13px (copilot.css `body{font-size:13px}`,
  // `.msg{font-size:12.5px}`). Without this the message text inherited the UA
  // 16px default — the single largest text in the cockpit ("too big").
  fontSize: 13,
  fontFamily: "var(--font-sans)",
});

const messageListStyle = (ghost: boolean): CSSProperties => ({
  flex: 1,
  overflowY: "auto",
  display: "flex",
  flexDirection: "column",
  gap: 8,
  padding: 8,
  opacity: ghost ? 0.55 : 1,
  pointerEvents: ghost ? "none" : "auto",
});

const ghostBannerStyle: CSSProperties = {
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
  flexShrink: 0,
};

const statusStyle: CSSProperties = {
  color: PALETTE.textLo,
  fontSize: "var(--font-size-xs)",
  padding: 12,
};

const ulStyle: CSSProperties = {
  listStyle: "none",
  margin: 0,
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
});

// PR-3.8 — the fleet card carries its own chrome (`.aui-fleet-card`), so the
// list item is a bare positioning slot.
const fleetItemStyle: CSSProperties = {
  listStyle: "none",
  padding: 0,
};

// Workstream D — the inline tool-call card. Quiet v3 aesthetic: small, muted,
// a mono tool label; design-system tokens only (no hardcoded hex). Scoped
// spinner keyframes are injected once per mount (design-system owns no spinner
// primitive) and gated off under reduce-motion.
const TOOL_SPINNER_CSS = `
@keyframes tc-tool-spin { to { transform: rotate(360deg); } }
.tc-tool-spinner { animation: tc-tool-spin 0.7s linear infinite; }
[data-reduce-motion="1"] .tc-tool-spinner,
[data-reduce-motion="always"] .tc-tool-spinner { animation: none; }
@media (prefers-reduced-motion: reduce) { .tc-tool-spinner { animation: none; } }
`;

const toolItemStyle: CSSProperties = {
  listStyle: "none",
  padding: 0,
};

const toolCardStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 5,
  padding: "7px 10px",
  borderRadius: 8,
  background: "var(--color-surface-muted)",
  border: `1px solid ${PALETTE.cardBorder}`,
  color: PALETTE.textHi,
};

const toolHeadStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  minWidth: 0,
};

const toolNameStyle: CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-2xs)",
  letterSpacing: "var(--tracking-caption)",
  color: PALETTE.textHi,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const toolStatusStyle: CSSProperties = {
  marginLeft: "auto",
  flexShrink: 0,
  fontSize: "var(--font-size-2xs)",
  color: PALETTE.textLo,
};

const toolMarkStyle = (status: ToolCallEntry["status"]): CSSProperties => ({
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  width: 14,
  height: 14,
  flexShrink: 0,
  fontSize: "var(--font-size-2xs)",
  fontWeight: 700,
  lineHeight: 1,
  color:
    status === "error"
      ? "var(--color-danger)"
      : status === "complete"
        ? "var(--color-success)"
        : PALETTE.textLo,
});

const toolSpinnerStyle: CSSProperties = {
  width: 10,
  height: 10,
  borderRadius: "50%",
  border: "1.5px solid var(--color-border-strong)",
  borderTopColor: "var(--color-accent)",
  boxSizing: "border-box",
};

const toolSummaryStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-2xs)",
  lineHeight: 1.5,
  color: PALETTE.textLo,
};

const toolDetailsStyle: CSSProperties = {
  margin: 0,
};

const toolDetailsSummaryStyle: CSSProperties = {
  cursor: "pointer",
  fontSize: "var(--font-size-2xs)",
  color: PALETTE.textLo,
  userSelect: "none",
};

const toolPreStyle: CSSProperties = {
  margin: "6px 0 0",
  padding: "6px 8px",
  borderRadius: 6,
  background: "var(--color-surface)",
  border: `1px solid ${PALETTE.cardBorder}`,
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-2xs)",
  lineHeight: 1.45,
  color: PALETTE.textLo,
  overflowX: "auto",
  maxHeight: 180,
  overflowY: "auto",
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
};

const toolErrorStyle: CSSProperties = {
  margin: "6px 0 0",
  fontSize: "var(--font-size-2xs)",
  lineHeight: 1.45,
  color: "var(--color-danger)",
};

// Focus mode: the SAME transcript + composer as Studio, in a centered reading
// column (v3 `.fx-col` max-width 730). Flush pane; flex column so the transcript
// scrolls and the composer pins to the bottom.
const focusContainerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  height: "100%",
  minHeight: 0,
  width: "100%",
  maxWidth: 760,
  margin: "0 auto",
  background: "transparent",
  padding: 12,
  color: PALETTE.textHi,
  fontSize: 13,
  fontFamily: "var(--font-sans)",
};

// PR-3.10 — in-chat approvals (design-system tokens only; sky accent, jade
// success, ember danger — no lime, no hardcoded hex).

const approvalsWrapStyle: CSSProperties = {
  flexShrink: 0,
  display: "flex",
  flexDirection: "column",
  gap: 8,
  padding: "0 8px",
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

const confCardsWrapStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
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
