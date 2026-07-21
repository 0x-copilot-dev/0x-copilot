import type { CSSProperties, ReactElement } from "react";

import type { SaaSRendererAdapter } from "@0x-copilot/chat-surface";

import { SURFACE_PALETTE as PALETTE } from "../_shared/palette";
import {
  cardStyle,
  DiffFieldRow,
  EmptyBody,
  fieldGridStyle,
  GenericFieldList,
  pageStyle,
  PreparingHint,
  SurfaceHeader,
  SurfaceLinkRow,
} from "../_shared/primitives";
import { formatValue, isNumericFormat, resolvePath } from "../_shared/path";
import {
  changesFromDiff,
  dataFromState,
  specFromState,
  type SurfaceColumn,
  type SurfaceDiff,
  type SurfaceSpec,
  type SurfaceState,
} from "../_shared/specTypes";
import { resolveColumnWindow } from "../sheet/_columns";

const KICKER = "Table";

/** Hard cap on painted rows — the render-budget guard (PRD-03). */
export const ROW_RENDER_CAP = 200;

/**
 * The `table://` archetype — columns from the spec, rows from `items_path`.
 * ≥50 columns window through the shared `sheet/_columns` helper; >200 rows show
 * a "showing 200 of N" cap. Spec-less state falls back to the generic list.
 */
export function TableRenderer(state: SurfaceState | unknown): ReactElement {
  const spec = specFromState(state);
  const data = dataFromState(state);
  return (
    <article
      style={pageStyle}
      data-testid="table-renderer"
      data-mode="current"
      data-spec={spec ? "present" : "absent"}
      aria-label="Table surface"
    >
      <section style={cardStyle}>
        {spec ? renderWithSpec(spec, data) : renderFallback(data)}
      </section>
    </article>
  );
}

