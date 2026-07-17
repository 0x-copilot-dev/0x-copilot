// <DocList> — list-of-document rows primitive.
//
// Source: destinations-master-prd §4.1. Two consumption modes:
//
// 1. Snapshot-driven: pass `refs: ReadonlyArray<ItemRefSnapshot>`. The
//    grid renders one `<li><ItemLink ref={…}/></li>` per ref, using the
//    snapshot's `display_label` as a low-cost fallback while the
//    resolver runs.
//
// 2. Slot-driven: pass `items` + `renderRow`. Destinations that need
//    bespoke row chrome (icons, side actions) write their own row but
//    still get the consistent `<ul>` shell + spacing.

import type { CSSProperties, ReactElement, ReactNode } from "react";

import type { ItemRefSnapshot } from "@0x-copilot/api-types";

import { ItemLink } from "../refs/ItemLink";

interface DocListBaseProps {
  /** Optional accessible label for the list. */
  readonly ariaLabel?: string;
  readonly className?: string;
}

interface DocListSnapshotProps extends DocListBaseProps {
  readonly refs: ReadonlyArray<ItemRefSnapshot>;
}

interface DocListSlotProps<T> extends DocListBaseProps {
  readonly items: ReadonlyArray<T>;
  /** Render a single row's inner content. Each row is wrapped in `<li>`. */
  readonly renderRow: (item: T, index: number) => ReactNode;
  /** Key fn for React identity; defaults to index when not supplied. */
  readonly keyFor?: (item: T, index: number) => string;
}

const listStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  margin: 0,
  padding: 0,
  listStyle: "none",
};

const rowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  padding: "8px 10px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border, #232325)",
  backgroundColor: "var(--color-bg-elevated, #161617)",
  color: "var(--color-text, #ededee)",
  fontSize: "var(--font-size-sm, 13px)",
};

// Snapshot-driven overload — declared first so TS picks it when `refs`
// is present.
export function DocList(props: DocListSnapshotProps): ReactElement;
export function DocList<T>(props: DocListSlotProps<T>): ReactElement;
export function DocList<T>(
  props: DocListSnapshotProps | DocListSlotProps<T>,
): ReactElement {
  if ("refs" in props) {
    return (
      <ul
        style={listStyle}
        className={props.className}
        aria-label={props.ariaLabel}
        data-testid="doc-list"
        data-mode="refs"
      >
        {props.refs.map((snapshot, index) => (
          <li
            key={`${snapshot.ref.kind}:${snapshot.ref.id}:${index}`}
            style={rowStyle}
            data-testid="doc-list-row"
          >
            <ItemLink
              ref={snapshot.ref}
              deletedLabel={
                snapshot.display_label !== undefined
                  ? `deleted: ${snapshot.display_label}`
                  : undefined
              }
            />
          </li>
        ))}
      </ul>
    );
  }
  return (
    <ul
      style={listStyle}
      className={props.className}
      aria-label={props.ariaLabel}
      data-testid="doc-list"
      data-mode="slot"
    >
      {props.items.map((item, index) => (
        <li
          key={props.keyFor !== undefined ? props.keyFor(item, index) : index}
          style={rowStyle}
          data-testid="doc-list-row"
        >
          {props.renderRow(item, index)}
        </li>
      ))}
    </ul>
  );
}
