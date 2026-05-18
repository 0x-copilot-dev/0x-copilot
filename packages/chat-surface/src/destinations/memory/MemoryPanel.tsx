// Memory — right-side context panel (P12-B2).
//
// Per team-memory-cmdk-prd §7.2 the panel carries the same kind /
// scope filter axes that the destination's `<FilterTabs>` show, plus
// a tag-chip surface and an "Add memory" CTA. Mirrors the shape of
// `RoutinesPanel` (left rail) — a thin filter strip + scope toggle +
// tag chips + footer slot.
//
// Out of scope (carved out so the panel stays focused):
//   - Saved-searches CRUD (Inbox §3.3 #5 — future).
//   - Search input (lives on the destination, not in the panel).
//   - Project-scope filter (the destination's `project_id` filter is
//     project-aware; the panel surfaces the tag axis instead).
//
// Substrate-agnostic. No fetch, no router calls.

import {
  useMemo,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

import { ContextPanel } from "../../shell/ContextPanel";
import { FilterTabs, type FilterTabOption } from "../../shell/FilterTabs";

import {
  type MemoryKindFilterCounts,
  type MemoryKindFilterSlug,
  type MemoryScopeFilterSlug,
} from "./MemoryDestination";

// ===========================================================================
// Tag chip surface
// ===========================================================================

export interface MemoryPanelTagChip {
  readonly tag: string;
  readonly count: number;
}

// ===========================================================================
// Public props
// ===========================================================================

export interface MemoryPanelProps {
  /** Active kind filter — shared with the destination. */
  readonly kindFilter?: MemoryKindFilterSlug;
  readonly onKindFilterChange?: (next: MemoryKindFilterSlug) => void;
  readonly kindCounts?: MemoryKindFilterCounts;

  /** Active scope filter — shared with the destination. */
  readonly scopeFilter?: MemoryScopeFilterSlug;
  readonly onScopeFilterChange?: (next: MemoryScopeFilterSlug) => void;

  /** Tag chips with usage counts. Empty array hides the section. */
  readonly tags?: ReadonlyArray<MemoryPanelTagChip>;
  /** Currently selected tag — null means "no tag filter". */
  readonly activeTag?: string | null;
  readonly onTagFilterChange?: (next: string | null) => void;

  /** "Add memory" CTA — same callback as the destination's PageHeader
   *  primary action. */
  readonly onCreateMemory?: () => void;

  /** Optional footer slot. */
  readonly footer?: ReactNode;
}

// ===========================================================================
// Filter-axis labels (mirror MemoryDestination — single source of truth in
// the destination module).
// ===========================================================================

const KIND_OPTIONS_ORDER: ReadonlyArray<MemoryKindFilterSlug> = [
  "all",
  "skill",
  "fact",
  "preference",
];

const KIND_OPTIONS_LABEL: Readonly<Record<MemoryKindFilterSlug, string>> = {
  all: "All",
  skill: "Skills",
  fact: "Facts",
  preference: "Preferences",
};

const SCOPE_OPTIONS_ORDER: ReadonlyArray<MemoryScopeFilterSlug> = [
  "all",
  "user",
  "workspace",
];

const SCOPE_OPTIONS_LABEL: Readonly<Record<MemoryScopeFilterSlug, string>> = {
  all: "All",
  user: "My",
  workspace: "Workspace",
};

// ===========================================================================
// Top-level panel
// ===========================================================================

export function MemoryPanel(props: MemoryPanelProps = {}): ReactElement {
  const {
    kindFilter = "all",
    onKindFilterChange,
    kindCounts,
    scopeFilter = "all",
    onScopeFilterChange,
    tags = [],
    activeTag = null,
    onTagFilterChange,
    onCreateMemory,
    footer,
  } = props;

  const kindOptions = useMemo<
    ReadonlyArray<FilterTabOption<MemoryKindFilterSlug>>
  >(
    () =>
      KIND_OPTIONS_ORDER.map((slug) => ({
        slug,
        label: KIND_OPTIONS_LABEL[slug],
        count: kindCounts?.[slug],
      })),
    [kindCounts],
  );

  const scopeOptions = useMemo<
    ReadonlyArray<FilterTabOption<MemoryScopeFilterSlug>>
  >(
    () =>
      SCOPE_OPTIONS_ORDER.map((slug) => ({
        slug,
        label: SCOPE_OPTIONS_LABEL[slug],
      })),
    [],
  );

  const handleKindChange = (next: MemoryKindFilterSlug): void => {
    if (onKindFilterChange !== undefined) onKindFilterChange(next);
  };
  const handleScopeChange = (next: MemoryScopeFilterSlug): void => {
    if (onScopeFilterChange !== undefined) onScopeFilterChange(next);
  };

  return (
    <ContextPanel
      title="Memory"
      subtitle="What Atlas remembers"
      destination="memory"
      primaryAction={
        onCreateMemory !== undefined
          ? { label: "Add memory", onClick: onCreateMemory }
          : undefined
      }
    >
      <div data-testid="memory-panel">
        <PanelSectionWrapper testId="memory-panel-section-kind" title="Kind">
          <FilterTabs<MemoryKindFilterSlug>
            value={kindFilter}
            onChange={handleKindChange}
            options={kindOptions}
            ariaLabel="Memory kind filter"
            idPrefix="memory-panel-kind"
          />
        </PanelSectionWrapper>

        <PanelSectionWrapper testId="memory-panel-section-scope" title="Scope">
          <FilterTabs<MemoryScopeFilterSlug>
            value={scopeFilter}
            onChange={handleScopeChange}
            options={scopeOptions}
            ariaLabel="Memory scope filter"
            idPrefix="memory-panel-scope"
          />
        </PanelSectionWrapper>

        {tags.length > 0 ? (
          <PanelSectionWrapper testId="memory-panel-section-tags" title="Tags">
            <ul data-testid="memory-panel-tags" style={tagListStyle}>
              <li>
                <button
                  type="button"
                  data-testid="memory-panel-tag-all"
                  data-active={activeTag === null ? "true" : "false"}
                  onClick={() => {
                    if (onTagFilterChange !== undefined) {
                      onTagFilterChange(null);
                    }
                  }}
                  style={tagButtonStyle(activeTag === null)}
                >
                  <span>All tags</span>
                </button>
              </li>
              {tags.map((t) => {
                const active = activeTag === t.tag;
                return (
                  <li key={t.tag}>
                    <button
                      type="button"
                      data-testid={`memory-panel-tag-${t.tag}`}
                      data-active={active ? "true" : "false"}
                      onClick={() => {
                        if (onTagFilterChange !== undefined) {
                          onTagFilterChange(t.tag);
                        }
                      }}
                      style={tagButtonStyle(active)}
                    >
                      <span>#{t.tag}</span>
                      <span style={tagCountStyle}>{t.count}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </PanelSectionWrapper>
        ) : null}

        {footer !== undefined ? (
          <PanelSectionWrapper
            testId="memory-panel-section-footer"
            title="Settings"
          >
            <div data-testid="memory-panel-footer">{footer}</div>
          </PanelSectionWrapper>
        ) : null}
      </div>
    </ContextPanel>
  );
}

// ===========================================================================
// PanelSectionWrapper — local clone of the Inbox / Todos / Routines frame.
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

const tagListStyle: CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: 0,
  display: "flex",
  flexDirection: "column",
  gap: 2,
};

const tagCountStyle: CSSProperties = {
  fontSize: "var(--font-size-2xs, 11px)",
  color: "var(--color-text-subtle, #7e7e84)",
  background: "var(--color-surface-muted, #222224)",
  borderRadius: "var(--radius-full, 999px)",
  padding: "1px 8px",
  minWidth: 18,
  textAlign: "center",
};

function tagButtonStyle(active: boolean): CSSProperties {
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
