// TcCompactionDivider — the context-compaction boundary, as a QUIET rule. 🎨
//
// This is deliberately not a card, and the distinction is the whole design.
// Every card in this transcript is something the reader can act on or open: a
// tool call, an approval, an artifact. A compaction is none of those. It is a
// statement about the transcript itself — "from here, the model no longer held
// all of that" — and the right shape for a statement about a boundary is the
// boundary: a hairline across the column with the sentence sitting in it.
//
// So it carries no frame, no background, no chevron and no control. The one
// thing it must do is be READABLE at the moment the user is asking why the
// agent forgot something, which is why the label is centred in the rule rather
// than tucked into a corner.
//
// THE LABEL IS THE SERVER'S. `display_title` is projected at the presentation
// boundary from the counts the producer validated, so the sentence and the
// numbers beside it cannot disagree. This component never re-words it and never
// derives one from the event name — the same rule the rest of the cockpit
// follows for activity titles.
//
// The counts render as a second, quieter segment (`12.4k → 380`) and are
// dropped entirely when the wire carried only one of them. A one-sided arrow
// would read as a measurement nobody made.
//
// Kit-only styling; framework-agnostic (no window/document/fetch). Presentation
// lives in `review-surfaces.css` under package-owned `tc-compaction*` class
// names, never inline: a host stylesheet re-declaring a package class is how a
// shared surface ends up right on web and wrong on desktop, and the names here
// exist nowhere else in the product.

import type { ReactElement } from "react";

export interface TcCompactionDividerProps {
  /**
   * The server-projected line ("Compacted 8.6k tokens of read_file output").
   * Required — a divider with nothing to say is a rule across the transcript
   * that the reader cannot account for.
   */
  readonly label: string;
  /** Estimated tokens of the source, before the runtime bounded it. */
  readonly beforeTokens?: number | null;
  /** Estimated tokens of what the model was handed instead. */
  readonly afterTokens?: number | null;
  /** Overridden per-notice by the transcript; the default is a standalone mount. */
  readonly testId?: string;
}

/**
 * Render a token count the way a reader scans it, not exactly.
 *
 * Mirrors `Messages.Event._compact_token_count` on the server deliberately: the
 * title says "8.6k" and the detail beside it must not say "8,634", or the two
 * halves of one row read as two different measurements.
 */
function compactCount(tokens: number): string {
  if (tokens < 1000) return String(tokens);
  const thousands = tokens / 1000;
  if (thousands < 10) return `${thousands.toFixed(1).replace(/\.0$/, "")}k`;
  return `${Math.round(thousands)}k`;
}

export function TcCompactionDivider({
  label,
  beforeTokens = null,
  afterTokens = null,
  testId = "tc-compaction",
}: TcCompactionDividerProps): ReactElement {
  // BOTH or NEITHER. The arrow is a claim about a change, and a change needs
  // two ends; printing "→ 380" over a missing `before_tokens` would invent the
  // half the wire did not carry.
  const counts =
    beforeTokens === null || afterTokens === null
      ? null
      : `${compactCount(beforeTokens)} → ${compactCount(afterTokens)}`;

  return (
    <div
      className="tc-compaction"
      data-testid={testId}
      // Not `role="separator"`: this rule carries the sentence that explains it,
      // and a separator's accessible name is not announced by every AT. A plain
      // group with a label is read out whole, which is the point of drawing it.
      role="group"
      aria-label={label}
    >
      <span aria-hidden="true" className="tc-compaction__rule" />
      <span className="tc-compaction__label" data-testid={`${testId}-label`}>
        {label}
      </span>
      {counts === null ? null : (
        <span
          className="tc-compaction__counts"
          data-testid={`${testId}-counts`}
        >
          {counts}
        </span>
      )}
      <span aria-hidden="true" className="tc-compaction__rule" />
    </div>
  );
}
