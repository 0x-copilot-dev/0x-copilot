import type { CSSProperties, ReactElement, ReactNode } from "react";

const RAIL_WIDTH = 380;

export interface RightRailProps {
  readonly open: boolean;
  readonly onToggle: () => void;
  /**
   * Optional header title — defaults to "Atlas conversation". Lets the
   * host re-label the right rail per destination without forking the
   * shell component.
   */
  readonly title?: string;
  /**
   * Optional content. When undefined, a neutral empty-state is rendered
   * so the rail never shows hardcoded "Placeholder message" lines.
   */
  readonly children?: ReactNode;
}

export function RightRail({
  open,
  onToggle,
  title,
  children,
}: RightRailProps): ReactElement {
  const headerLabel = title ?? "Atlas conversation";
  const containerStyle: CSSProperties = {
    width: open ? RAIL_WIDTH : 0,
    minWidth: open ? RAIL_WIDTH : 0,
    height: "100%",
    overflow: "hidden",
    position: "relative",
    backgroundColor: "var(--color-bg-elevated)",
    borderLeft: open ? "1px solid var(--color-border)" : "none",
    color: "var(--color-text)",
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
    borderBottom: "1px solid var(--color-border)",
    fontSize: 13,
    fontWeight: 600,
    color: "var(--color-text)",
  };
  const bodyStyle: CSSProperties = {
    flex: 1,
    minHeight: 0,
    overflowY: "auto",
  };
  const toggleEdgeStyle: CSSProperties = {
    position: "absolute",
    top: 12,
    left: -28,
    width: 24,
    height: 24,
    background: "var(--color-bg-elevated)",
    border: "1px solid var(--color-border)",
    color: "var(--color-text)",
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
    color: "var(--color-text-muted)",
    cursor: "pointer",
    fontSize: 13,
    padding: 0,
  };

  if (!open) {
    return (
      <aside
        aria-label={`${headerLabel} (collapsed)`}
        data-component="right-rail"
        data-state="closed"
        style={{ position: "relative", width: 0 }}
      >
        <button
          type="button"
          aria-label={`Open ${headerLabel}`}
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
      aria-label={headerLabel}
      data-component="right-rail"
      data-state="open"
      style={containerStyle}
    >
      <button
        type="button"
        aria-label={`Close ${headerLabel}`}
        aria-expanded="true"
        data-testid="right-rail-toggle"
        onClick={onToggle}
        style={toggleEdgeStyle}
      >
        {">"}
      </button>
      <div style={headerStyle}>
        <span>{headerLabel}</span>
        <button
          type="button"
          aria-label={`${headerLabel} menu`}
          style={toggleInsideStyle}
        >
          ⋯
        </button>
      </div>
      <div style={bodyStyle} data-testid="right-rail-body">
        {children ?? <EmptyState />}
      </div>
    </aside>
  );
}

function EmptyState(): ReactElement {
  return (
    <p
      style={{
        margin: 0,
        padding: "24px 16px",
        color: "var(--color-text-subtle)",
        fontSize: 12.5,
        lineHeight: 1.55,
      }}
      data-testid="right-rail-empty"
    >
      Per-destination context surfaces here.
    </p>
  );
}

export { RAIL_WIDTH as RIGHT_RAIL_WIDTH };
