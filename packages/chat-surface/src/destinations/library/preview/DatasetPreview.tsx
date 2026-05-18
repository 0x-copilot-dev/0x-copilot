// <DatasetPreview /> — Library dataset cell-grid preview.
//
// Source:
//   docs/atlas-new-design/destinations/library-prd.md §3.4.3 (dataset
//     detail — schema panel + cell grid; first 200 rows lazily loaded
//     via GET /v1/library/<id>/preview?rows=200). This phase ships the
//     pure-presentation table; full virtualisation (`@tanstack/react-virtual`)
//     lands when the dep is added — the table here uses a windowed
//     overflow container that handles 100-row payloads well below the
//     React reconciliation budget.
//
// Invariants:
//   - **Pure presentation.** Host owns the GET; we render
//     `state.kind === "ready"` rows.
//   - **First 100 rows by default** (task constraint). Header lists
//     declared row count for "showing N of M".
//   - **Schema-driven columns.** Column headers come from the host-
//     supplied `schema` array; row values resolve by column name.
//   - **Windowed overflow container.** Wrapped in an overflow box with
//     a max height; sticky thead. Beyond ~200 rows the host should swap
//     to a virtualised renderer — we render rows directly here.

import type { CSSProperties, ReactElement } from "react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type DatasetColumnType =
  | "string"
  | "integer"
  | "float"
  | "boolean"
  | "date"
  | "datetime"
  | "json"
  | "binary";

export interface DatasetColumnSpec {
  readonly name: string;
  readonly type: DatasetColumnType;
  readonly nullable: boolean;
  /** Optional sample values for the schema panel; not rendered in the grid. */
  readonly sampleValues?: ReadonlyArray<string>;
}

/** Per-row payload — values keyed by column name; host pre-stringifies. */
export type DatasetRow = Readonly<
  Record<string, string | number | boolean | null>
>;

export type DatasetPreviewState =
  | { readonly kind: "idle" }
  | { readonly kind: "loading" }
  | { readonly kind: "error"; readonly message: string }
  | {
      readonly kind: "ready";
      readonly rows: ReadonlyArray<DatasetRow>;
      /** Total row count in the dataset (for the "N of M" header). */
      readonly totalRows: number;
    };

export interface DatasetPreviewProps {
  readonly schema: ReadonlyArray<DatasetColumnSpec>;
  readonly state: DatasetPreviewState;
  readonly onRetry?: () => void;
  /** Maximum rows rendered before the host should virtualise (default 100). */
  readonly maxRows?: number;
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const wrapperStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  borderRadius: 10,
  border: "1px solid var(--color-border)",
  background: "var(--color-surface)",
  overflow: "hidden",
};

const headerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  padding: "10px 12px",
  fontSize: 12,
  color: "var(--color-text-muted)",
  borderBottom: "1px solid var(--color-border)",
};

const scrollContainerStyle: CSSProperties = {
  maxHeight: 480,
  overflow: "auto",
  // Bottom border for separation from the rest of the layout.
  background: "var(--color-bg)",
};

const tableStyle: CSSProperties = {
  width: "100%",
  borderCollapse: "collapse",
  tableLayout: "auto",
  fontSize: 12,
  color: "var(--color-text)",
};

const theadCellStyle: CSSProperties = {
  position: "sticky",
  top: 0,
  background: "var(--color-bg-elevated)",
  borderBottom: "1px solid var(--color-border)",
  padding: "8px 10px",
  textAlign: "left",
  fontWeight: 600,
  fontSize: 11,
  textTransform: "uppercase",
  letterSpacing: 0.4,
  color: "var(--color-text-muted)",
  whiteSpace: "nowrap",
  zIndex: 1,
};

const tdStyle: CSSProperties = {
  padding: "6px 10px",
  borderBottom: "1px solid var(--color-border)",
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
  maxWidth: 320,
};

const nullCellStyle: CSSProperties = {
  ...tdStyle,
  color: "var(--color-text-subtle)",
  fontStyle: "italic",
};

const placeholderStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  justifyContent: "center",
  gap: 8,
  padding: 32,
  minHeight: 200,
  color: "var(--color-text-muted)",
  fontSize: 13,
  textAlign: "center",
};

