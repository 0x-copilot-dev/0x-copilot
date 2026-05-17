import type { CSSProperties, ReactElement } from "react";

const RAIL_WIDTH = 380;
const BACKGROUND = "#16181F";
const BORDER = "#22252E";
const TEXT_PRIMARY = "#E4E5E9";
const TEXT_SECONDARY = "#7E8492";

export interface RightRailProps {
  readonly open: boolean;
  readonly onToggle: () => void;
}

export function RightRail({ open, onToggle }: RightRailProps): ReactElement {
  const containerStyle: CSSProperties = {
    width: open ? RAIL_WIDTH : 0,
    minWidth: open ? RAIL_WIDTH : 0,
    height: "100%",
    overflow: "hidden",
    position: "relative",
    backgroundColor: BACKGROUND,
    borderLeft: open ? `1px solid ${BORDER}` : "none",
    color: TEXT_PRIMARY,
    boxSizing: "border-box",
    display: "flex",
    flexDirection: "column",
    transition: "width 120ms ease, min-width 120ms ease",
  };
  const headerStyle: CSSProperties = {
    height: 44,
    minHeight: 44,
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "0 16px",
    borderBottom: `1px solid ${BORDER}`,
    fontSize: 13,
    fontWeight: 600,
  };
  const listStyle: CSSProperties = {
    listStyle: "none",
    margin: 0,
    padding: "12px 16px",
    color: TEXT_SECONDARY,
    fontSize: 13,
    display: "flex",
    flexDirection: "column",
    gap: 8,
  };
  const toggleEdgeStyle: CSSProperties = {
    position: "absolute",
    top: 12,
    left: -28,
    width: 24,
    height: 24,
    background: BACKGROUND,
    border: `1px solid ${BORDER}`,
    color: TEXT_PRIMARY,
    borderRadius: 6,
    cursor: "pointer",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    padding: 0,
  };
  const toggleInsideStyle: CSSProperties = {
    background: "transparent",
    border: "none",
    color: TEXT_SECONDARY,
    cursor: "pointer",
    fontSize: 13,
    padding: 0,
  };

  if (!open) {
    return (
      <aside
        aria-label="Atlas conversation (collapsed)"
        data-component="right-rail"
        data-state="closed"
        style={{ position: "relative", width: 0 }}
      >
        <button
          type="button"
          aria-label="Open Atlas conversation"
          aria-expanded="false"
          data-testid="right-rail-toggle"
          onClick={onToggle}
          style={{ ...toggleEdgeStyle, left: -32 }}
        >
          {"<"}
        </button>
      </aside>
    );
  }

  return (
    <aside
      aria-label="Atlas conversation"
      data-component="right-rail"
      data-state="open"
      style={containerStyle}
    >
      <button
        type="button"
        aria-label="Close Atlas conversation"
        aria-expanded="true"
        data-testid="right-rail-toggle"
        onClick={onToggle}
        style={toggleEdgeStyle}
      >
        {">"}
      </button>
      <div style={headerStyle}>
        <span>Atlas conversation</span>
        <button
          type="button"
          aria-label="Atlas conversation menu"
          style={toggleInsideStyle}
        >
          ⋯
        </button>
      </div>
      <ul style={listStyle} data-testid="right-rail-placeholder-list">
        <li>Placeholder message 1</li>
        <li>Placeholder message 2</li>
        <li>Placeholder message 3</li>
      </ul>
    </aside>
  );
}
