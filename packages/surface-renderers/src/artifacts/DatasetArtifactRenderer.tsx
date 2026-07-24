import type { ReactElement } from "react";

import type { ArtifactRenderState } from "./model";
import { previewNotice } from "./model";

export interface CsvTable {
  readonly rows: readonly (readonly string[])[];
  readonly formulaCells: number;
}

/** RFC4180-aware parser: quoted commas/newlines, CRLF, BOM, empty cells and Unicode survive untouched. */
export function parseCsv(
  text: string,
  maxRows = 10_000,
  maxCells = 100_000,
  delimiter = ",",
): CsvTable {
  const source = text.charCodeAt(0) === 0xfeff ? text.slice(1) : text;
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  let cells = 0;
  const pushCell = (): boolean => {
    row.push(cell);
    cells += 1;
    cell = "";
    return cells <= maxCells;
  };
  const pushRow = (): boolean => {
    if (!pushCell()) return false;
    rows.push(row);
    row = [];
    return rows.length < maxRows;
  };
  for (let index = 0; index < source.length; index += 1) {
    const char = source[index]!;
    if (quoted) {
      if (char === '"' && source[index + 1] === '"') {
        cell += '"';
        index += 1;
      } else if (char === '"') quoted = false;
      else cell += char;
      continue;
    }
    if (char === '"' && cell === "") {
      quoted = true;
      continue;
    }
    if (char === delimiter) {
      if (!pushCell()) break;
      continue;
    }
    if (char === "\r" || char === "\n") {
      if (char === "\r" && source[index + 1] === "\n") index += 1;
      if (!pushRow()) break;
      continue;
    }
    cell += char;
  }
  if (row.length > 0 || cell !== "" || source.endsWith(delimiter)) pushRow();
  const formulaCells = rows
    .flat()
    .filter((value) => /^[=+\-@]/.test(value)).length;
  return { rows, formulaCells };
}

function parseJsonObjectRows(text: string): CsvTable | null {
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
    const rows = parsed.slice(0, 10_000) as readonly Record<string, unknown>[];
    const headers = [...new Set(rows.flatMap((row) => Object.keys(row)))].slice(
      0,
      100,
    );
    const tableRows = [
      headers,
      ...rows.map((row) => headers.map((header) => jsonCell(row[header]))),
    ];
    return {
      rows: tableRows,
      formulaCells: tableRows.flat().filter((value) => /^[=+\-@]/.test(value))
        .length,
    };
  } catch {
    return null;
  }
}

function jsonCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean")
    return String(value);
  // Nested JSON is display-only in this fixed table: it remains an inert
  // string rather than becoming an expandable model-authored UI structure.
  try {
    return JSON.stringify(value);
  } catch {
    return "[unserializable value]";
  }
}

function parseDataset(artifact: ArtifactRenderState): CsvTable | null {
  if (artifact.text === undefined) return null;
  if (artifact.mediaType === "text/tab-separated-values") {
    return parseCsv(artifact.text, 10_000, 100_000, "\t");
  }
  if (artifact.mediaType === "application/json") {
    return parseJsonObjectRows(artifact.text);
  }
  return artifact.mediaType === "text/csv" ||
    artifact.mediaType === "text/plain"
    ? parseCsv(artifact.text)
    : null;
}

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
  const csv = parseDataset(artifact);
  if (csv === null) {
    return (
      <div className="ui-card ui-body" data-testid="artifact-dataset-fallback">
        This dataset format cannot be safely previewed. Download the exact
        artifact bytes.
      </div>
    );
  }
  const [head = [], ...body] = csv.rows;
  return (
    <section className="ui-card" data-testid="artifact-dataset-renderer">
      {csv.formulaCells > 0 ? (
        <p className="ui-caption" role="note">
          Formula-like cells are shown as text and are never evaluated.
        </p>
      ) : null}
      <div className="ui-table-wrap">
        <table>
          <thead>
            <tr>
              {head.map((value, index) => (
                <th key={index}>{value}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {body.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {head.map((_, cellIndex) => (
                  <td key={cellIndex}>{row[cellIndex] ?? ""}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
