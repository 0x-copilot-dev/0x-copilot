// The pure half of editing a connector-origin table: what a cell edit IS, and
// what the batch becomes on Save.
//
// The property everything here serves is one sentence from the design: **the
// object the user approves is the object that is sent.** The model downstream
// picks the write op and maps field NAMES onto its schema; it never retypes a
// value. So a value that leaves this module wrong is wrong in the user's real
// Linear, and no gate downstream can notice — the diff they approve is built
// from the same string.
//
// Three rules follow from that, and each one is narrower than it looks:
//
// 1. **A cell is edited RAW, never as it is displayed.** `formatValue` truncates
//    long text, reformats numbers and localises dates; seeding an input with any
//    of those and sending it back would silently destroy the datum. The read-only
//    cell keeps its presentation; the input holds the value.
//
// 2. **The typed text is sent verbatim, as text.** No coercion back to a number
//    or a boolean, however tempting: that is a second author deciding what the
//    user meant, and a connector that wanted a number tells us so at the gate —
//    loudly, before anything is written — which is the failure we can live with.
//
// 3. **A structured cell is not editable.** An object or an array has no honest
//    text form to round-trip: typing JSON into the cell would send a STRING that
//    merely looks like the list it replaced. The server's provenance audit is
//    exact about lists for the same reason, so a value that cannot be edited
//    faithfully is simply not offered.
//
// Rows are addressed by ROW KEY, never by index: a connector re-read can reorder
// or drop rows between the edit and the Save, and an index would then land the
// user's value on somebody else's row. A key that no longer resolves fails the
// whole Save rather than dropping that row quietly.

import type { SurfaceColumn, SurfaceSpec } from "../_shared/specTypes";
import { formatValue, resolvePath } from "../_shared/path";

/** One row's identity, as the surface can derive it from the row itself. */
const ID_FIELDS = [
  "id",
  "key",
  "uid",
  "row_id",
  "rowId",
  "identifier",
  "slug",
  "number",
] as const;

/** Bound on a generated row title — a ledger-visible string, so it is capped. */
const TITLE_MAX = 120;

/** A row as read: the object the surface rendered, and the provenance half of a
 *  write-back edit. A row that is not an object cannot carry one. */
export type ConnectorRow = Readonly<Record<string, unknown>>;

/** The batch, keyed by {@link cellKey} so re-editing one cell replaces it. */
export type PendingCellEdits = Readonly<Record<string, string>>;

/** One field's old→new diff, in the shape the write-back body carries. */
export interface ConnectorFieldChange {
  readonly field: string;
  readonly old?: unknown;
  readonly new?: unknown;
}

/** One row's batched edits plus that row exactly as it was read. */
export interface ConnectorRowEdit {
  readonly row_key: string;
  readonly title: string;
  readonly row: ConnectorRow;
  readonly changes: readonly ConnectorFieldChange[];
}

/**
 * A cell's stable identity across a re-read: which row, which column path.
 *
 * NUL joins the two halves. A connector id and a spec path are both arbitrary
 * text, so a printable separator would let `AB C` + `x` and `AB` + `C x` collide
 * — one row's typed value landing on another row's column.
 */
const KEY_SEPARATOR = "\u0000";

export function cellKey(rowKey: string, columnPath: string): string {
  return `${rowKey}${KEY_SEPARATOR}${columnPath}`;
}

/**
 * The two halves of a cell key, or `null` for a string that is not one.
 *
 * Exported so the separator itself never leaves this module: one place decides
 * how a key is built and how it is read, which is the only way those two stay
 * the same decision.
 */
export function splitCellKey(
  key: string,
): { readonly rowKey: string; readonly columnPath: string } | null {
  const at = key.indexOf(KEY_SEPARATOR);
  if (at < 0) return null;
  return { rowKey: key.slice(0, at), columnPath: key.slice(at + 1) };
}

/** The rows a table spec resolves to, or `[]`. Never throws. */
export function rowsOf(spec: SurfaceSpec, data: unknown): readonly unknown[] {
  const raw = spec.items_path ? resolvePath(data, spec.items_path) : undefined;
  return Array.isArray(raw) ? raw : [];
}

/** A row usable as write-back provenance, or `null` (a scalar, a hole, a list). */
export function asConnectorRow(row: unknown): ConnectorRow | null {
  return typeof row === "object" && row !== null && !Array.isArray(row)
    ? (row as ConnectorRow)
    : null;
}

