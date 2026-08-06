import { useEffect, useMemo, useState, type ReactElement } from "react";

import { magnitudeShares, numericValue } from "../_shared/path";
import {
  DatasetRevisionDiff,
  datasetRevisionChangeFor,
  diffDatasetGrids,
} from "./DatasetRevisionDiff";
import type { ArtifactEditorActions, ArtifactRenderState } from "./model";
import { hostEditorActions, previewNotice } from "./model";

/**
 * B2 capacity contract: a preview may retain at most 100k rows / 1m cells,
 * while each virtual table window mounts at most 100 body rows. These are
 * explicit work and DOM budgets, rather than timing assertions that vary by
 * browser or CI host.
 */
export const DATASET_PREVIEW_ROW_BUDGET = 100_000;
export const DATASET_PREVIEW_CELL_BUDGET = 1_000_000;
export const DATASET_DOM_ROW_BUDGET = 100;
const MAX_PREVIEW_COLUMNS = 100;
const EMPTY_DATASET_PATCH: DatasetPatch = {};

export type { ArtifactRevisionSaveOutcome } from "./model";

/**
 * Host-created editor capability. It is never decoded from artifact content.
 *
 * The same shape the document surface takes, and deliberately the same TYPE:
 * one host grant, read by one reader (`hostEditorActions`), so the two editable
 * artifact surfaces cannot drift into two ideas of what "the host let me write"
 * means. The alias is kept because it is the published name.
 */
export type DatasetEditorActions = ArtifactEditorActions;

export interface CsvTable {
  readonly rows: readonly (readonly string[])[];
  readonly formulaCells: number;
}

export interface LosslessDelimitedCell {
  /** Original source fragment, including original quotes and escaped quotes. */
  readonly raw: string;
  /** Decoded logical cell value. */
  readonly value: string;
}

export interface LosslessDelimitedRow {
  readonly cells: readonly LosslessDelimitedCell[];
  /** Original row terminator: CRLF, LF, CR, or the empty final terminator. */
  readonly ending: string;
}

export interface LosslessDelimitedDataset {
  readonly bom: string;
  readonly delimiter: string;
  readonly rows: readonly LosslessDelimitedRow[];
  readonly complete: boolean;
  readonly roundTripSafe: boolean;
  readonly fidelityWarning: string | null;
}

export type DatasetPatch = Readonly<Record<string, string>>;

interface DatasetModel {
  readonly kind: "delimited" | "json";
  readonly headers: readonly string[];
  readonly rows: readonly (readonly string[])[];
  readonly formulaCells: number;
  /** False when the preview budget truncated the parse, so rows are missing. */
  readonly complete: boolean;
  readonly editable: boolean;
  readonly fidelityWarning: string | null;
  readonly serialize: (patch: DatasetPatch) => string;
  readonly safeExport: (patch: DatasetPatch) => string;
}

/**
 * The read-only half of the parsed model — what a comparison needs, without the
 * write path. Exported so the revision diff reads the SAME parse the grid
 * renders from; a second CSV reader is how a diff and its own table would come
 * to disagree about what a row is.
 */
export type DatasetGrid = Pick<
  DatasetModel,
  "kind" | "headers" | "rows" | "complete"
>;

export function datasetGrid(
  text: string,
  mediaType: string,
): DatasetGrid | null {
  return parseDatasetModel(text, mediaType);
}

/**
 * RFC4180-aware tokenizer that retains every untouched source fragment. A
 * patched revision serializes only changed cells, preserving BOM, delimiters,
 * quoting and row endings for all untouched cells exactly.
 */
