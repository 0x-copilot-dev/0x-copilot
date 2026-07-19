import {
  useEffect,
  useState,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

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

export interface TcChatProps {
  readonly conversationId: string;
  readonly mode: TcChatMode;
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
const EMPTY_APPROVALS: readonly TcChatApproval[] = [];
const APPROVAL_REASSURANCE =
  "You're always asked before Copilot acts outside this chat.";

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
    onSend,
    portalTarget,
    markdownComponents,
    fleets = EMPTY_FLEETS,
    approvals = EMPTY_APPROVALS,
    onApprove,
    onReject,
    renderComposer,
  } = props;
  const transport = useTransport();
  const scrub = useSwimlaneScrub();
  const [state, setState] = useState<LoadState>({ status: "idle" });

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    transport
      .request<ApiChatMessagesResponse>({
        method: "GET",
        path: `/v1/agent/conversations/${conversationId}/messages`,
      })
      .then((res) => {
        if (cancelled) {
          return;
        }
        setState({
          status: "ready",
          messages: (res.messages ?? []).map(toTcChatMessage),
        });
      })
      .catch(() => {
        if (cancelled) {
          return;
        }
        setState({ status: "error" });
      });
    return () => {
      cancelled = true;
    };
  }, [conversationId, transport]);

  // PR-3.10 — approvals are HIDDEN while scrubbed off-now (you cannot approve a
  // past state). The host also drops them from `approvals` when scrubbed, but
  // guarding on the scrub cursor here keeps standalone usage correct too.
  const scrubbedOffNow = scrub.scrubbedTo !== "now";
  const visibleApprovals = scrubbedOffNow ? EMPTY_APPROVALS : approvals;

  if (mode === "focus") {
    // PR-3.10 (FR-3.13/FR-3.22) — Focus collapses to Chat-only; a pending
    // approval surfaces as a `.conf-card` confirmation card (a resolved one as
    // its receipt) above the focus tabs.
    return (
      <div data-testid="tc-chat" data-mode="focus" style={focusContainerStyle}>
        {visibleApprovals.length > 0 ? (
          <div data-testid="tc-chat-conf-cards" style={confCardsWrapStyle}>
            {visibleApprovals.map((approval) =>
              approval.resolved
                ? renderApprovalReceipt(approval)
                : renderConfCard(approval, onApprove, onReject),
            )}
          </div>
        ) : null}
        <FocusTabs />
      </div>
    );
  }

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

  return (
    <div
      data-testid="tc-chat"
      data-mode={mode}
      data-ghost={ghost ? "true" : "false"}
      style={chatContainerStyle()}
      aria-live="polite"
    >
      {ghost && ghostLabel !== null ? (
        <div
          role="status"
          data-testid="tc-chat-ghost-banner"
          style={ghostBannerStyle}
        >
          Viewing {ghostLabel}
        </div>
      ) : null}
      <div data-testid="tc-chat-messages" style={messageListStyle(ghost)}>
        <MessageListBody
          state={state}
          messages={filteredMessages}
          fleets={filteredFleets}
          markdownComponents={markdownComponents}
        />
      </div>
      {/* PR-3.10 (FR-3.22) — in-chat approvals sit between the transcript and
          the composer: pending ones render the 4-zone ApprovalCard, resolved
          ones their receipt. Outside the ghost-dimmed message list so they stay
          interactive. */}
      {visibleApprovals.length > 0 ? (
        <div data-testid="tc-chat-approvals" style={approvalsWrapStyle}>
          {visibleApprovals.map((approval) =>
            approval.resolved
              ? renderApprovalReceipt(approval)
              : renderStudioApprovalCard(approval, onApprove, onReject),
          )}
        </div>
      ) : null}
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
  readonly markdownComponents?: MarkdownTextProps["components"];
}

