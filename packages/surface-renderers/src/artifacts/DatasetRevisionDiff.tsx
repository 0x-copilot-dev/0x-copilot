import type { CSSProperties, ReactElement } from "react";

import { DiffText, wordDiff } from "@0x-copilot/chat-surface";

import type { ArtifactRenderState } from "./model";
// Type-only, so no runtime edge back to the renderer that renders this panel.
// The grids themselves are parsed there and handed in — one parse feeds both
// the table the reader scrolls and the diff above it.
import type { DatasetGrid } from "./DatasetArtifactRenderer";

/** Rendered when a diffed value is absent, so an empty side is still legible. */
const EMPTY_CELL = "(empty)";

/**
 * The revision change to announce on a dataset surface (PRD-03 D4). Created by
 * the host, never decoded from artifact content — the same rule
 * `DatasetEditorActions` follows.
 */
export interface DatasetRevisionChange {
  /** The revision that was on screen, and the comparison base. */
  readonly baseRevision: number;
  /** Full base-revision source, for the cell-level grid diff. */
  readonly baseText: string;
  /**
   * The host's bounded changed-region pair, rendered as the word-level text
   * diff when the two revisions do not read as grids. Precomputed there
   * because trimming it to a render budget belongs next to the byte bounds
   * that governed the read; a second trim here would be a second answer.
   */
  readonly textBefore: string;
  readonly textAfter: string;
}

export interface DatasetDiffCell {
  readonly before: string;
  readonly after: string;
  readonly changed: boolean;
}

export interface DatasetDiffRow {
  readonly kind: "changed" | "added" | "removed";
  /**
   * 1-based line number of the row, header included, in the revision it comes
   * from — the landed revision for `changed`/`added`, the base for `removed`.
   * Matches the row numbering the cell editor labels its inputs with.
   */
  readonly row: number;
  readonly cells: readonly DatasetDiffCell[];
}

export interface DatasetRevisionCellDiff {
  /** Column labels from the landed revision; may be shorter than `columnCount`. */
  readonly headers: readonly string[];
  readonly columnCount: number;
  readonly rows: readonly DatasetDiffRow[];
  readonly changedCells: number;
  readonly addedRows: number;
  readonly removedRows: number;
  readonly truncated: boolean;
}

/** Host-created change payload, narrowed structurally like the editor actions. */
export function datasetRevisionChangeFor(
  artifact: ArtifactRenderState,
): DatasetRevisionChange | null {
  const candidate = (
    artifact as ArtifactRenderState & {
      readonly datasetRevisionChange?: unknown;
    }
  ).datasetRevisionChange;
  if (typeof candidate !== "object" || candidate === null) return null;
  const value = candidate as Partial<DatasetRevisionChange>;
  return Number.isInteger(value.baseRevision) &&
    typeof value.baseText === "string" &&
    typeof value.textBefore === "string" &&
    typeof value.textAfter === "string"
    ? (value as DatasetRevisionChange)
    : null;
}

/**
 * Cell-level diff of two parsed revisions of one dataset.
 *
 * Rows are aligned by trimming the shared leading and trailing rows, exactly
 * as the text comparison trims shared lines, and the remaining window is then
 * paired positionally: pairs are changed rows, and whichever side is longer
 * contributes added or removed rows. That reads a same-position edit, an
 * insertion and a deletion correctly; several edits far apart widen the window
 * and pair rows that merely share a position, which is why every row carries
 * its own line number rather than being presented as a matched pair.
 *
 * `null` means "no honest cell reading" and the caller falls back to the text
 * diff: either side failing to parse as a grid, either side truncated by the
 * preview budget, or a change that moved no cell value — quoting, delimiters
 * and whitespace all live in the bytes, and only the text diff can show them.
 */
export function diffDatasetGrids(
  base: DatasetGrid | null,
  current: DatasetGrid | null,
  maxRows: number,
): DatasetRevisionCellDiff | null {
  if (base === null || current === null) return null;
  if (!base.complete || !current.complete) return null;
  const before: readonly (readonly string[])[] = [base.headers, ...base.rows];
  const after: readonly (readonly string[])[] = [
    current.headers,
    ...current.rows,
  ];
  let prefix = 0;
  while (
    prefix < before.length &&
    prefix < after.length &&
    rowsEqual(before[prefix]!, after[prefix]!)
  ) {
    prefix += 1;
  }
  let suffix = 0;
  while (
    suffix < before.length - prefix &&
    suffix < after.length - prefix &&
    rowsEqual(
      before[before.length - suffix - 1]!,
      after[after.length - suffix - 1]!,
    )
  ) {
    suffix += 1;
  }
  const removed = before.slice(prefix, before.length - suffix);
  const added = after.slice(prefix, after.length - suffix);
  const rows: DatasetDiffRow[] = [];
  let changedCells = 0;
  const paired = Math.min(removed.length, added.length);
  for (let index = 0; index < paired; index += 1) {
    const cells = pairedCells(removed[index]!, added[index]!);
    const changed = cells.filter((cell) => cell.changed).length;
    // An interior row of a wide window can be identical on both sides; it is
    // not a change and is not reported as one.
    if (changed === 0) continue;
    changedCells += changed;
    rows.push({ kind: "changed", row: prefix + index + 1, cells });
  }
  for (let index = paired; index < removed.length; index += 1) {
    rows.push({
      kind: "removed",
      row: prefix + index + 1,
      cells: sideCells(removed[index]!, "removed"),
    });
  }
  for (let index = paired; index < added.length; index += 1) {
    rows.push({
      kind: "added",
      row: prefix + index + 1,
      cells: sideCells(added[index]!, "added"),
    });
  }
  if (rows.length === 0) return null;
  const kept = rows.slice(0, maxRows);
  return {
    headers: current.headers,
    columnCount: Math.max(
      current.headers.length,
      ...kept.map((row) => row.cells.length),
    ),
    rows: kept,
    changedCells,
    addedRows: added.length - paired,
    removedRows: removed.length - paired,
    truncated: rows.length > maxRows,
  };
}