const errorButtonStyle: CSSProperties = {
  height: 30,
  padding: "0 12px",
  borderRadius: 6,
  border: "1px solid var(--color-border-strong)",
  background: "transparent",
  color: "var(--color-accent)",
  fontSize: 12,
  fontWeight: 600,
  cursor: "pointer",
};

const typeBadgeStyle: CSSProperties = {
  marginLeft: 6,
  fontSize: 10,
  color: "var(--color-text-subtle)",
  textTransform: "lowercase",
  letterSpacing: 0,
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

function renderCell(
  key: string,
  value: string | number | boolean | null,
): ReactElement {
  if (value === null) {
    return (
      <td key={key} style={nullCellStyle}>
        —
      </td>
    );
  }
  // Stringify booleans / numbers identically so the column doesn't
  // jitter on type-mixed columns.
  const display = typeof value === "string" ? value : String(value);
  return (
    <td key={key} style={tdStyle} title={display}>
      {display}
    </td>
  );
}

export function DatasetPreview({
  schema,
  state,
  onRetry,
  maxRows = 100,
}: DatasetPreviewProps): ReactElement {
  if (schema.length === 0) {
    return (
      <div
        style={wrapperStyle}
        data-testid="library-dataset-preview"
        data-state="no-schema"
      >
        <div style={placeholderStyle}>
          <span style={{ fontWeight: 600 }}>No schema declared</span>
          <span style={{ color: "var(--color-text-subtle)" }}>
            This dataset has no column schema and cannot be previewed.
          </span>
        </div>
      </div>
    );
  }

  if (state.kind === "idle" || state.kind === "loading") {
    return (
      <div
        style={wrapperStyle}
        data-testid="library-dataset-preview"
        data-state={state.kind}
      >
        <div style={headerStyle}>
          <span>{schema.length} columns</span>
          <span>{state.kind === "loading" ? "Loading rows…" : "Preview"}</span>
        </div>
        <div style={scrollContainerStyle}>
          <table style={tableStyle} role="table">
            <thead>
              <tr>
                {schema.map((col) => (
                  <th key={col.name} style={theadCellStyle} scope="col">
                    {col.name}
                    <span style={typeBadgeStyle}>{col.type}</span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {Array.from({ length: 4 }).map((_, rowIdx) => (
                <tr key={rowIdx} aria-hidden="true">
                  {schema.map((col) => (
                    <td key={col.name} style={tdStyle}>
                      <span
                        style={{
                          display: "inline-block",
                          width: 80,
                          height: 10,
                          background: "var(--color-surface-muted)",
                          borderRadius: 3,
                          opacity: 0.6,
                        }}
                      />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  if (state.kind === "error") {
    return (
      <div
        style={wrapperStyle}
        data-testid="library-dataset-preview"
        data-state="error"
      >
        <div style={placeholderStyle} role="alert">
          <span style={{ fontWeight: 600 }}>Could not load rows</span>
          <span style={{ color: "var(--color-text-subtle)" }}>
            {state.message}
          </span>
          {onRetry !== undefined && (
            <button
              type="button"
              style={errorButtonStyle}
              onClick={onRetry}
              data-testid="library-dataset-preview-retry"
            >
              Retry
            </button>
          )}
        </div>
      </div>
    );
  }

  // state.kind === "ready"
  const rows = state.rows.slice(0, maxRows);
  const shown = rows.length;
  const total = state.totalRows;
  return (
    <div
      style={wrapperStyle}
      data-testid="library-dataset-preview"
      data-state="ready"
    >
      <div style={headerStyle}>
        <span>{schema.length} columns</span>
        <span>
          Showing {shown.toLocaleString()} of {total.toLocaleString()} rows
        </span>
      </div>
      <div style={scrollContainerStyle}>
        <table style={tableStyle} role="table">
          <thead>
            <tr>
              {schema.map((col) => (
                <th key={col.name} style={theadCellStyle} scope="col">
                  {col.name}
                  <span style={typeBadgeStyle}>{col.type}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIdx) => (
              <tr key={rowIdx} data-testid="library-dataset-preview-row">
                {schema.map((col) =>
                  renderCell(col.name, row[col.name] ?? null),
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
