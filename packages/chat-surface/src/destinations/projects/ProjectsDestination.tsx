// Projects — destination shell (PRD-10).
//
// The ONE Projects list, mounted on BOTH hosts (web `apps/frontend`, desktop
// `apps/desktop`). Before PRD-10 the web app rendered its own bespoke grid and
// only mounted this component as a `renderDetail` host; that fork drifted from
// this card within a release of the reason for it being fixed. D1 deletes the
// web scaffold; this is the single card implementation.
//
// Anatomy, to the design (`copilot-app.jsx:386-425`, `copilot.css:1672-1720`):
//   * `<Page>` (960px column, left-aligned) + `<PageLead>` — NO 22px page title
//     (the topbar already labels the screen; D4). The old `<PageHeader>` is gone
//     from every branch.
//   * a fixed 3-up `.grid3` (`CardGrid variant="grid3"`, D7) of cards. Each card
//     is ONE `<button className="ui-card ui-card--proj">` hit area (D2), so the
//     whole tile navigates; the lifecycle actions (star / archive / delete) live
//     in a hover/focus-revealed overlay OUTSIDE the button in DOM order, so they
//     stay keyboard reachable.
//   * the identity tile is the shared `<ProjectIconTile>` (D3) — the name's
//     monogram on the per-project hue, NEVER `icon_emoji` (which the server
//     defaults to 📁 for every project — the desktop emoji-wall bug).
//   * the create affordance moves to the filter row as a right-aligned quiet
//     control (D4) — a deliberate divergence from the mock, which pre-populates
//     projects and ships no create control.
//
// Pure presentation: no fetch, no router, no SSE — the host wires those and owns
// focus (`detail` binding + `onOpenProject`).

