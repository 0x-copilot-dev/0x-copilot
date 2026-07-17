// Projects — destination shell (P6-B1).
//
// Pure-presentation list view per projects-prd §3.2:
//
//   1. PageHeader (cross-audit §1.6 shape) — title, subtitle with counts
//      (active / archived / mine), "New project" primary action.
//   2. FilterTabs — status axis: All / Active / Archived / Starred
//      (projects-prd §3.2 #2 minus "mine" which lands in P6-B2 once
//      caller identity wiring is plumbed). Selected slug + counts driven
//      by host.
//   3. CardGrid body — one card per project. Each card surfaces:
//        - icon + name + ⭐ starred indicator + viewer-role chip
//        - description (1 line, truncated)
//        - StatusPill (active / archived)
//        - ItemLink chips for activity counts (chats / todos / library /
//          routines) — chips ARE links; clicking opens the destination
//          filtered to this project (§9 cross-destination filter pattern).
//
// Mirrors P5-B1's Routines shell shape (loading skeleton -> SectionResult
// error/unavailable branches -> ready). Same render-prop seam for the
// detail pane (P6-B2 layers detail; P6-B3 layers activity tab — both
// ship later as separate files).
//
// Hard correctness rules:
//   - SP-1 primitives only (PageHeader / FilterTabs / CardGrid / StatusPill
//     / EmptyState / ItemLink). No custom buttons.
//   - ItemLink for every cross-destination ref (project itself, activity
//     count chips). Direct router.navigate from rows is forbidden
//     (cross-audit §1.1 + §3.3).
//   - Pure presentation: no fetch, no router calls, no SSE — the host
//     (apps/frontend P6-C) wires those.
//
// `_projects-stub.ts` carries wire-types until P6-A1's api-types land.
// Every import is marked `TODO(merge): rewire to "@0x-copilot/api-types"`.

