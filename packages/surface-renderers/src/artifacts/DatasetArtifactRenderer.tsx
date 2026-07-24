import { useEffect, useMemo, useState, type ReactElement } from "react";

import type { ArtifactRenderState } from "./model";
import { previewNotice } from "./model";

const MAX_PREVIEW_ROWS = 10_000;
const MAX_PREVIEW_CELLS = 100_000;
const MAX_PREVIEW_COLUMNS = 100;
const TABLE_WINDOW_ROWS = 100;

export type ArtifactRevisionSaveOutcome = "saved" | "conflict" | "error";

/** Host-created editor capability. It is never decoded from artifact content. */
export interface DatasetEditorActions {
  readonly disabled: boolean;
  readonly saveRevision: (
    source: string,
  ) => Promise<ArtifactRevisionSaveOutcome>;
}

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
  readonly editable: boolean;
  readonly fidelityWarning: string | null;
  readonly serialize: (patch: DatasetPatch) => string;
  readonly safeExport: (patch: DatasetPatch) => string;
}

/**
 * RFC4180-aware tokenizer that retains every untouched source fragment. A
 * patched revision serializes only changed cells, preserving BOM, delimiters,
 * quoting and row endings for all untouched cells exactly.
 */
export function parseLosslessDelimited(
  text: string,
  delimiter = ",",
  maxRows = MAX_PREVIEW_ROWS,
  maxCells = MAX_PREVIEW_CELLS,
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
  maxRows = MAX_PREVIEW_ROWS,
  maxCells = MAX_PREVIEW_CELLS,
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
    const records = parsed.slice(0, MAX_PREVIEW_ROWS) as readonly Record<
      string,
      unknown
    >[];
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
      editable: records.length === parsed.length,
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
  if (artifact.text === undefined) return null;
  if (artifact.mediaType === "application/json") {
    return parseJsonObjectRows(artifact.text);
  }
  const delimiter =
    artifact.mediaType === "text/tab-separated-values" ? "\t" : ",";
  if (
    artifact.mediaType !== "text/csv" &&
    artifact.mediaType !== "text/tab-separated-values" &&
    artifact.mediaType !== "text/plain"
  ) {
    return null;
  }
  const parsed = parseLosslessDelimited(artifact.text, delimiter);
  const rows = parsed.rows.map((row) => row.cells.map((cell) => cell.value));
  const [headers = [], ...body] = rows;
  return {
    kind: "delimited",
    headers: headers.slice(0, MAX_PREVIEW_COLUMNS),
    rows: body.map((row) => row.slice(0, MAX_PREVIEW_COLUMNS)),
    formulaCells: formulaCount(rows),
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

function editorActionsFor(
  artifact: ArtifactRenderState,
): DatasetEditorActions | null {
  const candidate = (
    artifact as ArtifactRenderState & {
      readonly datasetEditor?: unknown;
    }
  ).datasetEditor;
  if (typeof candidate !== "object" || candidate === null) return null;
  const value = candidate as Partial<DatasetEditorActions>;
  return typeof value.disabled === "boolean" &&
    typeof value.saveRevision === "function"
    ? (value as DatasetEditorActions)
    : null;
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
  if (model === null) {
    return (
      <div className="ui-card ui-body" data-testid="artifact-dataset-fallback">
        This dataset format cannot be safely previewed. Download the exact
        artifact bytes.
      </div>
    );
  }
  const actions = editorActionsFor(props.artifact);
  return (
    <section className="ui-card" data-testid="artifact-dataset-renderer">
      {model.formulaCells > 0 ? (
        <p className="ui-caption" role="note">
          Formula-like cells are shown as text and are never evaluated. Exact
          download preserves them; safe export is explicit.
        </p>
      ) : null}
      {model.fidelityWarning !== null ? (
        <p className="ui-caption" role="note">
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

function DatasetGrid(props: { readonly model: DatasetModel }): ReactElement {
  const [windowStart, setWindowStart] = useState(0);
  const windowedRows = props.model.rows.slice(
    windowStart,
    windowStart + TABLE_WINDOW_ROWS,
  );
  return (
    <>
      <DatasetWindowControls
        start={windowStart}
        total={props.model.rows.length}
        onChange={setWindowStart}
      />
      <div className="ui-table-wrap">
        <table role="grid" aria-label="Dataset preview">
          <thead>
            <tr role="row">
              {props.model.headers.map((value, index) => (
                <th key={index} role="columnheader" scope="col">
                  {value}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {windowedRows.map((row, rowIndex) => (
              <tr key={windowStart + rowIndex} role="row">
                {props.model.headers.map((_, columnIndex) => (
                  <td key={columnIndex} role="gridcell">
                    {row[columnIndex] ?? ""}
                  </td>
                ))}
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
  const [windowStart, setWindowStart] = useState(0);
  useEffect(() => {
    setPatch({});
    setStatus("idle");
    setPending(null);
    setWindowStart(0);
  }, [props.artifactKey]);

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
  const windowedRows = props.model.rows.slice(
    windowStart,
    windowStart + TABLE_WINDOW_ROWS,
  );
  return (
    <section aria-label="Dataset cell editor">
      <p className="ui-caption" id="dataset-cell-editor-help">
        Edits are held in memory until you save a complete immutable revision.
        Keyboard focus moves through cells normally.
      </p>
      <DatasetWindowControls
        start={windowStart}
        total={props.model.rows.length}
        onChange={setWindowStart}
      />
      <div className="ui-table-wrap">
        <table
          role="grid"
          aria-label="Dataset cell editor"
          aria-describedby="dataset-cell-editor-help"
        >
          <thead>
            <tr role="row">
              {props.model.headers.map((header, column) => (
                <th key={column} role="columnheader" scope="col">
                  <input
                    aria-label={`Header ${column + 1}`}
                    className="ui-input"
                    disabled={!canEdit}
                    value={patch[cellKey(0, column)] ?? header}
                    onChange={(event) => update(0, column, event.target.value)}
                  />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {windowedRows.map((row, offset) => {
              const rowIndex = windowStart + offset;
              return (
                <tr key={rowIndex} role="row">
                  {props.model.headers.map((header, column) => {
                    const rowNumber = rowIndex + 2;
                    return (
                      <td key={column} role="gridcell">
                        <input
                          aria-label={`${header || `Column ${column + 1}`}, row ${rowNumber}`}
                          className="ui-input"
                          disabled={!canEdit}
                          value={
                            patch[cellKey(rowIndex + 1, column)] ??
                            row[column] ??
                            ""
                          }
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
        <div className="ui-card" role="alert">
          <p className="ui-caption">
            {props.model.fidelityWarning ??
              "Formula-like cells will be prefixed with an apostrophe only in this new safe-export revision. The canonical revision remains unchanged."}
          </p>
          <button
            className="ui-button"
            type="button"
            onClick={() => commit(pending.source)}
          >
            {pending.label}
          </button>
          <button
            className="ui-button ui-button--ghost"
            type="button"
            onClick={() => setPending(null)}
          >
            Cancel
          </button>
        </div>
      ) : null}
      {status === "conflict" ? (
        <p className="ui-caption" role="alert">
          A newer revision exists. Your in-memory cell edits are preserved;
          compare and rebase manually before saving.
        </p>
      ) : null}
      {status === "error" ? (
        <p className="ui-caption" role="alert">
          This dataset could not be saved. Your in-memory cell edits are still
          here.
        </p>
      ) : null}
      <div className="ui-toolbar" aria-label="Dataset revision actions">
        <button
          className="ui-button"
          type="button"
          disabled={!canEdit}
          onClick={() => prepareSave(false)}
        >
          {status === "saving" ? "Saving…" : "Save patched revision"}
        </button>
        <button
          className="ui-button ui-button--ghost"
          type="button"
          disabled={!canEdit || props.model.formulaCells === 0}
          onClick={() => prepareSave(true)}
        >
          Create formula-safe revision
        </button>
      </div>
    </section>
  );
}

function DatasetWindowControls(props: {
  readonly start: number;
  readonly total: number;
  readonly onChange: (start: number) => void;
}): ReactElement | null {
  if (props.total <= TABLE_WINDOW_ROWS) return null;
  const end = Math.min(props.start + TABLE_WINDOW_ROWS, props.total);
  return (
    <div className="ui-toolbar" aria-label="Dataset row navigation">
      <span className="ui-caption" aria-live="polite">
        Showing rows {props.start + 1}–{end} of {props.total.toLocaleString()}
      </span>
      <button
        className="ui-button ui-button--ghost"
        type="button"
        disabled={props.start === 0}
        onClick={() =>
          props.onChange(Math.max(0, props.start - TABLE_WINDOW_ROWS))
        }
      >
        Previous rows
      </button>
      <button
        className="ui-button ui-button--ghost"
        type="button"
        disabled={end === props.total}
        onClick={() => props.onChange(Math.min(end, props.total - 1))}
      >
        Next rows
      </button>
    </div>
  );
}
