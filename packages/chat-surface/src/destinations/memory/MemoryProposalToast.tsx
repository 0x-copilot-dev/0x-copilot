// <MemoryProposalToast /> + <MemoryProposalToastStack /> — site-wide
// toast that lifts pending Memory proposals.
//
// Source:
//   docs/atlas-new-design/destinations/team-memory-cmdk-prd.md §7.2:
//     "MemoryProposalToast.tsx — site-wide toast that lifts pending
//     proposals; reuse Inbox's notification component."
//   §9.2 "Memory proposals → toast (auto-dismiss after 8s) + permanent
//     in `/memory/proposals`."
//
// Invariants:
//   - Pure presentation. Accept / Reject / Snooze each lift through a
//     callback. The host calls the corresponding endpoint
//     (`POST /v1/memory/proposals/{id}/accept|reject|snooze`).
//   - SP-1 primitives. The body excerpt renders as plain text — we
//     deliberately do NOT pull in the markdown renderer here: the toast
//     is a notification, not a viewer, and Streamdown's vertical
//     rhythm is wrong at toast scale (cross-audit §1.6 — one renderer,
//     used where appropriate).
//   - Stack behaviour: at most `maxVisible` (default 3) toasts visible
//     vertically. Anything beyond collapses into a single "+N more"
//     chip that, on click, lifts an `onExpandStack` callback. The host
//     is free to either show all toasts inline or navigate to
//     `/memory/proposals` — we don't dictate.
//   - Auto-dismiss is host-driven: when the host wants 8s auto-dismiss
//     (§9.2), it removes the proposal from `proposals` after 8s. We
//     don't run timers here so the component stays deterministic in
//     tests.

import { type CSSProperties, type ReactElement } from "react";

import type { MemoryProposal } from "@0x-copilot/api-types";

import { ItemLink } from "../../refs/ItemLink";
import { StatusPill } from "../../shell/StatusPill";

// ===========================================================================
// One toast row
// ===========================================================================

export interface MemoryProposalToastProps {
  readonly proposal: MemoryProposal;
  readonly onAccept: (id: MemoryProposal["id"]) => void;
  readonly onReject: (id: MemoryProposal["id"]) => void;
  readonly onSnooze: (id: MemoryProposal["id"]) => void;
}

export function MemoryProposalToast({
  proposal,
  onAccept,
  onReject,
  onSnooze,
}: MemoryProposalToastProps): ReactElement {
  const excerpt = makeExcerpt(proposal.proposed_body);

  return (
    <div
      role="status"
      aria-live="polite"
      aria-label={`Memory proposal: ${proposal.proposed_title}`}
      data-testid="memory-proposal-toast"
      data-proposal-id={proposal.id}
      style={toastStyle}
    >
      <div style={headerStyle}>
        <StatusPill status="info" label={kindLabel(proposal.proposed_kind)} />
        <span style={titleStyle} data-testid="memory-proposal-toast-title">
          {proposal.proposed_title}
        </span>
      </div>
      {excerpt.length > 0 ? (
        <p style={excerptStyle} data-testid="memory-proposal-toast-excerpt">
          {excerpt}
        </p>
      ) : null}
      <div style={actionRowStyle}>
        <button
          type="button"
          onClick={() => onAccept(proposal.id)}
          style={primaryButtonStyle}
          data-testid="memory-proposal-toast-accept"
          aria-label={`Accept memory: ${proposal.proposed_title}`}
        >
          Accept
        </button>
        <button
          type="button"
          onClick={() => onReject(proposal.id)}
          style={secondaryButtonStyle}
          data-testid="memory-proposal-toast-reject"
          aria-label={`Reject memory: ${proposal.proposed_title}`}
        >
          Reject
        </button>
        <button
          type="button"
          onClick={() => onSnooze(proposal.id)}
          style={secondaryButtonStyle}
          data-testid="memory-proposal-toast-snooze"
          aria-label={`Snooze memory: ${proposal.proposed_title}`}
        >
          Snooze
        </button>
        <span style={sourceStyle} data-testid="memory-proposal-toast-source">
          <ItemLink ref={proposal.source} />
        </span>
      </div>
    </div>
  );
}

