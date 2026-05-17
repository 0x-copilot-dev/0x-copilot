import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";

import type {
  InlineDiffState,
  PendingDiff,
  SurfaceRendererProps,
} from "@enterprise-search/chat-surface";

import { EmailDiffOverlay } from "./EmailDiffOverlay";

interface EmailDraftPayload {
  readonly draftId: string;
  readonly to: string;
  readonly cc: string;
  readonly subject: string;
  readonly bodyPrefix: string;
  readonly bodySuffix: string;
}

interface PendingDiffEvent {
  readonly type: "pending_diff_appeared";
  readonly diffId: string;
  readonly provenance: string;
  readonly title: string;
  readonly description?: string;
  readonly regionAnchorId: string;
}

interface ToolCallChunkEvent {
  readonly type: "tool_call_chunk";
  readonly chunk: string;
  readonly progressPercent?: number;
}

interface ToolCallStartEvent {
  readonly type: "tool_call_start";
}

interface ToolCallEndEvent {
  readonly type: "tool_call_end";
}

type StreamEvent =
  | ToolCallStartEvent
  | ToolCallChunkEvent
  | ToolCallEndEvent
  | PendingDiffEvent
  | { readonly type: string };

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

const NULL_DRAFT: EmailDraftPayload = {
  draftId: "",
  to: "",
  cc: "",
  subject: "",
  bodyPrefix: "",
  bodySuffix: "",
};

export function EmailRenderer(props: SurfaceRendererProps): ReactNode {
  const { transport, activeDiff, onApproveDiff, onRejectDiff } = props;

  const [draft, setDraft] = useState<EmailDraftPayload>(NULL_DRAFT);
  const [streamedBody, setStreamedBody] = useState("");
  const [progress, setProgress] = useState(0);
  const [diffState, setDiffState] = useState<InlineDiffState>("idle");
  const [streamedDiff, setStreamedDiff] = useState<PendingDiff | null>(null);
  const hasMounted = useRef(false);

  useEffect(() => {
    // StrictMode double-invoke guard. We only want one request + one
    // subscription per renderer instance.
    if (hasMounted.current) {
      return;
    }
    hasMounted.current = true;

    let cancelled = false;
    void transport
      .request<EmailDraftPayload>({
        method: "GET",
        path: "/drafts/draft-1",
      })
      .then((payload) => {
        if (!cancelled) setDraft(payload);
      })
      .catch(() => {
        // Intentionally swallow — the spike renderer keeps NULL_DRAFT
        // so the UI still mounts. Production renderers surface this.
      });

    const subscription = transport.subscribeServerSentEvents({
      path: "/drafts/draft-1/events",
      onMessage: (raw: string) => {
        if (cancelled) return;
        let evt: StreamEvent;
        try {
          evt = JSON.parse(raw) as StreamEvent;
        } catch {
          return;
        }
        applyEvent(evt);
      },
    });

    const applyEvent = (evt: StreamEvent): void => {
      switch (evt.type) {
        case "tool_call_start": {
          setDiffState("streaming");
          break;
        }
        case "tool_call_chunk": {
          const chunk = evt as ToolCallChunkEvent;
          setStreamedBody((prev) => prev + chunk.chunk);
          if (typeof chunk.progressPercent === "number") {
            setProgress(chunk.progressPercent);
          }
          break;
        }
        case "tool_call_end": {
          setProgress(100);
          break;
        }
        case "pending_diff_appeared": {
          const diff = evt as PendingDiffEvent;
          setStreamedDiff({
            diffId: diff.diffId,
            provenance: diff.provenance,
            title: diff.title,
            description: diff.description,
            regionAnchorId: diff.regionAnchorId,
          });
          setDiffState("pending");
          break;
        }
        default:
          break;
      }
    };

    return () => {
      cancelled = true;
      subscription.close();
    };
  }, [transport]);

  const renderedDiff = activeDiff ?? streamedDiff;

  const handleApprove = (): void => {
    if (renderedDiff && onApproveDiff) {
      onApproveDiff(renderedDiff.diffId);
    }
    setDiffState("accepted");
  };

  const handleReject = (): void => {
    if (renderedDiff && onRejectDiff) {
      onRejectDiff(renderedDiff.diffId);
    }
    setDiffState("rejected");
  };

  return (
    <form
      onSubmit={(e) => e.preventDefault()}
      style={pageStyle}
      data-testid="email-renderer"
    >
      <div style={cardStyle}>
        <header style={headerRowStyle}>
          <span style={titleLabelStyle}>New message</span>
          <div style={headerRightStyle}>
            {diffState === "streaming" || diffState === "pending" ? (
              <span style={draftingPillStyle} data-testid="drafting-pill">
                Drafting…
              </span>
            ) : null}
            <button type="button" style={ghostButtonStyle}>
              Save draft
            </button>
          </div>
        </header>

        <FieldRow id="email-to" label="To:" value={draft.to} />
        <FieldRow id="email-cc" label="Cc:" value={draft.cc} />
        <FieldRow id="email-subject" label="Subject:" value={draft.subject} />

        <div style={bodyContainerStyle}>
          {draft.bodyPrefix ? (
            <p style={bodyParagraphStyle}>{draft.bodyPrefix.trim()}</p>
          ) : null}
          <section
            id="pending-block"
            aria-label="Pending edit"
            style={pendingAnchorStyle(diffState)}
            data-testid="pending-block"
            data-state={diffState}
          >
            <span style={pendingLabelStyle}>
              {(renderedDiff ?? streamedDiff)
                ? `PENDING · ${(renderedDiff ?? streamedDiff)!.provenance}`
                : "PENDING · DRAFTED FROM SALESFORCE + Q4 SHEET"}
            </span>
            <div style={pendingBodyStyle} data-testid="pending-body">
              {streamedBody.length > 0 ? streamedBody : " "}
            </div>
            {renderedDiff ? (
              <EmailDiffOverlay
                diff={renderedDiff}
                state={diffState === "idle" ? "pending" : diffState}
                progressPercent={progress}
                onApprove={handleApprove}
                onReject={handleReject}
              />
            ) : null}
          </section>
          {draft.bodySuffix ? (
            <p style={bodyParagraphStyle}>{draft.bodySuffix.trim()}</p>
          ) : null}
        </div>

        <footer style={footerRowStyle}>
          <div style={footerLeftStyle}>
            <button type="button" style={primaryButtonStyle}>
              Send
            </button>
            <button type="button" style={ghostButtonStyle}>
              Schedule
            </button>
          </div>
          <span style={autoSavedStyle}>Auto-saved · 2s ago</span>
        </footer>
      </div>
    </form>
  );
}

