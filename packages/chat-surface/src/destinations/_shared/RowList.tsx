// <RowList> — the design `.rowlist` card wrapping list rows.
//
// Source: docs/plan/frontend-parity-v3/PRD-G-destination-parity.md (FR-G.1).
// ONE bordered (1px --color-border), rounded (--radius-md), --color-surface card
// per group. Rows are separated by internal hairline borders (a border-bottom on
// every row but the last), so a day of Activity runs / a Chats bucket reads as a
// single card, not a stack of chips.
//
// Slot-driven like `shell/DocList`: pass `items` + `renderRow`; RowList owns the
// `<li>` wrapper, keys, and the hairline separators. Substrate-agnostic;
// token-driven only.

import type { CSSProperties, ReactElement, ReactNode } from "react";

export interface RowListProps<T> {
  readonly items: ReadonlyArray<T>;
  /** Render a single row's content (typically a <Row>). Wrapped in an `<li>`. */
  readonly renderRow: (item: T, index: number) => ReactNode;
  /** Stable React key per row; defaults to the index. */
  readonly keyFor?: (item: T, index: number) => string;
  /** Accessible label for the list. */
  readonly ariaLabel?: string;
  readonly className?: string;
  readonly style?: CSSProperties;
  readonly "data-testid"?: string;
}

const cardStyle: CSSProperties = {
  margin: 0,
  padding: 0,
  listStyle: "none",
  border: "1px solid var(--color-border)",
  borderRadius: "var(--radius-md)",
  backgroundColor: "var(--color-surface)",
  overflow: "hidden",
};

const rowItemStyle = (isLast: boolean): CSSProperties => ({
  listStyle: "none",
  // Internal hairline between rows; the last row carries none (the card's own
  // bottom border closes it off).
  borderBottom: isLast ? undefined : "1px solid var(--color-border)",
});

export function RowList<T>({
  items,
  renderRow,
  keyFor,
  ariaLabel,
  className,
  style,
  "data-testid": dataTestId = "row-list",
}: RowListProps<T>): ReactElement {
  return (
    <ul
      className={className === undefined ? "rowlist" : `rowlist ${className}`}
      aria-label={ariaLabel}
      data-testid={dataTestId}
      style={{ ...cardStyle, ...style }}
    >
      {items.map((item, index) => (
        <li
          key={keyFor !== undefined ? keyFor(item, index) : index}
          style={rowItemStyle(index === items.length - 1)}
          data-testid="row-list-item"
        >
          {renderRow(item, index)}
        </li>
      ))}
    </ul>
  );
}
