import type { CSSProperties, ReactElement } from "react";

import type { SaaSRendererAdapter } from "@0x-copilot/chat-surface";

import { SURFACE_PALETTE as PALETTE } from "../_shared/palette";
import {
  cardStyle,
  DiffFieldRow,
  EmptyBody,
  fieldGridStyle,
  NoSpecView,
  pageStyle,
  paintsAsChip,
  SurfaceHeader,
  SurfaceLinkRow,
  SurfaceValueBadge,
  toolNameFromState,
} from "../_shared/primitives";
import {
  formatValue,
  isNumericFormat,
  magnitudeShares,
  resolvePath,
} from "../_shared/path";
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
import { rowsOf } from "./connectorEdits";
import { hostConnectorEditor } from "./connectorEditor";
import { EditableConnectorTable } from "./EditableConnectorTable";

const KICKER = "Table";

/** Hard cap on painted rows — the render-budget guard (PRD-03). */
export const ROW_RENDER_CAP = 200;

/**
 * The `table://` archetype — columns from the spec, rows from `items_path`.
 * ≥50 columns window through the shared `sheet/_columns` helper; >200 rows show
 * a "showing 200 of N" cap. Spec-less state falls back to the no-spec view
 * (PRD-02).
 *
 * Two renderings, one archetype. Read-only is the default and the only thing a
 * connector table gets unless the HOST attached a write-back grant — an object
 * it built around its own Transport and the run that owns the surface, never a
 * field decoded from tool output, never something a model-authored `SurfaceSpec`
 * could carry. When it is present the rows render as editable CELLS, and Save
 * stages them for approval rather than writing anything.
 *
 * The grant alone is not enough: a spec-less table has no columns to address a
 * cell by, so it stays read-only however the host is wired. Editability is the
 * conjunction of "the host opened this" and "this surface has a shape".
 */
export function TableRenderer(state: SurfaceState | unknown): ReactElement {
  const spec = specFromState(state);
  const data = dataFromState(state);
  const editor = hostConnectorEditor(state);
  return (
    <article
      style={pageStyle}
      data-testid="table-renderer"
      data-mode="current"
      data-spec={spec ? "present" : "absent"}
      data-editable={spec && editor !== null ? "true" : "false"}
      aria-label="Table surface"
    >
      <section style={cardStyle}>
        {spec === undefined ? (
          renderFallback(state, data)
        ) : editor === null ? (
          renderWithSpec(spec, data, toolNameFromState(state))
        ) : (
          <EditableConnectorTable
            spec={spec}
            data={data}
            rows={rowsOf(spec, data)}
            title={titleFor(spec, data, toolNameFromState(state))}
            actions={editor}
          />
        )}
      </section>
    </article>
  );
}

/**
 * The surface's own title — computed here so the read view and the editable view
 * cannot end up calling one surface two things.
 *
 * A table's title lives OUTSIDE its rows: `{"team": {...}, "issues": [...]}`
 * yields `team.name`. Plenty of list payloads carry no such headline at all, and
 * because `title_path` is schema-REQUIRED the inference floor has to emit some
 * path even when none resolves. Falling through to the header's "Untitled" in
 * that case reads as a broken surface rather than an untitled one, so the tool
 * that produced the rows is the honest label — and it is the fallback the
 * floor's own spec (PRD §3.3 step 4) always intended. `?? ""` keeps
 * `SurfaceHeader`'s own "Untitled" as the last resort, for the one case where
 * even the tool has no name to give.
 */
function titleFor(
  spec: SurfaceSpec,
  data: unknown,
  toolName: string | undefined,
): string {
  return formatValue(resolvePath(data, spec.title_path)) || (toolName ?? "");
}

