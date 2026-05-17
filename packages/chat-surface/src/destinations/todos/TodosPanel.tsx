// Todos — right-side context panel (P3-B1).
//
// Per todos-prd §3.1 the panel carries:
//   - Filter chips (All / Mine / per-project)
//   - Saved-filter selector (Wave 4+ stub — visible button, no-op
//     handler — implementation-plan §3 Phase 3 Q6 "context-aware
//     defaults" leaves the saved-filter UX to a later wave)
//   - An inline-add affordance that defaults to the panel's active
//     filter (the slot itself is owned by P3-B2)
//
// The panel keeps no fetch logic — the host supplies the list of
// projects + the current filter value + the change callback. This
// stays substrate-agnostic (web + desktop).
//
// `_todos-stub.ts` carries wire-types until P3-A1 merges.

import {
  useMemo,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

import type { ProjectId } from "@enterprise-search/api-types";

import { ContextPanel } from "../../shell/ContextPanel";
import { EmptyState } from "../../shell/EmptyState";
import { FilterTabs, type FilterTabOption } from "../../shell/FilterTabs";
import { StatusPill } from "../../shell/StatusPill";

// ===========================================================================
// Filter contract
// ===========================================================================

/**
 * Slug shape for the project filter chips. Encoded as a string so the
 * `<FilterTabs>` typed generic compiles cleanly:
 *   - `"all"`     — show every todo the user can read
 *   - `"mine"`    — owner_user_id = current user (the default)
 *   - `"project:<id>"` — project_id = id (one chip per starred project)
 *
 * P3-C wires these to the `GET /v1/todos?filter[project_id]=…` query
 * shape. The shell only cares about the slug string; it doesn't decode
 * the project id (no business logic at the panel).
 */
export type TodosFilterSlug = "all" | "mine" | `project:${string}`;

/** Project chip descriptor — the host supplies these from `/v1/projects`
 *  (or whatever the projects destination exposes). */
export interface TodosProjectChip {
  readonly project_id: ProjectId;
  readonly name: string;
  readonly icon_emoji?: string;
  /** Optional unread / open count to show on the chip. */
  readonly count?: number;
}

/** Saved filter (Wave 4+ stub — visible affordance, no-op handler). */
export interface TodosSavedFilter {
  readonly id: string;
  readonly label: string;
}

export interface TodosPanelProps {
  /** Currently active filter slug. */
  readonly filter?: TodosFilterSlug;
  readonly onFilterChange?: (next: TodosFilterSlug) => void;

  /** Per-project filter chips. Order follows the host (typically the
   *  user's starred projects, then most-active). */
  readonly projects?: ReadonlyArray<TodosProjectChip>;

  /** Saved-filter selector (Wave 4+). When omitted, only the
   *  "Save current filter" affordance renders. */
  readonly savedFilters?: ReadonlyArray<TodosSavedFilter>;
  readonly onSelectSavedFilter?: (id: string) => void;
  /** Callback for "Save current filter" — Wave 4+ host wires this; the
   *  stub default is a no-op. */
  readonly onSaveCurrentFilter?: () => void;

  /** P3-B2 inline-add slot. Rendered at the bottom of the panel head. */
  readonly renderInlineAdd?: () => ReactNode;

  /** Total open count for the page-header style subtitle. */
  readonly openCount?: number;
}

// ===========================================================================
// Top-level shell
// ===========================================================================

export function TodosPanel(props: TodosPanelProps = {}): ReactElement {
  const {
    filter = "mine",
    onFilterChange,
    projects = [],
    savedFilters,
    onSelectSavedFilter,
    onSaveCurrentFilter,
    renderInlineAdd,
    openCount,
  } = props;

  // Build the FilterTabs option list. We separate the project chips into
  // their own tablist below so the layout doesn't wrap a long horizontal
  // strip when the user has many projects.
  const primaryOptions = useMemo<
    ReadonlyArray<FilterTabOption<TodosFilterSlug>>
  >(
    () => [
      { slug: "all" as const, label: "All", count: undefined },
      {
        slug: "mine" as const,
        label: "Mine",
        count: openCount,
      },
    ],
    [openCount],
  );

  const projectOptions = useMemo<
    ReadonlyArray<FilterTabOption<TodosFilterSlug>>
  >(
    () =>
      projects.map((p) => ({
        slug: `project:${p.project_id}` as TodosFilterSlug,
        label: p.icon_emoji ? `${p.icon_emoji} ${p.name}` : p.name,
        count: p.count,
      })),
    [projects],
  );

  const handleChange = (next: TodosFilterSlug): void => {
    if (onFilterChange !== undefined) onFilterChange(next);
  };

  const subtitle =
    openCount !== undefined && openCount > 0 ? `${openCount} open` : undefined;

  return (
    <ContextPanel title="Todos" subtitle={subtitle} destination="todos">
      <div data-testid="todos-panel">
        {/* === Primary filter chips (All / Mine) === */}
        <PanelSectionWrapper
          testId="todos-panel-section-primary-filters"
          title="Filter"
        >
          <FilterTabs<TodosFilterSlug>
            value={
              filter === "all" || filter === "mine"
                ? filter
                : ("mine" as TodosFilterSlug)
            }
            onChange={handleChange}
            options={primaryOptions}
            ariaLabel="Todos primary filter"
            idPrefix="todos-panel-primary"
          />
        </PanelSectionWrapper>

        {/* === Per-project chips === */}
        {projectOptions.length > 0 ? (
          <PanelSectionWrapper
            testId="todos-panel-section-project-filters"
            title="Projects"
          >
            <FilterTabs<TodosFilterSlug>
              value={
                projectOptions.some((o) => o.slug === filter)
                  ? filter
                  : ("mine" as TodosFilterSlug)
              }
              onChange={handleChange}
              options={projectOptions}
              ariaLabel="Todos project filter"
              idPrefix="todos-panel-project"
            />
          </PanelSectionWrapper>
        ) : null}

        {/* === Saved filter selector (Wave 4+ stub) === */}
        <PanelSectionWrapper
          testId="todos-panel-section-saved-filters"
          title="Saved filters"
        >
          <SavedFiltersBody
            savedFilters={savedFilters ?? []}
            onSelect={onSelectSavedFilter}
            onSave={onSaveCurrentFilter}
          />
        </PanelSectionWrapper>

        {/* === Inline-add slot === */}
        <PanelSectionWrapper
          testId="todos-panel-section-inline-add"
          title="Quick add"
        >
          {renderInlineAdd !== undefined ? (
            <div data-testid="todos-panel-inline-add-slot">
              {renderInlineAdd()}
            </div>
          ) : (
            <EmptyState
              title="Inline add coming soon"
              body="The quick-add affordance lands in P3-B2."
            />
          )}
        </PanelSectionWrapper>
      </div>
    </ContextPanel>
  );
}

// ===========================================================================
// PanelSectionWrapper — small visual frame inside the panel
// ===========================================================================

interface PanelSectionWrapperProps {
  readonly testId: string;
  readonly title: string;
  readonly children: ReactNode;
}

function PanelSectionWrapper({
  testId,
  title,
  children,
}: PanelSectionWrapperProps): ReactElement {
  const wrapperStyle: CSSProperties = {
    padding: "12px 14px",
    borderBottom: "1px solid var(--color-border, #232325)",
    display: "flex",
    flexDirection: "column",
    gap: 8,
  };
  const titleStyle: CSSProperties = {
    fontSize: "var(--font-size-xs, 12px)",
    fontWeight: 600,
    color: "var(--color-text-muted, #b4b4b8)",
    margin: 0,
    textTransform: "uppercase",
    letterSpacing: 0.4,
  };
  return (
    <section style={wrapperStyle} data-testid={testId}>
      <h3 style={titleStyle}>{title}</h3>
      {children}
    </section>
  );
}

// ===========================================================================
// SavedFiltersBody — Wave 4+ stub
// ===========================================================================

function SavedFiltersBody({
  savedFilters,
  onSelect,
  onSave,
}: {
  readonly savedFilters: ReadonlyArray<TodosSavedFilter>;
  readonly onSelect?: (id: string) => void;
  readonly onSave?: () => void;
}): ReactElement {
  const listStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 6,
    margin: 0,
    padding: 0,
    listStyle: "none",
  };
  const itemButtonStyle: CSSProperties = {
    width: "100%",
    textAlign: "left",
    padding: "6px 10px",
    border: "1px solid var(--color-border, #232325)",
    borderRadius: "var(--radius-sm, 6px)",
    backgroundColor: "transparent",
    color: "var(--color-text, #ededee)",
    fontSize: "var(--font-size-sm, 13px)",
    cursor: "pointer",
  };
  const saveButtonStyle: CSSProperties = {
    height: 28,
    padding: "0 12px",
    border: "1px dashed var(--color-border-strong, #2a2a2c)",
    borderRadius: "var(--radius-sm, 6px)",
    backgroundColor: "transparent",
    color: "var(--color-accent, #d97757)",
    fontSize: "var(--font-size-sm, 13px)",
    fontWeight: 600,
    cursor: "pointer",
  };

  return (
    <div data-testid="todos-panel-saved-filters">
      {savedFilters.length === 0 ? (
        <div
          style={{
            fontSize: "var(--font-size-xs, 12px)",
            color: "var(--color-text-subtle, #7e7e84)",
          }}
        >
          No saved filters yet.
        </div>
      ) : (
        <ul style={listStyle} data-testid="todos-panel-saved-filter-list">
          {savedFilters.map((f) => (
            <li key={f.id}>
              <button
                type="button"
                data-testid="todos-panel-saved-filter-item"
                data-filter-id={f.id}
                onClick={() => {
                  if (onSelect !== undefined) onSelect(f.id);
                }}
                style={itemButtonStyle}
              >
                <StatusPill status="muted" label="Filter" />
                <span style={{ marginLeft: 6 }}>{f.label}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <button
        type="button"
        data-testid="todos-panel-save-current-filter"
        onClick={() => {
          // Wave 4+ wires this; default is a no-op so the affordance is
          // visible (per task spec — "visible button, no-op").
          if (onSave !== undefined) onSave();
        }}
        style={{ ...saveButtonStyle, marginTop: 8 }}
      >
        Save current filter
      </button>
    </div>
  );
}