export function parseLosslessDelimited(
  text: string,
  delimiter = ",",
  maxRows = DATASET_PREVIEW_ROW_BUDGET,
  maxCells = DATASET_PREVIEW_CELL_BUDGET,
): LosslessDelimitedDataset {
  const bom = text.startsWith("\ufeff") ? "\ufeff" : "";
  const source = bom === "" ? text : text.slice(1);
  const rows: LosslessDelimitedRow[] = [];
  let cells: LosslessDelimitedCell[] = [];
  let index = 0;
  let cellStart = 0;
  let value = "";
  let quoted = false;
  let quoteClosed = false;
  let malformed = false;
  let limited = false;
  let cellCount = 0;

  const pushCell = (end: number): boolean => {
    cellCount += 1;
    if (cellCount > maxCells) {
      limited = true;
      return false;
    }
    cells.push({ raw: source.slice(cellStart, end), value });
    value = "";
    quoted = false;
    quoteClosed = false;
    return true;
  };
  const pushRow = (end: number, ending: string): boolean => {
    if (!pushCell(end)) return false;
    if (rows.length >= maxRows) {
      limited = true;
      return false;
    }
    rows.push({ cells, ending });
    cells = [];
    return true;
  };

  while (index < source.length) {
    const character = source[index]!;
    if (quoted) {
      if (character === '"' && source[index + 1] === '"') {
        value += '"';
        index += 2;
        continue;
      }
      if (character === '"') {
        quoted = false;
        quoteClosed = true;
        index += 1;
        continue;
      }
      value += character;
      index += 1;
      continue;
    }
    if (
      quoteClosed &&
      character !== delimiter &&
      character !== "\r" &&
      character !== "\n"
    ) {
      malformed = true;
      value += character;
      index += 1;
      continue;
    }
    if (character === '"' && index === cellStart) {
      quoted = true;
      index += 1;
      continue;
    }
    if (character === delimiter) {
      if (!pushCell(index)) break;
      index += 1;
      cellStart = index;
      continue;
    }
    if (character === "\r" || character === "\n") {
      const ending =
        character === "\r" && source[index + 1] === "\n" ? "\r\n" : character;
      if (!pushRow(index, ending)) break;
      index += ending.length;
      cellStart = index;
      continue;
    }
    if (character === '"') malformed = true;
    value += character;
    index += 1;
  }

  if (quoted) malformed = true;
  const hasUnterminatedRow =
    !limited &&
    (cells.length > 0 ||
      cellStart < source.length ||
      source.endsWith(delimiter));
  if (hasUnterminatedRow && !pushRow(source.length, "")) limited = true;

  const warning = limited
    ? "Preview limits were reached. Cell editing is disabled so a partial table cannot overwrite the canonical artifact."
    : malformed
      ? "This delimited file is malformed. Cell editing is disabled because exact round-trip fidelity cannot be guaranteed."
      : null;
  return {
    bom,
    delimiter,
    rows,
    complete: !limited,
    roundTripSafe: !limited && !malformed,
    fidelityWarning: warning,
  };
}

/** Backward-compatible display parser backed by the lossless tokenizer. */
export function parseCsv(
  text: string,
  maxRows = DATASET_PREVIEW_ROW_BUDGET,
  maxCells = DATASET_PREVIEW_CELL_BUDGET,
  delimiter = ",",
): CsvTable {
  const parsed = parseLosslessDelimited(text, delimiter, maxRows, maxCells);
  const rows = parsed.rows.map((row) => row.cells.map((cell) => cell.value));
  return { rows, formulaCells: formulaCount(rows) };
}

export function serializeDelimitedPatch(
  dataset: LosslessDelimitedDataset,
  patch: DatasetPatch,
): string {
  if (!dataset.roundTripSafe) {
    throw new Error("Delimited dataset cannot be safely round-tripped");
  }
  return (
    dataset.bom +
    dataset.rows
      .map((row, rowIndex) => {
        const cells = row.cells.map((cell, columnIndex) => {
          const changed = patch[cellKey(rowIndex, columnIndex)];
          return changed === undefined
            ? cell.raw
            : encodeDelimitedCell(changed, dataset.delimiter);
        });
        return cells.join(dataset.delimiter) + row.ending;
      })
      .join("")
  );
}

