import type { CSSProperties, ReactElement } from "react";

import type { CanvasLifecycleState } from "./canvasLifecycle";

/** Exact, test-pinned copy for a terminal narrative-only Studio run. */
export const CHAT_ONLY_CANVAS_COPY =
  "This run completed in chat. No artifact was created.";

export function CanvasLifecyclePanel(props: {
  readonly lifecycle: CanvasLifecycleState;
  readonly failure: string | null;
  readonly onRetry?: () => void;
}): ReactElement | null {
  const content = contentFor(props.lifecycle, props.failure);
  if (content === null) return null;
  return (
    <section
      aria-live={props.lifecycle === "failed" ? "assertive" : "polite"}
      data-testid="canvas-lifecycle-panel"
      data-lifecycle={props.lifecycle}
      style={panelStyle}
    >
      <p style={eyebrowStyle}>{content.eyebrow}</p>
      <h2 style={titleStyle}>{content.title}</h2>
      <p style={copyStyle}>{content.copy}</p>
      {props.lifecycle === "failed" && props.onRetry !== undefined ? (
        <button type="button" onClick={props.onRetry} style={retryStyle}>
          Retry run
        </button>
      ) : null}
    </section>
  );
}

function contentFor(
  lifecycle: CanvasLifecycleState,
  failure: string | null,
): { eyebrow: string; title: string; copy: string } | null {
  switch (lifecycle) {
    case "assembling":
      return {
        eyebrow: "RUN IN PROGRESS",
        title: "Preparing this run",
        copy: "Activity will appear here if this run creates something to review.",
      };
    case "chat_only":
      return {
        eyebrow: "CHAT RESPONSE",
        title: "Answered in chat",
        copy: CHAT_ONLY_CANVAS_COPY,
      };
    case "parked":
      return {
        eyebrow: "WAITING",
        title: "Waiting for your approval or access",
        copy: "Review the compact cards in Focus or open Studio to continue.",
      };
    case "failed":
      return {
        eyebrow: "RUN INTERRUPTED",
        title: "This run needs attention",
        copy: failure ?? "The run could not finish. You can retry safely.",
      };
    case "complete_empty":
      return {
        eyebrow: "RUN COMPLETE",
        title: "Nothing to open",
        copy: "This run finished without an artifact.",
      };
    case "presenting":
      return null;
  }
}

const panelStyle: CSSProperties = {
  display: "grid",
  // This is the canvas' empty state, not a card inside that canvas. Keeping it
  // transparent and borderless preserves the Studio hierarchy: the canvas
  // itself supplies the frame while this panel simply centers the honest state.
  gap: "normal",
  alignContent: "center",
  alignItems: "center",
  flex: "1 1 auto",
  minHeight: "100%",
  padding: 26,
  border: 0,
  borderRadius: 0,
  background: "transparent",
};
const eyebrowStyle: CSSProperties = {
  margin: 0,
  color: "var(--color-text-muted, #9aa1af)",
  fontFamily: "var(--font-mono)",
  fontWeight: 700,
};
const titleStyle: CSSProperties = {
  margin: 0,
  color: "var(--color-text, #f4f5f6)",
};
const copyStyle: CSSProperties = {
  margin: 0,
  color: "var(--color-text-muted, #9aa1af)",
};
const retryStyle: CSSProperties = {
  justifySelf: "start",
  border: "1px solid var(--color-border, #30343d)",
  borderRadius: "var(--radius-sm, 6px)",
  padding: "var(--space-2, 8px) var(--space-3, 12px)",
  color: "var(--color-text, #f4f5f6)",
  background: "var(--color-surface-raised, #202530)",
  cursor: "pointer",
};
