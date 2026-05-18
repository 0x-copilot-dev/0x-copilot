// Projects — right-side context panel (P6-B1).
//
// Per projects-prd §3.3 the panel carries (P6-B1 ships #1, #6 from the
// 6-section spec — the rest land with P6-B2/B3):
//
//   1. Status quick-filters (vertical mirror of the destination's
//      FilterTabs — same vocabulary, one source of truth).
//   6. "New project" CTA — pivots host into the editor (P6-B2).
//
// Out of scope for P6-B1 (carved out so the shell merge is small):
//   - Search (debounced 250ms; future hook-up against
//     `/v1/projects?q=…`).
//   - Starred — collapsible list of the user's starred projects.
//   - By owner — list of owners with ≥ 1 visible project.
//   - By recency — last 10 projects with activity in the past 7 days.
//   - "Project ACL guide" footer link.
//
// Substrate-agnostic (web + desktop). No fetch, no router calls.
// Counts + active filters arrive from the host (P6-C).

import {
  useMemo,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

import { ContextPanel } from "../../shell/ContextPanel";
import { FilterTabs, type FilterTabOption } from "../../shell/FilterTabs";

import type {
  ProjectsFilterCounts,
  ProjectsFilterSlug,
} from "./ProjectsDestination";

// ===========================================================================
// Public props
// ===========================================================================

export interface ProjectsPanelProps {
  /** Currently active status filter slug (shared with destination). */
  readonly statusFilter?: ProjectsFilterSlug;
  readonly onStatusFilterChange?: (next: ProjectsFilterSlug) => void;
  readonly statusCounts?: ProjectsFilterCounts;

  /** "New project" CTA — same callback as the destination's PageHeader
   *  primary action. Surfacing here too is a workflow nicety (panel is
   *  sticky; CTA stays reachable while scrolling the list). */
  readonly onCreateProject?: () => void;

  /** Optional footer slot — host may surface "Project ACL guide" doc
   *  per §3.3 footer. */
  readonly footer?: ReactNode;
}

// ===========================================================================
// Top-level panel
// ===========================================================================

export function ProjectsPanel(props: ProjectsPanelProps = {}): ReactElement {
  const {
    statusFilter = "all",
    onStatusFilterChange,
    statusCounts,
    onCreateProject,
    footer,
  } = props;

  const statusOptions = useMemo<
    ReadonlyArray<FilterTabOption<ProjectsFilterSlug>>
  >(
    () => [
      { slug: "all", label: "All", count: statusCounts?.all },
      { slug: "active", label: "Active", count: statusCounts?.active },
      { slug: "archived", label: "Archived", count: statusCounts?.archived },
      { slug: "starred", label: "Starred", count: statusCounts?.starred },
    ],
    [statusCounts],
  );

  const handleStatusChange = (next: ProjectsFilterSlug): void => {
    if (onStatusFilterChange !== undefined) onStatusFilterChange(next);
  };

  return (
    <ContextPanel
      title="Projects"
      subtitle="Workspaces for related work"
      destination="projects"
      primaryAction={
        onCreateProject !== undefined
          ? { label: "New project", onClick: onCreateProject }
          : undefined
      }
    >
      <div data-testid="projects-panel">
        {/* === Status filter chips === */}
        <PanelSectionWrapper
          testId="projects-panel-section-status"
          title="Status"
        >
          <FilterTabs<ProjectsFilterSlug>
            value={statusFilter}
            onChange={handleStatusChange}
            options={statusOptions}
            ariaLabel="Projects status filter"
            idPrefix="projects-panel-status"
          />
        </PanelSectionWrapper>

        {footer !== undefined ? (
          <PanelSectionWrapper
            testId="projects-panel-section-footer"
            title="Settings"
          >
            <div data-testid="projects-panel-footer">{footer}</div>
          </PanelSectionWrapper>
        ) : null}
      </div>
    </ContextPanel>
  );
}

// ===========================================================================
// PanelSectionWrapper — local clone of the Inbox / Todos / Routines section
// frame. Could be extracted to a shared shell primitive in P7+ once a
// third destination needs it (we have three uses now; one more and we
// converge — DRY threshold).
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
