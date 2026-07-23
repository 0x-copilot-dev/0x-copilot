// Todos — destination shell (P3-B1).
//
// This file is the *layout + section bucketing scaffolding*. The four
// interactive bodies — inline-add (P3-B2), extraction-banner (P3-B2),
// recurrence-editor (P3-B3), subtask-tree (P3-B3) — ship as separate
// files under this directory and are slotted via render-props. The
// shell owns:
//
//   1. Section bucketing (todos-prd §3.2 — Overdue / Today / This week /
//      Upcoming / No due / Done). Client-side per cross-audit decision
//      §9.6 / sub-PRD §13: the server returns a flat list; the shell
//      buckets in render so drag-reorder can re-bucket without a
//      refetch.
//   2. Empty-when-zero rendering — sections with no rows are *not*
//      rendered; if every section is empty, a single `<EmptyState>`
//      stands in (todos-prd §3.2 + §12.4).
//   3. Bulk-select toolbar — a sticky surface using `<StatusPill>` to
//      announce the selection count (todos-prd §3.6).
//   4. Per-row primitives — `<DocList items renderRow>` for virtualised
//      rendering, `<ItemLink>` for cross-destination links (todos-prd
//      §13.1), `<StatusPill>` for priority/recurrence chips.
//   5. Subtask nesting — one level, collapsed by default
//      (implementation-plan §11.2). Sub-rows render inside the parent's
//      DocList entry so they live in the same section as the parent.
//
// `_todos-stub.ts` carries wire-types until P3-A1's api-types lands.
// Every import is marked `TODO(merge): rewire to "@0x-copilot/api-types"`.

