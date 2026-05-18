// Library — right-side context panel (P7-B1).
//
// Per library-prd §3.3 the panel carries (P7-B1 ships the four filter
// axes + sort — the rest land with P7-B2):
//
//   1. Source filter (user_upload / agent_save / connector_sync) — single
//      vertical FilterTabs chiplist, OR semantics within the axis per
//      cross-audit §1.5.
//   2. Project filter — shared `<ProjectFilterChip>` from Projects P6.
//   3. Sort selector — library-prd §4.4 sort allowlist surfaced as a
//      native `<select>` (single-select; the allowlist is small).
//
// Out of scope for P7-B1 (carved out so the shell merge is small):
//   - Pinned items list (per-user pins live in the panel; needs
//     `library_pins` table on the backend — P7-A*).
//   - "By project" collapsible groups (panel-side groupings; needs
//     the `/v1/library?group_by=project` aggregation that P7-A1 owns).
//   - "Recently accessed" panel section (the destination already
//     renders a horizontal strip; the panel duplicate is deferred).
//   - Saved searches (≤ 20 per user, library-prd §3.3 #7).
//   - Footer link to "Library guide" doc.
//
// Substrate-agnostic (web + desktop). No fetch, no router calls; the
// host (apps/frontend P7-C) wires those.

import {
  useMemo,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

import type { ProjectId } from "@enterprise-search/api-types";

import { ContextPanel } from "../../shell/ContextPanel";
import { FilterTabs, type FilterTabOption } from "../../shell/FilterTabs";
import {
  ProjectFilterChip,
  type ProjectFilterChipOption,
} from "../projects/ProjectFilterChip";

// TODO(merge): rewire to "@enterprise-search/api-types"
import type { LibrarySortSlug, LibrarySourceKind } from "./_library-stub";

// ===========================================================================
// Source filter
// ===========================================================================
//
// Three axes, OR within. "all" is the implicit no-filter state; the chip
// list models it via a leading "All sources" tab so the user can reset
// without hunting for an explicit clear.

export type LibrarySourceFilterSlug = "all" | LibrarySourceKind;

const SOURCE_ORDER: ReadonlyArray<LibrarySourceFilterSlug> = [
  "all",
  "user_upload",
  "agent_save",
  "connector_sync",
];

const SOURCE_LABEL: Readonly<Record<LibrarySourceFilterSlug, string>> = {
  all: "All sources",
  user_upload: "Uploaded",
  agent_save: "Saved from chats",
  connector_sync: "Synced",
};

export type LibrarySourceFilterCounts = Readonly<
  Record<LibrarySourceFilterSlug, number>
>;

// ===========================================================================
// Sort
// ===========================================================================

const SORT_ORDER: ReadonlyArray<LibrarySortSlug> = [
  "updated_at:desc",
  "created_at:desc",
  "name:asc",
  "name:desc",
  "last_accessed_at:desc",
  "size_bytes:desc",
];

const SORT_LABEL: Readonly<Record<LibrarySortSlug, string>> = {
  "updated_at:desc": "Recently updated",
  "created_at:desc": "Recently created",
  "name:asc": "Name (A→Z)",
  "name:desc": "Name (Z→A)",
  "last_accessed_at:desc": "Recently accessed",
  "size_bytes:desc": "Largest first",
};

// ===========================================================================
// Public props
// ===========================================================================

export interface LibraryPanelProps {
  /** Source filter axis (library-prd §3.3 #5). */
  readonly sourceFilter?: LibrarySourceFilterSlug;
  readonly onSourceFilterChange?: (next: LibrarySourceFilterSlug) => void;
  readonly sourceCounts?: LibrarySourceFilterCounts;

  /** Project filter axis — shared widget from Projects P6 (library-prd
   *  §3.3 #4). */
  readonly projects?: ReadonlyArray<ProjectFilterChipOption>;
  readonly projectFilter?: ProjectId | null;
  readonly onProjectFilterChange?: (next: ProjectId | null) => void;

  /** Sort (library-prd §4.4 allowlist). */
  readonly sort?: LibrarySortSlug;
  readonly onSortChange?: (next: LibrarySortSlug) => void;

  /** Primary CTAs (library-prd §3.3 #1). Single primary action on the
   *  panel header — "Upload". Side actions land via the footer slot. */
  readonly onUploadFile?: () => void;
  readonly footer?: ReactNode;
}

// ===========================================================================
// Top-level panel
// ===========================================================================

export function LibraryPanel(props: LibraryPanelProps = {}): ReactElement {
  const {
    sourceFilter = "all",
    onSourceFilterChange,
    sourceCounts,
    projects,
    projectFilter = null,
    onProjectFilterChange,
    sort = "updated_at:desc",
    onSortChange,
    onUploadFile,
    footer,
  } = props;

  const sourceOptions = useMemo<
    ReadonlyArray<FilterTabOption<LibrarySourceFilterSlug>>
  >(
    () =>
      SOURCE_ORDER.map((slug) => ({
        slug,
        label: SOURCE_LABEL[slug],
        count: sourceCounts?.[slug],
      })),
    [sourceCounts],
  );

  const handleSourceChange = (next: LibrarySourceFilterSlug): void => {
    if (onSourceFilterChange !== undefined) onSourceFilterChange(next);
  };

  const handleProjectChange = (next: ProjectId | null): void => {
    if (onProjectFilterChange !== undefined) onProjectFilterChange(next);
  };

  return (
    <ContextPanel
      title="Library"
      subtitle="What you've saved, in one place"
      destination="library"
      primaryAction={
        onUploadFile !== undefined
          ? { label: "+ Upload file", onClick: onUploadFile }
          : undefined
      }
    >
      <div data-testid="library-panel">
        <PanelSectionWrapper
          testId="library-panel-section-source"
          title="Source"
        >
          <FilterTabs<LibrarySourceFilterSlug>
            value={sourceFilter}
            onChange={handleSourceChange}
            options={sourceOptions}
            ariaLabel="Library source filter"
            idPrefix="library-panel-source"
          />
        </PanelSectionWrapper>

        {projects !== undefined ? (
          <PanelSectionWrapper
            testId="library-panel-section-project"
            title="Project"
          >
            <ProjectFilterChip
              projects={projects}
              value={projectFilter}
              onChange={handleProjectChange}
            />
          </PanelSectionWrapper>
        ) : null}

        <PanelSectionWrapper testId="library-panel-section-sort" title="Sort">
          <select
            value={sort}
            onChange={(e) => {
              if (onSortChange !== undefined) {
                onSortChange(e.target.value as LibrarySortSlug);
              }
            }}
            style={sortSelectStyle}
            data-testid="library-panel-sort"
            aria-label="Sort library items"
          >
            {SORT_ORDER.map((slug) => (
              <option key={slug} value={slug}>
                {SORT_LABEL[slug]}
              </option>
            ))}
          </select>
        </PanelSectionWrapper>

        {footer !== undefined ? (
          <PanelSectionWrapper
            testId="library-panel-section-footer"
            title="More"
          >
            <div data-testid="library-panel-footer">{footer}</div>
          </PanelSectionWrapper>
        ) : null}
      </div>
    </ContextPanel>
  );
}

// ===========================================================================
// PanelSectionWrapper — local clone of the Projects panel's section frame.
// Same threshold rule: extract to a shared shell primitive when a fourth
// destination uses it (we currently have three: Projects, Library, and
// the implicit Inbox/Todos sections).
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

const sortSelectStyle: CSSProperties = {
  width: "100%",
  height: 28,
  padding: "0 8px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border, #232325)",
  backgroundColor: "var(--color-surface, #1a1a1c)",
  color: "var(--color-text, #ededee)",
  fontSize: "var(--font-size-sm, 13px)",
  outline: "none",
};