import {
  useEffect,
  useMemo,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

import type { ProjectId, SectionResult } from "@0x-copilot/api-types";

import type { ProjectsDetailBinding } from "../../contract/shellBinding";
import { cacheProjectNames } from "./projectNameCache";
import { CardGrid } from "../../shell/CardGrid";
import { EmptyState } from "../../shell/EmptyState";
import { FilterTabs, type FilterTabOption } from "../../shell/FilterTabs";
import { StatusPill, type StatusTone } from "../../shell/StatusPill";
import { Page, PageLead, ProjectIconTile } from "../_shared";

import type { ProjectStatus, ProjectSummary } from "@0x-copilot/api-types";

// The design's lead paragraph copy (copilot-app.jsx:391-394).
const PROJECTS_LEAD_COPY =
  "Group related chats, files, and context. Open a project to see its conversations and working files.";

// ===========================================================================
// Filter slug
// ===========================================================================

export type ProjectsFilterSlug = "all" | "active" | "archived" | "starred";

const FILTER_ORDER: ReadonlyArray<ProjectsFilterSlug> = [
  "all",
  "active",
  "archived",
  "starred",
];

// Lowercase filter-tab labels — the design's chip/tab vocabulary is lowercase
// mono; kept consistent with the status chips (PRD-02).
const FILTER_LABEL: Readonly<Record<ProjectsFilterSlug, string>> = {
  all: "all",
  active: "active",
  archived: "archived",
  starred: "starred",
};

/** Per-filter counts driven by the host (same query result feeds list +
 *  filter chips so they don't drift). */
export type ProjectsFilterCounts = Readonly<Record<ProjectsFilterSlug, number>>;

// ===========================================================================
// Public props
// ===========================================================================

/** Slot for the detail / activity pane. Rendered in place of the grid body
 *  when `focusedProjectId` is set. */
export type RenderProjectDetailSlot = (props: {
  readonly projectId: ProjectId;
  readonly onClose: () => void;
}) => ReactNode;

export interface ProjectsDestinationProps {
  /**
   * Server-projected list result. `null` = loading skeleton; `error`
   * shows the destination-level error empty-state with retry; `ok`
   * renders the filtered list.
   */
  readonly items?: SectionResult<ReadonlyArray<ProjectSummary>> | null;

  /** Active status-filter slug. Defaults to "all". */
  readonly filter?: ProjectsFilterSlug;
  readonly onFilterChange?: (next: ProjectsFilterSlug) => void;

  /** Per-filter counts. When omitted, chips render without count chips. */
  readonly counts?: ProjectsFilterCounts;

  /** "New project" CTA — a deliberate live-only divergence (D4). Rendered as a
   *  right-aligned quiet control on the filter row when supplied. */
  readonly onCreateProject?: () => void;

  /** Card click → focus the project (the whole card is the hit area, D2). The
   *  host owns `focusedProjectId` and the detail fetch. */
  readonly onOpenProject?: (id: ProjectId) => void;

  /** Per-card lifecycle actions — wired by the host so the shell stays pure
   *  presentation. `onDeleteProject` is the one genuinely new prop (D1). */
  readonly onArchiveProject?: (id: ProjectId) => void;
  readonly onActivateProject?: (id: ProjectId) => void;
  readonly onStarProject?: (id: ProjectId) => void;
  readonly onUnstarProject?: (id: ProjectId) => void;
  readonly onDeleteProject?: (id: ProjectId) => void;

  /** Retry callback when `items.status === "error"`. */
  readonly onRetry?: () => void;

  /**
   * Total detail binding (PRD-03 Move 2). `{ mode: "disabled" }` is an
   * explicit, reviewable statement that the host has no project-detail flow
   * yet; `{ mode: "enabled", … }` carries the focused id + slot + close callback
   * together.
   */
  readonly detail: ProjectsDetailBinding;

  /** Reference instant — test seam for relative-time formatting. */
  readonly now?: number;
}

// ===========================================================================
// Top-level shell
// ===========================================================================

export function ProjectsDestination(
  props: ProjectsDestinationProps,
): ReactElement {
  const {
    items = null,
    filter = "all",
    onFilterChange,
    counts,
    onCreateProject,
    onOpenProject,
    onArchiveProject,
    onActivateProject,
    onStarProject,
    onUnstarProject,
    onDeleteProject,
    onRetry,
    detail,
  } = props;

  const renderDetail =
    detail.mode === "enabled" ? detail.renderDetail : undefined;
  const focusedProjectId =
    detail.mode === "enabled" ? detail.focusedProjectId : null;
  const onCloseDetail =
    detail.mode === "enabled" ? detail.onCloseDetail : undefined;

  // Prime the cross-destination project-name cache from the loaded list so
  // `<ItemLink kind="project">` on other surfaces renders the real name (PRD-03
  // Move 1). Desktop never primed it before, so every desktop project link read
  // the literal "Project"; converging on this component fixes both hosts.
  useEffect(() => {
    if (items !== null && items.status === "ok" && items.data !== undefined) {
      cacheProjectNames(items.data);
    }
  }, [items]);

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
  const pageStyle: CSSProperties = {
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
        <Page style={pageStyle}>
          <PageLead>{PROJECTS_LEAD_COPY}</PageLead>
          <CardGrid ariaLabel="Projects loading skeleton" variant="grid3">
            {Array.from({ length: 6 }).map((_, i) => (
              <CardSkeleton key={i} index={i} />
            ))}
          </CardGrid>
        </Page>
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
        <Page style={pageStyle}>
          <PageLead>{PROJECTS_LEAD_COPY}</PageLead>
          <EmptyState
            title="Could not load projects"
            body={items.error ?? "Network error — try again."}
            action={
              onRetry !== undefined
                ? { label: "Retry", onClick: onRetry }
                : undefined
            }
          />
        </Page>
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
        <Page style={pageStyle}>
          <PageLead>{PROJECTS_LEAD_COPY}</PageLead>
          <EmptyState
            title="Projects unavailable"
            body={
              items.error ??
              "This destination is not enabled for your workspace."
            }
          />
        </Page>
      </section>
    );
  }

  // === Ready state ======================================================
  const rows = items.data ?? [];
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
      {showingDetail ? (
        // The detail slot renders `ProjectDetailView`, which supplies its OWN
        // `<Page>` column (D4) — so the destination does NOT wrap it in a second
        // Page (that would double the 960px cap + `20px 24px 40px` padding).
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
      ) : (
        <Page style={pageStyle}>
          <PageLead>{PROJECTS_LEAD_COPY}</PageLead>
          <div style={filterRowStyle}>
            <FilterTabs<ProjectsFilterSlug>
              value={filter}
              onChange={handleFilterChange}
              options={filterOptions}
              ariaLabel="Projects status filter"
              idPrefix="projects"
            />
            {/* The create affordance — a deliberate live-only divergence from
                the mock (D4). Right-aligned quiet control on the filter row. */}
            {onCreateProject !== undefined ? (
              <button
                type="button"
                className="ui-button ui-button--sm"
                data-testid="projects-create"
                onClick={onCreateProject}
                style={createButtonStyle}
              >
                New project
              </button>
            ) : null}
          </div>
          {rows.length === 0 ? (
            <EmptyState
              title="No projects yet"
              body="Group related chats, files, and context under a shared project. Create the first one to get started."
              action={
                onCreateProject !== undefined
                  ? { label: "New project", onClick: onCreateProject }
                  : undefined
              }
            />
          ) : (
            <CardGrid ariaLabel="Projects" variant="grid3">
              {rows.map((project) => (
                <ProjectCard
                  key={project.id}
                  project={project}
                  onOpenProject={onOpenProject}
                  onArchiveProject={onArchiveProject}
                  onActivateProject={onActivateProject}
                  onStarProject={onStarProject}
                  onUnstarProject={onUnstarProject}
                  onDeleteProject={onDeleteProject}
                />
              ))}
            </CardGrid>
          )}
        </Page>
      )}
    </section>
  );
}

const filterRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 12,
};

const createButtonStyle: CSSProperties = {
  marginInlineStart: "auto",
  flexShrink: 0,
};

// ===========================================================================
// ProjectCard — one card in the grid
// ===========================================================================

interface ProjectCardProps {
  readonly project: ProjectSummary;
  readonly onOpenProject?: (id: ProjectId) => void;
  readonly onArchiveProject?: (id: ProjectId) => void;
  readonly onActivateProject?: (id: ProjectId) => void;
  readonly onStarProject?: (id: ProjectId) => void;
  readonly onUnstarProject?: (id: ProjectId) => void;
  readonly onDeleteProject?: (id: ProjectId) => void;
}

function ProjectCard({
  project,
  onOpenProject,
  onArchiveProject,
  onActivateProject,
  onStarProject,
  onUnstarProject,
  onDeleteProject,
}: ProjectCardProps): ReactElement {
  const isActive = project.status === "active";

  return (
    <div
      className="ui-proj-card"
      style={cardWrapperStyle}
      data-testid="project-card-wrapper"
      data-project-id={project.id}
      data-status={project.status}
      data-viewer-starred={project.viewer_starred ? "true" : "false"}
    >
      {/* The whole card is one hit area (D2). Border/radius/padding live HERE so
          the measured `default.card` and `default.card.hitarea` collapse onto
          the same element. */}
      <button
        type="button"
        className="ui-card ui-card--proj"
        style={cardButtonStyle}
        data-testid="project-card"
        data-project-id={project.id}
        onClick={
          onOpenProject !== undefined
            ? () => onOpenProject(project.id)
            : undefined
        }
        aria-label={`Open project ${project.name}`}
      >
        <div style={headStyle}>
          <ProjectIconTile
            name={project.name}
            colorHue={project.color_hue}
            testId="project-card-icon"
          />
          {/* Plain span, not an <ItemLink> — a link inside a button is invalid,
              and the card itself owns navigation (D2). */}
          <span style={nameStyle} data-testid="project-card-name">
            {project.name}
          </span>
          <StatusPill
            status={statusTone(project.status)}
            label={statusLabel(project.status)}
          />
        </div>

        {project.description.length > 0 ? (
          <div style={descStyle} data-testid="project-card-description">
            {project.description}
          </div>
        ) : null}

        <div style={metaRowStyle}>
          {/* Viewer role chip — live-only chrome kept from the web scaffold (D1),
              conditioned on `viewer_role !== null` so `single_user_desktop`
              (which returns a null role) shows no empty strip. */}
          {project.viewer_role !== null ? (
            <span
              data-testid="project-card-role"
              data-role={project.viewer_role}
            >
              <StatusPill status="muted" label={project.viewer_role} />
            </span>
          ) : null}
          {/* Counts line — the design's mono `.lrow__sub`
              (copilot-app.jsx:422-424): `{chats} chats · {files} files`.
              `counts.files` is library `kind='file'` only. The chats segment is
              HIDDEN when `counts.chats === null` so a fabricated "0 chats" never
              reaches the card (PRD-07 DoD 13). */}
          <span
            data-testid="project-card-counts"
            style={{
              fontFamily: "var(--font-mono)",
              color: "var(--color-text-subtle)",
              fontSize: "var(--font-size-2xs)",
            }}
          >
            {project.counts.chats !== null
              ? `${project.counts.chats} chats · ${project.counts.files} files`
              : `${project.counts.files} files`}
          </span>
        </div>
      </button>

      {/* Lifecycle actions — OUTSIDE the button in DOM order (D2), so they keep
          tab order and stay keyboard reachable; `position:absolute` is visual
          only. Revealed on `:hover` / `:focus-within` via `.ui-proj-card`. */}
      <div className="ui-proj-card__actions" style={actionsOverlayStyle}>
        {onStarProject !== undefined && !project.viewer_starred ? (
          <button
            type="button"
            data-testid="project-card-star"
            onClick={() => onStarProject(project.id)}
            style={actionButtonStyle}
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
            style={starActiveButtonStyle}
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
        {onDeleteProject !== undefined ? (
          <button
            type="button"
            data-testid="project-card-delete"
            onClick={() => onDeleteProject(project.id)}
            style={actionButtonStyle}
            aria-label={`Delete ${project.name}`}
          >
            Delete
          </button>
        ) : null}
      </div>
    </div>
  );
}

const cardWrapperStyle: CSSProperties = {
  position: "relative",
  minWidth: 0,
  display: "flex",
};

// `.card.proj-card` (copilot.css:737-742, :1711-1716): the button IS the card —
// border / radius / padding here so the hit area collapses onto the card element.
const cardButtonStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 10,
  width: "100%",
  minWidth: 0,
  padding: "var(--space-card-pad)",
  borderRadius: "var(--radius-md)",
  border: "1px solid var(--color-border)",
  backgroundColor: "var(--color-surface)",
  color: "var(--color-text)",
  boxSizing: "border-box",
  // The design `.card` has no shadow; the `.ui-card` recipe adds one, so
  // override it inline (radius + padding are already overridden above).
  boxShadow: "none",
  textAlign: "left",
  font: "inherit",
  cursor: "pointer",
};

const headStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 12,
  minWidth: 0,
};

