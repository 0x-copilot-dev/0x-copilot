import type { CSSProperties, ReactElement } from "react";

import type { CanvasLifecycleState } from "./canvasLifecycle";

/** Exact, test-pinned copy for a terminal narrative-only Studio run. */
export const CHAT_ONLY_CANVAS_COPY =
  "This run completed in chat. No artifact was created.";

/**
 * The canvas' empty state. It reports on the CANVAS — never on the run.
 *
 * It carries no failure state and no action. It previously rendered a
 * "This run needs attention" alarm with a "Retry run" button; both are gone.
 * The alarm contradicted the chat pane whenever the agent recovered, and the
 * button was wired to an SSE reconnect, so it could not retry anything. A
 * terminal run failure is now reported once, in the chat stream.
 */
export function CanvasLifecyclePanel(props: {
  readonly lifecycle: CanvasLifecycleState;
}): ReactElement | null {
  const content = contentFor(props.lifecycle);
  if (content === null) return null;
  return (
    <section
      aria-live="polite"
      data-testid="canvas-lifecycle-panel"
      data-lifecycle={props.lifecycle}
      style={panelStyle}
    >
      <p style={eyebrowStyle}>{content.eyebrow}</p>
      <h2 style={titleStyle}>{content.title}</h2>
      <p style={copyStyle}>{content.copy}</p>
    </section>
  );
}

function contentFor(
  lifecycle: CanvasLifecycleState,
): { eyebrow: string; title: string; copy: string } | null {
  switch (lifecycle) {
    case "assembling":
      // "Preparing this run" was a claim about the RUN, which is the one thing
      // this panel promises never to make — and it was wrong in the ordinary
      // case: seen live with `Agents 6` in the tab strip beside it, six
      // subagents working while the largest surface in the app said the run was
      // being prepared. The canvas is what has nothing yet; say that instead.
      return {
        eyebrow: "RUN IN PROGRESS",
        title: "Nothing to review yet",
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
