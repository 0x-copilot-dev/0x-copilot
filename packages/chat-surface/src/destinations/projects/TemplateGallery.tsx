// TemplateGallery — P6.5-B1
//
// List view of `ProjectTemplate`s with preview cards. Mirrors the
// destinations-master-prd grid pattern (CardGrid + FilterTabs +
// EmptyState) used by every "browse" destination.
//
// Source: projects-extensions-prd §7.6.
//
// Pure presentation: the host fetches templates and supplies callbacks
// for fork / edit / delete / save-from-project.
//
// SP-1 primitives:
//   - <FilterTabs> for the "all" / "mine" filter (cross-audit §1.5)
//   - <CardGrid> for the responsive grid
//   - <EmptyState> for the no-templates state
//   - <StatusPill> for the "owned by you" chip

import {
  useMemo,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

import type { ProjectTemplateId } from "@0x-copilot/api-types";

import { CardGrid } from "../../shell/CardGrid";
import { EmptyState } from "../../shell/EmptyState";
import { FilterTabs, type FilterTabOption } from "../../shell/FilterTabs";
import { StatusPill } from "../../shell/StatusPill";

// ── Tokens ───────────────────────────────────────────────────────────

const PANEL_BACKGROUND = "var(--color-surface)";
const PANEL_BORDER = "var(--color-border)";
const PANEL_BORDER_STRONG = "var(--color-border-strong)";
const TEXT_PRIMARY = "var(--color-text)";
const TEXT_SECONDARY = "var(--color-text-muted)";
const TEXT_FAINT = "var(--color-text-subtle)";
const ACCENT = "var(--color-accent)";
const ACCENT_CONTRAST = "var(--color-accent-contrast)";
const DANGER = "var(--color-danger)";

// ── Public types ─────────────────────────────────────────────────────

/** Canonical brand from `@0x-copilot/api-types/brands.ts`
 *  (projects-extensions-prd §7.2). Re-exported so existing
 *  `import { ProjectTemplateId } from "..."/TemplateGallery"` keep
 *  working without a churn pass. */
export type { ProjectTemplateId };

/** Card-level view-model for a project template. Subset of §7.2's full
 *  `ProjectTemplate` shape — only fields the gallery card needs. */
export interface ProjectTemplateCard {
  readonly id: ProjectTemplateId;
  readonly name: string;
  readonly description: string;
  /** Optional emoji captured from the snapshot. */
  readonly iconEmoji?: string;
  /** Optional color hue captured from the snapshot. */
  readonly colorHue?: number;
  /** Display name of the template author (server-projected). */
  readonly ownerDisplayName: string;
  readonly ownerUserId: string;
  /** Whether the current viewer authored this template (drives the
   *  "owned by you" pill and edit/delete affordances). */
  readonly viewerIsOwner: boolean;
  readonly seededTodosCount: number;
  readonly seededRoutinesCount: number;
  /** Forks-out count, surfaced for popularity hinting (§7.6 "fork count"). */
  readonly forkCount: number;
  readonly createdAt: string;
}

export type TemplateGalleryFilterSlug = "all" | "mine";

export interface TemplateGalleryFilterCounts {
  readonly all: number;
  readonly mine: number;
}

export interface TemplateGalleryProps {
  /** `null` = loading skeleton state. */
  readonly templates: ReadonlyArray<ProjectTemplateCard> | null;

  readonly filter?: TemplateGalleryFilterSlug;
  readonly counts?: TemplateGalleryFilterCounts;
  readonly onFilterChange?: (next: TemplateGalleryFilterSlug) => void;

  readonly onFork?: (templateId: ProjectTemplateId) => void;
  readonly onEdit?: (templateId: ProjectTemplateId) => void;
  readonly onDelete?: (templateId: ProjectTemplateId) => void;

  /** Primary action: "New from project" — opens the per-project
   *  save-as-template flow (§7.6). */
  readonly onSaveFromProject?: () => void;

  /** Optional custom empty-state body (e.g. when filtering by "mine"). */
  readonly emptyStateOverride?: ReactNode;

  readonly className?: string;
}

// ── Helpers ──────────────────────────────────────────────────────────

function formatCreatedAt(iso: string, now: number = Date.now()): string {
  const parsed = Date.parse(iso);
  if (Number.isNaN(parsed)) return "—";
  const diff = Math.max(0, now - parsed);
  const days = Math.floor(diff / (1000 * 60 * 60 * 24));
  if (days < 1) return "today";
  if (days < 7) return `${days}d ago`;
  if (days < 30) return `${Math.floor(days / 7)}w ago`;
  if (days < 365) return `${Math.floor(days / 30)}mo ago`;
  return `${Math.floor(days / 365)}y ago`;
}

// ── Card ─────────────────────────────────────────────────────────────

function TemplateCard({
  template,
  onFork,
  onEdit,
  onDelete,
  now,
}: {
  template: ProjectTemplateCard;
  onFork?: (id: ProjectTemplateId) => void;
  onEdit?: (id: ProjectTemplateId) => void;
  onDelete?: (id: ProjectTemplateId) => void;
  now: number;
}): ReactElement {
  const cardStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 10,
    padding: 16,
    borderRadius: 10,
    border: `1px solid ${PANEL_BORDER}`,
    backgroundColor: PANEL_BACKGROUND,
    color: TEXT_PRIMARY,
    boxSizing: "border-box",
    minHeight: 168,
  };
  const headerRow: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 10,
  };
  const iconStyle: CSSProperties = {
    width: 36,
    height: 36,
    borderRadius: 8,
    backgroundColor: `hsl(${template.colorHue ?? 200}, 55%, 35%)`,
    color: ACCENT_CONTRAST,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    fontSize: 18,
    flexShrink: 0,
  };
  const nameStyle: CSSProperties = {
    margin: 0,
    fontSize: 14,
    fontWeight: 600,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };
  const descStyle: CSSProperties = {
    fontSize: 12,
    color: TEXT_SECONDARY,
    display: "-webkit-box",
    WebkitLineClamp: 2,
    WebkitBoxOrient: "vertical",
    overflow: "hidden",
    lineHeight: 1.4,
    minHeight: 32,
  };
  const metaStyle: CSSProperties = {
    fontSize: 11,
    color: TEXT_FAINT,
    display: "flex",
    gap: 10,
    alignItems: "center",
    flexWrap: "wrap",
  };
  const actionsRow: CSSProperties = {
    display: "flex",
    gap: 6,
    marginTop: "auto",
  };
  const primaryBtn: CSSProperties = {
    height: 28,
    padding: "0 12px",
    borderRadius: 6,
    border: "none",
    backgroundColor: ACCENT,
    color: ACCENT_CONTRAST,
    fontSize: 12,
    fontWeight: 600,
    cursor: "pointer",
  };
  const secondaryBtn: CSSProperties = {
    height: 28,
    padding: "0 10px",
    borderRadius: 6,
    border: `1px solid ${PANEL_BORDER_STRONG}`,
    backgroundColor: "transparent",
    color: TEXT_SECONDARY,
    fontSize: 12,
    cursor: "pointer",
  };
  const dangerBtn: CSSProperties = {
    ...secondaryBtn,
    color: DANGER,
  };
  return (
    <article
      style={cardStyle}
      data-testid="template-card"
      data-template-id={template.id}
    >
      <div style={headerRow}>
        <div style={iconStyle} aria-hidden="true">
          {template.iconEmoji ?? "📁"}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <h3 style={nameStyle}>{template.name}</h3>
          <div style={metaStyle}>
            <span data-testid="template-card-owner">
              by {template.ownerDisplayName}
            </span>
            {template.viewerIsOwner ? (
              <StatusPill status="info" label="You" />
            ) : null}
          </div>
        </div>
      </div>
      <p style={descStyle} data-testid="template-card-description">
        {template.description || "No description."}
      </p>
      <div style={metaStyle}>
        <span data-testid="template-card-seeded">
          Seeds {template.seededTodosCount} todo
          {template.seededTodosCount === 1 ? "" : "s"} ·{" "}
          {template.seededRoutinesCount} routine
          {template.seededRoutinesCount === 1 ? "" : "s"}
        </span>
        <span aria-hidden="true">·</span>
        <span data-testid="template-card-forks">
          {template.forkCount} fork{template.forkCount === 1 ? "" : "s"}
        </span>
        <span aria-hidden="true">·</span>
        <span>{formatCreatedAt(template.createdAt, now)}</span>
      </div>
      <div style={actionsRow}>
        {onFork !== undefined ? (
          <button
            type="button"
            onClick={() => onFork(template.id)}
            style={primaryBtn}
            data-testid="template-card-fork"
            aria-label={`Fork ${template.name}`}
          >
            Fork
          </button>
        ) : null}
        {onEdit !== undefined && template.viewerIsOwner ? (
          <button
            type="button"
            onClick={() => onEdit(template.id)}
            style={secondaryBtn}
            data-testid="template-card-edit"
            aria-label={`Edit ${template.name}`}
          >
            Edit
          </button>
        ) : null}
        {onDelete !== undefined && template.viewerIsOwner ? (
          <button
            type="button"
            onClick={() => onDelete(template.id)}
            style={dangerBtn}
            data-testid="template-card-delete"
            aria-label={`Delete ${template.name}`}
          >
            Delete
          </button>
        ) : null}
      </div>
    </article>
  );
}

