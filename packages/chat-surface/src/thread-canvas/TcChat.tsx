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
import { useSwimlaneScrub } from "./SwimlaneScrubContext";

export type TcChatMode = "studio" | "focus";

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
}

const EMPTY_FLEETS: readonly FleetProjection[] = [];

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
  } = props;
  const transport = useTransport();
  const scrub = useSwimlaneScrub();
  const [state, setState] = useState<LoadState>({ status: "idle" });

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    transport
      .request<TcChatMessagesResponse>({
        method: "GET",
        path: `/v1/conversations/${conversationId}/messages`,
      })
      .then((res) => {
        if (cancelled) {
          return;
        }
        setState({ status: "ready", messages: res.messages ?? [] });
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

  if (mode === "focus") {
    return (
      <div data-testid="tc-chat" data-mode="focus" style={focusContainerStyle}>
        <FocusTabs />
      </div>
    );
  }

  const ghost = scrub.scrubbedTo !== "now";
  const ghostLabel =
    typeof scrub.scrubbedTo === "number"
      ? formatGhostTime(scrub.scrubbedTo)
      : null;

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
      <div style={composerSlotStyle}>
        <Composer
          onSend={(text) => onSend?.(text)}
          disabled={ghost}
          placeholder={
            ghost ? "Snap to now to send a message" : "Send a message…"
          }
          portalTarget={portalTarget}
        />
      </div>
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
      {m.parts.map((part, idx) => {
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
