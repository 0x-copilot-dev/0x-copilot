// <MemoryProposalCard /> — full proposal row for the `/memory/proposals`
// route. The toast (`<MemoryProposalToast />`) is the short, top-of-
// screen variant; this is the persistent permanent surface listed at
// `/memory/proposals` (sub-PRD §9.2).
//
// Invariants:
//   - Pure presentation. Accept / Reject / Snooze each lift through a
//     callback prop. The host calls the proposal-decision endpoints.
//   - SP-1 primitives — body excerpt is plain text (markdown body is
//     rendered through `<PagePreview>` when the host opts in by passing
//     `renderBody="markdown"`). Source ref renders through `<ItemLink>`
//     (cross-audit §1.1).
//   - `formatRelativeTime` from `../../util/time` (cross-audit §3.4).
//   - Wire types from `@0x-copilot/api-types/memory` only.

import { type CSSProperties, type ReactElement } from "react";

import type { MemoryProposal } from "@0x-copilot/api-types";

import { ItemLink } from "../../refs/ItemLink";
import { itemKindNoun } from "../../refs/itemKindNoun";
import { StatusPill, type StatusTone } from "../../shell/StatusPill";
import { formatRelativeTime } from "../../util/time";

import { PagePreview } from "../library/preview/PagePreview";

// ===========================================================================
// Public props
// ===========================================================================

export interface MemoryProposalCardProps {
  readonly proposal: MemoryProposal;
  readonly onAccept?: (id: MemoryProposal["id"]) => void;
  readonly onReject?: (id: MemoryProposal["id"]) => void;
  readonly onSnooze?: (id: MemoryProposal["id"]) => void;

  /**
   * Body rendering mode. `"excerpt"` (default) renders a plain-text
   * 160-char clamp; `"markdown"` renders the full proposed body via
   * the shared Streamdown-based `<PagePreview>`. The accept flow is
   * usually decided from the excerpt; the full markdown is opt-in.
   */
  readonly renderBody?: "excerpt" | "markdown";

  /** Reference instant — test seam for relative-time. */
  readonly now?: number;
}

// ===========================================================================
// Implementation
// ===========================================================================

const STATUS_TONE: Readonly<Record<MemoryProposal["status"], StatusTone>> = {
  pending: "info",
  accepted: "ok",
  rejected: "error",
  snoozed: "muted",
};

const STATUS_LABEL: Readonly<Record<MemoryProposal["status"], string>> = {
  pending: "Pending",
  accepted: "Accepted",
  rejected: "Rejected",
  snoozed: "Snoozed",
};

const KIND_LABEL: Readonly<Record<MemoryProposal["proposed_kind"], string>> = {
  skill: "Skill",
  fact: "Fact",
  preference: "Preference",
};

const EXCERPT_MAX = 240;

function makeExcerpt(body: string): string {
  const stripped = body
    .replace(/```[\s\S]*?```/g, "")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\*([^*]+)\*/g, "$1")
    .replace(/_([^_]+)_/g, "$1")
    .replace(/!\[[^\]]*\]\([^)]+\)/g, "")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
  if (stripped.length <= EXCERPT_MAX) return stripped;
  return `${stripped.slice(0, EXCERPT_MAX - 1).trimEnd()}…`;
}

