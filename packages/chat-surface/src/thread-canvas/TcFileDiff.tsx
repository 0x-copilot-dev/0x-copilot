// A unified-diff view for a file change the agent made.
//
// STYLING IS INLINE ON PURPOSE. Class-based CSS authored in a package has
// stranded on the desktop host before — the host stylesheet simply never
// imported it, so the rules existed, the unit tests passed, and the live app
// rendered unstyled text (PR #459). `ToolCallCard` survived that class of bug
// precisely because its styles are inline, and this component sits beside it,
// so it follows the same rule. Only the pieces that genuinely need a selector
// (hover, reduced motion) go in a scoped <style>.

import type { CSSProperties, ReactElement } from "react";

import type { DiffHunk, DiffLine, FileDiff } from "./lineDiff";

export interface TcFileDiffProps {
  readonly diff: FileDiff;
  /** Absolute path of the edited file, rendered as the diff's header. */
  readonly filePath?: string;
  /**
   * Rows rendered before the view stops and reports the remainder. The
   * transcript is not a file viewer: a large `write_file` would otherwise mount
   * thousands of rows into an already un-virtualized transcript.
   */
  readonly maxRows?: number;
  /**
   * Did the change actually land?
   *
   * The diff is built from the CALL'S ARGUMENTS, not from its result — the
   * runtime returns one prose sentence, so the arguments are the only place the
   * before/after text exists. That means a REFUSED or FAILED edit carries a
   * perfectly well-formed diff, and rendering it unqualified states that a
   * change happened when it did not. A live run made this concrete: both file
   * tools were refused for want of a workspace grant, and the card still had a
   * confident red/green hunk inside it.
   *
   * So the card says which it is. `false` keeps the diff — what was ATTEMPTED
   * is worth reading, especially on a refusal — but labels it and drops the
   * change-colour, so it can never be mistaken for an applied edit.
   */
  readonly applied?: boolean;
  readonly testId?: string;
}

const DEFAULT_MAX_ROWS = 160;

export function TcFileDiff({
  diff,
  filePath,
  maxRows = DEFAULT_MAX_ROWS,
  applied = true,
  testId = "tc-file-diff",
}: TcFileDiffProps): ReactElement | null {
  if (diff.hunks.length === 0) return null;

  const budget = takeRows(diff.hunks, maxRows);

  return (
    <div
      style={frameStyle}
      data-testid={testId}
      data-applied={applied ? "true" : "false"}
      data-approximate={diff.approximate ? "true" : "false"}
    >
      <div style={headerStyle}>
        {filePath !== undefined ? (
          <span
            style={pathStyle}
            title={filePath}
            data-testid={`${testId}-path`}
          >
            {filePath}
          </span>
        ) : null}
        {!applied ? (
          <span style={notAppliedStyle} data-testid={`${testId}-not-applied`}>
            not applied
          </span>
        ) : null}
        <span style={countsStyle} data-testid={`${testId}-counts`}>
          <span style={applied ? addCountStyle : mutedCountStyle}>
            +{diff.additions}
          </span>
          <span style={applied ? delCountStyle : mutedCountStyle}>
            −{diff.deletions}
          </span>
        </span>
      </div>

      <div style={scrollStyle}>
        {budget.hunks.map((hunk, index) => (
          <div key={`${hunk.oldStart}-${hunk.newStart}-${index}`}>
            {index > 0 ? (
              <div style={hunkRuleStyle} aria-hidden="true">
                ⋮
              </div>
            ) : null}
            {hunk.lines.map((line, lineIndex) => (
              <DiffRow key={lineIndex} line={line} applied={applied} />
            ))}
          </div>
        ))}
      </div>

      {budget.omitted > 0 ? (
        <p style={footNoteStyle} data-testid={`${testId}-omitted`}>
          {budget.omitted} more {budget.omitted === 1 ? "line" : "lines"} not
          shown
        </p>
      ) : null}

      {diff.approximate ? (
        <p style={footNoteStyle} data-testid={`${testId}-approximate`}>
          File too large to diff exactly — shown as a full replacement
        </p>
      ) : null}
    </div>
  );
}

function DiffRow({
  line,
  applied,
}: {
  readonly line: DiffLine;
  readonly applied: boolean;
}): ReactElement {
  const sign = line.kind === "add" ? "+" : line.kind === "remove" ? "−" : " ";
  return (
    <div style={rowStyle(line.kind, applied)} data-diff-kind={line.kind}>
      <span style={gutterStyle}>{line.oldLine ?? ""}</span>
      <span style={gutterStyle}>{line.newLine ?? ""}</span>
      <span style={signStyle(line.kind, applied)} aria-hidden="true">
        {sign}
      </span>
      {/* A leading space keeps an empty line from collapsing the row height. */}
      <span style={textStyle}>{line.text === "" ? " " : line.text}</span>
    </div>
  );
}