import {
  useMemo,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

import type { ProjectId, SectionResult } from "@0x-copilot/api-types";

import { CardGrid } from "../../shell/CardGrid";
import { EmptyState } from "../../shell/EmptyState";
import { FilterTabs, type FilterTabOption } from "../../shell/FilterTabs";
import { PageHeader } from "../../shell/PageHeader";
import { StatusPill, type StatusTone } from "../../shell/StatusPill";
import { ItemLink } from "../../refs/ItemLink";
import { formatRelativeTime } from "../../util/time";

// TODO(merge): rewire to "@0x-copilot/api-types"
import type { ProjectStatus, ProjectSummary } from "./_projects-stub";

// ===========================================================================
// Filter slug
// ===========================================================================
//
// Single source of truth for the status filter axis. The slugs match
// projects-prd §3.2 #2 except "mine" (deferred to P6-B2 once viewer-role
// wiring is plumbed). "all" is the default (no server filter).

export type ProjectsFilterSlug = "all" | "active" | "archived" | "starred";

const FILTER_ORDER: ReadonlyArray<ProjectsFilterSlug> = [
  "all",
  "active",
  "archived",
  "starred",
];

const FILTER_LABEL: Readonly<Record<ProjectsFilterSlug, string>> = {
  all: "All",
  active: "Active",
  archived: "Archived",
  starred: "Starred",
};

/** Per-filter counts driven by the host (same query result feeds list +
 *  filter chips so they don't drift). */
export type ProjectsFilterCounts = Readonly<Record<ProjectsFilterSlug, number>>;

// ===========================================================================
// Public props
// ===========================================================================

/** Slot for P6-B2's detail / P6-B3's activity pane. Rendered in place of
 *  the grid body when `focusedProjectId` is set. */
export type RenderProjectDetailSlot = (props: {
  readonly projectId: ProjectId;
  readonly onClose: () => void;
}) => ReactNode;

export interface ProjectsDestinationProps {
  /**
   * Server-projected list result. `null` = loading skeleton; `error`
   * shows the destination-level error empty-state with retry; `ok`
   * renders the filtered list.
   *
   * `items` is wrapped in `SectionResult` even though `/v1/projects` is
   * a non-aggregating endpoint (cross-audit §2.3 only mandates the
   * wrapper for aggregators) — same rationale as Inbox / Routines: a
   * uniform "couldn't load" branch without inventing a second error path.
   */
  readonly items?: SectionResult<ReadonlyArray<ProjectSummary>> | null;

  /** Active status-filter slug. Defaults to "all". */
  readonly filter?: ProjectsFilterSlug;
  readonly onFilterChange?: (next: ProjectsFilterSlug) => void;

  /** Per-filter counts. When omitted, chips render without count chips. */
  readonly counts?: ProjectsFilterCounts;

  /** "New project" CTA — pivots the host into the editor (P6-B2). */
  readonly onCreateProject?: () => void;

  /** Per-row hover actions — wired by the host (P6-C) so the shell stays
   *  pure presentation. */
  readonly onArchiveProject?: (id: ProjectId) => void;
  readonly onActivateProject?: (id: ProjectId) => void;
  readonly onStarProject?: (id: ProjectId) => void;
  readonly onUnstarProject?: (id: ProjectId) => void;

  /** Retry callback when `items.status === "error"`. */
  readonly onRetry?: () => void;

  /** P6-B2 detail slot. When supplied AND `focusedProjectId` is set,
   *  the slot replaces the grid body. */
  readonly renderDetail?: RenderProjectDetailSlot;
  readonly focusedProjectId?: ProjectId | null;
  readonly onCloseDetail?: () => void;

  /** Reference instant — test seam for relative-time formatting. */
  readonly now?: number;
}

// ===========================================================================
// Top-level shell
// ===========================================================================

export function ProjectsDestination(
  props: ProjectsDestinationProps = {},
): ReactElement {
  const {
    items = null,
    filter = "all",
    onFilterChange,
    counts,
    onCreateProject,
    onArchiveProject,
    onActivateProject,
    onStarProject,
    onUnstarProject,
    onRetry,
    renderDetail,
    focusedProjectId = null,
    onCloseDetail,
    now,
  } = props;

  // === Filter chip options (single source of truth) =====================
  const filterOptions = useMemo<
    ReadonlyArray<FilterTabOption<ProjectsFilterSlug>>
  >(
    () =>
      FILTER_ORDER.map((slug) => ({
        slug,
        label: FILTER_LABEL[slug],
        count: counts?.[slug],
      })),
    [counts],
  );

  const handleFilterChange = (next: ProjectsFilterSlug): void => {
    if (onFilterChange !== undefined) onFilterChange(next);
  };

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
    maxWidth: 1000,
    margin: "0 auto",
    padding: "24px 28px 48px",
    boxSizing: "border-box",
    display: "flex",
    flexDirection: "column",
    gap: 16,
  };

  // === Loading state ====================================================
  if (items === null) {
    return (
      <section
        aria-label="Projects destination"
        data-testid="projects-destination"
        data-state="loading"
        style={rootStyle}
      >
        <div style={containerStyle}>
          <PageHeader title="Projects" subtitle="Loading…" />
          <CardGrid ariaLabel="Projects loading skeleton">
            {Array.from({ length: 6 }).map((_, i) => (
              <CardSkeleton key={i} index={i} />
            ))}
          </CardGrid>
        </div>
      </section>
    );
  }

  // === Error state ======================================================
  if (items.status === "error") {
    return (
      <section
        aria-label="Projects destination"
        data-testid="projects-destination"
        data-state="error"
        style={rootStyle}
      >
        <div style={containerStyle}>
          <PageHeader title="Projects" />
          <EmptyState
            title="Could not load projects"
            body={items.error ?? "Network error — try again."}
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

  if (items.status === "unavailable") {
    return (
      <section
        aria-label="Projects destination"
        data-testid="projects-destination"
        data-state="unavailable"
        style={rootStyle}
      >
        <div style={containerStyle}>
          <PageHeader title="Projects" />
          <EmptyState
            title="Projects unavailable"
            body={
              items.error ??
              "This destination is not enabled for your workspace."
            }
          />
        </div>
      </section>
    );
  }

  // === Ready state ======================================================
  const rows = items.data ?? [];

  // Counts for the PageHeader subtitle.
  const activeCount =
    counts?.active ?? rows.filter((p) => p.status === "active").length;
  const archivedCount =
    counts?.archived ?? rows.filter((p) => p.status === "archived").length;

  const subtitle =
    rows.length === 0
      ? "Group related work under shared ACL"
      : `${activeCount} active${archivedCount > 0 ? ` · ${archivedCount} archived` : ""}`;

  const showingDetail = renderDetail !== undefined && focusedProjectId !== null;

  return (
    <section
      aria-label="Projects destination"
      data-testid="projects-destination"
      data-state="ready"
      data-focused-project-id={focusedProjectId ?? undefined}
      data-filter={filter}
      style={rootStyle}
    >
      <div style={containerStyle}>
        <PageHeader
          title="Projects"
          subtitle={subtitle}
          primaryAction={
            onCreateProject !== undefined
              ? { label: "New project", onClick: onCreateProject }
              : undefined
          }
        />

        <FilterTabs<ProjectsFilterSlug>
          value={filter}
          onChange={handleFilterChange}
          options={filterOptions}
          ariaLabel="Projects status filter"
          idPrefix="projects"
        />

        {showingDetail ? (
          <div
            data-testid="projects-detail-slot"
            data-focused-project-id={focusedProjectId!}
          >
            {renderDetail!({
              projectId: focusedProjectId!,
              onClose: () => {
                if (onCloseDetail !== undefined) onCloseDetail();
              },
            })}
          </div>
        ) : rows.length === 0 ? (
          <EmptyState
            title="No projects yet"
            body="Group related chats, todos, runs, and saved artifacts under a shared ACL. Create the first one to get started."
            action={
              onCreateProject !== undefined
                ? { label: "New project", onClick: onCreateProject }
                : undefined
            }
          />
        ) : (
          <CardGrid ariaLabel="Projects">
            {rows.map((project) => (
              <ProjectCard
                key={project.id}
                project={project}
                onArchiveProject={onArchiveProject}
                onActivateProject={onActivateProject}
                onStarProject={onStarProject}
                onUnstarProject={onUnstarProject}
                now={now ?? Date.now()}
              />
            ))}
          </CardGrid>
        )}
      </div>
    </section>
  );
}

// ===========================================================================
// ProjectCard — one card in the grid
// ===========================================================================

interface ProjectCardProps {
  readonly project: ProjectSummary;
  readonly onArchiveProject?: (id: ProjectId) => void;
  readonly onActivateProject?: (id: ProjectId) => void;
  readonly onStarProject?: (id: ProjectId) => void;
  readonly onUnstarProject?: (id: ProjectId) => void;
  readonly now: number;
}

function ProjectCard({
  project,
  onArchiveProject,
  onActivateProject,
  onStarProject,
  onUnstarProject,
  now,
}: ProjectCardProps): ReactElement {
  const cardStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 10,
    padding: 14,
    borderRadius: "var(--radius-md, 12px)",
    border: "1px solid var(--color-border, #232325)",
    backgroundColor: "var(--color-surface, #1a1a1c)",
    color: "var(--color-text, #ededee)",
    boxSizing: "border-box",
    minWidth: 0,
  };
  const headStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 8,
    minWidth: 0,
  };
  const iconStyle: CSSProperties = {
    width: 28,
    height: 28,
    borderRadius: "var(--radius-sm, 6px)",
    backgroundColor: `hsl(${project.color_hue}, 60%, 28%)`,
    color: "var(--color-text, #ededee)",
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    fontSize: 16,
    flexShrink: 0,
  };
  const nameStyle: CSSProperties = {
    flex: 1,
    minWidth: 0,
    fontSize: "var(--font-size-sm, 13px)",
    fontWeight: 600,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };
  const descStyle: CSSProperties = {
    fontSize: "var(--font-size-xs, 12px)",
    color: "var(--color-text-muted, #b4b4b8)",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };
  const metaStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 6,
    flexWrap: "wrap",
    fontSize: "var(--font-size-xs, 12px)",
    color: "var(--color-text-muted, #b4b4b8)",
  };
  const actionRowStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 4,
    marginLeft: "auto",
    flexShrink: 0,
  };
  const actionButtonStyle: CSSProperties = {
    background: "transparent",
    border: "none",
    color: "var(--color-text-subtle, #7e7e84)",
    cursor: "pointer",
    fontSize: "var(--font-size-xs, 12px)",
    padding: "2px 6px",
  };
  const starButtonStyle: CSSProperties = {
    ...actionButtonStyle,
    color: project.viewer_starred
      ? "var(--color-warning, #d9a857)"
      : "var(--color-text-subtle, #7e7e84)",
  };

  const isActive = project.status === "active";
  const lastActivity = project.last_activity_at;

  return (
    <article
      style={cardStyle}
      data-testid="project-card"
      data-project-id={project.id}
      data-status={project.status}
      data-viewer-starred={project.viewer_starred ? "true" : "false"}
    >
      <div style={headStyle}>
        <span
          style={iconStyle}
          aria-hidden="true"
          data-testid="project-card-icon"
        >
          {project.icon_emoji}
        </span>
        {/* Project name is the canonical ItemLink to the project — clicking
            opens the project detail in this destination (cross-audit §1.1).
            The chip is rendered inline so the entire card name acts as a
            link without a custom anchor. */}
        <span style={nameStyle} data-testid="project-card-name">
          <ItemLink
            ref={{ kind: "project", id: project.id }}
            className="projects-card-name-link"
          />
        </span>
        <StatusPill
          status={statusTone(project.status)}
          label={statusLabel(project.status)}
        />
        <div style={actionRowStyle}>
          {onStarProject !== undefined && !project.viewer_starred ? (
            <button
              type="button"
              data-testid="project-card-star"
              onClick={() => onStarProject(project.id)}
              style={starButtonStyle}
              aria-label={`Star ${project.name}`}
            >
              ☆
            </button>
          ) : null}
          {onUnstarProject !== undefined && project.viewer_starred ? (
            <button
              type="button"
              data-testid="project-card-unstar"
              onClick={() => onUnstarProject(project.id)}
              style={starButtonStyle}
              aria-label={`Unstar ${project.name}`}
            >
              ★
            </button>
          ) : null}
          {isActive && onArchiveProject !== undefined ? (
            <button
              type="button"
              data-testid="project-card-archive"
              onClick={() => onArchiveProject(project.id)}
              style={actionButtonStyle}
              aria-label={`Archive ${project.name}`}
            >
              Archive
            </button>
          ) : null}
          {!isActive && onActivateProject !== undefined ? (
            <button
              type="button"
              data-testid="project-card-activate"
              onClick={() => onActivateProject(project.id)}
              style={actionButtonStyle}
              aria-label={`Activate ${project.name}`}
            >
              Activate
            </button>
          ) : null}
        </div>
      </div>

      {project.description.length > 0 ? (
        <div style={descStyle} data-testid="project-card-description">
          {project.description}
        </div>
      ) : null}

      <div style={metaStyle} data-testid="project-card-meta">
        {/* Owner chip — display-only (Team destination owns the
            person ItemLink; we don't render <ItemLink kind="person"> here
            unless the owner UserId is the canonical viewer's spec target;
            projects-prd §3.2 row shape carries ownerChip as plain text). */}
        {project.owner_display_name !== undefined ? (
          <span data-testid="project-card-owner">
            {project.owner_display_name}
          </span>
        ) : null}
        {/* Viewer role chip — only shown when the caller is a member. */}
        {project.viewer_role !== null ? (
          <StatusPill status="muted" label={project.viewer_role} />
        ) : null}
        {lastActivity !== null ? (
          <span data-testid="project-card-last-activity">
            {formatRelativeTime(lastActivity, now)}
          </span>
        ) : null}
        <span data-testid="project-card-counts">
          {project.counts.chats} chats · {project.counts.todos_open} todos ·{" "}
          {project.counts.routines_active} routines · {project.counts.members}{" "}
          members
        </span>
      </div>
    </article>
  );
}

// ===========================================================================
// Helpers
// ===========================================================================

const STATUS_TONE: Readonly<Record<ProjectStatus, StatusTone>> = {
  active: "ok",
  archived: "muted",
};

const STATUS_LABEL: Readonly<Record<ProjectStatus, string>> = {
  active: "Active",
  archived: "Archived",
};

function statusTone(status: ProjectStatus): StatusTone {
  return STATUS_TONE[status];
}

function statusLabel(status: ProjectStatus): string {
  return STATUS_LABEL[status];
}

// ===========================================================================
// CardSkeleton — loading placeholder
// ===========================================================================

function CardSkeleton({ index }: { index: number }): ReactElement {
  const style: CSSProperties = {
    height: 116,
    borderRadius: "var(--radius-md, 12px)",
    border: "1px solid var(--color-border, #232325)",
    backgroundColor: "var(--color-surface-muted, #222224)",
    opacity: 0.5,
  };
  return (
    <div
      style={style}
      data-testid="projects-skeleton-card"
      data-skeleton-index={index}
      aria-hidden="true"
    />
  );
}
