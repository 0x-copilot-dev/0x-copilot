import type { CSSProperties, ReactElement } from "react";

import type { DiffHunk } from "./wordDiff";

// Presentational renderer for a `wordDiff` result (PRD-06). Paints each hunk as a
// semantic <del>/<ins> (or plain span for equal runs) with palette-consistent,
// theme-aware styling: deletes are struck-through + danger-tinted, inserts are
// underlined + accent/ghost-tinted. Color is never the only signal — the
// strikethrough/underline carry the meaning for non-colour vision.
//
// Pure display. The optional `onHunkToggle` is the PRD-09 seam: absent, the hunks
// render with no interactivity at all; present, changed hunks call it on click.
// PRD-06 wires nothing beyond that callback — no accept/reject state, buttons, or
// keyboard affordances (those land in PRD-09).

export interface DiffTextProps {
  readonly hunks: readonly DiffHunk[];
  /** PRD-09 seam. When provided, changed (<ins>/<del>) hunks invoke it with the
   * hunk `id` on click. When omitted, the diff is inert, non-interactive text. */
  readonly onHunkToggle?: (id: string) => void;
}

export function DiffText(props: DiffTextProps): ReactElement {
  const { hunks, onHunkToggle } = props;
  let insertions = 0;
  let deletions = 0;
  for (const hunk of hunks) {
    if (hunk.kind === "insert") {
      insertions += 1;
    } else if (hunk.kind === "delete") {
      deletions += 1;
    }
  }
  return (
    <span
      role="group"
      aria-label={summaryLabel(insertions, deletions)}
      data-testid="diff-text"
      style={containerStyle}
    >
      {hunks.map((hunk) => (
        <HunkView key={hunk.id} hunk={hunk} onToggle={onHunkToggle} />
      ))}
    </span>
  );
}

function summaryLabel(insertions: number, deletions: number): string {
  const ins = `${insertions} insertion${insertions === 1 ? "" : "s"}`;
  const del = `${deletions} deletion${deletions === 1 ? "" : "s"}`;
  return `${ins}, ${del}`;
}

interface HunkViewProps {
  readonly hunk: DiffHunk;
  readonly onToggle?: (id: string) => void;
}

function HunkView(props: HunkViewProps): ReactElement {
  const { hunk, onToggle } = props;
  if (hunk.kind === "equal") {
    return (
      <span style={equalStyle} data-testid="diff-equal">
        {hunk.text}
      </span>
    );
  }
  const interactive = typeof onToggle === "function";
  const onClick = interactive ? () => onToggle?.(hunk.id) : undefined;
  if (hunk.kind === "insert") {
    return (
      <ins
        data-testid="diff-insert"
        data-hunk-id={hunk.id}
        style={interactive ? insertInteractiveStyle : insertStyle}
        onClick={onClick}
      >
        {hunk.text}
      </ins>
    );
  }
  return (
    <del
      data-testid="diff-delete"
      data-hunk-id={hunk.id}
      style={interactive ? deleteInteractiveStyle : deleteStyle}
      onClick={onClick}
    >
      {hunk.text}
    </del>
  );
}

const containerStyle: CSSProperties = {
  whiteSpace: "pre-wrap",
  overflowWrap: "anywhere",
  wordBreak: "break-word",
};

const equalStyle: CSSProperties = {
  color: "inherit",
};

const insertStyle: CSSProperties = {
  textDecoration: "underline",
  textDecorationColor: "var(--color-accent)",
  background:
    "var(--color-accent-soft, color-mix(in srgb, var(--color-accent) 18%, transparent))",
  borderRadius: 3,
  padding: "0 2px",
};

const deleteStyle: CSSProperties = {
  textDecoration: "line-through",
  textDecorationColor: "var(--color-danger)",
  color: "var(--color-text-danger, var(--color-danger))",
  background: "var(--color-bg-danger-subtle, transparent)",
  borderRadius: 3,
  padding: "0 2px",
};

const insertInteractiveStyle: CSSProperties = {
  ...insertStyle,
  cursor: "pointer",
};

const deleteInteractiveStyle: CSSProperties = {
  ...deleteStyle,
  cursor: "pointer",
};