export function MemoryProposalCard({
  proposal,
  onAccept,
  onReject,
  onSnooze,
  renderBody = "excerpt",
  now,
}: MemoryProposalCardProps): ReactElement {
  const reference = now ?? Date.now();
  const terminal = proposal.status !== "pending";

  return (
    <article
      aria-label={`Memory proposal: ${proposal.proposed_title}`}
      data-testid="memory-proposal-card"
      data-proposal-id={proposal.id}
      data-status={proposal.status}
      style={cardStyle}
    >
      <header style={headerStyle}>
        <div style={titleBlockStyle}>
          <h3 style={titleStyle}>{proposal.proposed_title}</h3>
          <div style={chipRowStyle}>
            <StatusPill
              status="info"
              label={KIND_LABEL[proposal.proposed_kind]}
            />
            <StatusPill
              status={STATUS_TONE[proposal.status]}
              label={STATUS_LABEL[proposal.status]}
            />
            <span
              style={whenStyle}
              data-testid="memory-proposal-card-proposed-at"
            >
              proposed {formatRelativeTime(proposal.proposed_at, reference)}
            </span>
          </div>
        </div>
        <div style={sourceCellStyle} data-testid="memory-proposal-card-source">
          <span style={sourceLabelStyle}>From</span>
          <ItemLink
            ref={proposal.source}
            label={itemKindNoun(proposal.source.kind)}
          />
        </div>
      </header>

      <div style={bodyStyle} data-testid="memory-proposal-card-body">
        {renderBody === "markdown" ? (
          <PagePreview markdown={proposal.proposed_body} />
        ) : (
          <p style={excerptStyle}>{makeExcerpt(proposal.proposed_body)}</p>
        )}
      </div>

      {!terminal ? (
        <footer style={actionRowStyle}>
          {onAccept !== undefined ? (
            <button
              type="button"
              onClick={() => onAccept(proposal.id)}
              style={primaryButtonStyle}
              data-testid="memory-proposal-card-accept"
              aria-label={`Accept memory: ${proposal.proposed_title}`}
            >
              Accept
            </button>
          ) : null}
          {onReject !== undefined ? (
            <button
              type="button"
              onClick={() => onReject(proposal.id)}
              style={secondaryButtonStyle}
              data-testid="memory-proposal-card-reject"
              aria-label={`Reject memory: ${proposal.proposed_title}`}
            >
              Reject
            </button>
          ) : null}
          {onSnooze !== undefined ? (
            <button
              type="button"
              onClick={() => onSnooze(proposal.id)}
              style={secondaryButtonStyle}
              data-testid="memory-proposal-card-snooze"
              aria-label={`Snooze memory: ${proposal.proposed_title}`}
            >
              Snooze
            </button>
          ) : null}
        </footer>
      ) : (
        <footer
          style={terminalFooterStyle}
          data-testid="memory-proposal-card-terminal"
        >
          {proposal.decided_at !== null ? (
            <span>
              {STATUS_LABEL[proposal.status]}{" "}
              {formatRelativeTime(proposal.decided_at, reference)}
            </span>
          ) : (
            <span>{STATUS_LABEL[proposal.status]}</span>
          )}
        </footer>
      )}
    </article>
  );
}

// ===========================================================================
// Styles
// ===========================================================================

const cardStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 10,
  padding: 14,
  borderRadius: "var(--radius-md, 12px)",
  border: "1px solid var(--color-border, #232325)",
  background: "var(--color-bg-elevated, #161617)",
  color: "var(--color-text, #ededee)",
};

const headerStyle: CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  justifyContent: "space-between",
  gap: 12,
};

const titleBlockStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  minWidth: 0,
};

const titleStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-md, 14px)",
  fontWeight: 600,
};

const chipRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  flexWrap: "wrap",
};

const whenStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-subtle, #7e7e84)",
};

const sourceCellStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  flexShrink: 0,
};

const sourceLabelStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-subtle, #7e7e84)",
};

const bodyStyle: CSSProperties = {
  fontSize: "var(--font-size-sm, 13px)",
  color: "var(--color-text-muted, #b4b4b8)",
  lineHeight: 1.5,
};

const excerptStyle: CSSProperties = {
  margin: 0,
};

const actionRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
};

const primaryButtonStyle: CSSProperties = {
  height: 28,
  padding: "0 12px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-accent, #d97757)",
  background: "var(--color-accent, #d97757)",
  color: "var(--color-accent-contrast, #1a0f0a)",
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: 600,
  cursor: "pointer",
};

const secondaryButtonStyle: CSSProperties = {
  height: 28,
  padding: "0 12px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border, #232325)",
  background: "transparent",
  color: "var(--color-text-muted, #b4b4b8)",
  fontSize: "var(--font-size-xs, 12px)",
  cursor: "pointer",
};

const terminalFooterStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-subtle, #7e7e84)",
};
