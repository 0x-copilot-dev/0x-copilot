// A connector read, edited where it renders.
//
// The table archetype draws rows a connector returned. Until now that was the
// end of it — the user could read their Linear issues and could not touch them,
// while an ARTIFACT table three panes away was fully editable. The gesture is
// the same gesture; only the destination differs, and the destination is exactly
// why this half waited: an artifact edit is a local revision the user can
// restore, and this one becomes a request to a real vendor.
//
// So Save here does not write. It POSTs the batch to the write-back route, which
// maps it onto ONE connector op and STAGES it, and the rows come back proposed.
// The decision is taken at the write gate that already exists, by the surface
// that already renders staged rows. Nothing in this component approves anything,
// and the absence of an Approve control here is the design, not an omission.
//
// What it shares with `EditableDocument`, deliberately and to the letter:
//
//   * the rendered cell IS the control — click or Enter opens an input in place,
//     never a box underneath restating the surface as text;
//   * nothing leaves on a keystroke — edits accumulate locally, one Save sends
//     the batch, Discard drops it;
//   * a failure keeps the batch on screen. Losing a user's typing to a 503 is
//     the one outcome that makes an editor untrustworthy.
//
// What differs, and must keep differing: a successful Save does not clear the
// edits and return the cells to their read values. It marks them STAGED and
// freezes them, because the connector still holds the OLD value and the user's
// new one is a proposal. A cell that snapped back to "Todo" the moment a save
// succeeded would read as a save that failed; a cell that showed "In Progress"
// with no marking would read as a write that happened. Neither is true, and the
// gap between them is where an unnoticed write lives.

import {
  useMemo,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type ReactElement,
} from "react";

import type { ConnectorSurfaceEditorActions } from "@0x-copilot/chat-surface";

import { SURFACE_PALETTE as PALETTE } from "../_shared/palette";
import { formatValue, resolvePath } from "../_shared/path";
import {
  paintsAsChip,
  SurfaceHeader,
  SurfaceLinkRow,
  SurfaceValueBadge,
} from "../_shared/primitives";
import type { SurfaceColumn, SurfaceSpec } from "../_shared/specTypes";
import {
  buildConnectorRowEdits,
  cellKey,
  editableCellText,
  rowKeyFor,
  splitCellKey,
  type PendingCellEdits,
} from "./connectorEdits";

/** Hard cap on painted rows — the same render-budget guard the read view uses. */
export const EDITABLE_ROW_CAP = 200;

const KICKER = "Table";

const MESSAGES = {
  UNRESOLVED:
    "These rows changed underneath the edit, so nothing was staged. Discard and try again.",
  EMPTY: "There is nothing to save.",
} as const;

/**
 * One cell already staged for approval: the value proposed, and the value the
 * connector held when it was proposed.
 *
 * `base` is what lets the overlay expire itself. A staged mark is a claim about
 * a proposal that is still outstanding, and the moment a re-read brings a
 * different value for that cell the claim is settled — the write applied, or
 * something else changed it — so the fresh read becomes the truth and the mark
 * goes. Without it, a cell would keep displaying a proposal forever, which is
 * the one thing worse than not showing it: an approved-looking value that
 * nothing on the server agrees with.
 */
interface StagedCell {
  readonly value: string;
  readonly base: string;
}

interface Draft {
  /** The surface these edits were typed against. A different one resets them. */
  readonly surfaceId: string;
  readonly pending: PendingCellEdits;
  readonly staged: Readonly<Record<string, StagedCell>>;
  readonly stageId: string | null;
}

function freshDraft(surfaceId: string): Draft {
  return { surfaceId, pending: {}, staged: {}, stageId: null };
}

/**
 * The connector this surface came from, as the sentence names it.
 *
 * Read defensively even though `source` is schema-required: `specFromState`
 * narrows an UNTRUSTED boundary value on two fields only, so a spec that reached
 * a renderer is not a spec that satisfied its own schema. A missing name degrades
 * to "the connector" — a vaguer true sentence, never a thrown render.
 */
function connectorName(spec: SurfaceSpec): string {
  const server = spec.source?.server;
  return typeof server === "string" && server.trim() !== ""
    ? server
    : "the connector";
}

/**
 * What a cell key currently reads, or `null` when it addresses nothing on screen
 * (the row is gone, the column left the spec, or the value stopped being text).
 */
function currentCellText(
  key: string,
  rowKeys: readonly string[],
  rows: readonly unknown[],
): string | null {
  const split = splitCellKey(key);
  if (split === null) return null;
  const index = rowKeys.indexOf(split.rowKey);
  if (index < 0) return null;
  return editableCellText(resolvePath(rows[index], split.columnPath));
}

