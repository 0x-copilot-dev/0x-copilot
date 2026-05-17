import type { CSSProperties, ReactElement, ReactNode } from "react";

import {
  TcInlineDiff,
  type SaaSRendererAdapter,
} from "@enterprise-search/chat-surface";

type DiffVisualState = "pending" | "streaming";

export interface EmailState {
  readonly to: string;
  readonly cc: string;
  readonly subject: string;
  readonly body: string;
  readonly autoSavedLabel?: string;
}

export interface EmailDiffPending {
  readonly provenance: string;
  readonly title: string;
  readonly description?: string;
  readonly bodyPrefix: string;
  readonly streamingBody: string;
  readonly bodySuffix: string;
  readonly progressPercent?: number;
  readonly streaming?: boolean;
}

export interface EmailDiff {
  readonly base: EmailState;
  readonly pending: EmailDiffPending;
}

const PALETTE = {
  pageBg: "#101113",
  surface: "#181a1c",
  surfaceMute: "#1f2226",
  border: "#2a2d31",
  textHi: "#f4f5f6",
  textMid: "#c8ccd1",
  textLo: "#9aa0a6",
  lime: "#c2ff5a",
  limeBgSoft: "rgba(194, 255, 90, 0.12)",
} as const;

const DEFAULT_AUTOSAVED_LABEL = "Auto-saved · 2s ago";

const STREAM_KEYFRAMES_ID = "tc-email-streaming-cursor-keyframes";
const STREAM_KEYFRAMES_CSS = `
@keyframes tc-email-streaming-cursor-blink {
  0%, 49% { opacity: 1; }
  50%, 100% { opacity: 0; }
}
`;

export const emailAdapter: SaaSRendererAdapter<EmailState, EmailDiff> = {
  scheme: "email",
  matches: (uri: string): boolean => uri.startsWith("email://"),
  renderCurrent: (state: EmailState): ReactElement => (
    <EmailComposerShell state={state}>
      <EmailBodyParagraph text={state.body} />
    </EmailComposerShell>
  ),
  renderDiff: (diff: EmailDiff): ReactElement => (
    <EmailComposerShell state={diff.base} drafting={diff.pending.streaming}>
      <EmailDiffBody diff={diff} />
    </EmailComposerShell>
  ),
  metadata: {
    origin: "first-party",
    schemaVersion: 1,
  },
};

interface EmailComposerShellProps {
  readonly state: EmailState;
  readonly children: ReactNode;
  readonly drafting?: boolean;
}

function EmailComposerShell(props: EmailComposerShellProps): ReactElement {
  const { state, children, drafting } = props;
  const autoSavedLabel = state.autoSavedLabel ?? DEFAULT_AUTOSAVED_LABEL;
  return (
    <form
      onSubmit={(e) => e.preventDefault()}
      style={pageStyle}
      data-testid="email-renderer"
      aria-label="Email composer"
    >
      <div style={cardStyle}>
        <header style={headerRowStyle}>
          <span style={titleLabelStyle}>New message</span>
          <div style={headerRightStyle}>
            {drafting ? (
              <span style={draftingPillStyle} data-testid="drafting-pill">
                Drafting…
              </span>
            ) : null}
            <button type="button" style={ghostButtonStyle}>
              Save draft
            </button>
          </div>
        </header>

        <FieldRow id="email-to" label="To:" value={state.to} />
        <FieldRow id="email-cc" label="Cc:" value={state.cc} />
        <FieldRow id="email-subject" label="Subject:" value={state.subject} />

        <div style={bodyContainerStyle}>{children}</div>

        <footer style={footerRowStyle}>
          <div style={footerLeftStyle}>
            <button type="button" style={primaryButtonStyle}>
              Send
            </button>
            <button type="button" style={ghostButtonStyle}>
              Schedule
            </button>
          </div>
          <span style={autoSavedStyle} data-testid="email-auto-saved">
            {autoSavedLabel}
          </span>
        </footer>
      </div>
    </form>
  );
}

interface FieldRowProps {
  readonly id: string;
  readonly label: string;
  readonly value: string;
}

function FieldRow(props: FieldRowProps): ReactElement {
  const { id, label, value } = props;
  return (
    <div style={fieldRowStyle}>
      <label htmlFor={id} style={fieldLabelStyle}>
        {label}
      </label>
      <input
        id={id}
        type="text"
        readOnly
        value={value}
        style={fieldInputStyle}
        data-testid={id}
      />
    </div>
  );
}