export function serializeFormulaSafeDelimitedPatch(
  dataset: LosslessDelimitedDataset,
  patch: DatasetPatch,
): string {
  const safePatch: Record<string, string> = { ...patch };
  for (const [rowIndex, row] of dataset.rows.entries()) {
    for (const [columnIndex, cell] of row.cells.entries()) {
      const key = cellKey(rowIndex, columnIndex);
      const effective = safePatch[key] ?? cell.value;
      if (isFormulaLike(effective)) safePatch[key] = `'${effective}`;
    }
  }
  return serializeDelimitedPatch(dataset, safePatch);
}

function parseJsonObjectRows(text: string): DatasetModel | null {
  try {
    const parsed: unknown = JSON.parse(text);
    if (
      !Array.isArray(parsed) ||
      !parsed.every(
        (row) => typeof row === "object" && row !== null && !Array.isArray(row),
      )
    ) {
      return null;
    }
    const records = parsed.slice(
      0,
      DATASET_PREVIEW_ROW_BUDGET,
    ) as readonly Record<string, unknown>[];
    const complete = records.length === parsed.length;
    const headers = [
      ...new Set(records.flatMap((row) => Object.keys(row))),
    ].slice(0, MAX_PREVIEW_COLUMNS);
    const rows = records.map((row) =>
      headers.map((header) => jsonCell(row[header])),
    );
    const serialize = (patch: DatasetPatch, safeExport = false): string => {
      const outputHeaders = headers.map(
        (header, columnIndex) => patch[cellKey(0, columnIndex)] ?? header,
      );
      const output = rows.map((row, rowIndex) =>
        Object.fromEntries(
          outputHeaders.map((header, columnIndex) => {
            const value =
              patch[cellKey(rowIndex + 1, columnIndex)] ??
              row[columnIndex] ??
              "";
            return [
              header,
              safeExport && isFormulaLike(value) ? `'${value}` : value,
            ];
          }),
        ),
      );
      return `${JSON.stringify(output, null, 2)}\n`;
    };
    return {
      kind: "json",
      headers,
      rows,
      formulaCells: formulaCount([headers, ...rows]),
      complete,
      editable: complete,
      fidelityWarning:
        "Saving JSON cell edits normalizes indentation, key serialization and scalar values. Review this fidelity change before saving.",
      serialize: (patch) => serialize(patch),
      safeExport: (patch) => serialize(patch, true),
    };
  } catch {
    return null;
  }
}

function datasetModel(artifact: ArtifactRenderState): DatasetModel | null {
  return artifact.text === undefined
    ? null
    : parseDatasetModel(artifact.text, artifact.mediaType);
}

function parseDatasetModel(
  text: string,
  mediaType: string,
): DatasetModel | null {
  if (mediaType === "application/json") {
    return parseJsonObjectRows(text);
  }
  const delimiter = mediaType === "text/tab-separated-values" ? "\t" : ",";
  if (
    mediaType !== "text/csv" &&
    mediaType !== "text/tab-separated-values" &&
    mediaType !== "text/plain"
  ) {
    return null;
  }
  // Delimited previews reserve one retained row for the header so the public
  // data-row budget still means a 100k-row CSV can be viewed and edited.
  const parsed = parseLosslessDelimited(
    text,
    delimiter,
    DATASET_PREVIEW_ROW_BUDGET + 1,
  );
  const rows = parsed.rows.map((row) => row.cells.map((cell) => cell.value));
  const [headers = [], ...body] = rows;
  return {
    kind: "delimited",
    headers: headers.slice(0, MAX_PREVIEW_COLUMNS),
    rows: body.map((row) => row.slice(0, MAX_PREVIEW_COLUMNS)),
    formulaCells: formulaCount(rows),
    complete: parsed.complete,
    editable: parsed.roundTripSafe,
    fidelityWarning: parsed.fidelityWarning,
    serialize: (patch) => serializeDelimitedPatch(parsed, patch),
    safeExport: (patch) => serializeFormulaSafeDelimitedPatch(parsed, patch),
  };
}

function jsonCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean")
    return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return "[unserializable value]";
  }
}

function formulaCount(rows: readonly (readonly string[])[]): number {
  return rows.flat().filter(isFormulaLike).length;
}

function isFormulaLike(value: string): boolean {
  return /^[=+\-@]/.test(value);
}

function encodeDelimitedCell(value: string, delimiter: string): string {
  return /["\r\n]/.test(value) || value.includes(delimiter)
    ? `"${value.replaceAll('"', '""')}"`
    : value;
}

function cellKey(row: number, column: number): string {
  return `${row}:${column}`;
}

/** Fixed, inert table renderer. It never interprets formulas, HTML, or JSON values. */
export function DatasetArtifactRenderer(props: {
  readonly artifact: ArtifactRenderState;
}): ReactElement {
  const { artifact } = props;
  const notice = previewNotice(artifact);
  if (notice !== null || artifact.text === undefined) {
    return (
      <div className="ui-card ui-body" data-testid="artifact-dataset-fallback">
        {notice ?? "Loading dataset…"}
      </div>
    );
  }
  return <DatasetPreview artifact={artifact} />;
}

function DatasetPreview(props: {
  readonly artifact: ArtifactRenderState;
}): ReactElement {
  const model = useMemo(
    () => datasetModel(props.artifact),
    [props.artifact.mediaType, props.artifact.text],
  );
  // A revision the reader did not make landed on this artifact (PRD-03 D4).
  // Diffed against the SAME parse the grid below renders from, so the change
  // reads in cells rather than as a word diff of CSV source.
  const change = datasetRevisionChangeFor(props.artifact);
  const cellDiff = useMemo(
    () =>
      change === null
        ? null
        : diffDatasetGrids(
            datasetGrid(change.baseText, props.artifact.mediaType),
            model,
            DATASET_DOM_ROW_BUDGET,
          ),
    [change?.baseText, model, props.artifact.mediaType],
  );
  const revisionDiff =
    change === null ? null : (
      <DatasetRevisionDiff
        change={change}
        diff={cellDiff}
        revision={props.artifact.revision}
      />
    );
  if (model === null) {
    return (
      <>
        {revisionDiff}
        <div
          className="ui-card ui-body"
          data-testid="artifact-dataset-fallback"
        >
          This dataset format cannot be safely previewed. Download the exact
          artifact bytes.
        </div>
      </>
    );
  }
  const actions = hostEditorActions(props.artifact, "datasetEditor");
  return (
    <section
      className="ui-dataset-surface"
      data-testid="artifact-dataset-renderer"
    >
      {revisionDiff}
      {model.formulaCells > 0 ? (
        <p className="ui-dataset-notice" role="note">
          Formula-like cells are shown as text and are never evaluated. Exact
          download preserves them; safe export is explicit.
        </p>
      ) : null}
      {model.fidelityWarning !== null ? (
        <p className="ui-dataset-notice ui-dataset-notice--warning" role="note">
          {model.fidelityWarning}
        </p>
      ) : null}
      {actions === null ? (
        <DatasetGrid model={model} />
      ) : (
        <DatasetPatchEditor
          artifactKey={`${props.artifact.artifactId}@${props.artifact.revision}`}
          model={model}
          actions={actions}
        />
      )}
    </section>
  );
}

/**
 * Per-column magnitude for the read-only dataset grid.
 *
 * A published CSV carries no `SurfaceSpec`, so there is no `format` hint to say
 * which columns are numeric — it has to come from the data. A column qualifies
 * only when EVERY value present reads as a number; one unparseable entry and the
 * whole column is left alone, which is the same fail-closed rule
 * `magnitudeShares` applies (and the reason it is reused here rather than
 * reimplemented: the dataset artifact and `table://` must resolve one table
 * language, and two copies of this logic is exactly how they would drift).
 *
 * Scaled over the FULL column, not the mounted window. The grid virtualizes to
 * 100 rows, and scaling per window would make a row's bar change length as the
 * user scrolls — a magnitude that depends on scroll position is not a magnitude.
 */
function useColumnMagnitudes(
  model: DatasetModel,
): ReadonlyMap<number, readonly (number | null)[]> {
  return useMemo(() => {
    const byColumn = new Map<number, readonly (number | null)[]>();
    model.headers.forEach((_header, column) => {
      const values = model.rows.map((row) => row[column] ?? "");
      const populated = values.filter((value) => value.trim() !== "");
      if (populated.length === 0) return;
      if (!populated.every((value) => numericValue(value) !== null)) return;
      const shares = magnitudeShares(values);
      if (shares.some((share) => share !== null)) byColumn.set(column, shares);
    });
    return byColumn;
  }, [model]);
}

function DatasetGrid(props: { readonly model: DatasetModel }): ReactElement {
  const view = useDatasetView(props.model, EMPTY_DATASET_PATCH);
  const magnitudes = useColumnMagnitudes(props.model);
  return (
    <>
      <DatasetViewControls model={props.model} view={view} />
      <DatasetWindowControls
        start={view.windowStart}
        total={view.rowIndexes.length}
        onChange={view.setWindowStart}
      />
      <div
        className="ui-dataset-table-wrap"
        data-testid="dataset-virtual-window"
        data-dom-row-budget={DATASET_DOM_ROW_BUDGET}
        data-window-row-count={view.windowedRowIndexes.length}
        data-window-start={view.windowStart}
      >
        <table
          className="ui-dataset-table"
          role="grid"
          aria-label="Dataset preview"
        >
          <thead>
            <tr role="row">
              {props.model.headers.map((value, index) => (
                <th
                  className={
                    magnitudes.has(index)
                      ? "ui-dataset-table__header sf-col--numeric"
                      : "ui-dataset-table__header"
                  }
                  key={index}
                  role="columnheader"
                  scope="col"
                >
                  {value}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {view.windowedRowIndexes.map((rowIndex) => (
              <tr className="sf-row" key={rowIndex} role="row">
                {props.model.headers.map((_, columnIndex) => {
                  const share = magnitudes.get(columnIndex)?.[rowIndex];
                  const bar =
                    typeof share === "number" && share > 0 ? share : null;
                  return (
                    <td
                      className={
                        bar === null
                          ? "ui-dataset-table__cell"
                          : "ui-dataset-table__cell sf-cell--numeric"
                      }
                      key={columnIndex}
                      role="gridcell"
                    >
                      {bar === null ? null : (
                        <span
                          aria-hidden="true"
                          className="sf-value-bar"
                          data-testid={`dataset-value-bar-${rowIndex}-${columnIndex}`}
                          style={{ left: `${(1 - bar) * 100}%` }}
                        />
                      )}
                      <span
                        className={
                          bar === null ? undefined : "sf-value-bar__value"
                        }
                      >
                        {view.valueAt(rowIndex, columnIndex)}
                      </span>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function DatasetPatchEditor(props: {
  readonly artifactKey: string;
  readonly model: DatasetModel;
  readonly actions: DatasetEditorActions;
}): ReactElement {
  const [patch, setPatch] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<
    "idle" | "saving" | "conflict" | "error"
  >("idle");
  const [pending, setPending] = useState<{
    readonly source: string;
    readonly label: string;
  } | null>(null);
  useEffect(() => {
    setPatch({});
    setStatus("idle");
    setPending(null);
  }, [props.artifactKey]);

  const view = useDatasetView(props.model, patch);
  const magnitudes = useColumnMagnitudes(props.model);

  const update = (row: number, column: number, value: string): void => {
    const key = cellKey(row, column);
    const original =
      row === 0
        ? (props.model.headers[column] ?? "")
        : (props.model.rows[row - 1]?.[column] ?? "");
    setPatch((current) => {
      if (value === original) {
        const { [key]: _removed, ...rest } = current;
        return rest;
      }
      return { ...current, [key]: value };
    });
  };
  const commit = (source: string): void => {
    setPending(null);
    setStatus("saving");
    void props.actions.saveRevision(source).then((outcome) => {
      setStatus(outcome === "saved" ? "idle" : outcome);
    });
  };
  const prepareSave = (safeExport: boolean): void => {
    let source: string;
    try {
      source = safeExport
        ? props.model.safeExport(patch)
        : props.model.serialize(patch);
    } catch {
      setStatus("error");
      return;
    }
    if (!safeExport && props.model.fidelityWarning !== null) {
      setPending({ source, label: "Save normalized revision" });
      return;
    }
    if (safeExport) {
      setPending({ source, label: "Create formula-safe revision" });
      return;
    }
    commit(source);
  };
  const canEdit =
    props.model.editable && !props.actions.disabled && status !== "saving";
  const changedCellCount = Object.keys(patch).length;
  return (
    <section className="ui-dataset-editor" aria-label="Dataset cell editor">
      <p className="ui-dataset-helper" id="dataset-cell-editor-help">
        <span>
          Edit cells directly. Changes stay local until you save an immutable
          revision.
        </span>
        <span className="ui-dataset-helper__status">
          {changedCellCount === 0
            ? "No unsaved edits"
            : `${changedCellCount} unsaved ${changedCellCount === 1 ? "edit" : "edits"}`}
        </span>
      </p>
      <DatasetWindowControls
        start={view.windowStart}
        total={view.rowIndexes.length}
        onChange={view.setWindowStart}
      />
      <DatasetViewControls model={props.model} view={view} />
      <div
        className="ui-dataset-table-wrap"
        data-testid="dataset-virtual-window"
        data-dom-row-budget={DATASET_DOM_ROW_BUDGET}
        data-window-row-count={view.windowedRowIndexes.length}
        data-window-start={view.windowStart}
      >
        <table
          className="ui-dataset-table ui-dataset-table--editable"
          role="grid"
          aria-label="Dataset cell editor"
          aria-describedby="dataset-cell-editor-help"
        >
          <thead>
            <tr role="row">
              {props.model.headers.map((header, column) => (
                <th
                  className={
                    magnitudes.has(column)
                      ? "ui-dataset-table__header sf-col--numeric"
                      : "ui-dataset-table__header"
                  }
                  key={column}
                  role="columnheader"
                  scope="col"
                >
                  <input
                    aria-label={`Header ${column + 1}`}
                    className="ui-dataset-cell-input ui-dataset-cell-input--header"
                    data-modified={
                      patch[cellKey(0, column)] === undefined ? "false" : "true"
                    }
                    disabled={!canEdit}
                    value={patch[cellKey(0, column)] ?? header}
                    onChange={(event) => update(0, column, event.target.value)}
                  />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {view.windowedRowIndexes.map((rowIndex) => {
              const row = props.model.rows[rowIndex]!;
              return (
                <tr className="sf-row" key={rowIndex} role="row">
                  {props.model.headers.map((header, column) => {
                    const rowNumber = rowIndex + 2;
                    const share = magnitudes.get(column)?.[rowIndex];
                    const bar =
                      typeof share === "number" && share > 0 ? share : null;
                    return (
                      <td
                        className={
                          bar === null
                            ? "ui-dataset-table__cell"
                            : "ui-dataset-table__cell sf-cell--numeric"
                        }
                        key={column}
                        role="gridcell"
                      >
                        {bar === null ? null : (
                          // Behind the editable input, which is already
                          // transparent — so the magnitude reads through the
                          // cell the user types into, rather than forcing a
                          // read-only view to see it.
                          <span
                            aria-hidden="true"
                            className="sf-value-bar"
                            data-testid={`dataset-value-bar-${rowIndex}-${column}`}
                            style={{ left: `${(1 - bar) * 100}%` }}
                          />
                        )}
                        <input
                          aria-label={`${header || `Column ${column + 1}`}, row ${rowNumber}`}
                          className="ui-dataset-cell-input"
                          data-modified={
                            patch[cellKey(rowIndex + 1, column)] === undefined
                              ? "false"
                              : "true"
                          }
                          disabled={!canEdit}
                          value={view.valueAt(rowIndex, column)}
                          onChange={(event) =>
                            update(rowIndex + 1, column, event.target.value)
                          }
                        />
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {pending !== null ? (
        <div className="ui-dataset-confirmation" role="alert">
          <p>
            {props.model.fidelityWarning ??
              "Formula-like cells will be prefixed with an apostrophe only in this new safe-export revision. The canonical revision remains unchanged."}
          </p>
          <div className="ui-dataset-confirmation__actions">
            <button
              className="ui-button ui-button--primary ui-button--sm"
              type="button"
              onClick={() => commit(pending.source)}
            >
              {pending.label}
            </button>
            <button
              className="ui-button ui-button--ghost ui-button--sm"
              type="button"
              onClick={() => setPending(null)}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}
      {status === "conflict" ? (
        <p
          className="ui-dataset-status ui-dataset-status--warning"
          role="alert"
        >
          A newer revision exists. Your in-memory cell edits are preserved;
          compare and rebase manually before saving.
        </p>
      ) : null}
      {status === "error" ? (
        <p className="ui-dataset-status ui-dataset-status--danger" role="alert">
          This dataset could not be saved. Your in-memory cell edits are still
          here.
        </p>
      ) : null}
      <div
        className="ui-dataset-action-bar"
        aria-label="Dataset revision actions"
      >
        <span className="ui-dataset-action-bar__copy">
          New immutable revision; original stays unchanged.
        </span>
        <button
          className="ui-button ui-button--primary ui-button--sm"
          type="button"
          disabled={!canEdit || changedCellCount === 0}
          onClick={() => prepareSave(false)}
        >
          {status === "saving" ? "Saving…" : "Save patched revision"}
        </button>
        {props.model.formulaCells > 0 ? (
          <button
            className="ui-button ui-button--ghost ui-button--sm"
            type="button"
            disabled={!canEdit}
            onClick={() => prepareSave(true)}
          >
            Create formula-safe revision
          </button>
        ) : null}
      </div>
    </section>
  );
}

interface DatasetView {
  readonly filter: string;
  readonly setFilter: (value: string) => void;
  readonly sortColumn: number | null;
  readonly setSortColumn: (column: number | null) => void;
  readonly sortDirection: "ascending" | "descending";
  readonly toggleSortDirection: () => void;
  readonly rowIndexes: readonly number[];
  readonly windowStart: number;
  readonly setWindowStart: (start: number) => void;
  readonly windowedRowIndexes: readonly number[];
  readonly valueAt: (rowIndex: number, columnIndex: number) => string;
}

function useDatasetView(
  model: DatasetModel,
  patch: Readonly<Record<string, string>>,
): DatasetView {
  const [filter, setFilter] = useState("");
  const [sortColumn, setSortColumnState] = useState<number | null>(null);
  const [sortDirection, setSortDirection] = useState<
    "ascending" | "descending"
  >("ascending");
  const [windowStart, setWindowStart] = useState(0);
  const valueAt = (rowIndex: number, columnIndex: number): string =>
    patch[cellKey(rowIndex + 1, columnIndex)] ??
    model.rows[rowIndex]?.[columnIndex] ??
    "";
  const rowIndexes = useMemo(() => {
    const query = filter.trim().toLocaleLowerCase();
    const matching: number[] = [];
    for (const [rowIndex, row] of model.rows.entries()) {
      if (
        query === "" ||
        row.some((_, columnIndex) =>
          valueAt(rowIndex, columnIndex).toLocaleLowerCase().includes(query),
        )
      ) {
        matching.push(rowIndex);
      }
    }
    if (sortColumn === null) return matching;
    // Copy-then-sort rather than `toSorted`: the repo compiles at ES2022
    // (tsconfig.base.json), where `Array.prototype.toSorted` is not in `lib`.
    // `matching` is local to this memo, so the copy is only about keeping the
    // non-mutating shape the sort already had.
    return [...matching].sort((left, right) => {
      const comparison = valueAt(left, sortColumn).localeCompare(
        valueAt(right, sortColumn),
      );
      return comparison === 0
        ? left - right
        : sortDirection === "ascending"
          ? comparison
          : -comparison;
    });
  }, [filter, model.rows, patch, sortColumn, sortDirection]);
  const maxWindowStart = Math.max(0, rowIndexes.length - 1);
  const safeWindowStart = Math.min(windowStart, maxWindowStart);
  const windowedRowIndexes = rowIndexes.slice(
    safeWindowStart,
    safeWindowStart + DATASET_DOM_ROW_BUDGET,
  );

  useEffect(() => {
    setWindowStart(0);
  }, [filter, sortColumn, sortDirection]);

  return {
    filter,
    setFilter,
    sortColumn,
    setSortColumn: (column) => {
      setSortColumnState(column);
      setSortDirection("ascending");
    },
    sortDirection,
    toggleSortDirection: () =>
      setSortDirection((current) =>
        current === "ascending" ? "descending" : "ascending",
      ),
    rowIndexes,
    windowStart: safeWindowStart,
    setWindowStart,
    windowedRowIndexes,
    valueAt,
  };
}

function DatasetViewControls(props: {
  readonly model: DatasetModel;
  readonly view: DatasetView;
}): ReactElement {
  return (
    <div className="ui-dataset-toolbar" aria-label="Dataset view controls">
      <label className="ui-dataset-field ui-dataset-field--filter">
        <span>Filter</span>
        <input
          aria-label="Filter dataset rows"
          className="ui-input"
          placeholder="Filter rows…"
          value={props.view.filter}
          onChange={(event) => props.view.setFilter(event.target.value)}
        />
      </label>
      <label className="ui-dataset-field">
        <span>Sort</span>
        <select
          aria-label="Sort dataset rows"
          className="ui-select"
          value={props.view.sortColumn ?? ""}
          onChange={(event) =>
            props.view.setSortColumn(
              event.target.value === "" ? null : Number(event.target.value),
            )
          }
        >
          <option value="">Original order</option>
          {props.model.headers.map((header, column) => (
            <option key={column} value={column}>
              {header || `Column ${column + 1}`}
            </option>
          ))}
        </select>
      </label>
      <button
        className="ui-button ui-button--ghost ui-button--sm"
        type="button"
        disabled={props.view.sortColumn === null}
        onClick={props.view.toggleSortDirection}
      >
        Sort {props.view.sortDirection}
      </button>
    </div>
  );
}

function DatasetWindowControls(props: {
  readonly start: number;
  readonly total: number;
  readonly onChange: (start: number) => void;
}): ReactElement | null {
  if (props.total <= DATASET_DOM_ROW_BUDGET) return null;
  const end = Math.min(props.start + DATASET_DOM_ROW_BUDGET, props.total);
  return (
    <div
      className="ui-dataset-window-controls"
      aria-label="Dataset row navigation"
    >
      <span aria-live="polite">
        Showing rows {props.start + 1}–{end} of {props.total.toLocaleString()}
      </span>
      <button
        className="ui-button ui-button--ghost ui-button--sm"
        type="button"
        disabled={props.start === 0}
        onClick={() =>
          props.onChange(Math.max(0, props.start - DATASET_DOM_ROW_BUDGET))
        }
      >
        Previous rows
      </button>
      <button
        className="ui-button ui-button--ghost ui-button--sm"
        type="button"
        disabled={end === props.total}
        onClick={() => props.onChange(Math.min(end, props.total - 1))}
      >
        Next rows
      </button>
    </div>
  );
}
