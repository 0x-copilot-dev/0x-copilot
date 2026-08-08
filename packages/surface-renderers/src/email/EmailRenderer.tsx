import type { CSSProperties, ReactElement, ReactNode } from "react";

import {
  DiffText,
  TcInlineDiff,
  wordDiff,
  type SaaSRendererAdapter,
} from "@0x-copilot/chat-surface";

import { SURFACE_PALETTE as PALETTE } from "../_shared/palette";
import { dataFromState } from "../_shared/specTypes";

type DiffVisualState = "pending" | "streaming";

export interface EmailState {
  readonly to: string;
  readonly cc: string;
  readonly subject: string;
  readonly body: string;
  readonly autoSavedLabel?: string;
}

/** An `EmailState` with every slot empty — what an unreadable payload draws. */
const EMPTY_EMAIL_STATE: EmailState = { to: "", cc: "", subject: "", body: "" };

/**
 * Read an `EmailState` out of whatever the host mounted.
 *
 * Two shapes arrive at this adapter and both are legitimate:
 *
 * - the **surface envelope** `{spec?, source?, data}` — what `TcSurfaceMount`
 *   passes for every surface, hydrated from `GET /v1/agent/runs/{id}/surfaces`.
 *   The backend's `email://` producer normalises the connector's field names
 *   onto the four `EmailState` keys, so `data` IS the composer state; and
 * - a **bare `EmailState`**, which is how this renderer has always been driven
 *   from a standalone mount and from its own tests.
 *
 * `dataFromState` is the package's shared narrowing for exactly that first
 * shape — the same one every archetype renderer uses — so there is one rule for
 * "where does a surface keep its payload", not a second copy here.
 *
 * Every slot is coerced to a string and a missing one becomes empty. The
 * composer must never print `undefined` into a recipient row, and it must never
 * invent one: an unreadable payload draws an empty composer, not a filled one.
 */
export function emailStateFrom(value: unknown): EmailState {
  const data = dataFromState(value);
  if (typeof data !== "object" || data === null || Array.isArray(data)) {
    return EMPTY_EMAIL_STATE;
  }
  const record = data as Record<string, unknown>;
  const text = (key: string): string =>
    typeof record[key] === "string" ? (record[key] as string) : "";
  const autoSavedLabel = record["autoSavedLabel"];
  return {
    to: text("to"),
    cc: text("cc"),
    subject: text("subject"),
    body: text("body"),
    ...(typeof autoSavedLabel === "string" ? { autoSavedLabel } : {}),
  };
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
  // PRD-06 word-diff payload. When both are present strings (and not streaming),
  // the pending body renders `DiffText(wordDiff(before_body, after_body))` — the
  // VSCode/Cursor-style red/green inline diff — instead of the plain ghost
  // paragraph. Named per the PRD's `{before_body, after_body}` diff-payload keys.
  readonly before_body?: string;
  readonly after_body?: string;
}

export interface EmailDiff {
  readonly base: EmailState;
  readonly pending: EmailDiffPending;
}

const DEFAULT_AUTOSAVED_LABEL = "Auto-saved · 2s ago";

const STREAM_KEYFRAMES_ID = "tc-email-streaming-cursor-keyframes";
const STREAM_KEYFRAMES_CSS = `
@keyframes tc-email-streaming-cursor-blink {
  0%, 49% { opacity: 1; }
  50%, 100% { opacity: 0; }
}
`;

/**
 * The `email://` adapter.
 *
 * `renderCurrent` takes `unknown` rather than `EmailState` because the host
 * mounts the surface envelope, not the composer state — see
 * {@link emailStateFrom}. Narrowing happens here, at the boundary, exactly as
 * the archetype renderers narrow theirs; the shell below still works in
 * `EmailState` only.
 */
export const emailAdapter: SaaSRendererAdapter<unknown, EmailDiff> = {
  scheme: "email",
  matches: (uri: string): boolean => uri.startsWith("email://"),
  renderCurrent: (state: unknown): ReactElement => {
    const email = emailStateFrom(state);
    return (
      <EmailComposerShell state={email}>
        <EmailBodyParagraph text={email.body} />
      </EmailComposerShell>
    );
  },
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

/** The word diff renders only once the edit has settled (not streaming) and both
 * before/after bodies are present — otherwise the streaming ghost stands in. */
function showWordDiff(
  pending: EmailDiffPending,
): pending is EmailDiffPending & { before_body: string; after_body: string } {
  return (
    !pending.streaming &&
    typeof pending.before_body === "string" &&
    typeof pending.after_body === "string"
  );
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
          {showWordDiff(pending) ? (
            // Diff computes once on the settled pending state; while streaming we
            // keep the ghost paragraph + cursor below (no diff yet).
            <DiffText
              hunks={wordDiff(pending.before_body, pending.after_body)}
            />
          ) : (
            <>
              <span>{pending.streamingBody}</span>
              {pending.streaming ? <StreamingCursor /> : null}
            </>
          )}
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