function MessageListBody(props: MessageListBodyProps): ReactNode {
  const { state, messages, fleets, markdownComponents } = props;
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
  if (messages.length === 0 && fleets.length === 0) {
    return (
      <div role="status" style={statusStyle} data-testid="tc-chat-empty">
        No messages yet.
      </div>
    );
  }
  // PR-3.8 — messages (GET) and fleet cards (projected off the run stream) are
  // interleaved by timestamp so a dispatched batch lands where it happened.
  const items = mergeStream(messages, fleets);
  return (
    <ul style={ulStyle}>
      {items.map((item) =>
        item.kind === "fleet"
          ? renderFleetCard(item.fleet)
          : renderMessage(item.message, markdownComponents),
      )}
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

type StreamItem =
  | { readonly kind: "message"; readonly message: TcChatMessage }
  | { readonly kind: "fleet"; readonly fleet: FleetProjection };

/**
 * Interleave fleet cards into the message stream WITHOUT reordering messages:
 * messages keep their exact GET order (they may lack timestamps), and each
 * fleet slots in just before the first message dated after its dispatch. Any
 * fleet with no earlier-dated message anchor falls to the end.
 */
function mergeStream(
  messages: ReadonlyArray<TcChatMessage>,
  fleets: readonly FleetProjection[],
): readonly StreamItem[] {
  if (fleets.length === 0) {
    return messages.map((message) => ({ kind: "message", message }));
  }
  const pending = [...fleets].sort((a, b) => fleetAt(a) - fleetAt(b));
  const out: StreamItem[] = [];
  let fi = 0;
  for (const message of messages) {
    const at =
      typeof message.created_at_ms === "number" ? message.created_at_ms : null;
    while (fi < pending.length && at !== null && fleetAt(pending[fi]) <= at) {
      out.push({ kind: "fleet", fleet: pending[fi] });
      fi += 1;
    }
    out.push({ kind: "message", message });
  }
  while (fi < pending.length) {
    out.push({ kind: "fleet", fleet: pending[fi] });
    fi += 1;
  }
  return out;
}

function fleetAt(fleet: FleetProjection): number {
  return fleet.createdAtMs ?? Number.MAX_SAFE_INTEGER;
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

function FocusTabs(): ReactElement {
  const [tab, setTab] = useState<"activity" | "approvals">("activity");
  return (
    <div style={focusInnerStyle}>
      <div
        role="tablist"
        style={tabStripStyle}
        data-testid="tc-chat-focus-tabs"
      >
        <button
          type="button"
          role="tab"
          aria-selected={tab === "activity"}
          onClick={() => setTab("activity")}
          style={tabButtonStyle(tab === "activity")}
          data-testid="tc-chat-tab-activity"
        >
          Activity
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "approvals"}
          onClick={() => setTab("approvals")}
          style={tabButtonStyle(tab === "approvals")}
          data-testid="tc-chat-tab-approvals"
        >
          Approvals
        </button>
      </div>
      <div
        role="tabpanel"
        style={tabPanelStyle}
        data-testid="tc-chat-focus-panel"
      >
        {tab === "activity" ? "No recent activity." : "No pending approvals."}
      </div>
    </div>
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

const PALETTE = {
  cardBg: "#101213",
  cardBorder: "#1f2225",
  textHi: "#f4f5f6",
  textLo: "#9aa0a6",
  ghostBg: "#1a1d20",
  ghostBorder: "#3a3e44",
  ghostAccent: "var(--color-accent)",
} as const;

const chatContainerStyle = (): CSSProperties => ({
  display: "flex",
  flexDirection: "column",
  height: "100%",
  background: PALETTE.cardBg,
  border: `1px solid ${PALETTE.cardBorder}`,
  borderRadius: 12,
  padding: 12,
  gap: 10,
  color: PALETTE.textHi,
  fontFamily:
    "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
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
  background: role === "user" ? "#1f2225" : "transparent",
  border: role === "user" ? `1px solid ${PALETTE.cardBorder}` : "none",
  borderRadius: 8,
  padding: role === "user" ? "8px 12px" : "0",
  color: PALETTE.textHi,
});

// PR-3.8 — the fleet card carries its own chrome (`.aui-fleet-card`), so the
// list item is a bare positioning slot.
const fleetItemStyle: CSSProperties = {
  listStyle: "none",
  padding: 0,
};

const focusContainerStyle: CSSProperties = {
  height: "100%",
  background: PALETTE.cardBg,
  border: `1px solid ${PALETTE.cardBorder}`,
  borderRadius: 12,
  padding: 12,
  color: PALETTE.textHi,
  fontFamily:
    "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
};

const focusInnerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  height: "100%",
};

const tabStripStyle: CSSProperties = {
  display: "flex",
  gap: 4,
  borderBottom: `1px solid ${PALETTE.cardBorder}`,
};

const tabButtonStyle = (selected: boolean): CSSProperties => ({
  background: "transparent",
  border: "none",
  color: selected ? PALETTE.textHi : PALETTE.textLo,
  padding: "8px 12px",
  fontSize: "var(--font-size-sm)",
  borderBottom: selected
    ? "2px solid var(--color-accent)"
    : "2px solid transparent",
  cursor: "pointer",
});

const tabPanelStyle: CSSProperties = {
  flex: 1,
  color: PALETTE.textLo,
  fontSize: "var(--font-size-sm)",
  padding: 12,
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
