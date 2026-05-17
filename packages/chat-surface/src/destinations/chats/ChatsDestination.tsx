import { useState, type CSSProperties, type ReactElement } from "react";

import { ChatsSidebar } from "./ChatsSidebar";

// Design tokens (see packages/design-system/src/styles.css). Settings →
// Appearance theme/accent changes flow through via the var(--color-…) refs.
const CANVAS_BACKGROUND = "var(--color-bg)";
const TEXT_SECONDARY = "var(--color-text-muted)";

export function ChatsDestination(): ReactElement {
  const [fullscreen, setFullscreen] = useState(false);

  const outerStyle: CSSProperties = {
    width: "100%",
    height: "100%",
    minHeight: 0,
    display: "grid",
    gridTemplateColumns: fullscreen ? "0 1fr" : "256px 1fr",
    gridTemplateRows: "100%",
  };
  const canvasStyle: CSSProperties = {
    minWidth: 0,
    minHeight: 0,
    backgroundColor: CANVAS_BACKGROUND,
    color: TEXT_SECONDARY,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    fontSize: 13,
  };

  return (
    <div
      data-component="chats-destination"
      data-fullscreen={fullscreen ? "on" : "off"}
      style={outerStyle}
    >
      <ChatsSidebar
        fullscreen={fullscreen}
        onFullscreenChange={setFullscreen}
      />
      <div style={canvasStyle} data-testid="thread-canvas-placeholder">
        ThreadCanvas mounts here (Phase 2-B).
      </div>
    </div>
  );
}