// ── Skeleton card ────────────────────────────────────────────────────

const SKELETON_COUNT = 6;

function SkeletonCard({ index }: { index: number }): ReactElement {
  const style: CSSProperties = {
    height: 168,
    borderRadius: 10,
    border: `1px solid ${PANEL_BORDER}`,
    backgroundColor: PANEL_BACKGROUND,
    padding: 16,
    display: "flex",
    flexDirection: "column",
    gap: 10,
    opacity: 0.7,
  };
  const bar: CSSProperties = {
    backgroundColor: "var(--color-surface-muted)",
    borderRadius: 4,
    height: 12,
  };
  return (
    <div
      style={style}
      data-testid="template-skeleton-card"
      data-skeleton-index={index}
      aria-hidden="true"
    >
      <div style={{ ...bar, width: "70%", height: 14 }} />
      <div style={{ ...bar, width: "100%" }} />
      <div style={{ ...bar, width: "60%" }} />
      <div style={{ flex: 1 }} />
      <div style={{ ...bar, width: "40%" }} />
    </div>
  );
}

// ── Component ────────────────────────────────────────────────────────

export function TemplateGallery(props: TemplateGalleryProps): ReactElement {
  const {
    templates,
    filter = "all",
    counts,
    onFilterChange,
    onFork,
    onEdit,
    onDelete,
    onSaveFromProject,
    emptyStateOverride,
    className,
  } = props;

  const now = Date.now();

  const filterOptions = useMemo<
    ReadonlyArray<FilterTabOption<TemplateGalleryFilterSlug>>
  >(
    () => [
      { slug: "all", label: "All", count: counts?.all },
      { slug: "mine", label: "Mine", count: counts?.mine },
    ],
    [counts],
  );

  const state: "loading" | "ready-empty" | "ready" =
    templates === null
      ? "loading"
      : templates.length === 0
        ? "ready-empty"
        : "ready";

  const headerStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 12,
    marginBottom: 12,
  };
  const titleStyle: CSSProperties = {
    fontSize: 18,
    fontWeight: 600,
    margin: 0,
  };
  const primaryBtn: CSSProperties = {
    height: 32,
    padding: "0 14px",
    borderRadius: 8,
    border: "none",
    backgroundColor: ACCENT,
    color: ACCENT_CONTRAST,
    fontSize: 13,
    fontWeight: 600,
    cursor: "pointer",
  };

  return (
    <section
      aria-label="Project templates"
      data-testid="template-gallery"
      data-state={state}
      data-filter={filter}
      className={className}
      style={{ display: "flex", flexDirection: "column", gap: 12 }}
    >
      <div style={headerStyle}>
        <h2 style={titleStyle}>Project templates</h2>
        {onSaveFromProject !== undefined ? (
          <button
            type="button"
            onClick={onSaveFromProject}
            style={primaryBtn}
            data-testid="template-gallery-new-from-project"
          >
            New from project
          </button>
        ) : null}
      </div>

      {onFilterChange !== undefined ? (
        <FilterTabs<TemplateGalleryFilterSlug>
          value={filter}
          onChange={onFilterChange}
          options={filterOptions}
          ariaLabel="Template filter"
          idPrefix="template-gallery"
        />
      ) : null}

      {state === "loading" ? (
        <CardGrid minCardWidth={280} gap={12}>
          {Array.from({ length: SKELETON_COUNT }).map((_, i) => (
            <SkeletonCard key={i} index={i} />
          ))}
        </CardGrid>
      ) : null}

      {state === "ready-empty"
        ? (emptyStateOverride ?? (
            <EmptyState
              title="No project templates yet"
              body="Save an existing project as a template to share its setup with your tenant."
              action={
                onSaveFromProject !== undefined
                  ? {
                      label: "New from project",
                      onClick: onSaveFromProject,
                    }
                  : undefined
              }
            />
          ))
        : null}

      {state === "ready" && templates !== null ? (
        <CardGrid minCardWidth={280} gap={12} ariaLabel="Templates">
          {templates.map((t) => (
            <TemplateCard
              key={t.id}
              template={t}
              onFork={onFork}
              onEdit={onEdit}
              onDelete={onDelete}
              now={now}
            />
          ))}
        </CardGrid>
      ) : null}
    </section>
  );
}
