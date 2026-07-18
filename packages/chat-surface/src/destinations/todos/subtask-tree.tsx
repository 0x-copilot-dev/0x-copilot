// Subtask tree — Phase 3 sub-PRD §16 Q3 + implementation-plan §11.2.
//
// One-level subtask renderer. Backend enforces the depth=1 invariant
// (sub-PRD §11.2: "A subtask CANNOT have its own subtasks. Backend
// rejects with 400."); this component additionally guards against
// rendering a nested subtask, so a stale client cannot accidentally
// draw a tree.
//
// Display-only concerns this component owns:
//   - Render parent + its subtasks (deepest legal nesting).
//   - Collapse/expand affordance.
//   - Inline-add row that proposes `project_id` inherited from parent
//     (the surface displays parent.project_id; the server enforces it on
//     POST /v1/todos — implementation-plan §11.2 "Subtasks inherit
//     parent's project_id on create (server enforces)").
//   - "All subtasks done → mark parent done?" inline CTA when every
//     subtask is complete and the parent is still open.
//
// Substrate-agnostic (no `window`/`document`/`fetch`). Parent owns state
// via callbacks.

import {
  useCallback,
  useMemo,
  useState,
  type CSSProperties,
  type ReactElement,
} from "react";

import type { TodoId } from "@0x-copilot/api-types";

// ===========================================================================
// Local Todo shape — until api-types/src/todos.ts (P3-A) merges. The
// fields below match implementation-plan §11.2's subtask additions on
// the canonical `Todo`.
// ===========================================================================

export interface SubtaskTreeTodo {
  readonly id: TodoId;
  readonly text: string;
  readonly done: boolean;
  /** Null for top-level todos; the parent's `TodoId` for subtasks. */
  readonly parent_id: TodoId | null;
  /** Inherited from parent on the server; surfaced here for display. */
  readonly project_id?: string | null;
}

// ===========================================================================
// Public props
// ===========================================================================

export interface SubtaskTreeProps {
  readonly parent: SubtaskTreeTodo;
  readonly subtasks: ReadonlyArray<SubtaskTreeTodo>;
  /**
   * Called when the user submits the inline-add. The surface should
   * `POST /v1/todos { text, parent_id: parent.id }`; the server inherits
   * `project_id` from the parent.
   */
  readonly onAddSubtask: (input: {
    readonly parentId: TodoId;
    readonly text: string;
    /** Surfaced for telemetry / display only — server is the truth. */
    readonly inheritedProjectId?: string | null;
  }) => void;
  readonly onCompleteSubtask: (input: {
    readonly subtaskId: TodoId;
    readonly nextDone: boolean;
  }) => void;
  /** Optional — surfaced when the "all subtasks done · mark parent done?" CTA is taken. */
  readonly onCompleteParent?: (input: { readonly parentId: TodoId }) => void;
  /** Initial collapsed state. Defaults to `false` (expanded). */
  readonly defaultCollapsed?: boolean;
}

// ===========================================================================
// Tokens (mirrors recurrence-editor.tsx / TodosDestination.tsx)
// ===========================================================================

const PANEL_BORDER = "var(--color-border)";
const PANEL_BORDER_STRONG = "var(--color-border-strong)";
const TEXT_PRIMARY = "var(--color-text)";
const TEXT_SECONDARY = "var(--color-text-muted)";
const TEXT_FAINT = "var(--color-text-subtle)";
const ACCENT = "var(--color-accent)";
const SURFACE = "var(--color-surface)";

// ===========================================================================
// Component
// ===========================================================================