const nameStyle: CSSProperties = {
  flex: 1,
  minWidth: 0,
  fontFamily: "var(--font-sans)",
  fontSize: "var(--font-size-sm)",
  fontWeight: "var(--font-weight-semibold)",
  color: "var(--color-text)",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const descStyle: CSSProperties = {
  fontSize: "var(--font-size-2xs)",
  color: "var(--color-text-muted)",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const metaRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  flexWrap: "wrap",
  minWidth: 0,
};

const actionsOverlayStyle: CSSProperties = {
  position: "absolute",
  top: 8,
  right: 8,
  display: "flex",
  alignItems: "center",
  gap: 4,
};

const actionButtonStyle: CSSProperties = {
  background: "var(--color-surface-elevated)",
  border: "1px solid var(--color-border)",
  borderRadius: "var(--radius-sm)",
  color: "var(--color-text-subtle)",
  cursor: "pointer",
  fontSize: "var(--font-size-2xs)",
  padding: "2px 6px",
};

const starActiveButtonStyle: CSSProperties = {
  ...actionButtonStyle,
  color: "var(--color-warning)",
};

// ===========================================================================
// Helpers
// ===========================================================================

const STATUS_TONE: Readonly<Record<ProjectStatus, StatusTone>> = {
  active: "ok",
  archived: "muted",
};

const STATUS_LABEL: Readonly<Record<ProjectStatus, string>> = {
  active: "active",
  archived: "archived",
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
    height: 96,
    borderRadius: "var(--radius-md)",
    border: "1px solid var(--color-border)",
    backgroundColor: "var(--color-surface-muted)",
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