// ===========================================================================
// Stack — multiple toasts vertically, with collapse at >maxVisible.
// ===========================================================================

export interface MemoryProposalToastStackProps {
  readonly proposals: ReadonlyArray<MemoryProposal>;
  readonly onAccept: (id: MemoryProposal["id"]) => void;
  readonly onReject: (id: MemoryProposal["id"]) => void;
  readonly onSnooze: (id: MemoryProposal["id"]) => void;
  /**
   * Cap visible toasts. Defaults to 3 (PRD: "stack vertically; older
   * collapses to '+N more'"). The remaining toasts are summarised in a
   * single "+N more" chip rendered below the stack.
   */
  readonly maxVisible?: number;
  /**
   * Fired when the user clicks the "+N more" chip. The host typically
   * navigates to `/memory/proposals` or expands the stack inline.
   */
  readonly onExpandStack?: () => void;
}

export function MemoryProposalToastStack({
  proposals,
  onAccept,
  onReject,
  onSnooze,
  maxVisible = 3,
  onExpandStack,
}: MemoryProposalToastStackProps): ReactElement | null {
  if (proposals.length === 0) {
    return null;
  }
  const visible = proposals.slice(0, maxVisible);
  const overflow = proposals.length - visible.length;

  return (
    <div
      role="region"
      aria-label="Memory proposals"
      data-testid="memory-proposal-toast-stack"
      data-count={proposals.length}
      data-overflow={overflow}
      style={stackStyle}
    >
      {visible.map((p) => (
        <MemoryProposalToast
          key={p.id}
          proposal={p}
          onAccept={onAccept}
          onReject={onReject}
          onSnooze={onSnooze}
        />
      ))}
      {overflow > 0 ? (
        <button
          type="button"
          onClick={() => {
            if (onExpandStack !== undefined) onExpandStack();
          }}
          style={overflowChipStyle}
          data-testid="memory-proposal-toast-overflow"
          aria-label={`Show ${overflow} more memory proposals`}
        >
          +{overflow} more
        </button>
      ) : null}
    </div>
  );
}

// ===========================================================================
// Helpers
// ===========================================================================

const EXCERPT_MAX = 160;

function makeExcerpt(body: string): string {
  // Toast excerpt — strip markdown noise (basic; we don't need full
  // remark parsing for a 160-char preview) and clamp.
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

function kindLabel(kind: MemoryProposal["proposed_kind"]): string {
  if (kind === "skill") return "Skill";
  if (kind === "fact") return "Fact";
  return "Preference";
}

// ===========================================================================
// Styles
// ===========================================================================

const stackStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  width: 360,
  maxWidth: "calc(100vw - 32px)",
};

const toastStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  padding: 12,
  borderRadius: "var(--radius-md, 12px)",
  border: "1px solid var(--color-border, #232325)",
  background: "var(--color-bg-elevated, #161617)",
  color: "var(--color-text, #ededee)",
  boxShadow: "0 8px 24px rgba(0, 0, 0, 0.3)",
};

const headerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
};

const titleStyle: CSSProperties = {
  flex: 1,
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  color: "var(--color-text, #ededee)",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const excerptStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-muted, #b4b4b8)",
  lineHeight: 1.5,
};

const actionRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
};

const primaryButtonStyle: CSSProperties = {
  height: 28,
  padding: "0 10px",
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
  padding: "0 10px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border, #232325)",
  background: "transparent",
  color: "var(--color-text-muted, #b4b4b8)",
  fontSize: "var(--font-size-xs, 12px)",
  cursor: "pointer",
};

const sourceStyle: CSSProperties = {
  marginLeft: "auto",
  fontSize: "var(--font-size-xs, 12px)",
};

const overflowChipStyle: CSSProperties = {
  alignSelf: "center",
  padding: "4px 10px",
  borderRadius: "var(--radius-full, 999px)",
  border: "1px solid var(--color-border, #232325)",
  background: "var(--color-surface-muted, #222224)",
  color: "var(--color-text-muted, #b4b4b8)",
  fontSize: "var(--font-size-xs, 12px)",
  cursor: "pointer",
};