export interface EditableConnectorTableProps {
  readonly spec: SurfaceSpec;
  /** The untrusted tool payload, for the spec paths that read OUTSIDE a row. */
  readonly data: unknown;
  /** The rows the read view resolved, in the order it painted them. */
  readonly rows: readonly unknown[];
  /** The surface title the read view computed, so both agree. */
  readonly title: string;
  readonly actions: ConnectorSurfaceEditorActions;
  /** Scopes testids/labels when two editable surfaces are mounted at once. */
  readonly idPrefix?: string;
}

export function EditableConnectorTable(
  props: EditableConnectorTableProps,
): ReactElement {
  const [stored, setStored] = useState<Draft>(() =>
    freshDraft(props.actions.surfaceId),
  );
  const [status, setStatus] = useState<"idle" | "saving" | "error">("idle");
  const [notice, setNotice] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const prefix = props.idPrefix ?? "table";

  // Derived during render rather than reset by an effect: a batch addressed at a
  // surface that is no longer on screen must never be the thing rendered, not
  // even for the one frame an effect would take to clear it.
  const draft =
    stored.surfaceId === props.actions.surfaceId
      ? stored
      : freshDraft(props.actions.surfaceId);

  const columns: readonly SurfaceColumn[] = props.spec.columns ?? [];
  const visibleRows = props.rows.slice(0, EDITABLE_ROW_CAP);
  const truncated = props.rows.length > EDITABLE_ROW_CAP;
  const rowKeys = useMemo(
    () => visibleRows.map((row, index) => rowKeyFor(row, index)),
    [visibleRows],
  );
  // Every staged mark whose cell still reads the way it did when it was staged.
  // Settled proposals drop out here rather than being cleaned up by an effect —
  // a stale claim must never be the thing rendered, not even for one frame.
  const staged = useMemo(() => {
    const live: Record<string, StagedCell> = {};
    for (const [key, cell] of Object.entries(draft.staged)) {
      const current = currentCellText(key, rowKeys, visibleRows);
      if (current !== null && current === cell.base) live[key] = cell;
    }
    return live;
  }, [draft.staged, rowKeys, visibleRows]);

  const saving = status === "saving";
  const editable = !props.actions.disabled && !saving;
  const dirtyCount = Object.keys(draft.pending).length;
  const stagedCount = Object.keys(staged).length;

  const update = (change: (current: Draft) => Draft): void => {
    setStored((current) =>
      change(
        current.surfaceId === props.actions.surfaceId
          ? current
          : freshDraft(props.actions.surfaceId),
      ),
    );
  };

  const valueOf = (key: string, raw: string): string =>
    draft.pending[key] ?? staged[key]?.value ?? raw;

  const begin = (key: string): void => {
    // A staged cell is frozen: its value is already a proposal sitting at the
    // gate, and typing over it here would leave two different answers for one
    // cell with no way to tell which the gate holds.
    if (!editable || staged[key] !== undefined) return;
    setOpen(key);
  };
  const change = (key: string, value: string): void => {
    update((current) => ({
      ...current,
      pending: { ...current.pending, [key]: value },
    }));
  };
  const discard = (): void => {
    setStored(freshDraft(props.actions.surfaceId));
    setOpen(null);
    setStatus("idle");
    setNotice(null);
  };
  const save = (): void => {
    const built = buildConnectorRowEdits(columns, visibleRows, draft.pending);
    if (!built.ok) {
      setStatus("error");
      setNotice(
        built.reason === "empty" ? MESSAGES.EMPTY : MESSAGES.UNRESOLVED,
      );
      return;
    }
    // The base each staged mark expires against: the value the connector held
    // when this batch left. Captured HERE, from the same rows the batch was
    // built from, so the two cannot describe different reads.
    const marks: Record<string, StagedCell> = {};
    for (const [key, value] of Object.entries(draft.pending)) {
      const base = currentCellText(key, rowKeys, visibleRows);
      if (base !== null) marks[key] = { value, base };
    }
    setOpen(null);
    setStatus("saving");
    setNotice(null);
    void props.actions.saveEdits(built.edits).then((result) => {
      if (result.status === "error") {
        // The batch is untouched — the user's typing is still on screen and
        // still theirs to retry. Nothing was staged.
        setStatus("error");
        setNotice(result.message);
        return;
      }
      setStatus("idle");
      setNotice(null);
      update((current) => ({
        ...current,
        pending: {},
        staged: { ...current.staged, ...marks },
        stageId: result.stageId,
      }));
    });
  };

  const openProps = (
    key: string,
  ): {
    readonly tabIndex?: number;
    readonly onClick?: () => void;
    readonly onKeyDown?: (event: KeyboardEvent) => void;
  } =>
    !editable || open === key || staged[key] !== undefined
      ? {}
      : {
          tabIndex: 0,
          onClick: () => begin(key),
          onKeyDown: (event) => {
            if (event.key !== "Enter") return;
            event.preventDefault();
            begin(key);
          },
        };

  return (
    <>
      <SurfaceHeader
        kicker={KICKER}
        title={props.title}
        badge={`${props.rows.length} row${props.rows.length === 1 ? "" : "s"}`}
      />
      <div style={actionBarStyle} data-testid={`${prefix}-editor-actions`}>
        <span className="ui-caption" style={hintStyle}>
          {dirtyCount === 0
            ? "Click a cell to edit it. Saving proposes the changes for approval."
            : `${dirtyCount} unsaved edit${dirtyCount === 1 ? "" : "s"}`}
        </span>
        <button
          type="button"
          style={buttonStyle(false)}
          data-testid={`${prefix}-editor-discard`}
          disabled={dirtyCount === 0 && stagedCount === 0}
          onClick={discard}
        >
          Discard
        </button>
        <button
          type="button"
          style={buttonStyle(true)}
          data-testid={`${prefix}-editor-save`}
          disabled={!editable || dirtyCount === 0}
          onClick={save}
        >
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
      {/* The staged result. It states what happened in the only two words that
          are true — PROPOSED, not written — and names where the decision is
          taken. It carries no Approve control of its own: the write gate is the
          one surface that decides, and a second one here would be a second
          answer to "did this go out?". */}
      {draft.stageId !== null && stagedCount > 0 ? (
        <p
          className="ui-caption"
          role="status"
          style={stagedNoticeStyle}
          data-testid={`${prefix}-editor-staged`}
          data-stage-id={draft.stageId}
          data-staged-count={stagedCount}
        >
          {stagedCount} change{stagedCount === 1 ? "" : "s"} staged for
          approval. Nothing has been sent to {connectorName(props.spec)} yet —
          approve them at the write gate to apply.
        </p>
      ) : null}
      {status === "error" && notice !== null ? (
        <p
          className="ui-caption"
          role="alert"
          style={errorNoticeStyle}
          data-testid={`${prefix}-editor-error`}
        >
          {notice} Your edits are still here.
        </p>
      ) : null}
      {columns.length === 0 || visibleRows.length === 0 ? (
        <p className="ui-caption" style={hintStyle}>
          {columns.length === 0 ? "No columns configured." : "No rows to edit."}
        </p>
      ) : (
        <div style={scrollStyle}>
          <table style={tableStyle} data-testid={`${prefix}-editable-grid`}>
            <thead>
              <tr>
                {columns.map((column, index) => (
                  <th
                    key={`${column.path}:${index}`}
                    scope="col"
                    style={thStyle}
                    data-testid={`${prefix}-header-${index}`}
                  >
                    {column.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visibleRows.map((row, rowIndex) => (
                <tr
                  key={rowKeys[rowIndex]}
                  data-testid={`${prefix}-row-${rowIndex}`}
                >
                  {columns.map((column, colIndex) => {
                    const raw = resolvePath(row, column.path);
                    const rawText = editableCellText(raw);
                    const key = cellKey(rowKeys[rowIndex], column.path);
                    const isStaged = staged[key] !== undefined;
                    const isDirty = draft.pending[key] !== undefined;
                    const testId = `${prefix}-cell-${rowIndex}-${colIndex}`;
                    if (rawText === null) {
                      // Structured: rendered exactly as the read view renders
                      // it, with no affordance at all. A control that refuses is
                      // worse than no control — see rule 3 in `connectorEdits`.
                      return (
                        <td
                          key={`${column.path}:${colIndex}`}
                          style={tdStyle}
                          data-testid={testId}
                          data-editable="false"
                        >
                          {formatValue(raw, column.format)}
                        </td>
                      );
                    }
                    const shown = valueOf(key, rawText);
                    return (
                      // The CELL is the control, not a span inside it: the whole
                      // box is the hit target, and there is exactly one node
                      // carrying the cell's identity, its state and its opener.
                      // Splitting those across two elements is how a click lands
                      // on the padding and nothing happens.
                      <td
                        key={`${column.path}:${colIndex}`}
                        style={tdStyle}
                        data-testid={testId}
                        data-editable="true"
                        data-modified={isDirty ? "true" : "false"}
                        data-staged={isStaged ? "true" : "false"}
                        {...openProps(key)}
                      >
                        {open === key ? (
                          <input
                            autoFocus
                            type="text"
                            style={fieldStyle}
                            aria-label={`${column.label}, row ${rowIndex + 1}`}
                            data-testid={`${testId}-input`}
                            value={shown}
                            onChange={(event) =>
                              change(key, event.target.value)
                            }
                            onBlur={() => setOpen(null)}
                            onKeyDown={(event) => {
                              if (event.key === "Enter") {
                                event.preventDefault();
                                setOpen(null);
                              }
                              if (event.key === "Escape") {
                                event.preventDefault();
                                setOpen(null);
                              }
                            }}
                          />
                        ) : (
                          <span
                            style={cellReadStyle(isDirty || isStaged, editable)}
                          >
                            {/* An edited or staged cell shows the value the USER
                                typed, verbatim — never re-formatted. What is on
                                screen is what the batch carries. */}
                            {isDirty || isStaged ? (
                              shown
                            ) : paintsAsChip(
                                column.format,
                                formatValue(raw, column.format),
                              ) ? (
                              <SurfaceValueBadge
                                value={formatValue(raw, column.format)}
                                testId={`${prefix}-badge-${rowIndex}-${colIndex}`}
                              />
                            ) : (
                              formatValue(raw, column.format)
                            )}
                          </span>
                        )}
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
        <div style={capStyle} data-testid={`${prefix}-row-cap`}>
          Showing {EDITABLE_ROW_CAP} of {props.rows.length} rows.
        </div>
      ) : null}
      {/* Kept from the read view. Becoming editable must not cost a surface the
          affordances it already had — the way OUT to the vendor's own UI is the
          one a user reaches for exactly when an edit here will not do. */}
      {props.spec.link ? (
        <SurfaceLinkRow
          label={props.spec.link.label}
          value={resolvePath(props.data, props.spec.link.url_path)}
        />
      ) : null}
    </>
  );
}

const actionBarStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "8px",
  flexWrap: "wrap",
  margin: "0 0 10px",
};

const hintStyle: CSSProperties = {
  flex: "1 1 240px",
  minWidth: 0,
  color: PALETTE.textLo,
};

function buttonStyle(primary: boolean): CSSProperties {
  return {
    flex: "none",
    padding: "4px 10px",
    borderRadius: "6px",
    border: `1px solid ${PALETTE.border}`,
    background: primary ? PALETTE.limeBgSoft : "transparent",
    color: primary ? PALETTE.textHi : PALETTE.textMid,
    font: "inherit",
    fontSize: "12px",
    cursor: "pointer",
  };
}

const stagedNoticeStyle: CSSProperties = {
  margin: "0 0 10px",
  color: PALETTE.textMid,
};

const errorNoticeStyle: CSSProperties = {
  margin: "0 0 10px",
  color: PALETTE.textHi,
};

const scrollStyle: CSSProperties = { overflowX: "auto" };

const tableStyle: CSSProperties = {
  width: "100%",
  borderCollapse: "collapse",
  fontSize: "13px",
};

const thStyle: CSSProperties = {
  textAlign: "start",
  padding: "6px 10px",
  borderBottom: `1px solid ${PALETTE.border}`,
  color: PALETTE.textLo,
  fontWeight: 500,
  whiteSpace: "nowrap",
};

const tdStyle: CSSProperties = {
  padding: "4px 10px",
  borderBottom: `1px solid ${PALETTE.border}`,
  color: PALETTE.textMid,
  verticalAlign: "top",
};

function cellReadStyle(marked: boolean, editable: boolean): CSSProperties {
  return {
    display: "inline-block",
    minWidth: "2ch",
    minHeight: "1.4em",
    padding: "1px 3px",
    borderRadius: "4px",
    cursor: editable ? "text" : "default",
    background: marked ? PALETTE.limeBgSoft : "transparent",
    color: marked ? PALETTE.textHi : "inherit",
  };
}

const fieldStyle: CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  padding: "1px 3px",
  borderRadius: "4px",
  border: `1px solid ${PALETTE.lime}`,
  background: PALETTE.surface,
  color: PALETTE.textHi,
  font: "inherit",
  fontSize: "13px",
};

const capStyle: CSSProperties = {
  marginTop: "8px",
  fontSize: "12px",
  color: PALETTE.textLo,
};