/**
 * This row's key: its own identifier when it has one, else its position.
 *
 * The positional fallback is honest but weak — it is the case where the payload
 * carried no id at all, and a re-read that reorders rows genuinely cannot be
 * followed. It is still better than refusing to edit such a surface, because the
 * key travels with the batch and the row AS READ travels beside it, so the
 * server stages against the row the user was looking at.
 */
export function rowKeyFor(row: unknown, index: number): string {
  const record = asConnectorRow(row);
  if (record !== null) {
    for (const field of ID_FIELDS) {
      const value = record[field];
      if (typeof value === "string" && value.trim() !== "") return value;
      if (typeof value === "number" && Number.isFinite(value)) {
        return String(value);
      }
    }
  }
  return `row-${index}`;
}

/**
 * A human label for the row, for the staged write's own title.
 *
 * The first column that renders something is the label the user reads down the
 * table, so it is the one that names the row at the gate. Falls back to the key,
 * which always exists.
 */
export function rowTitleFor(
  columns: readonly SurfaceColumn[],
  row: unknown,
  rowKey: string,
): string {
  for (const column of columns) {
    const text = formatValue(resolvePath(row, column.path), column.format);
    if (text.trim() !== "") return text.slice(0, TITLE_MAX);
  }
  return rowKey.slice(0, TITLE_MAX);
}

/**
 * The cell's value as EDITABLE text, or `null` when it has no honest text form.
 *
 * `null`/`undefined` is an empty cell the user may fill — an absent value is
 * still a value they can author. An object or an array is not editable here at
 * all (rule 3 in the header).
 */
export function editableCellText(value: unknown): string | null {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (typeof value === "bigint") return value.toString();
  return null;
}

/** Whether this cell can be opened for editing at all. */
export function isEditableCell(row: unknown, column: SurfaceColumn): boolean {
  return editableCellText(resolvePath(row, column.path)) !== null;
}

export type ConnectorEditsResult =
  | { readonly ok: true; readonly edits: readonly ConnectorRowEdit[] }
  | { readonly ok: false; readonly reason: "unresolved" | "empty" };

/**
 * Turn the pending batch into the write-back body's `edits`.
 *
 * Every change carries `old` — the value AS READ, raw — beside the `new` the
 * user typed. Both halves matter server-side: `old` is what makes a write op
 * that echoes the prior value back provable, and `row` is what makes a binding
 * to an untouched field (the record id, the scoping key) provable. A trimmed
 * copy of either turns a legitimate binding into a refused save.
 *
 * Fails whole on a key that no longer resolves. Dropping that row instead would
 * save the user's OTHER edits while silently discarding one they can still see
 * on screen — the exact shape of a save the user believes happened.
 */
export function buildConnectorRowEdits(
  columns: readonly SurfaceColumn[],
  rows: readonly unknown[],
  pending: PendingCellEdits,
): ConnectorEditsResult {
  const entries = Object.entries(pending);
  if (entries.length === 0) return { ok: false, reason: "empty" };

  const byKey = new Map<
    string,
    { readonly row: unknown; readonly key: string }
  >();
  rows.forEach((row, index) => {
    const key = rowKeyFor(row, index);
    if (!byKey.has(key)) byKey.set(key, { row, key });
  });
  const byPath = new Map(columns.map((column) => [column.path, column]));

  const grouped = new Map<string, ConnectorFieldChange[]>();
  for (const [key, value] of entries) {
    const split = splitCellKey(key);
    if (split === null) return { ok: false, reason: "unresolved" };
    const { rowKey, columnPath } = split;
    const resolved = byKey.get(rowKey);
    if (resolved === undefined || !byPath.has(columnPath)) {
      return { ok: false, reason: "unresolved" };
    }
    const changes = grouped.get(rowKey) ?? [];
    changes.push({
      field: columnPath,
      old: resolvePath(resolved.row, columnPath) ?? null,
      new: value,
    });
    grouped.set(rowKey, changes);
  }

  const edits: ConnectorRowEdit[] = [];
  for (const [rowKey, changes] of grouped) {
    const resolved = byKey.get(rowKey);
    const record = asConnectorRow(resolved?.row);
    // A row with no object form cannot carry the provenance half, so there is
    // nothing to prove a binding against. Refusing here is the same fail-whole
    // as an unresolved key, and for the same reason.
    if (resolved === undefined || record === null) {
      return { ok: false, reason: "unresolved" };
    }
    edits.push({
      row_key: rowKey,
      title: rowTitleFor(columns, resolved.row, rowKey),
      row: record,
      changes,
    });
  }
  return { ok: true, edits };
}
