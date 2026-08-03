// PR 3.2 — Approvals tab body for the right-rail workspace pane.
// PR-1.7 — hoisted into @0x-copilot/chat-surface with the pane it serves.
//
// Pure projection over the existing thread items via
// `useApprovalsQueue` (PR 3.2, host-owned). Clicking a row jumps to the
// inline <ApprovalTool> card in the thread (Atlas's "approvals as
// content" rule) via the host-supplied `onJumpToApproval`. PR 3.3 will
// split `pending` into pending-on-me vs. pending-on-others; until then
// this surfaces a single pending list plus a small recent-resolutions
// section.

import type { CSSProperties, ReactElement } from "react";

import type { ApprovalsQueueItem, ApprovalsQueueProjection } from "./types";

export interface ApprovalsTabProps {
  queue: ApprovalsQueueProjection;
  onJumpToApproval?: (approvalId: string, messageId: string) => void;
  /** Approve a pending row in place. Omitted ⇒ the row shows its kind instead. */
  onApprove?: (approvalId: string) => void;
  /** Decline a pending row in place. */
  onReject?: (approvalId: string) => void;
  /**
   * Scope heading, e.g. "This conversation". Supply it whenever the panel also
   * shows a cross-run queue: this tab's empty state is conversation-scoped and
   * correct, but unlabelled it reads as speaking for the whole panel — which is
   * how cards ended up rendering directly above "No pending approvals in this
   * conversation."
   */
  groupLabel?: string;
}

export function ApprovalsTab({
  queue,
  onJumpToApproval,
  onApprove,
  onReject,
  groupLabel,
}: ApprovalsTabProps): ReactElement {
  const { pending, recent } = queue;
  const heading =
    groupLabel === undefined ? null : (
      <p className="ui-eyebrow" data-testid="workspace-approvals-group">
        {groupLabel}
      </p>
    );
  if (pending.length === 0 && recent.length === 0) {
    return (
      <div
        className="atlas-workspace-tab atlas-workspace-tab--empty"
        data-testid="workspace-approvals-tab-empty"
      >
        {heading}
        <p>
          {groupLabel === undefined
            ? "No pending approvals in this conversation."
            : "Nothing waiting here."}
        </p>
      </div>
    );
  }

  return (
    <div className="atlas-workspace-tab" data-testid="workspace-approvals-tab">
      {heading}
      {pending.length > 0 ? (
        <Section
          title="Pending"
          description={
            pending.length === 1
              ? "Copilot is waiting on you."
              : `Copilot is waiting on ${pending.length} decisions.`
          }
          items={pending}
          onJumpToApproval={onJumpToApproval}
          onApprove={onApprove}
          onReject={onReject}
        />
      ) : null}
      {recent.length > 0 ? (
        <Section
          title="Recent"
          description="Resolved within the last hour."
          items={recent}
          onJumpToApproval={onJumpToApproval}
        />
      ) : null}
    </div>
  );
}