function renderWithSpec(
  spec: SurfaceSpec,
  data: unknown,
  toolName: string | undefined,
): ReactElement {
  const title = titleFor(spec, data, toolName);
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

  // Magnitude per numeric column, computed once over the rows actually painted.
  // Scoped to the visible slice on purpose: the bars must be a true comparison
  // of what is on screen, and scaling them to an unseen row beyond the render
  // cap would make every visible bar shorter for a reason the user cannot see.
  const sharesByColumn = new Map<number, readonly (number | null)[]>();
  visibleColumns.forEach((column, index) => {
    if (!isNumericFormat(column.format)) return;
    sharesByColumn.set(
      index,
      magnitudeShares(visibleRows.map((row) => resolvePath(row, column.path))),
    );
  });

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
                    // Numeric headers lean toward the source hue so the measured
                    // columns are findable without a rule or a fill.
                    className={
                      isNumericFormat(column.format)
                        ? "sf-col--numeric"
                        : undefined
                    }
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
                <tr
                  key={rowIndex}
                  className="sf-row"
                  data-testid={`table-row-${rowIndex}`}
                >
                  {visibleColumns.map((column, colIndex) => {
                    const share = sharesByColumn.get(colIndex)?.[rowIndex];
                    const bar =
                      typeof share === "number" && share > 0 ? share : null;
                    // The cell's absolute column index — what every test id in
                    // this row is keyed on, so a windowed table still names the
                    // column the user is looking at rather than its offset.
                    const column0 = columnWindow.startColumn + colIndex;
                    const text = formatValue(
                      resolvePath(row, column.path),
                      column.format,
                    );
                    return (
                      <td
                        key={`${column.path}:${colIndex}`}
                        className={
                          bar === null ? undefined : "sf-cell--numeric"
                        }
                        style={tdStyle(column)}
                        data-testid={`table-cell-${rowIndex}-${column0}`}
                      >
                        {bar === null ? null : (
                          // Painted BEHIND the value, never over it, and hidden
                          // from assistive tech: the bar restates the number
                          // visually, so announcing it would just repeat the
                          // cell. `left` is what shrinks it, keeping every bar
                          // anchored to the right edge where the digits end.
                          <span
                            aria-hidden="true"
                            className="sf-value-bar"
                            data-testid={`table-value-bar-${rowIndex}-${column0}`}
                            style={{ left: `${(1 - bar) * 100}%` }}
                          />
                        )}
                        <span
                          className={
                            bar === null ? undefined : "sf-value-bar__value"
                          }
                        >
                          {/* A `badge` column is a closed vocabulary the user
                              scans DOWN rather than reads — the chip is what
                              makes it scannable. Nested inside the value span
                              rather than replacing it so the bar's stacking
                              context is untouched; the two never co-occur
                              anyway, because only a numeric column is measured. */}
                          {paintsAsChip(column.format, text) ? (
                            <SurfaceValueBadge
                              value={text}
                              testId={`table-badge-${rowIndex}-${column0}`}
                            />
                          ) : (
                            text
                          )}
                        </span>
                      </td>
                    );
                  })}
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

/**
 * Both halves of the boundary value: `state` is where a tool identity would
 * ride, `data` is the payload the note is about.
 *
 * A bare array of rows has no top-level fields of its own, so the note's "the
 * payload as the tool sent it" is served by the FIRST row — the shape the rest
 * of them share. Everything else goes in whole.
 *
 * The badge is what keeps that honest. Without it a 200-row payload rendered as
 * one row's fields would read as a tool that returned a single object, so the
 * header states the true count in the same words the spec-driven path uses.
 */
function renderFallback(state: unknown, data: unknown): ReactElement {
  const rows = Array.isArray(data) ? data : [];
  return (
    <>
      <SurfaceHeader
        kicker={KICKER}
        title="Table"
        badge={
          rows.length > 0
            ? `${rows.length} row${rows.length === 1 ? "" : "s"}`
            : undefined
        }
      />
      <NoSpecView
        // The first row that actually CARRIES something, not merely the first.
        // `rows[0]` can be a hole: `[null, {id:2}]` handed NoSpecView `null`, so
        // the note said "The tool returned no payload." directly beneath a badge
        // reading "2 rows" — two mutually exclusive statements in one card, and
        // the payload's real shape never shown. Falling back to the whole value
        // when every entry is a hole keeps the badge and the note describing the
        // same thing.
        data={rows.find((row) => row !== null && row !== undefined) ?? data}
        tool={toolNameFromState(state)}
      />
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
    // Read through a variable rather than naming the colour directly. An inline
    // declaration beats any non-`!important` stylesheet rule regardless of
    // specificity, so a literal here made `.sf-col--numeric` inert: the class
    // was present, the sheet was loaded, and the numeric header still painted
    // flat grey. Indirection is what lets the class win — it sets the variable
    // instead of fighting the declaration. The fallback keeps every non-numeric
    // header, and any host that has not loaded the sheet, exactly as before.
    color: `var(--sf-col-color, ${PALETTE.textLo})`,
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
