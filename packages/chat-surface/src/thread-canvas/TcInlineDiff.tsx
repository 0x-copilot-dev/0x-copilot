import { useReducer, type CSSProperties, type ReactNode } from "react";

export type InlineDiffState =
  | "idle"
  | "streaming"
  | "pending"
  | "accepted"
  | "rejected";

export type InlineDiffEvent =
  | "stream_start"
  | "stream_end"
  | "cancel"
  | "approve"
  | "reject"
  | "reset";

export class InvalidInlineDiffTransitionError extends Error {
  readonly from: InlineDiffState;
  readonly event: InlineDiffEvent;
  constructor(from: InlineDiffState, event: InlineDiffEvent) {
    super(
      `Invalid TcInlineDiff transition: cannot dispatch '${event}' from state '${from}'`,
    );
    this.name = "InvalidInlineDiffTransitionError";
    this.from = from;
    this.event = event;
  }
}

const TRANSITIONS: Readonly<
  Record<
    InlineDiffState,
    Readonly<Partial<Record<InlineDiffEvent, InlineDiffState>>>
  >
> = {
  idle: { stream_start: "streaming" },
  streaming: { stream_end: "pending", cancel: "idle" },
  pending: { approve: "accepted", reject: "rejected" },
  accepted: { reset: "idle" },
  rejected: { reset: "idle" },
};

export function nextInlineDiffState(
  current: InlineDiffState,
  event: InlineDiffEvent,
): InlineDiffState {
  const next = TRANSITIONS[current][event];
  if (next === undefined) {
    throw new InvalidInlineDiffTransitionError(current, event);
  }
  return next;
}

function inlineDiffReducer(
  state: InlineDiffState,
  event: InlineDiffEvent,
): InlineDiffState {
  return nextInlineDiffState(state, event);
}

export function useInlineDiffReducer(initial: InlineDiffState = "idle"): {
  readonly state: InlineDiffState;
  readonly dispatch: (event: InlineDiffEvent) => void;
} {
  const [state, dispatch] = useReducer(inlineDiffReducer, initial);
  return { state, dispatch };
}

export interface TcInlineDiffProps {
  readonly state: InlineDiffState;
  readonly progressPercent?: number;
  readonly provenance?: string;
  readonly title: string;
  readonly description?: string;
  readonly onApprove?: () => void;
  readonly onReject?: () => void;
  readonly onSuggestChanges?: () => void;
  readonly approveLabel?: string;
  readonly rejectLabel?: string;
  readonly suggestLabel?: string;
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
  progressTrack: "#23262a",
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

const STATE_ICON: Record<InlineDiffState, string | null> = {
  idle: null,
  streaming: null,
  pending: null,
  accepted: "✓",
  rejected: "✕",
};

const KEYFRAMES_ID = "tc-inline-diff-keyframes";
const KEYFRAMES_CSS = `
@keyframes tc-inline-diff-indeterminate {
  0% { transform: translateX(-100%); }
  100% { transform: translateX(400%); }
}
`;

export function TcInlineDiff(props: TcInlineDiffProps): ReactNode {
  const {
    state,
    progressPercent,
    provenance,
    title,
    description,
    onApprove,
    onReject,
    onSuggestChanges,
    approveLabel = "Approve",
    rejectLabel = "Reject",
    suggestLabel = "Suggest changes",
  } = props;

  const accent = STATE_ACCENT[state];
  const showButtons = state === "pending";
  const showSuggest = showButtons && typeof onSuggestChanges === "function";
  const icon = STATE_ICON[state];
  const isMuted = state === "accepted" || state === "rejected";
  const pillText =
    state === "streaming" && typeof progressPercent === "number"
      ? `${STATE_LABELS[state]} · ${Math.round(progressPercent)}%`
      : STATE_LABELS[state];
  const isDeterminate =
    state === "streaming" && typeof progressPercent === "number";

  return (
    <div
      role="group"
      aria-label={`Inline diff: ${state}`}
      data-state={state}
      style={cardStyle(accent)}
    >
      <style data-testid="tc-inline-diff-keyframes" id={KEYFRAMES_ID}>
        {KEYFRAMES_CSS}
      </style>
      {state === "streaming" ? (
        <div
          style={progressTrackStyle}
          data-testid="tc-inline-diff-progress-track"
        >
          <div
            style={progressFillStyle(accent, isDeterminate, progressPercent)}
            data-testid="tc-inline-diff-progress-fill"
            data-determinate={isDeterminate ? "true" : "false"}
          />
        </div>
      ) : null}
      <div style={headerRowStyle}>
        <span style={pillStyle(accent)} data-testid="tc-inline-diff-pill">
          {pillText}
        </span>
        {provenance ? (
          <span
            style={provenancePillStyle}
            data-testid="tc-inline-diff-provenance"
          >
            <span
              aria-hidden="true"
              style={provenanceDotStyle(accent)}
              data-testid="tc-inline-diff-provenance-dot"
            />
            {provenance}
          </span>
        ) : null}
      </div>
      <div style={titleRowStyle}>
        {icon ? (
          <span
            aria-hidden="true"
            style={iconStyle(accent)}
            data-testid="tc-inline-diff-icon"
          >
            {icon}
          </span>
        ) : null}
        <div style={titleStyle(isMuted)}>{title}</div>
      </div>
      {description ? <div style={descriptionStyle}>{description}</div> : null}
      {showButtons ? (
        <div style={buttonRowStyle}>
          {showSuggest ? (
            <button
              type="button"
              onClick={onSuggestChanges}
              style={secondaryButtonStyle}
              data-testid="tc-inline-diff-suggest"
            >
              {suggestLabel}
            </button>
          ) : null}
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
  position: "relative",
  overflow: "hidden",
});

const headerRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
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

const provenancePillStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  padding: "2px 8px",
  borderRadius: 999,
  border: `1px solid ${PALETTE.cardBorder}`,
  fontSize: 11,
  letterSpacing: 0.4,
  color: PALETTE.textLo,
  textTransform: "uppercase",
};

const provenanceDotStyle = (accent: string): CSSProperties => ({
  display: "inline-block",
  width: 6,
  height: 6,
  borderRadius: "50%",
  background: accent,
});

const titleRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
};

const iconStyle = (accent: string): CSSProperties => ({
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  width: 18,
  height: 18,
  borderRadius: 999,
  background: accent,
  color: PALETTE.cardBg,
  fontSize: 11,
  fontWeight: 700,
  lineHeight: 1,
});

const titleStyle = (muted: boolean): CSSProperties => ({
  fontSize: 14,
  lineHeight: 1.45,
  color: muted ? PALETTE.textLo : PALETTE.textHi,
});

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

const progressTrackStyle: CSSProperties = {
  position: "absolute",
  top: 0,
  left: 0,
  right: 0,
  height: 3,
  background: PALETTE.progressTrack,
  overflow: "hidden",
};

const progressFillStyle = (
  accent: string,
  determinate: boolean,
  percent: number | undefined,
): CSSProperties => {
  if (determinate) {
    const clamped = Math.max(0, Math.min(100, percent ?? 0));
    return {
      height: "100%",
      width: `${clamped}%`,
      background: accent,
      transition: "width 120ms linear",
    };
  }
  return {
    height: "100%",
    width: "25%",
    background: accent,
    animation: "tc-inline-diff-indeterminate 1.2s ease-in-out infinite",
  };
};
