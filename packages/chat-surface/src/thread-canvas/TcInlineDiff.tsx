import type { CSSProperties, ReactNode } from "react";

export type InlineDiffState =
  | "idle"
  | "streaming"
  | "pending"
  | "accepted"
  | "rejected";

export interface TcInlineDiffProps {
  readonly state: InlineDiffState;
  readonly progressPercent?: number;
  readonly provenance?: string;
  readonly title: string;
  readonly description?: string;
  readonly onApprove?: () => void;
  readonly onReject?: () => void;
  readonly approveLabel?: string;
  readonly rejectLabel?: string;
}

const PALETTE = {
  lime: "#c2ff5a",
  limeShadow: "rgba(194, 255, 90, 0.18)",
  cardBg: "#181a1c",
  cardBorder: "#2a2d31",
  textHi: "#f4f5f6",
  textLo: "#9aa0a6",
  accepted: "#3ddc97",
  rejected: "#ef5a5a",
} as const;

const STATE_LABELS: Record<InlineDiffState, string> = {
  idle: "IDLE",
  streaming: "STREAMING",
  pending: "PENDING",
  accepted: "ACCEPTED",
  rejected: "REJECTED",
};

const STATE_ACCENT: Record<InlineDiffState, string> = {
  idle: PALETTE.textLo,
  streaming: PALETTE.lime,
  pending: PALETTE.lime,
  accepted: PALETTE.accepted,
  rejected: PALETTE.rejected,
};

export function TcInlineDiff(props: TcInlineDiffProps): ReactNode {
  const {
    state,
    progressPercent,
    provenance,
    title,
    description,
    onApprove,
    onReject,
    approveLabel = "Approve",
    rejectLabel = "Reject",
  } = props;

  const accent = STATE_ACCENT[state];
  const showButtons = state === "pending";
  const pillText =
    state === "streaming" && typeof progressPercent === "number"
      ? `${STATE_LABELS[state]} · ${Math.round(progressPercent)}%`
      : STATE_LABELS[state];

  return (
    <div
      role="group"
      aria-label={`Inline diff: ${state}`}
      data-state={state}
      style={cardStyle(accent)}
    >
      <div style={headerRowStyle}>
        <span style={pillStyle(accent)} data-testid="tc-inline-diff-pill">
          {pillText}
        </span>
        {provenance ? (
          <span style={provenanceStyle} data-testid="tc-inline-diff-provenance">
            {provenance}
          </span>
        ) : null}
      </div>
      <div style={titleStyle}>{title}</div>
      {description ? <div style={descriptionStyle}>{description}</div> : null}
      {showButtons ? (
        <div style={buttonRowStyle}>
          <button
            type="button"
            onClick={onReject}
            style={secondaryButtonStyle}
            data-testid="tc-inline-diff-reject"
          >
            {rejectLabel}
          </button>
          <button
            type="button"
            onClick={onApprove}
            style={primaryButtonStyle(accent)}
            data-testid="tc-inline-diff-approve"
          >
            {approveLabel}
          </button>
        </div>
      ) : null}
    </div>
  );
}

const cardStyle = (accent: string): CSSProperties => ({
  background: PALETTE.cardBg,
  border: `1px solid ${PALETTE.cardBorder}`,
  borderRadius: 10,
  padding: 16,
  color: PALETTE.textHi,
  boxShadow: `0 6px 20px ${PALETTE.limeShadow}`,
  display: "flex",
  flexDirection: "column",
  gap: 10,
  width: "min(420px, 90%)",
  fontFamily:
    "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
  outline: "none",
  borderTop: `2px solid ${accent}`,
});

const headerRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 12,
  flexWrap: "wrap",
};

const pillStyle = (accent: string): CSSProperties => ({
  display: "inline-block",
  padding: "2px 8px",
  borderRadius: 999,
  fontSize: 11,
  fontWeight: 600,
  letterSpacing: 0.4,
  color: PALETTE.cardBg,
  background: accent,
});

const provenanceStyle: CSSProperties = {
  fontSize: 11,
  letterSpacing: 0.4,
  color: PALETTE.textLo,
  textTransform: "uppercase",
};

const titleStyle: CSSProperties = {
  fontSize: 14,
  lineHeight: 1.45,
  color: PALETTE.textHi,
};

const descriptionStyle: CSSProperties = {
  fontSize: 12,
  lineHeight: 1.5,
  color: PALETTE.textLo,
};

const buttonRowStyle: CSSProperties = {
  display: "flex",
  gap: 8,
  justifyContent: "flex-end",
  marginTop: 6,
};

const primaryButtonStyle = (accent: string): CSSProperties => ({
  background: accent,
  color: PALETTE.cardBg,
  border: "none",
  borderRadius: 8,
  padding: "8px 14px",
  fontSize: 12,
  fontWeight: 600,
  cursor: "pointer",
});

const secondaryButtonStyle: CSSProperties = {
  background: "transparent",
  color: PALETTE.textHi,
  border: `1px solid ${PALETTE.cardBorder}`,
  borderRadius: 8,
  padding: "8px 14px",
  fontSize: 12,
  fontWeight: 600,
  cursor: "pointer",
};