function FieldRow({
  id,
  label,
  value,
}: {
  id: string;
  label: string;
  value: string;
}): ReactNode {
  return (
    <div style={fieldRowStyle}>
      <label htmlFor={id} style={fieldLabelStyle}>
        {label}
      </label>
      <div id={id} style={fieldValueStyle} data-testid={id}>
        {value || " "}
      </div>
    </div>
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

const fieldValueStyle: CSSProperties = {
  color: PALETTE.textHi,
  fontSize: 13,
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

const pendingAnchorStyle = (state: InlineDiffState): CSSProperties => ({
  position: "relative",
  padding: 12,
  borderRadius: 8,
  background:
    state === "accepted"
      ? "rgba(61, 220, 151, 0.12)"
      : state === "rejected"
        ? "rgba(239, 90, 90, 0.10)"
        : PALETTE.limeBgSoft,
  border: `1px solid ${
    state === "accepted"
      ? "#3ddc97"
      : state === "rejected"
        ? "#ef5a5a"
        : PALETTE.lime
  }`,
  display: "flex",
  flexDirection: "column",
  gap: 8,
});

const pendingLabelStyle: CSSProperties = {
  fontSize: 10.5,
  fontWeight: 700,
  letterSpacing: 0.7,
  color: PALETTE.lime,
};

const pendingBodyStyle: CSSProperties = {
  fontSize: 14,
  lineHeight: 1.55,
  whiteSpace: "pre-wrap",
  color: PALETTE.textHi,
  minHeight: 22,
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