interface EmailBodyParagraphProps {
  readonly text: string;
}

function EmailBodyParagraph(props: EmailBodyParagraphProps): ReactElement {
  return <p style={bodyParagraphStyle}>{props.text}</p>;
}

interface EmailDiffBodyProps {
  readonly diff: EmailDiff;
}

function EmailDiffBody(props: EmailDiffBodyProps): ReactElement {
  const { diff } = props;
  const { pending } = diff;
  const state: DiffVisualState = pending.streaming ? "streaming" : "pending";
  return (
    <>
      {pending.bodyPrefix ? (
        <p style={bodyParagraphStyle}>{pending.bodyPrefix}</p>
      ) : null}
      <section
        id="pending-block"
        aria-label="Pending edit"
        style={pendingAnchorStyle(state)}
        data-testid="pending-block"
        data-state={state}
      >
        <div style={pendingHeaderRowStyle}>
          <span style={pendingLabelStyle} data-testid="pending-label">
            {`PENDING · ${pending.provenance}`}
          </span>
          <ProvenancePill provenance={pending.provenance} />
        </div>
        <div style={pendingBodyStyle} data-testid="pending-body">
          <span>{pending.streamingBody}</span>
          {pending.streaming ? <StreamingCursor /> : null}
        </div>
        {pending.streaming ? (
          <TcInlineDiff
            state="streaming"
            progressPercent={pending.progressPercent}
            provenance={pending.provenance}
            title={pending.title}
            description={pending.description}
          />
        ) : (
          <PendingDiffSummary
            title={pending.title}
            description={pending.description}
          />
        )}
      </section>
      {pending.bodySuffix ? (
        <p style={bodyParagraphStyle}>{pending.bodySuffix}</p>
      ) : null}
    </>
  );
}

interface ProvenancePillProps {
  readonly provenance: string;
}

function ProvenancePill(props: ProvenancePillProps): ReactElement {
  return (
    <span style={provenancePillStyle} data-testid="email-provenance-pill">
      <span aria-hidden="true" style={provenanceDotStyle} />
      {props.provenance}
    </span>
  );
}

interface PendingDiffSummaryProps {
  readonly title: string;
  readonly description?: string;
}

// The non-streaming pending state cannot delegate to TcInlineDiff: that
// primitive forces Approve/Reject buttons inside the card when state is
// 'pending', and PRD D28 mandates the host owns those buttons. We render
// a small inline summary that mirrors TcInlineDiff's title+description
// styling for visual continuity without the action surface.
function PendingDiffSummary(props: PendingDiffSummaryProps): ReactElement {
  return (
    <div style={pendingSummaryStyle} data-testid="email-pending-summary">
      <div style={pendingSummaryTitleStyle}>{props.title}</div>
      {props.description ? (
        <div style={pendingSummaryDescStyle}>{props.description}</div>
      ) : null}
    </div>
  );
}

function StreamingCursor(): ReactElement {
  return (
    <>
      <style data-testid="streaming-cursor-keyframes" id={STREAM_KEYFRAMES_ID}>
        {STREAM_KEYFRAMES_CSS}
      </style>
      <span
        aria-hidden="true"
        data-testid="streaming-cursor"
        style={streamingCursorStyle}
      >
        ▍
      </span>
    </>
  );
}

const pageStyle: CSSProperties = {
  background: PALETTE.pageBg,
  minHeight: "100%",
  padding: 24,
  fontFamily:
    "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
  color: PALETTE.textHi,
  display: "flex",
  justifyContent: "center",
};

const cardStyle: CSSProperties = {
  background: PALETTE.surface,
  border: `1px solid ${PALETTE.border}`,
  borderRadius: 14,
  width: "100%",
  maxWidth: 760,
  display: "flex",
  flexDirection: "column",
  gap: 18,
  padding: 22,
  boxShadow: "0 8px 28px rgba(0,0,0,0.4)",
};

const headerRowStyle: CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  borderBottom: `1px solid ${PALETTE.border}`,
  paddingBottom: 12,
};

const titleLabelStyle: CSSProperties = {
  fontSize: 13,
  letterSpacing: 0.6,
  color: PALETTE.textLo,
  textTransform: "uppercase",
};

const headerRightStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 10,
};

const draftingPillStyle: CSSProperties = {
  background: PALETTE.surfaceMute,
  color: PALETTE.textMid,
  fontSize: 11,
  padding: "4px 9px",
  borderRadius: 999,
  border: `1px solid ${PALETTE.border}`,
};

const ghostButtonStyle: CSSProperties = {
  background: "transparent",
  border: `1px solid ${PALETTE.border}`,
  color: PALETTE.textMid,
  borderRadius: 8,
  padding: "6px 12px",
  fontSize: 12,
  fontWeight: 500,
  cursor: "pointer",
};

const fieldRowStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "78px 1fr",
  alignItems: "baseline",
  gap: 8,
  paddingBlock: 4,
};

const fieldLabelStyle: CSSProperties = {
  color: PALETTE.textLo,
  fontSize: 12,
  letterSpacing: 0.4,
  textTransform: "uppercase",
};

const fieldInputStyle: CSSProperties = {
  background: "transparent",
  border: "none",
  outline: "none",
  color: PALETTE.textHi,
  fontSize: 13,
  width: "100%",
  padding: 0,
  font: "inherit",
};

const bodyContainerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 14,
  paddingTop: 10,
  borderTop: `1px solid ${PALETTE.border}`,
};

const bodyParagraphStyle: CSSProperties = {
  margin: 0,
  fontSize: 14,
  lineHeight: 1.6,
  whiteSpace: "pre-wrap",
  color: PALETTE.textMid,
};

const pendingAnchorStyle = (state: DiffVisualState): CSSProperties => ({
  position: "relative",
  padding: 12,
  borderRadius: 8,
  background: PALETTE.limeBgSoft,
  border: `1px solid ${PALETTE.lime}`,
  display: "flex",
  flexDirection: "column",
  gap: 8,
  outline: state === "streaming" ? `1px dashed ${PALETTE.lime}` : "none",
  outlineOffset: 2,
});

const pendingHeaderRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 8,
  flexWrap: "wrap",
};

const pendingLabelStyle: CSSProperties = {
  fontSize: 10.5,
  fontWeight: 700,
  letterSpacing: 0.7,
  color: PALETTE.lime,
};

const provenancePillStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  padding: "2px 8px",
  borderRadius: 999,
  border: `1px solid ${PALETTE.border}`,
  fontSize: 11,
  letterSpacing: 0.4,
  color: PALETTE.textLo,
  textTransform: "uppercase",
};

const provenanceDotStyle: CSSProperties = {
  display: "inline-block",
  width: 6,
  height: 6,
  borderRadius: "50%",
  background: PALETTE.lime,
};

const pendingSummaryStyle: CSSProperties = {
  marginTop: 4,
  paddingTop: 8,
  borderTop: `1px solid ${PALETTE.border}`,
  display: "flex",
  flexDirection: "column",
  gap: 4,
};

const pendingSummaryTitleStyle: CSSProperties = {
  fontSize: 13,
  lineHeight: 1.4,
  color: PALETTE.textHi,
};

const pendingSummaryDescStyle: CSSProperties = {
  fontSize: 12,
  lineHeight: 1.5,
  color: PALETTE.textLo,
};

const pendingBodyStyle: CSSProperties = {
  fontSize: 14,
  lineHeight: 1.55,
  whiteSpace: "pre-wrap",
  color: PALETTE.textHi,
  minHeight: 22,
  display: "inline-flex",
  alignItems: "baseline",
  flexWrap: "wrap",
};

const streamingCursorStyle: CSSProperties = {
  display: "inline-block",
  marginLeft: 2,
  color: PALETTE.lime,
  animation: "tc-email-streaming-cursor-blink 1s steps(1, end) infinite",
};

const footerRowStyle: CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  borderTop: `1px solid ${PALETTE.border}`,
  paddingTop: 12,
};

const footerLeftStyle: CSSProperties = {
  display: "flex",
  gap: 8,
};

const primaryButtonStyle: CSSProperties = {
  background: PALETTE.lime,
  color: "#101113",
  border: "none",
  borderRadius: 8,
  padding: "8px 16px",
  fontSize: 13,
  fontWeight: 600,
  cursor: "pointer",
};

const autoSavedStyle: CSSProperties = {
  fontSize: 11,
  color: PALETTE.textLo,
};