function renderWithSpec(spec: SurfaceSpec, data: unknown): ReactElement {
  const title = formatValue(resolvePath(data, spec.title_path));
  const columns: readonly SurfaceColumn[] = spec.columns ?? [];
  const rawItems = spec.items_path
    ? resolvePath(data, spec.items_path)
    : undefined;
  const items = Array.isArray(rawItems) ? rawItems : [];

  // Reuse the tier-1 sheet windowing so ≥50-column tables don't paint every
  // column. `resolveColumnWindow` reads only `headers` + `viewport`.
  const columnWindow = resolveColumnWindow({
    sheetId: "table",
    regionId: "table",
    headers: columns.map((column) => column.label),
    rows: [],
  });
  const visibleColumns = columns.slice(
    columnWindow.startColumn,
    columnWindow.endColumn,
  );
  const visibleRows = items.slice(0, ROW_RENDER_CAP);
  const truncated = items.length > ROW_RENDER_CAP;

  return (
    <>
      <SurfaceHeader
        kicker={KICKER}
        title={title}
        badge={`${items.length} row${items.length === 1 ? "" : "s"}`}
      />
      {columns.length === 0 ? (
        <EmptyBody>No columns configured.</EmptyBody>
      ) : items.length === 0 ? (
        <EmptyBody>No rows to display.</EmptyBody>
      ) : (
        <div style={scrollStyle}>
          <table style={tableStyle} data-testid="table-grid">
            <thead>
              <tr>
                {visibleColumns.map((column, index) => (
                  <th
                    key={`${column.path}:${index}`}
                    scope="col"
                    style={thStyle(column)}
                    data-testid={`table-header-${columnWindow.startColumn + index}`}
                  >
                    {column.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visibleRows.map((row, rowIndex) => (
                <tr key={rowIndex} data-testid={`table-row-${rowIndex}`}>
                  {visibleColumns.map((column, colIndex) => (
                    <td
                      key={`${column.path}:${colIndex}`}
                      style={tdStyle(column)}
                      data-testid={`table-cell-${rowIndex}-${columnWindow.startColumn + colIndex}`}
                    >
                      {formatValue(
                        resolvePath(row, column.path),
                        column.format,
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {truncated ? (
        <div style={capStyle} data-testid="table-row-cap">
          Showing {ROW_RENDER_CAP} of {items.length} rows.
        </div>
      ) : null}
      {columnWindow.virtualized ? (
        <div style={capStyle} data-testid="table-column-cap">
          Showing {visibleColumns.length} of {columnWindow.totalColumns}{" "}
          columns.
        </div>
      ) : null}
      {spec.link ? (
        <SurfaceLinkRow
          label={spec.link.label}
          value={resolvePath(data, spec.link.url_path)}
        />
      ) : null}
    </>
  );
}

function renderFallback(data: unknown): ReactElement {
  const rows = Array.isArray(data) ? data : [];
  return (
    <>
      <SurfaceHeader kicker={KICKER} title="Table" />
      <PreparingHint />
      {rows.length > 0 ? (
        <GenericFieldList data={rows[0]} format={(v) => formatValue(v)} />
      ) : (
        <GenericFieldList data={data} format={(v) => formatValue(v)} />
      )}
    </>
  );
}

/** Diff view — one before→after row per proposed cell/field change. */
export function TableDiffRenderer(diff: SurfaceDiff | unknown): ReactElement {
  const spec = specFromState(diff);
  const changes = changesFromDiff(diff);
  const labelFor = new Map<string, string>(
    (spec?.columns ?? []).map((column) => [column.path, column.label]),
  );
  return (
    <article
      style={pageStyle}
      data-testid="table-renderer"
      data-mode="diff"
      aria-label="Table surface — proposed changes"
    >
      <section style={cardStyle}>
        <SurfaceHeader
          kicker={KICKER}
          title="Proposed changes"
          badge={`${changes.length} change${changes.length === 1 ? "" : "s"}`}
        />
        {changes.length > 0 ? (
          <div style={fieldGridStyle} data-testid="table-diff-rows">
            {changes.map((change, index) => (
              <DiffFieldRow
                key={`${change.field}:${index}`}
                fieldKey={change.field}
                label={labelFor.get(change.field) ?? change.field}
                previousValue={formatValue(change.old)}
                nextValue={formatValue(change.new)}
              />
            ))}
          </div>
        ) : (
          <EmptyBody>No pending changes.</EmptyBody>
        )}
      </section>
    </article>
  );
}

export const tableAdapter: SaaSRendererAdapter<SurfaceState, SurfaceDiff> = {
  scheme: "table",
  matches: (uri: string) => uri.startsWith("table://"),
  renderCurrent: (state: SurfaceState): ReactElement => TableRenderer(state),
  renderDiff: (diff: SurfaceDiff): ReactElement => TableDiffRenderer(diff),
  metadata: {
    origin: "first-party",
    schemaVersion: 1,
  },
};

const scrollStyle: CSSProperties = {
  overflowX: "auto",
  border: `1px solid ${PALETTE.border}`,
  borderRadius: 10,
};

const tableStyle: CSSProperties = {
  borderCollapse: "collapse",
  width: "100%",
  fontSize: 13,
};

function thStyle(column: SurfaceColumn): CSSProperties {
  return {
    textAlign: column.align === "end" ? "right" : "left",
    padding: "8px 12px",
    color: PALETTE.textLo,
    fontSize: 11,
    letterSpacing: 0.4,
    textTransform: "uppercase",
    fontWeight: 600,
    borderBottom: `1px solid ${PALETTE.border}`,
    whiteSpace: "nowrap",
    background: PALETTE.surfaceMute,
  };
}

const capStyle: CSSProperties = {
  fontSize: 12,
  color: PALETTE.textLo,
  letterSpacing: 0.3,
};

function tdStyle(column: SurfaceColumn): CSSProperties {
  return {
    textAlign: column.align === "end" ? "right" : "left",
    padding: "8px 12px",
    color: PALETTE.textHi,
    borderBottom: `1px solid ${PALETTE.border}`,
    fontVariantNumeric: isNumericFormat(column.format)
      ? "tabular-nums"
      : "normal",
    overflowWrap: "anywhere",
  };
}