export function SubtaskTree(props: SubtaskTreeProps): ReactElement {
  const {
    parent,
    subtasks,
    onAddSubtask,
    onCompleteSubtask,
    onCompleteParent,
    defaultCollapsed = false,
  } = props;

  // Hard correctness rule 1: one-level only. Filter out any subtask whose
  // own `parent_id` does not match the supplied parent — also drop any
  // grandchild (shape impossible per backend, but a stale client could
  // arrive here with one). We keep the children defensively rather than
  // throwing, so a bad payload doesn't blank the destination.
  const ownChildren = useMemo<ReadonlyArray<SubtaskTreeTodo>>(
    () => subtasks.filter((s) => s.parent_id === parent.id),
    [subtasks, parent.id],
  );

  const [collapsed, setCollapsed] = useState<boolean>(defaultCollapsed);
  const [draftText, setDraftText] = useState<string>("");

  const allDone = ownChildren.length > 0 && ownChildren.every((s) => s.done);
  const showMarkParentHint = allDone && !parent.done;

  const toggleCollapsed = useCallback(() => {
    setCollapsed((v) => !v);
  }, []);

  const submitDraft = useCallback(() => {
    const trimmed = draftText.trim();
    if (trimmed.length === 0) return;
    onAddSubtask({
      parentId: parent.id,
      text: trimmed,
      inheritedProjectId: parent.project_id ?? null,
    });
    setDraftText("");
  }, [draftText, onAddSubtask, parent.id, parent.project_id]);

  // ---- Styles -------------------------------------------------------------

  const treeStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 6,
    paddingLeft: 28,
    borderLeft: `1px dashed ${PANEL_BORDER}`,
    marginLeft: 8,
  };
  const headerStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 8,
    fontSize: "var(--font-size-xs)",
    color: TEXT_SECONDARY,
  };
  const collapseButtonStyle: CSSProperties = {
    width: 20,
    height: 20,
    padding: 0,
    borderRadius: 4,
    border: `1px solid ${PANEL_BORDER_STRONG}`,
    backgroundColor: "transparent",
    color: TEXT_SECONDARY,
    fontSize: "var(--font-size-xs)",
    cursor: "pointer",
    lineHeight: 1,
  };
  const rowStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 10,
    padding: "8px 10px",
    borderRadius: 8,
    border: `1px solid ${PANEL_BORDER}`,
    backgroundColor: SURFACE,
    color: TEXT_PRIMARY,
  };
  const checkboxStyle: CSSProperties = {
    width: 16,
    height: 16,
    accentColor: ACCENT,
    cursor: "pointer",
    flexShrink: 0,
  };
  const textStyleDone: CSSProperties = {
    flex: 1,
    minWidth: 0,
    fontSize: "var(--font-size-sm)",
    color: TEXT_SECONDARY,
    textDecoration: "line-through",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };
  const textStyleOpen: CSSProperties = {
    flex: 1,
    minWidth: 0,
    fontSize: "var(--font-size-sm)",
    color: TEXT_PRIMARY,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };
  const addRowStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "6px 10px",
    borderRadius: 8,
    border: `1px dashed ${PANEL_BORDER_STRONG}`,
    backgroundColor: "transparent",
  };
  const addInputStyle: CSSProperties = {
    flex: 1,
    minWidth: 0,
    height: 28,
    padding: "0 8px",
    borderRadius: 6,
    border: `1px solid ${PANEL_BORDER}`,
    backgroundColor: "transparent",
    color: TEXT_PRIMARY,
    fontSize: "var(--font-size-sm)",
  };
  const addButtonStyle: CSSProperties = {
    height: 28,
    padding: "0 12px",
    borderRadius: 6,
    border: `1px solid ${PANEL_BORDER_STRONG}`,
    backgroundColor: "transparent",
    color: ACCENT,
    fontSize: "var(--font-size-xs)",
    fontWeight: 600,
    cursor: "pointer",
  };
  const hintStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 10,
    padding: "6px 10px",
    borderRadius: 8,
    backgroundColor: "var(--color-accent-soft, transparent)",
    border: `1px solid ${ACCENT}`,
    color: ACCENT,
    fontSize: "var(--font-size-xs)",
    fontWeight: 600,
  };
  const hintButtonStyle: CSSProperties = {
    height: 24,
    padding: "0 10px",
    borderRadius: 999,
    border: `1px solid ${ACCENT}`,
    backgroundColor: "transparent",
    color: ACCENT,
    fontSize: "var(--font-size-xs)",
    fontWeight: 600,
    cursor: "pointer",
  };
  const projectHintStyle: CSSProperties = {
    fontSize: "var(--font-size-2xs)",
    color: TEXT_FAINT,
    fontStyle: "italic",
  };

  // ---- Render -------------------------------------------------------------

  // Display the inherited-project hint only when the parent has one — the
  // server enforces the inheritance on POST, but surfacing it here gives
  // the user the right mental model.
  const inheritedProjectHint: ReactElement | null =
    parent.project_id !== undefined && parent.project_id !== null ? (
      <span style={projectHintStyle} data-testid="subtask-inherited-project">
        Will be filed under parent's project
      </span>
    ) : null;

  return (
    <div
      style={treeStyle}
      data-testid="subtask-tree"
      data-parent-id={parent.id}
      data-collapsed={collapsed ? "true" : "false"}
      data-subtask-count={ownChildren.length}
    >
      <div style={headerStyle}>
        <button
          type="button"
          onClick={toggleCollapsed}
          style={collapseButtonStyle}
          aria-expanded={!collapsed}
          aria-controls={`subtask-tree-body-${parent.id}`}
          aria-label={
            collapsed
              ? `Expand subtasks of ${parent.text}`
              : `Collapse subtasks of ${parent.text}`
          }
          data-testid="subtask-tree-collapse"
        >
          {collapsed ? "+" : "−"}
        </button>
        <span>
          Subtasks ({ownChildren.filter((s) => s.done).length}/
          {ownChildren.length})
        </span>
      </div>

      {!collapsed ? (
        <div
          id={`subtask-tree-body-${parent.id}`}
          style={{ display: "flex", flexDirection: "column", gap: 6 }}
        >
          {ownChildren.map((subtask) => (
            // One-level guard — we never recurse. A `SubtaskRow` is a
            // plain leaf; rendering a `<SubtaskTree>` here would violate
            // the §11.2 invariant.
            <SubtaskRow
              key={subtask.id}
              subtask={subtask}
              onToggle={onCompleteSubtask}
              rowStyle={rowStyle}
              checkboxStyle={checkboxStyle}
              doneTextStyle={textStyleDone}
              openTextStyle={textStyleOpen}
            />
          ))}

          {showMarkParentHint ? (
            <div
              role="status"
              style={hintStyle}
              data-testid="subtask-tree-mark-parent-hint"
            >
              <span style={{ flex: 1 }}>
                All subtasks done · mark parent done?
              </span>
              {onCompleteParent !== undefined ? (
                <button
                  type="button"
                  onClick={() => onCompleteParent({ parentId: parent.id })}
                  style={hintButtonStyle}
                  data-testid="subtask-tree-mark-parent-cta"
                >
                  Mark done
                </button>
              ) : null}
            </div>
          ) : null}

          <div style={addRowStyle}>
            <input
              type="text"
              placeholder="Add a subtask…"
              value={draftText}
              onChange={(e) => setDraftText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  submitDraft();
                } else if (e.key === "Escape") {
                  setDraftText("");
                }
              }}
              style={addInputStyle}
              aria-label="Add a subtask"
              data-testid="subtask-tree-add-input"
            />
            <button
              type="button"
              onClick={submitDraft}
              disabled={draftText.trim().length === 0}
              style={addButtonStyle}
              data-testid="subtask-tree-add-submit"
            >
              Add
            </button>
          </div>

          {inheritedProjectHint}
        </div>
      ) : null}
    </div>
  );
}