function Section({
  title,
  description,
  items,
  onJumpToApproval,
  onApprove,
  onReject,
}: {
  title: string;
  description: string;
  items: readonly ApprovalsQueueItem[];
  onJumpToApproval?: (approvalId: string, messageId: string) => void;
  onApprove?: (approvalId: string) => void;
  onReject?: (approvalId: string) => void;
}): ReactElement {
  return (
    <section style={cardStyle} aria-label={`${title} approvals`}>
      <div style={headerStyle}>
        <span
          style={eyebrowStyle}
        >{`${title.toUpperCase()} · ${items.length}`}</span>
        <span style={noteStyle}>{description}</span>
      </div>
      <div role="list">
        {items.map((item) => (
          <div
            key={`${item.approvalId}-${item.messageId}`}
            role="listitem"
            data-approval-id={item.approvalId}
            data-resolved={item.resolved ? "true" : undefined}
            style={rowStyle}
          >
            <span aria-hidden="true" style={dotStyle(item.resolved)} />
            {/* The title area jumps to the card in the thread; the actions
                decide it here. Two buttons rather than one clickable row,
                because a <button> inside a <button> is invalid markup and the
                browser drops the inner one — the decision would have looked
                clickable and done nothing. */}
            <button
              type="button"
              style={rowMainStyle}
              onClick={() =>
                onJumpToApproval?.(item.approvalId, item.messageId)
              }
              aria-label={`Open approval "${item.title}" in thread`}
            >
              <span style={rowTitleStyle}>{item.title}</span>
              {item.summary !== null && item.summary.length > 0 ? (
                <span style={rowSubtitleStyle}>{item.summary}</span>
              ) : null}
            </button>
            {item.target !== null && item.target.length > 0 ? (
              <span style={targetStyle}>{item.target}</span>
            ) : null}
            {!item.resolved && onApprove !== undefined ? (
              <span style={actionsStyle}>
                <button
                  type="button"
                  className="ui-button ui-button--sm ui-button--primary"
                  data-testid="approvals-row-approve"
                  onClick={() => onApprove(item.approvalId)}
                >
                  Approve
                </button>
                <button
                  type="button"
                  className="ui-button ui-button--sm"
                  data-testid="approvals-row-decline"
                  onClick={() => onReject?.(item.approvalId)}
                >
                  Decline
                </button>
              </span>
            ) : (
              <span style={metaStyle}>
                {item.resolved && item.resolvedAt !== null
                  ? formatRelative(item.resolvedAt)
                  : kindLabel(item.approvalKind)}
              </span>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

// Geometry lifted from `CompactSourceList` — the Sources rail's dense row, which
// is the house list recipe: a bordered card, a mono eyebrow carrying the count,
// and 8x11 rows separated by hairlines.
//
// What was here instead: `atlas-workspace-approvals-*` class names plus a
// `<Card>` per row inside a `<ul>`. Two things broke on desktop. Those `atlas-*`
// rules live in the WEB app's stylesheet and never load in the Electron host, so
// every row rendered as unstyled markup — a list bullet beside a box. And
// `<Card>` is the PANEL recipe (`--radius-xl`, `--space-xl` padding, shadow), so
// each row carried panel chrome meant for a whole surface.
//
// Styling with inline token styles rather than host class names is what makes
// the Sources tab render identically in both hosts; this now matches it.
const cardStyle: CSSProperties = {
  background: "var(--color-surface)",
  border: "1px solid var(--color-border)",
  borderRadius: 10,
  color: "var(--color-text)",
  overflow: "hidden",
};

const headerStyle: CSSProperties = {
  alignItems: "center",
  borderBottom: "1px solid var(--color-border)",
  display: "flex",
  gap: 8,
  minWidth: 0,
  padding: "8px 11px",
};

const eyebrowStyle: CSSProperties = {
  color: "var(--color-text-subtle)",
  fontFamily: "var(--font-mono)",
  fontSize: 9,
  letterSpacing: "0.1em",
  lineHeight: "13.5px",
};

const noteStyle: CSSProperties = {
  color: "var(--color-text-muted)",
  fontFamily: "var(--font-mono)",
  fontSize: 9,
  lineHeight: 1.3,
  marginLeft: "auto",
  textAlign: "right",
};

const rowStyle: CSSProperties = {
  alignItems: "center",
  borderBottom: "1px solid var(--color-border)",
  display: "flex",
  gap: 9,
  minWidth: 0,
  padding: "8px 11px",
  width: "100%",
};

const rowMainStyle: CSSProperties = {
  background: "transparent",
  border: "none",
  color: "inherit",
  cursor: "pointer",
  display: "grid",
  gap: 1,
  minWidth: 0,
  padding: 0,
  textAlign: "left",
};

const actionsStyle: CSSProperties = {
  display: "flex",
  flex: "0 0 auto",
  gap: 4,
  marginLeft: "auto",
};

const rowTitleStyle: CSSProperties = {
  fontSize: "var(--font-size-sm)",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const rowSubtitleStyle: CSSProperties = {
  color: "var(--color-text-muted)",
  fontSize: "var(--font-size-2xs)",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const targetStyle: CSSProperties = {
  color: "var(--color-text-muted)",
  flex: "0 0 auto",
  fontFamily: "var(--font-mono)",
  fontSize: 9,
};

const metaStyle: CSSProperties = {
  color: "var(--color-text-subtle)",
  flex: "0 0 auto",
  fontFamily: "var(--font-mono)",
  fontSize: 9,
  marginLeft: "auto",
};

function dotStyle(resolved: boolean): CSSProperties {
  return {
    background: resolved
      ? "var(--color-success, #57c785)"
      : "var(--color-accent, #5fb2ec)",
    borderRadius: "50%",
    flex: "0 0 auto",
    height: 6,
    width: 6,
  };
}

function kindLabel(kind: ApprovalsQueueItem["approvalKind"]): string {
  switch (kind) {
    case "mcp_auth":
      return "Connector";
    case "mcp_tool":
      return "Connector tool";
    case "ask_a_question":
      return "Question";
    case "tool_action":
      return "Action";
    default:
      return "Approval";
  }
}

function formatRelative(iso: string): string {
  const ms = Date.parse(iso);
  if (!Number.isFinite(ms)) {
    return iso;
  }
  const diff = Date.now() - ms;
  if (diff < 60_000) {
    return "just now";
  }
  if (diff < 60 * 60_000) {
    const minutes = Math.floor(diff / 60_000);
    return `${minutes}m ago`;
  }
  return new Date(ms).toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
  });
}
