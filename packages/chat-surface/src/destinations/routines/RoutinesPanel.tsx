// Routines — right-side context panel (P5-B1).
//
// Per routines-prd §3.3 the panel carries:
//
//   1. Status quick-filters (vertical mirror of the destination's
//      FilterTabs — same vocabulary, one source of truth).
//   2. Trigger-kind filter chips — Schedule / Webhook / Event / Manual.
//   3. Project filter — list of projects with >=1 routine. Renders
//      `<ItemLink kind="project">` for primary navigation.
//   4. "New routine" CTA — pivots host into the editor (P5-B2).
//
// Out of scope for P5-B1 (carved out so the shell merge is small):
//   - Owner filter (admin triage; P5-B3 admin tab adds it).
//   - Saved-searches CRUD (same primitive as Inbox §3.3 #5; future).
//   - Search (debounced 250ms; future hook-up against
//     `/v1/routines?q=…`).
//   - Footer links (admin "Routine quotas" / "Webhook security guide").
//
// Substrate-agnostic (web + desktop). No fetch, no router calls.
// Counts + active filters arrive from the host (P5-C).

import {
  useMemo,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

import type { ProjectId } from "@0x-copilot/api-types";

import { ContextPanel } from "../../shell/ContextPanel";
import { FilterTabs, type FilterTabOption } from "../../shell/FilterTabs";
import { ItemLink } from "../../refs/ItemLink";

// TODO(merge): rewire to "@0x-copilot/api-types"
import type { RoutineTriggerKind } from "./_routines-stub";

import type {
  RoutinesFilterCounts,
  RoutinesFilterSlug,
} from "./RoutinesDestination";

// ===========================================================================
// Trigger-kind filter slug
// ===========================================================================
//
// The destination's status filter (`RoutinesFilterSlug`) is the primary
// axis; the trigger-kind filter is an orthogonal axis the panel surfaces
// alongside it. Encoded as a string union so it composes cleanly with
// `<FilterTabs>`. "all" = no trigger-kind filter (default).

export type RoutinesPanelTriggerSlug = "all" | RoutineTriggerKind;

const TRIGGER_ORDER: ReadonlyArray<RoutinesPanelTriggerSlug> = [
  "all",
  "schedule",
  "webhook",
  "event",
  "manual",
];

const TRIGGER_LABEL: Readonly<Record<RoutinesPanelTriggerSlug, string>> = {
  all: "All",
  schedule: "Schedule",
  webhook: "Webhook",
  event: "Event",
  manual: "Manual",
};

/** Per-trigger-kind counts (mirror of destination filter counts shape). */
export type RoutinesPanelTriggerCounts = Readonly<
  Record<RoutinesPanelTriggerSlug, number>
>;

// ===========================================================================
// Project chip
// ===========================================================================

/**
 * One row in the "By project" section. The shell renders the count + a
 * single `<ItemLink kind="project">` (cross-audit §1.1 — never an inline
 * route string).
 */
export interface RoutinesPanelProjectChip {
  readonly project_id: ProjectId;
  readonly name: string;
  readonly routine_count: number;
}

// ===========================================================================
// Public props
// ===========================================================================

export interface RoutinesPanelProps {
  /** Currently active status filter slug (shared with destination). */
  readonly statusFilter?: RoutinesFilterSlug;
  readonly onStatusFilterChange?: (next: RoutinesFilterSlug) => void;
  readonly statusCounts?: RoutinesFilterCounts;

  /** Currently active trigger-kind filter slug. */
  readonly triggerFilter?: RoutinesPanelTriggerSlug;
  readonly onTriggerFilterChange?: (next: RoutinesPanelTriggerSlug) => void;
  readonly triggerCounts?: RoutinesPanelTriggerCounts;

  /** Projects with >=1 routine. Empty array hides the section. */
  readonly projects?: ReadonlyArray<RoutinesPanelProjectChip>;
  readonly activeProjectId?: ProjectId | null;
  readonly onProjectFilterChange?: (next: ProjectId | null) => void;

  /** "New routine" CTA — same callback as the destination's
   *  PageHeader primary action. Surfacing here too is a workflow nicety
   *  (panel is sticky; CTA stays reachable while scrolling the list). */
  readonly onCreateRoutine?: () => void;

  /** Optional footer slot — host may surface "Routine quotas" admin
   *  link / "Webhook security guide" doc per §3.3 footer. */
  readonly footer?: ReactNode;
}

// ===========================================================================
// Top-level panel
// ===========================================================================

export function RoutinesPanel(props: RoutinesPanelProps = {}): ReactElement {
  const {
    statusFilter = "all",
    onStatusFilterChange,
    statusCounts,
    triggerFilter = "all",
    onTriggerFilterChange,
    triggerCounts,
    projects = [],
    activeProjectId = null,
    onProjectFilterChange,
    onCreateRoutine,
    footer,
  } = props;

  const statusOptions = useMemo<
    ReadonlyArray<FilterTabOption<RoutinesFilterSlug>>
  >(
    () => [
      { slug: "all", label: "All", count: statusCounts?.all },
      { slug: "active", label: "Active", count: statusCounts?.active },
      { slug: "paused", label: "Paused", count: statusCounts?.paused },
      { slug: "errored", label: "Errored", count: statusCounts?.errored },
      { slug: "draft", label: "Draft", count: statusCounts?.draft },
    ],
    [statusCounts],
  );

  const triggerOptions = useMemo<
    ReadonlyArray<FilterTabOption<RoutinesPanelTriggerSlug>>
  >(
    () =>
      TRIGGER_ORDER.map((slug) => ({
        slug,
        label: TRIGGER_LABEL[slug],
        count: triggerCounts?.[slug],
      })),
    [triggerCounts],
  );

  const handleStatusChange = (next: RoutinesFilterSlug): void => {
    if (onStatusFilterChange !== undefined) onStatusFilterChange(next);
  };

  const handleTriggerChange = (next: RoutinesPanelTriggerSlug): void => {
    if (onTriggerFilterChange !== undefined) onTriggerFilterChange(next);
  };

  return (
    <ContextPanel
      title="Routines"
      subtitle="Scheduled and triggered work"
      destination="routines"
      primaryAction={
        onCreateRoutine !== undefined
          ? { label: "New routine", onClick: onCreateRoutine }
          : undefined
      }
    >
      <div data-testid="routines-panel">
        {/* === Status filter chips === */}
        <PanelSectionWrapper
          testId="routines-panel-section-status"
          title="Status"
        >
          <FilterTabs<RoutinesFilterSlug>
            value={statusFilter}
            onChange={handleStatusChange}
            options={statusOptions}
            ariaLabel="Routines status filter"
            idPrefix="routines-panel-status"
          />
        </PanelSectionWrapper>

        {/* === Trigger-kind filter chips === */}
        <PanelSectionWrapper
          testId="routines-panel-section-triggers"
          title="Triggers"
        >
          <FilterTabs<RoutinesPanelTriggerSlug>
            value={triggerFilter}
            onChange={handleTriggerChange}
            options={triggerOptions}
            ariaLabel="Routines trigger filter"
            idPrefix="routines-panel-trigger"
          />
        </PanelSectionWrapper>

        {/* === Project filter === */}
        {projects.length > 0 ? (
          <PanelSectionWrapper
            testId="routines-panel-section-projects"
            title="By project"
          >
            <ul data-testid="routines-panel-projects" style={projectListStyle}>
              <li>
                <button
                  type="button"
                  data-testid="routines-panel-project-all"
                  data-active={activeProjectId === null ? "true" : "false"}
                  onClick={() => {
                    if (onProjectFilterChange !== undefined) {
                      onProjectFilterChange(null);
                    }
                  }}
                  style={projectButtonStyle(activeProjectId === null)}
                >
                  <span>All projects</span>
                </button>
              </li>
              {projects.map((p) => {
                const active = activeProjectId === p.project_id;
                return (
                  <li key={p.project_id}>
                    <button
                      type="button"
                      data-testid={`routines-panel-project-${p.project_id}`}
                      data-active={active ? "true" : "false"}
                      onClick={() => {
                        if (onProjectFilterChange !== undefined) {
                          onProjectFilterChange(p.project_id);
                        }
                      }}
                      style={projectButtonStyle(active)}
                    >
                      <ItemLink
                        ref={{ kind: "project", id: p.project_id }}
                        className="routines-panel-project-link"
                      />
                      <span style={projectCountStyle}>{p.routine_count}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </PanelSectionWrapper>
        ) : null}

        {footer !== undefined ? (
          <PanelSectionWrapper
            testId="routines-panel-section-footer"
            title="Settings"
          >
            <div data-testid="routines-panel-footer">{footer}</div>
          </PanelSectionWrapper>
        ) : null}
      </div>
    </ContextPanel>
  );
}

// ===========================================================================
// PanelSectionWrapper — local clone of the Inbox / Todos section frame.
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
// Project list styles
// ===========================================================================

const projectListStyle: CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: 0,
  display: "flex",
  flexDirection: "column",
  gap: 2,
};

const projectCountStyle: CSSProperties = {
  fontSize: "var(--font-size-2xs, 11px)",
  color: "var(--color-text-subtle, #7e7e84)",
  background: "var(--color-surface-muted, #222224)",
  borderRadius: "var(--radius-full, 999px)",
  padding: "1px 8px",
  minWidth: 18,
  textAlign: "center",
};

function projectButtonStyle(active: boolean): CSSProperties {
  return {
    width: "100%",
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 8,
    padding: "4px 8px",
    background: active
      ? "color-mix(in srgb, var(--color-accent, #d97757) 12%, transparent)"
      : "transparent",
    color: active
      ? "var(--color-text, #ededee)"
      : "var(--color-text-muted, #b4b4b8)",
    border: "none",
    borderRadius: "var(--radius-sm, 6px)",
    fontSize: "var(--font-size-sm, 13px)",
    textAlign: "left",
    cursor: "pointer",
  };
}