/**
 * What changed between the base revision and the one on screen, in the
 * dataset's own language: the cells that moved, not a word diff of the source
 * (PRD-03 D4). Falls back to that word diff when the content is not a grid on
 * both sides — the honest reading of a CSV whose delimiters or quoting changed.
 */
export function DatasetRevisionDiff(props: {
  readonly change: DatasetRevisionChange;
  readonly revision: number;
  readonly diff: DatasetRevisionCellDiff | null;
}): ReactElement {
  const { change, diff, revision } = props;
  return (
    <section
      aria-label="Dataset revision changes"
      data-testid="dataset-revision-diff"
      data-shape={diff === null ? "text" : "cells"}
      style={panelStyle}
    >
      <p className="ui-section-label">
        What changed: r{change.baseRevision} → r{revision}
      </p>
      {diff === null ? (
        <>
          <p className="ui-caption">
            These revisions do not read as a grid on both sides, so the change
            is shown as text.
          </p>
          <div aria-label="Revision change details">
            <DiffText hunks={wordDiff(change.textBefore, change.textAfter)} />
          </div>
        </>
      ) : (
        <>
          <p className="ui-caption">
            {count(diff.changedCells, "changed cell")};{" "}
            {count(diff.addedRows, "added row")};{" "}
            {count(diff.removedRows, "removed row")}.
            {diff.truncated
              ? " Change output is capped for safe rendering."
              : ""}
          </p>
          <div className="ui-dataset-table-wrap" style={tableWrapStyle}>
            <table className="ui-dataset-table" aria-label="Changed cells">
              <thead>
                <tr>
                  <th className="ui-dataset-table__header" scope="col">
                    Row
                  </th>
                  {columns(diff).map((header, column) => (
                    <th
                      className="ui-dataset-table__header"
                      key={column}
                      scope="col"
                    >
                      {header}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {diff.rows.map((row) => (
                  <tr
                    className="sf-row"
                    data-kind={row.kind}
                    data-testid={`dataset-diff-row-${row.kind}-${row.row}`}
                    key={`${row.kind}-${row.row}`}
                  >
                    <th className="ui-dataset-table__cell" scope="row">
                      {row.kind} · row {row.row}
                    </th>
                    {columns(diff).map((_header, column) => (
                      <td
                        className="ui-dataset-table__cell"
                        data-changed={
                          row.cells[column]?.changed === true ? "true" : "false"
                        }
                        key={column}
                      >
                        <CellChange cell={row.cells[column]} kind={row.kind} />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}

/**
 * `<del>`/`<ins>` rather than a colour: no stylesheet rule exists for a changed
 * diff cell, and the strikethrough/underline a browser gives these elements
 * carries the meaning on its own — the same rule `DiffText` follows.
 */
function CellChange(props: {
  readonly cell: DatasetDiffCell | undefined;
  readonly kind: DatasetDiffRow["kind"];
}): ReactElement | null {
  const { cell, kind } = props;
  if (cell === undefined) return null;
  if (kind === "added") return <ins>{cell.after}</ins>;
  if (kind === "removed") return <del>{cell.before}</del>;
  if (!cell.changed) return <span>{cell.after}</span>;
  return (
    <>
      <del>{cell.before === "" ? EMPTY_CELL : cell.before}</del>{" "}
      <ins>{cell.after === "" ? EMPTY_CELL : cell.after}</ins>
    </>
  );
}

/** Column labels, padded for a revision that gained or lost columns. */
function columns(diff: DatasetRevisionCellDiff): readonly string[] {
  return Array.from(
    { length: diff.columnCount },
    (_value, column) => diff.headers[column] || `Column ${column + 1}`,
  );
}

function rowsEqual(left: readonly string[], right: readonly string[]): boolean {
  if (left.length !== right.length) return false;
  return left.every((value, index) => value === right[index]);
}

function pairedCells(
  before: readonly string[],
  after: readonly string[],
): readonly DatasetDiffCell[] {
  const width = Math.max(before.length, after.length);
  return Array.from({ length: width }, (_value, column) => {
    const left = before[column] ?? "";
    const right = after[column] ?? "";
    return { before: left, after: right, changed: left !== right };
  });
}

function sideCells(
  row: readonly string[],
  kind: "added" | "removed",
): readonly DatasetDiffCell[] {
  return row.map((value) => ({
    before: kind === "removed" ? value : "",
    after: kind === "added" ? value : "",
    changed: true,
  }));
}

function count(total: number, noun: string): string {
  return `${total} ${noun}${total === 1 ? "" : "s"}`;
}

// The dataset surface is a flex column whose table wrap owns the remaining
// height. No design-system rule covers a second, bounded table above it, and
// this package cannot add one, so the two measurements stay inline rather than
// shipping class names with no stylesheet behind them.
const panelStyle: CSSProperties = {
  display: "grid",
  flex: "none",
  gap: 6,
  maxHeight: "40%",
  minHeight: 0,
  padding: "10px 0",
};

// The wrap already scrolls (`ui-dataset-table-wrap`); inside a grid it needs
// this to be allowed to shrink below its content and actually do so.
const tableWrapStyle: CSSProperties = { minHeight: 0 };
