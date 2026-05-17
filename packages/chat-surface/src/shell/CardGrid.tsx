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
  /** Minimum card width in pixels. Default 260. */
  readonly minCardWidth?: number;
  /** Gap between cards in pixels. Default 12. */
  readonly gap?: number;
  readonly className?: string;
  /** Optional accessible label when the grid is its own region. */
  readonly ariaLabel?: string;
}

export function CardGrid({
  children,
  minCardWidth = 260,
  gap = 12,
  className,
  ariaLabel,
}: CardGridProps): ReactElement {
  const style: CSSProperties = {
    display: "grid",
    gridTemplateColumns: `repeat(auto-fill, minmax(${minCardWidth}px, 1fr))`,
    gap,
    width: "100%",
  };
  return (
    <div
      style={style}
      className={className}
      data-testid="card-grid"
      data-min-card-width={minCardWidth}
      role={ariaLabel !== undefined ? "region" : undefined}
      aria-label={ariaLabel}
    >
      {children}
    </div>
  );
}