// ===========================================================================
// Internal leaf — explicitly NOT exported. A subtask row is a leaf node.
// One-level invariant lives here: the row renders no tree.
// ===========================================================================

interface SubtaskRowProps {
  readonly subtask: SubtaskTreeTodo;
  readonly onToggle: (input: {
    readonly subtaskId: TodoId;
    readonly nextDone: boolean;
  }) => void;
  readonly rowStyle: CSSProperties;
  readonly checkboxStyle: CSSProperties;
  readonly doneTextStyle: CSSProperties;
  readonly openTextStyle: CSSProperties;
}

function SubtaskRow(props: SubtaskRowProps): ReactElement {
  const {
    subtask,
    onToggle,
    rowStyle,
    checkboxStyle,
    doneTextStyle,
    openTextStyle,
  } = props;
  const ariaLabel = subtask.done
    ? `Mark subtask ${subtask.text} as open`
    : `Mark subtask ${subtask.text} as done`;
  return (
    <div
      style={rowStyle}
      data-testid="subtask-row"
      data-subtask-id={subtask.id}
      data-done={subtask.done ? "true" : "false"}
    >
      <input
        type="checkbox"
        checked={subtask.done}
        onChange={(e) =>
          onToggle({ subtaskId: subtask.id, nextDone: e.target.checked })
        }
        style={checkboxStyle}
        aria-label={ariaLabel}
        data-testid="subtask-row-toggle"
      />
      <span style={subtask.done ? doneTextStyle : openTextStyle}>
        {subtask.done ? `Completed: ${subtask.text}` : subtask.text}
      </span>
    </div>
  );
}