import {
  useCallback,
  useMemo,
  useState,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

import type {
  ConversationId,
  SectionResult,
  TodoId,
} from "@0x-copilot/api-types";

import { DocList } from "../../shell/DocList";
import { EmptyState } from "../../shell/EmptyState";
import { PageHeader } from "../../shell/PageHeader";
import { StatusPill, type StatusTone } from "../../shell/StatusPill";
import { ItemLink } from "../../refs/ItemLink";
import { itemKindNoun } from "../../refs/itemKindNoun";
import { formatRelativeTime } from "../../util/time";

// TODO(merge): rewire to "@0x-copilot/api-types"
import type {
  Todo,
  TodoExtraction,
  TodoPriority,
  TodoSectionKey,
  TodoSource,
  TodosPayload,
} from "./_todos-stub";

// TODO(merge): rewire to "@0x-copilot/api-types"
export type {
  Todo,
  TodoExtraction,
  TodoPriority,
  TodoSectionKey,
  TodoSource,
  TodosPayload,
} from "./_todos-stub";

// ===========================================================================
// §3.2 fixed section order
// ===========================================================================

// Overdue first — todos-prd §3.2: "rendered ABOVE Today so the user
// can't miss it". Done last + collapsed by default.
const SECTION_ORDER: ReadonlyArray<TodoSectionKey> = [
  "overdue",
  "today",
  "this_week",
  "upcoming",
  "no_due",
  "done",
];

const SECTION_HEADINGS: Readonly<Record<TodoSectionKey, string>> = {
  overdue: "Overdue",
  today: "Today",
  this_week: "This week",
  upcoming: "Upcoming",
  no_due: "No due date",
  done: "Done",
};

const SECTION_TONE: Readonly<Record<TodoSectionKey, StatusTone>> = {
  overdue: "error",
  today: "info",
  this_week: "muted",
  upcoming: "muted",
  no_due: "muted",
  done: "muted",
};

// Done-section cap — implementation-plan Q8 (14d). Older done items
// reachable via "Show all done"; that affordance is wired by P3-C.
const DONE_LOOKBACK_MS = 14 * 24 * 60 * 60 * 1000;

// ===========================================================================
// Public props
// ===========================================================================

/** Slot for P3-B2's inline-add component. Rendered at the top of every
 *  section (per todos-prd §3.4). Receives the section key so the slot
 *  can default the new todo's due-date / status to the section context. */
export type InlineAddSlot = (props: {
  readonly sectionKey: TodoSectionKey;
}) => ReactNode;

/** Slot for P3-B2's extraction-banner. Rendered above the section list
 *  whenever extractions exist (todos-prd §3.7). */
export type ExtractionBannerSlot = (props: {
  readonly extractions: ReadonlyArray<TodoExtraction>;
}) => ReactNode;

/** Slot for P3-B3's recurrence-editor. Rendered as a modal/overlay; the
 *  shell only exposes the open-state callback. */
export type RecurrenceEditorSlot = (props: {
  readonly todo: Todo;
  readonly onClose: () => void;
}) => ReactNode;

/** Slot for P3-B3's subtask-tree. Rendered inside the parent row's
 *  expanded body when the user toggles the parent's chevron. */
export type SubtaskTreeSlot = (props: {
  readonly parent: Todo;
  readonly subtasks: ReadonlyArray<Todo>;
}) => ReactNode;

export interface TodosDestinationProps {
  /**
   * Server-projected list result. `null` = loading skeleton; `error`
   * shows the section-level error empty-state with retry; `ok` buckets
   * rows into sections.
   *
   * `todos` is wrapped in `SectionResult` even though `/v1/todos` is
   * a non-aggregating endpoint (cross-audit §2.3 only mandates the
   * wrapper for aggregators). We carry the wrapper here so the shell
   * has a uniform branch for "couldn't load" without inventing a
   * second error path.
   */
  readonly todos?: SectionResult<ReadonlyArray<Todo>> | null;

  /** Pending extractions to feed the extraction-banner slot. */
  readonly extractions?: ReadonlyArray<TodoExtraction>;

  /** Cache marker for telemetry / stale banners. */
  readonly cachedAt?: string;

  /** Click handler for the row checkbox. */
  readonly onCompleteTodo?: (id: TodoId, nextDone: boolean) => void;

  /** Optional row delete affordance (X on hover; todos-prd §3.3). */
  readonly onDeleteTodo?: (id: TodoId) => void;

  /** Open the row's expanded view (excerpt + source). */
  readonly onSelectTodo?: (id: TodoId) => void;

  /** Open the recurrence editor for a given todo. P3-B3 wires the body. */
  readonly onEditRecurrence?: (id: TodoId) => void;

  /** Bulk-action handlers (todos-prd §3.6). Called with the selected ids. */
  readonly onBulkMarkDone?: (ids: ReadonlyArray<TodoId>) => void;
  readonly onBulkDelete?: (ids: ReadonlyArray<TodoId>) => void;
  readonly onBulkClear?: () => void;

  /** Retry callback when `todos.status === "error"`. */
  readonly onRetry?: () => void;

  /** P3-B2 inline-add slot. When absent, the inline-add row is not rendered. */
  readonly renderInlineAdd?: InlineAddSlot;

  /** P3-B2 extraction-banner slot. When absent, the banner is not rendered
   *  even if `extractions` is non-empty. */
  readonly renderExtractionBanner?: ExtractionBannerSlot;

  /** P3-B3 subtask-tree slot. When absent, subtasks render with the shell's
   *  built-in minimalist tree. */
  readonly renderSubtaskTree?: SubtaskTreeSlot;

  /** Reference instant — test seam for the bucket cut-offs. */
  readonly now?: number;

  /** Initial state of the Done section: collapsed by default per
   *  todos-prd §3.2. Tests can flip this. */
  readonly initialDoneCollapsed?: boolean;
}

// ===========================================================================
// Top-level shell
// ===========================================================================

export function TodosDestination(
  props: TodosDestinationProps = {},
): ReactElement {
  const {
    todos = null,
    extractions,
    cachedAt,
    onCompleteTodo,
    onDeleteTodo,
    onSelectTodo,
    onEditRecurrence,
    onBulkMarkDone,
    onBulkDelete,
    onBulkClear,
    onRetry,
    renderInlineAdd,
    renderExtractionBanner,
    renderSubtaskTree,
    now,
    initialDoneCollapsed = true,
  } = props;

  // === Bulk-select state ================================================
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<TodoId>>(
    () => new Set<TodoId>(),
  );
  const selectedCount = selectedIds.size;

  // === Done-section collapse state =====================================
  const [doneCollapsed, setDoneCollapsed] =
    useState<boolean>(initialDoneCollapsed);

  // === Subtask-tree expand state =======================================
  const [expandedParents, setExpandedParents] = useState<ReadonlySet<TodoId>>(
    () => new Set<TodoId>(),
  );

  const toggleSelected = useCallback((id: TodoId) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const toggleParentExpanded = useCallback((id: TodoId) => {
    setExpandedParents((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const clearSelection = useCallback(() => {
    setSelectedIds(new Set<TodoId>());
    if (onBulkClear !== undefined) onBulkClear();
  }, [onBulkClear]);

  // === Bucket the flat list (client-side per §13) ======================
  const buckets = useMemo(
    () => bucketTodos(todos, now ?? Date.now()),
    [todos, now],
  );

  // === Styles ===========================================================
  const rootStyle: CSSProperties = {
    width: "100%",
    height: "100%",
    minHeight: 0,
    backgroundColor: "var(--color-bg)",
    color: "var(--color-text)",
    boxSizing: "border-box",
    display: "flex",
    flexDirection: "column",
    overflow: "auto",
  };
  const containerStyle: CSSProperties = {
    width: "100%",
    maxWidth: 920, // todos-prd §3.1 — mirrors projects-todos.css line 227.
    margin: "0 auto",
    padding: "24px 28px 96px", // bottom pad so the bulk bar doesn't cover content.
    boxSizing: "border-box",
    display: "flex",
    flexDirection: "column",
    gap: 16,
  };
  const sectionGridStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 20,
  };

  // === Loading state ====================================================
  if (todos === null) {
    return (
      <section
        aria-label="Todos destination"
        data-testid="todos-destination"
        data-state="loading"
        style={rootStyle}
      >
        <div style={containerStyle}>
          <PageHeader title="Todos" subtitle="Loading…" />
          <div
            style={sectionGridStyle}
            data-testid="todos-sections"
            data-state="loading"
            aria-hidden="true"
          >
            {Array.from({ length: 3 }).map((_, i) => (
              <SectionSkeleton key={i} />
            ))}
          </div>
        </div>
      </section>
    );
  }

  // === Error state (whole-list) =========================================
  if (todos.status === "error") {
    return (
      <section
        aria-label="Todos destination"
        data-testid="todos-destination"
        data-state="error"
        style={rootStyle}
      >
        <div style={containerStyle}>
          <PageHeader title="Todos" />
          <EmptyState
            title="Could not load todos"
            body={todos.error ?? "Network error — try again."}
            action={
              onRetry !== undefined
                ? { label: "Retry", onClick: onRetry }
                : undefined
            }
          />
        </div>
      </section>
    );
  }

  if (todos.status === "unavailable") {
    return (
      <section
        aria-label="Todos destination"
        data-testid="todos-destination"
        data-state="unavailable"
        style={rootStyle}
      >
        <div style={containerStyle}>
          <PageHeader title="Todos" />
          <EmptyState
            title="Todos unavailable"
            body={
              todos.error ??
              "This destination is not enabled for your workspace."
            }
          />
        </div>
      </section>
    );
  }

  // === Ready state ======================================================

  // All-empty: render the single EmptyState (todos-prd §3.2 final note).
  const hasAnyTodos = SECTION_ORDER.some(
    (k) => (buckets.get(k) ?? []).length > 0,
  );

  // Extraction banner — render only when slot is provided AND extractions
  // exist; the slot decides how to render (collapsed/expanded). Empty list
  // is a no-op even if the slot exists.
  const hasExtractions = extractions !== undefined && extractions.length > 0;

  // Page header subtitle: total open count + recurrence count summary
  // (cheap aggregations from the buckets; no extra fetch).
  const openCount = SECTION_ORDER.reduce((acc, key) => {
    if (key === "done") return acc;
    return acc + (buckets.get(key) ?? []).length;
  }, 0);
  const subtitle =
    openCount === 0
      ? "Nothing open"
      : `${openCount} open${cachedAt !== undefined ? " · " + formatRelativeTime(cachedAt, now ?? Date.now()) : ""}`;

  return (
    <section
      aria-label="Todos destination"
      data-testid="todos-destination"
      data-state="ready"
      data-cached-at={cachedAt}
      data-selection-count={selectedCount}
      style={rootStyle}
    >
      <div style={containerStyle}>
        <PageHeader title="Todos" subtitle={subtitle} />

        {hasExtractions && renderExtractionBanner !== undefined ? (
          <div
            data-testid="todos-extraction-banner-slot"
            data-extraction-count={extractions!.length}
          >
            {renderExtractionBanner({ extractions: extractions! })}
          </div>
        ) : null}

        {!hasAnyTodos ? (
          <EmptyState
            title="Nothing here yet"
            body="Copilot extracts followups whenever it finds an action item — try the launch demo, or add one above."
            action={undefined}
          />
        ) : (
          <div
            style={sectionGridStyle}
            data-testid="todos-sections"
            data-state="ready"
          >
            {SECTION_ORDER.map((sectionKey) => {
              const rows = buckets.get(sectionKey) ?? [];
              // §3.2: a section with zero rows does not render — except
              // we DO want to show the inline-add affordance for "today"
              // and "no_due" so users can add a new todo when the list
              // is empty. We render an inline-add-only stub for those.
              const showInlineAddOnly =
                rows.length === 0 &&
                renderInlineAdd !== undefined &&
                (sectionKey === "today" || sectionKey === "no_due");
              if (rows.length === 0 && !showInlineAddOnly) return null;

              const collapsed = sectionKey === "done" && doneCollapsed;
              return (
                <Section
                  key={sectionKey}
                  sectionKey={sectionKey}
                  rows={rows}
                  selectedIds={selectedIds}
                  expandedParents={expandedParents}
                  toggleSelected={toggleSelected}
                  toggleParentExpanded={toggleParentExpanded}
                  onCompleteTodo={onCompleteTodo}
                  onDeleteTodo={onDeleteTodo}
                  onSelectTodo={onSelectTodo}
                  onEditRecurrence={onEditRecurrence}
                  renderInlineAdd={renderInlineAdd}
                  renderSubtaskTree={renderSubtaskTree}
                  collapsed={collapsed}
                  onToggleCollapsed={
                    sectionKey === "done"
                      ? () => setDoneCollapsed((v) => !v)
                      : undefined
                  }
                  now={now ?? Date.now()}
                />
              );
            })}
          </div>
        )}
      </div>

      {selectedCount > 0 ? (
        <BulkActionBar
          count={selectedCount}
          selectedIds={selectedIds}
          onMarkDone={onBulkMarkDone}
          onDelete={onBulkDelete}
          onClear={clearSelection}
        />
      ) : null}
    </section>
  );
}

// ===========================================================================
// Section — one bucket render (heading + rows + per-section inline-add)
// ===========================================================================

interface SectionProps {
  readonly sectionKey: TodoSectionKey;
  readonly rows: ReadonlyArray<Todo>;
  readonly selectedIds: ReadonlySet<TodoId>;
  readonly expandedParents: ReadonlySet<TodoId>;
  readonly toggleSelected: (id: TodoId) => void;
  readonly toggleParentExpanded: (id: TodoId) => void;
  readonly onCompleteTodo?: (id: TodoId, nextDone: boolean) => void;
  readonly onDeleteTodo?: (id: TodoId) => void;
  readonly onSelectTodo?: (id: TodoId) => void;
  readonly onEditRecurrence?: (id: TodoId) => void;
  readonly renderInlineAdd?: InlineAddSlot;
  readonly renderSubtaskTree?: SubtaskTreeSlot;
  readonly collapsed: boolean;
  readonly onToggleCollapsed?: () => void;
  readonly now: number;
}

function Section({
  sectionKey,
  rows,
  selectedIds,
  expandedParents,
  toggleSelected,
  toggleParentExpanded,
  onCompleteTodo,
  onDeleteTodo,
  onSelectTodo,
  onEditRecurrence,
  renderInlineAdd,
  renderSubtaskTree,
  collapsed,
  onToggleCollapsed,
  now,
}: SectionProps): ReactElement {
  const headingId = `todos-section-${sectionKey}-heading`;
  const wrapperStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 10,
  };
  const headerRowStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 10,
  };
  const headingStyle: CSSProperties = {
    fontSize: "var(--font-size-md, 14px)",
    fontWeight: 600,
    color: "var(--color-text)",
    margin: 0,
    flex: 1,
  };
  const collapseButtonStyle: CSSProperties = {
    background: "transparent",
    border: "1px solid var(--color-border, #232325)",
    color: "var(--color-text-muted, #b4b4b8)",
    borderRadius: "var(--radius-sm, 6px)",
    height: 24,
    padding: "0 8px",
    fontSize: "var(--font-size-xs, 12px)",
    cursor: "pointer",
  };

  // Partition: top-level rows (parent_id undefined) vs subtasks.
  // Subtasks render under their parent within the SAME section, per
  // todos-prd §13.2 / implementation-plan §11.2.
  const topLevel = rows.filter((r) => r.parent_id === undefined);
  const subtasksByParent = new Map<TodoId, Todo[]>();
  for (const r of rows) {
    if (r.parent_id !== undefined) {
      const arr = subtasksByParent.get(r.parent_id) ?? [];
      arr.push(r);
      subtasksByParent.set(r.parent_id, arr);
    }
  }
  // Sort subtasks by sort_index_within_parent (asc, undefined last).
  for (const [, arr] of subtasksByParent) {
    arr.sort((a, b) => {
      const av = a.sort_index_within_parent ?? Number.POSITIVE_INFINITY;
      const bv = b.sort_index_within_parent ?? Number.POSITIVE_INFINITY;
      return av - bv;
    });
  }

  return (
    <section
      aria-labelledby={headingId}
      data-testid={`todos-section-${sectionKey}`}
      data-section-key={sectionKey}
      data-row-count={rows.length}
      style={wrapperStyle}
    >
      <div style={headerRowStyle}>
        <h2 id={headingId} style={headingStyle}>
          {SECTION_HEADINGS[sectionKey]}
        </h2>
        <StatusPill
          status={SECTION_TONE[sectionKey]}
          label={String(rows.length)}
        />
        {onToggleCollapsed !== undefined ? (
          <button
            type="button"
            data-testid={`todos-section-${sectionKey}-collapse`}
            onClick={onToggleCollapsed}
            aria-expanded={!collapsed}
            aria-controls={`todos-section-${sectionKey}-body`}
            style={collapseButtonStyle}
          >
            {collapsed ? "Show" : "Hide"}
          </button>
        ) : null}
      </div>

      {!collapsed ? (
        <div
          id={`todos-section-${sectionKey}-body`}
          data-testid={`todos-section-${sectionKey}-body`}
        >
          {renderInlineAdd !== undefined ? (
            <div
              data-testid={`todos-section-${sectionKey}-inline-add-slot`}
              style={{ marginBottom: 8 }}
            >
              {renderInlineAdd({ sectionKey })}
            </div>
          ) : null}
          {topLevel.length > 0 ? (
            <DocList<Todo>
              ariaLabel={SECTION_HEADINGS[sectionKey]}
              items={topLevel}
              keyFor={(t) => t.id}
              renderRow={(todo) => (
                <TodoRow
                  todo={todo}
                  subtasks={subtasksByParent.get(todo.id) ?? []}
                  expanded={expandedParents.has(todo.id)}
                  onToggleExpanded={() => toggleParentExpanded(todo.id)}
                  selected={selectedIds.has(todo.id)}
                  onToggleSelected={() => toggleSelected(todo.id)}
                  onComplete={onCompleteTodo}
                  onDelete={onDeleteTodo}
                  onSelect={onSelectTodo}
                  onEditRecurrence={onEditRecurrence}
                  renderSubtaskTree={renderSubtaskTree}
                  now={now}
                />
              )}
            />
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

// ===========================================================================
// TodoRow — one parent row + optional collapsed subtask tree
// ===========================================================================

interface TodoRowProps {
  readonly todo: Todo;
  readonly subtasks: ReadonlyArray<Todo>;
  readonly expanded: boolean;
  readonly onToggleExpanded: () => void;
  readonly selected: boolean;
  readonly onToggleSelected: () => void;
  readonly onComplete?: (id: TodoId, nextDone: boolean) => void;
  readonly onDelete?: (id: TodoId) => void;
  readonly onSelect?: (id: TodoId) => void;
  readonly onEditRecurrence?: (id: TodoId) => void;
  readonly renderSubtaskTree?: SubtaskTreeSlot;
  readonly now: number;
}

function TodoRow({
  todo,
  subtasks,
  expanded,
  onToggleExpanded,
  selected,
  onToggleSelected,
  onComplete,
  onDelete,
  onSelect,
  onEditRecurrence,
  renderSubtaskTree,
  now,
}: TodoRowProps): ReactElement {
  const wrapStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 6,
    flex: 1,
    minWidth: 0,
  };
  const headStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 10,
    flex: 1,
    minWidth: 0,
  };
  const checkboxStyle: CSSProperties = {
    width: 18,
    height: 18,
    accentColor: "var(--color-accent, #d97757)",
    cursor: "pointer",
    flexShrink: 0,
  };
  const titleStyle: CSSProperties = {
    flex: 1,
    minWidth: 0,
    fontSize: "var(--font-size-sm, 13px)",
    fontWeight: 600,
    color: todo.done
      ? "var(--color-text-muted, #b4b4b8)"
      : "var(--color-text, #ededee)",
    textDecoration: todo.done ? "line-through" : "none",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
    background: "transparent",
    border: "none",
    padding: 0,
    margin: 0,
    cursor: onSelect !== undefined ? "pointer" : "default",
    textAlign: "left",
  };
  const metaStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 8,
    flexWrap: "wrap",
  };
  const expandButtonStyle: CSSProperties = {
    background: "transparent",
    border: "none",
    color: "var(--color-text-muted, #b4b4b8)",
    cursor: "pointer",
    padding: "2px 6px",
    fontSize: "var(--font-size-xs, 12px)",
    flexShrink: 0,
  };
  const deleteButtonStyle: CSSProperties = {
    background: "transparent",
    border: "none",
    color: "var(--color-text-subtle, #7e7e84)",
    cursor: "pointer",
    fontSize: "var(--font-size-sm, 13px)",
    padding: "2px 6px",
  };

  const hasSubtasks = subtasks.length > 0;

  return (
    <div
      style={wrapStyle}
      data-testid="todo-row"
      data-todo-id={todo.id}
      data-done={todo.done ? "true" : "false"}
      data-priority={todo.priority}
      data-selected={selected ? "true" : "false"}
      data-has-subtasks={hasSubtasks ? "true" : "false"}
    >
      <div style={headStyle}>
        {/* Bulk-select checkbox — distinct from done-toggle. */}
        <input
          type="checkbox"
          aria-label={`Select todo ${todo.text}`}
          data-testid="todo-row-select"
          checked={selected}
          onChange={onToggleSelected}
          style={{
            ...checkboxStyle,
            width: 14,
            height: 14,
            opacity: 0.7,
          }}
        />
        {/* Done toggle */}
        <input
          type="checkbox"
          aria-label={
            todo.done
              ? `Mark ${todo.text} as open`
              : `Mark ${todo.text} as done`
          }
          data-testid="todo-row-done"
          checked={todo.done}
          onChange={(e) => {
            if (onComplete !== undefined) onComplete(todo.id, e.target.checked);
          }}
          style={checkboxStyle}
        />
        {/* Expand chevron — only when subtasks exist. */}
        {hasSubtasks ? (
          <button
            type="button"
            aria-expanded={expanded}
            aria-controls={`todo-row-${todo.id}-subtasks`}
            data-testid="todo-row-expand"
            data-todo-id={todo.id}
            onClick={onToggleExpanded}
            style={expandButtonStyle}
          >
            {expanded ? "▾" : "▸"} {subtasks.length}
          </button>
        ) : null}
        <button
          type="button"
          style={titleStyle}
          onClick={() => {
            if (onSelect !== undefined) onSelect(todo.id);
          }}
          data-testid="todo-row-open"
          aria-label={`Open todo ${todo.text}`}
        >
          {todo.text}
        </button>
        {todo.recurrence !== undefined ? (
          <button
            type="button"
            data-testid="todo-row-recurrence-chip"
            data-todo-id={todo.id}
            onClick={() => {
              if (onEditRecurrence !== undefined) onEditRecurrence(todo.id);
            }}
            style={{
              background: "transparent",
              border: "none",
              padding: 0,
              cursor: onEditRecurrence !== undefined ? "pointer" : "default",
            }}
            aria-label={`Edit recurrence for ${todo.text}`}
          >
            <StatusPill status="info" label="Recurring" />
          </button>
        ) : null}
        {onDelete !== undefined ? (
          <button
            type="button"
            aria-label={`Delete ${todo.text}`}
            data-testid="todo-row-delete"
            onClick={() => onDelete(todo.id)}
            style={deleteButtonStyle}
          >
            ×
          </button>
        ) : null}
      </div>
      <div style={metaStyle} data-testid="todo-row-meta">
        {todo.priority !== "low" ? (
          <StatusPill
            status={priorityTone(todo.priority)}
            label={todo.priority}
          />
        ) : null}
        {todo.due !== undefined ? (
          <span
            data-testid="todo-row-due"
            style={{
              fontSize: "var(--font-size-xs, 12px)",
              color:
                isOverdue(todo.due, now) && !todo.done
                  ? "var(--color-danger, #d97777)"
                  : "var(--color-text-muted, #b4b4b8)",
            }}
          >
            {formatDueLabel(todo.due, now)}
          </span>
        ) : null}
        {todo.project_id !== undefined ? (
          <ItemLink
            ref={{ kind: "project", id: todo.project_id }}
            label={itemKindNoun("project")}
          />
        ) : null}
        {renderSourceChip(todo.source)}
      </div>
      {hasSubtasks && expanded ? (
        <div
          id={`todo-row-${todo.id}-subtasks`}
          data-testid={`todo-row-${todo.id}-subtasks`}
          style={{ paddingLeft: 24 }}
        >
          {renderSubtaskTree !== undefined ? (
            renderSubtaskTree({ parent: todo, subtasks })
          ) : (
            <DefaultSubtaskTree
              parent={todo}
              subtasks={subtasks}
              onComplete={onComplete}
              now={now}
            />
          )}
        </div>
      ) : null}
    </div>
  );
}

// ===========================================================================
// DefaultSubtaskTree — minimal fallback when P3-B3 slot isn't supplied
// ===========================================================================

function DefaultSubtaskTree({
  parent,
  subtasks,
  onComplete,
  now,
}: {
  readonly parent: Todo;
  readonly subtasks: ReadonlyArray<Todo>;
  readonly onComplete?: (id: TodoId, nextDone: boolean) => void;
  readonly now: number;
}): ReactElement {
  void parent; // accepted for the slot contract; default tree doesn't need it
  void now;
  return (
    <DocList<Todo>
      ariaLabel="Subtasks"
      items={subtasks}
      keyFor={(t) => t.id}
      renderRow={(sub) => (
        <div
          data-testid="subtask-row"
          data-todo-id={sub.id}
          data-done={sub.done ? "true" : "false"}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            flex: 1,
            minWidth: 0,
          }}
        >
          <input
            type="checkbox"
            aria-label={
              sub.done ? `Mark ${sub.text} as open` : `Mark ${sub.text} as done`
            }
            data-testid="subtask-row-done"
            checked={sub.done}
            onChange={(e) => {
              if (onComplete !== undefined)
                onComplete(sub.id, e.target.checked);
            }}
            style={{
              width: 16,
              height: 16,
              accentColor: "var(--color-accent, #d97757)",
              cursor: "pointer",
              flexShrink: 0,
            }}
          />
          <span
            style={{
              fontSize: "var(--font-size-sm, 13px)",
              color: sub.done
                ? "var(--color-text-muted, #b4b4b8)"
                : "var(--color-text, #ededee)",
              textDecoration: sub.done ? "line-through" : "none",
              flex: 1,
              minWidth: 0,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {sub.text}
          </span>
        </div>
      )}
    />
  );
}

// ===========================================================================
// BulkActionBar — sticky bottom-of-viewport surface
// ===========================================================================

interface BulkActionBarProps {
  readonly count: number;
  readonly selectedIds: ReadonlySet<TodoId>;
  readonly onMarkDone?: (ids: ReadonlyArray<TodoId>) => void;
  readonly onDelete?: (ids: ReadonlyArray<TodoId>) => void;
  readonly onClear: () => void;
}

function BulkActionBar({
  count,
  selectedIds,
  onMarkDone,
  onDelete,
  onClear,
}: BulkActionBarProps): ReactElement {
  const wrapperStyle: CSSProperties = {
    position: "sticky",
    bottom: 0,
    left: 0,
    right: 0,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    padding: "12px 16px",
    backgroundColor: "var(--color-bg-elevated, #161617)",
    borderTop: "1px solid var(--color-border, #232325)",
    boxShadow: "0 -2px 8px rgba(0,0,0,0.18)",
    zIndex: 10,
  };
  const innerStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 12,
    maxWidth: 920,
    width: "100%",
  };
  const buttonStyle: CSSProperties = {
    height: 30,
    padding: "0 12px",
    borderRadius: "var(--radius-sm, 6px)",
    border: "1px solid var(--color-border-strong, #2a2a2c)",
    background: "transparent",
    color: "var(--color-text, #ededee)",
    fontSize: "var(--font-size-sm, 13px)",
    cursor: "pointer",
  };
  const ids = Array.from(selectedIds);
  return (
    <div
      role="region"
      aria-label="Bulk actions"
      data-testid="todos-bulk-bar"
      style={wrapperStyle}
    >
      <div style={innerStyle}>
        <StatusPill status="info" label={`${count} selected`} />
        <div style={{ flex: 1 }} />
        {onMarkDone !== undefined ? (
          <button
            type="button"
            data-testid="todos-bulk-mark-done"
            onClick={() => onMarkDone(ids)}
            style={buttonStyle}
          >
            Mark done
          </button>
        ) : null}
        {onDelete !== undefined ? (
          <button
            type="button"
            data-testid="todos-bulk-delete"
            onClick={() => onDelete(ids)}
            style={buttonStyle}
          >
            Delete
          </button>
        ) : null}
        <button
          type="button"
          data-testid="todos-bulk-clear"
          onClick={onClear}
          style={buttonStyle}
        >
          Done
        </button>
      </div>
    </div>
  );
}

// ===========================================================================
// SectionSkeleton — loading placeholder
// ===========================================================================

function SectionSkeleton(): ReactElement {
  const style: CSSProperties = {
    height: 120,
    borderRadius: "var(--radius-md, 12px)",
    border: "1px solid var(--color-border, #232325)",
    backgroundColor: "var(--color-surface-muted, #222224)",
    opacity: 0.5,
  };
  return (
    <div
      style={style}
      data-testid="todos-skeleton-section"
      aria-hidden="true"
    />
  );
}

// ===========================================================================
// Section bucketing (client-side per cross-audit §9.6)
// ===========================================================================

/**
 * Bucket a flat list of todos into the six sections defined by
 * todos-prd §3.2. Returns a Map keyed by `TodoSectionKey` with each
 * bucket sorted by `sort_index` (ascending — server-managed float).
 *
 * Bucket cut-offs:
 *   - start_of_today  : `now` floor to day in UTC for stability across
 *                       test seams; production hosts pass a tz-localised
 *                       `now` (P3-C wires this from the session).
 *   - end_of_week     : `start_of_today + 7 days`. Sunday-vs-Monday week
 *                       starts are a Wave 5+ concern (todos-prd §16).
 *
 * Done bucket: 14-day rolling window (implementation-plan Q8). Older done
 * todos are simply not bucketed — the "Show all done" affordance fetches
 * the next page.
 */
export function bucketTodos(
  todos: SectionResult<ReadonlyArray<Todo>> | null,
  now: number,
): Map<TodoSectionKey, ReadonlyArray<Todo>> {
  const buckets = new Map<TodoSectionKey, Todo[]>([
    ["overdue", []],
    ["today", []],
    ["this_week", []],
    ["upcoming", []],
    ["no_due", []],
    ["done", []],
  ]);

  if (todos === null || todos.status !== "ok" || todos.data === undefined) {
    return buckets;
  }

  const startOfToday = startOfDayUtc(now);
  const endOfToday = startOfToday + 24 * 60 * 60 * 1000;
  const endOfWeek = startOfToday + 7 * 24 * 60 * 60 * 1000;
  const doneCutoff = now - DONE_LOOKBACK_MS;

  for (const t of todos.data) {
    if (t.done) {
      const ts =
        t.completed_at !== undefined
          ? Date.parse(t.completed_at)
          : Date.parse(t.updated_at);
      if (!Number.isNaN(ts) && ts >= doneCutoff) {
        buckets.get("done")!.push(t);
      }
      continue;
    }
    if (t.due === undefined) {
      buckets.get("no_due")!.push(t);
      continue;
    }
    const dueMs = Date.parse(t.due);
    if (Number.isNaN(dueMs)) {
      buckets.get("no_due")!.push(t);
      continue;
    }
    if (dueMs < startOfToday) {
      buckets.get("overdue")!.push(t);
    } else if (dueMs < endOfToday) {
      buckets.get("today")!.push(t);
    } else if (dueMs < endOfWeek) {
      buckets.get("this_week")!.push(t);
    } else {
      buckets.get("upcoming")!.push(t);
    }
  }

  // Stable sort by sort_index (server-managed float between neighbours).
  for (const [, arr] of buckets) {
    arr.sort((a, b) => a.sort_index - b.sort_index);
  }

  // Re-narrow to ReadonlyArray to match the public signature.
  const out = new Map<TodoSectionKey, ReadonlyArray<Todo>>();
  for (const [k, v] of buckets) out.set(k, v);
  return out;
}

function startOfDayUtc(now: number): number {
  const d = new Date(now);
  return Date.UTC(
    d.getUTCFullYear(),
    d.getUTCMonth(),
    d.getUTCDate(),
    0,
    0,
    0,
    0,
  );
}

function isOverdue(dueIso: string, now: number): boolean {
  const due = Date.parse(dueIso);
  if (Number.isNaN(due)) return false;
  return due < startOfDayUtc(now);
}

function formatDueLabel(dueIso: string, now: number): string {
  const due = Date.parse(dueIso);
  if (Number.isNaN(due)) return "—";
  const startToday = startOfDayUtc(now);
  const dayMs = 24 * 60 * 60 * 1000;
  if (due < startToday) {
    const days = Math.floor((startToday - due) / dayMs);
    return days === 0 ? "Due today" : `${days}d overdue`;
  }
  if (due < startToday + dayMs) return "Due today";
  if (due < startToday + 2 * dayMs) return "Due tomorrow";
  const days = Math.floor((due - startToday) / dayMs);
  if (days < 30) return `Due in ${days}d`;
  const months = Math.floor(days / 30);
  if (months < 12) return `Due in ${months}mo`;
  const years = Math.floor(months / 12);
  return `Due in ${years}y`;
}

function priorityTone(p: TodoPriority): StatusTone {
  switch (p) {
    case "high":
      return "error";
    case "med":
      return "warning";
    case "low":
      return "muted";
  }
}

function renderSourceChip(source: TodoSource): ReactNode {
  // Per todos-prd §13.1, source chips use `<ItemLink>` for thread/run/agent.
  if (source.kind === "user") return null;
  if (source.kind === "chat") {
    // `thread_id` is a ConversationId-shaped string at the wire level;
    // the local stub keeps it as `string` to avoid a fan-out brand
    // cast at the source definition. The route through `unknown`
    // matches home's pattern for branded-id forwarding.
    return (
      <ItemLink
        ref={{
          kind: "chat",
          id: source.thread_id as unknown as ConversationId,
        }}
        label={itemKindNoun("chat")}
      />
    );
  }
  if (source.kind === "agent") {
    if (source.run_id !== undefined) {
      return (
        <ItemLink
          ref={{
            kind: "run",
            id: source.run_id,
          }}
          label={itemKindNoun("run")}
        />
      );
    }
    return (
      <ItemLink
        ref={{
          kind: "agent",
          id: source.agent_id,
        }}
        label={itemKindNoun("agent")}
      />
    );
  }
  return null;
}