/** Hunks trimmed to `maxRows` total rows, plus how many rows were dropped. */
function takeRows(
  hunks: readonly DiffHunk[],
  maxRows: number,
): { readonly hunks: readonly DiffHunk[]; readonly omitted: number } {
  const total = hunks.reduce((sum, hunk) => sum + hunk.lines.length, 0);
  if (total <= maxRows) return { hunks, omitted: 0 };

  const kept: DiffHunk[] = [];
  let used = 0;
  for (const hunk of hunks) {
    if (used >= maxRows) break;
    const room = maxRows - used;
    if (hunk.lines.length <= room) {
      kept.push(hunk);
      used += hunk.lines.length;
    } else {
      kept.push({ ...hunk, lines: hunk.lines.slice(0, room) });
      used = maxRows;
    }
  }
  return { hunks: kept, omitted: total - used };
}

const frameStyle: CSSProperties = {
  background: "var(--color-surface)",
  border: "1px solid var(--color-border)",
  borderRadius: 8,
  overflow: "hidden",
};

const headerStyle: CSSProperties = {
  alignItems: "center",
  borderBottom: "1px solid var(--color-border)",
  display: "flex",
  gap: 10,
  minWidth: 0,
  padding: "6px 10px",
};

const pathStyle: CSSProperties = {
  color: "var(--color-text-strong)",
  direction: "rtl",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-10)",
  flex: "1 1 auto",
  lineHeight: "15px",
  minWidth: 0,
  overflow: "hidden",
  // `direction: rtl` keeps the FILENAME visible when a long absolute path is
  // clipped; `textAlign: left` stops that from also right-aligning the text.
  textAlign: "left",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const countsStyle: CSSProperties = {
  display: "inline-flex",
  flex: "0 0 auto",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-9-5)",
  fontVariantNumeric: "tabular-nums",
  gap: 6,
  lineHeight: "14px",
};

const addCountStyle: CSSProperties = { color: "var(--color-success)" };
const mutedCountStyle: CSSProperties = { color: "var(--color-text-subtle)" };

/** Says plainly that the change did not land. Warning-toned, not danger: the
 *  call failing is already reported by the card header's own status. */
const notAppliedStyle: CSSProperties = {
  background: "var(--color-warning-bg)",
  borderRadius: 4,
  color: "var(--color-warning)",
  flex: "0 0 auto",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-9-5)",
  letterSpacing: "0.04em",
  lineHeight: "14px",
  padding: "1px 5px",
  whiteSpace: "nowrap",
};
const delCountStyle: CSSProperties = { color: "var(--color-danger)" };

const scrollStyle: CSSProperties = {
  maxHeight: 320,
  overflow: "auto",
};

// An unapplied diff keeps its SHAPE (gutters, signs, alignment) and loses its
// change-colour. Green and red are the app's "this happened" signal; spending
// them on a refusal is the lie this guards against.
const rowStyle = (kind: DiffLine["kind"], applied: boolean): CSSProperties => ({
  background: !applied
    ? "transparent"
    : kind === "add"
      ? "var(--color-success-bg)"
      : kind === "remove"
        ? "var(--color-danger-bg)"
        : "transparent",
  display: "grid",
  gridTemplateColumns: "38px 38px 14px minmax(0, 1fr)",
  minWidth: 0,
});

const gutterStyle: CSSProperties = {
  color: "var(--color-text-subtle)",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-9-5)",
  fontVariantNumeric: "tabular-nums",
  lineHeight: "17px",
  padding: "0 6px",
  textAlign: "right",
  userSelect: "none",
};

const signStyle = (
  kind: DiffLine["kind"],
  applied: boolean,
): CSSProperties => ({
  color: !applied
    ? "var(--color-text-muted)"
    : kind === "add"
      ? "var(--color-success)"
      : kind === "remove"
        ? "var(--color-danger)"
        : "var(--color-text-subtle)",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-10)",
  fontWeight: 700,
  lineHeight: "17px",
  textAlign: "center",
  userSelect: "none",
});

const textStyle: CSSProperties = {
  color: "var(--color-text-strong)",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-10)",
  lineHeight: "17px",
  // `pre` (not pre-wrap): a wrapped code line breaks the line-number gutter's
  // alignment, so the row scrolls horizontally with its neighbours instead.
  whiteSpace: "pre",
};

const hunkRuleStyle: CSSProperties = {
  borderTop: "1px solid var(--color-border)",
  color: "var(--color-text-subtle)",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-9-5)",
  lineHeight: "15px",
  paddingLeft: 12,
  userSelect: "none",
};

const footNoteStyle: CSSProperties = {
  borderTop: "1px solid var(--color-border)",
  color: "var(--color-text-subtle)",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-9-5)",
  lineHeight: "14px",
  margin: 0,
  padding: "5px 10px",
};

export type { FileDiff };
