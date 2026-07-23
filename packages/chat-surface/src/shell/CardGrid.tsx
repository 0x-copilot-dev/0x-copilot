// <CardGrid> — responsive grid wrapper used by every destination that
// renders cards (Home pinned chats, Library knowledge cards, Tools).
//
// Source: destinations-master-prd §4.1. Hosts pass arbitrary children;
// the grid lays them out via `auto-fill` with a configurable min width
// and gap. The grid is layout-only — it does NOT add card chrome (the
// caller's children own their own card styling).

import type { CSSProperties, ReactElement, ReactNode } from "react";

export interface CardGridProps {
  readonly children: ReactNode;
  /** Minimum card width in pixels. Default 260. Ignored when `variant="grid3"`. */
  readonly minCardWidth?: number;
  /** Gap between cards in pixels. Default 12. Ignored when `variant="grid3"`. */
  readonly gap?: number;
  readonly className?: string;
  /** Optional accessible label when the grid is its own region. */
  readonly ariaLabel?: string;
  /**
   * Layout mode (PRD-10 D7). `"auto-fill"` (default — no existing consumer
   * changes) keeps the `repeat(auto-fill, minmax(min, 1fr))` responsive grid.
   * `"grid3"` emits `className="ui-grid3"` and NO inline
   * `gridTemplateColumns`/`gap`, so the design's fixed 3-column grid that
   * collapses to 1-up below 900px (`copilot.css:1672-1682`) applies — a media
   * query cannot be written in an inline style object, so the rule lives in the
   * kit and this variant opts into it.
   */
  readonly variant?: "auto-fill" | "grid3";
}

export function CardGrid({
  children,
  minCardWidth = 260,
  gap = 12,
  className,
  ariaLabel,
  variant = "auto-fill",
}: CardGridProps): ReactElement {
  const grid3 = variant === "grid3";
  const style: CSSProperties = grid3
    ? { width: "100%" }
    : {
        display: "grid",
        gridTemplateColumns: `repeat(auto-fill, minmax(${minCardWidth}px, 1fr))`,
        gap,
        width: "100%",
      };
  const mergedClassName = grid3
    ? className === undefined
      ? "ui-grid3"
      : `ui-grid3 ${className}`
    : className;
  return (
    <div
      style={style}
      className={mergedClassName}
      data-testid="card-grid"
      data-min-card-width={minCardWidth}
      data-variant={variant}
      role={ariaLabel !== undefined ? "region" : undefined}
      aria-label={ariaLabel}
    >
      {children}
    </div>
  );
}
