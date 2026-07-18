import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { useTransport } from "../providers/TransportProvider";

export interface MentionCandidate {
  readonly slug: string;
  readonly label: string;
  readonly kind?: string;
}

export interface MentionPopoverProps {
  readonly open: boolean;
  readonly query: string;
  readonly onSelect: (candidate: MentionCandidate) => void;
  readonly onClose: () => void;
  readonly portalTarget?: HTMLElement;
  readonly anchorRect?: { readonly top: number; readonly left: number };
}

type LoadState =
  | { readonly status: "idle" }
  | { readonly status: "loading" }
  | {
      readonly status: "ready";
      readonly candidates: ReadonlyArray<MentionCandidate>;
    }
  | { readonly status: "error" };

interface MentionResponse {
  readonly candidates?: ReadonlyArray<MentionCandidate>;
}

export function MentionPopover(props: MentionPopoverProps): ReactNode {
  const { open, query, onSelect, onClose, portalTarget, anchorRect } = props;
  const transport = useTransport();
  const [state, setState] = useState<LoadState>({ status: "idle" });

  useEffect(() => {
    if (!open) {
      setState({ status: "idle" });
      return;
    }
    let cancelled = false;
    setState({ status: "loading" });
    transport
      .request<MentionResponse>({
        method: "GET",
        path: "/v1/mentions",
        query: { q: query },
      })
      .then((res) => {
        if (cancelled) {
          return;
        }
        setState({ status: "ready", candidates: res.candidates ?? [] });
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
  }, [open, query, transport]);

  if (!open) {
    return null;
  }

  const positionedStyle: CSSProperties = anchorRect
    ? {
        ...panelStyle,
        position: "absolute",
        top: anchorRect.top,
        left: anchorRect.left,
        boxShadow: "var(--shadow-soft)",
      }
    : panelStyle;

  const panel = (
    <div
      role="listbox"
      aria-label="Mention candidates"
      data-testid="mention-popover"
      style={positionedStyle}
    >
      <MentionPopoverBody
        state={state}
        onSelect={(c) => {
          onSelect(c);
          onClose();
        }}
      />
    </div>
  );

  if (portalTarget !== undefined) {
    return createPortal(panel, portalTarget);
  }
  return panel;
}

interface BodyProps {
  readonly state: LoadState;
  readonly onSelect: (candidate: MentionCandidate) => void;
}

function MentionPopoverBody(props: BodyProps): ReactNode {
  const { state, onSelect } = props;

  if (state.status === "idle" || state.status === "loading") {
    return (
      <div role="status" style={statusStyle} data-testid="mention-loading">
        Searching…
      </div>
    );
  }
  if (state.status === "error") {
    return (
      <div role="alert" style={statusStyle} data-testid="mention-error">
        Failed to load mentions.
      </div>
    );
  }
  if (state.candidates.length === 0) {
    return (
      <div role="status" style={statusStyle} data-testid="mention-empty">
        No matches.
      </div>
    );
  }
  return (
    <ul style={listStyle}>
      {state.candidates.map((c) => (
        <li key={c.slug} style={listItemStyle}>
          <button
            type="button"
            role="option"
            aria-selected="false"
            onClick={() => onSelect(c)}
            style={rowStyle}
            data-testid={`mention-row-${c.slug}`}
          >
            <span style={labelStyle}>@{c.label}</span>
            {c.kind ? <span style={kindStyle}>{c.kind}</span> : null}
          </button>
        </li>
      ))}
    </ul>
  );
}

const panelStyle: CSSProperties = {
  background: "var(--color-bg-elevated)",
  border: "1px solid var(--color-border)",
  borderRadius: 10,
  padding: 6,
  width: 240,
  color: "var(--color-text)",
  fontFamily: "var(--font-sans)",
  fontSize: "var(--font-size-sm)",
  display: "flex",
  flexDirection: "column",
  gap: 4,
};

const statusStyle: CSSProperties = {
  padding: "8px 10px",
  color: "var(--color-text-muted)",
  fontSize: "var(--font-size-xs)",
};

const listStyle: CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: 0,
  display: "flex",
  flexDirection: "column",
  gap: 2,
  maxHeight: 240,
  overflowY: "auto",
};

const listItemStyle: CSSProperties = {
  margin: 0,
};

const rowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  width: "100%",
  background: "transparent",
  border: "none",
  borderRadius: 6,
  padding: "6px 10px",
  color: "var(--color-text)",
  cursor: "pointer",
  textAlign: "left",
  fontSize: "var(--font-size-sm)",
};

const labelStyle: CSSProperties = {
  flex: 1,
};

const kindStyle: CSSProperties = {
  fontSize: "var(--font-size-2xs)",
  color: "var(--color-text-muted)",
  textTransform: "uppercase",
  letterSpacing: 0.4,
};
