// Tools — destination shell (P10-B1).
//
// Pure-presentation catalog landing per tools-prd §7.2 + §7.4:
//
//   1. <PageHeader title="Tools" /> — title + subtitle + "Onboard" CTA.
//   2. <FilterTabs> — My / Installed / Available / Custom / By kind
//      (tools-prd §7.2). When "By kind" is selected, the kind-pill row
//      renders below the tabs and narrows the catalog by `ToolKind`.
//   3. Search bar (controlled, debounced via host); sort-by select
//      restricted to the tools-prd §4.12 allowlist.
//   4. <CardGrid> of <ToolCard>s — one row per `Tool`.
//   5. <EmptyState> with 4 onboarding tiles (MCP / OpenAPI / Code /
//      Skill) when the catalog is empty (tools-prd §7.4).
//
// Hard correctness rules (staff-engineer preamble):
//   - SP-1 primitives only (PageHeader / FilterTabs / EmptyState /
//     CardGrid / StatusPill / ItemLink). No bespoke buttons, colors,
//     or px constants outside design-system tokens.
//   - Wire-type single source: imports `Tool` etc. from
//     `@0x-copilot/api-types` via `_tools-stub.ts`. Zero
//     `__brand:` re-declarations.
//   - Pure presentation: NO transport, NO router, NO fetch. Props +
//     callbacks only. The data-binder phase (P10-C) wires real data
//     from apps/frontend.
//   - One canonical `formatRelativeTime` from `../../util/time`
//     (consumed inside `ToolCard`).

import {
  useMemo,
  useState,
  type CSSProperties,
  type ReactElement,
} from "react";

import { CardGrid } from "../../shell/CardGrid";
import { EmptyState } from "../../shell/EmptyState";
import { FilterTabs, type FilterTabOption } from "../../shell/FilterTabs";
import { PageHeader } from "../../shell/PageHeader";

import { ToolCard } from "./ToolCard";
import {
  ONBOARD_KIND_TILES,
  TOOLS_FILTER_LABELS,
  TOOLS_FILTER_ORDER,
  TOOLS_KIND_LABELS,
  TOOLS_KIND_ORDER,
  TOOLS_SORT_LABELS,
  TOOLS_SORT_ORDER,
  filterTools,
  searchTools,
  sortTools,
  type Tool,
  type ToolKind,
  type ToolsFilterSlug,
  type ToolsSortSlug,
} from "./_tools-stub";

const BACKGROUND = "var(--color-bg)";
const BORDER = "var(--color-border)";
const TEXT_PRIMARY = "var(--color-text)";
const TEXT_SECONDARY = "var(--color-text-muted)";
const ACCENT = "var(--color-accent)";

// ===========================================================================
// Public props
// ===========================================================================

export interface ToolsDestinationProps {
  /**
   * Tool catalog. Defaults to an empty list, which exercises the
   * "onboarding 4-tile" empty state (tools-prd §7.4). The data-binder
   * phase (P10-C) will wire a real fetch through Transport.
   */
  readonly tools?: ReadonlyArray<Tool>;
  /** Current viewer's user id; drives the "My" filter axis. */
  readonly currentUserId?: string | null;

  /** Active filter slug. Controlled when supplied; uncontrolled otherwise. */
  readonly filter?: ToolsFilterSlug;
  readonly onFilterChange?: (next: ToolsFilterSlug) => void;
  /** Active kind narrow — only meaningful when `filter === "by_kind"`. */
  readonly kindFilter?: ToolKind | null;
  readonly onKindFilterChange?: (next: ToolKind | null) => void;

  /** Active search query. Controlled when supplied; uncontrolled otherwise. */
  readonly search?: string;
  readonly onSearchChange?: (next: string) => void;

  /** Active sort slug. Controlled when supplied; uncontrolled otherwise. */
  readonly sort?: ToolsSortSlug;
  readonly onSortChange?: (next: ToolsSortSlug) => void;

  /** Open a tool's detail view — wired by the host. */
  readonly onOpenTool?: (tool: Tool) => void;
  /** "Onboard" CTA — opens the wizard at `/tools/onboard/<kind?>`. */
  readonly onOnboard?: (kind?: ToolKind) => void;

  /** Reference instant for relative-time formatting (test seam). */
  readonly now?: number;
}

// ===========================================================================
// Shell
// ===========================================================================

export function ToolsDestination(
  props: ToolsDestinationProps = {},
): ReactElement {
  const {
    tools = [],
    currentUserId = null,
    filter: filterProp,
    onFilterChange,
    kindFilter: kindFilterProp,
    onKindFilterChange,
    search: searchProp,
    onSearchChange,
    sort: sortProp,
    onSortChange,
    onOpenTool,
    onOnboard,
    now,
  } = props;

  // Uncontrolled fallbacks (so the destination renders without wiring).
  const [filterLocal, setFilterLocal] = useState<ToolsFilterSlug>("my");
  const [kindLocal, setKindLocal] = useState<ToolKind | null>(null);
  const [searchLocal, setSearchLocal] = useState("");
  const [sortLocal, setSortLocal] = useState<ToolsSortSlug>("name_asc");

  const filter = filterProp ?? filterLocal;
  const kindFilter = kindFilterProp ?? kindLocal;
  const search = searchProp ?? searchLocal;
  const sort = sortProp ?? sortLocal;

  const handleFilter = (next: ToolsFilterSlug): void => {
    if (onFilterChange !== undefined) onFilterChange(next);
    if (filterProp === undefined) setFilterLocal(next);
  };
  const handleKind = (next: ToolKind | null): void => {
    if (onKindFilterChange !== undefined) onKindFilterChange(next);
    if (kindFilterProp === undefined) setKindLocal(next);
  };
  const handleSearch = (next: string): void => {
    if (onSearchChange !== undefined) onSearchChange(next);
    if (searchProp === undefined) setSearchLocal(next);
  };
  const handleSort = (next: ToolsSortSlug): void => {
    if (onSortChange !== undefined) onSortChange(next);
    if (sortProp === undefined) setSortLocal(next);
  };

  // === Derive the visible set ===========================================
  const visible = useMemo<ReadonlyArray<Tool>>(() => {
    const filtered = filterTools(tools, {
      filter,
      kindFilter,
      currentUserId,
    });
    const searched = searchTools(filtered, search);
    return sortTools(searched, sort);
  }, [tools, filter, kindFilter, currentUserId, search, sort]);

  // === Filter-tab options (single source of labels + counts) ============
  const filterOptions = useMemo<
    ReadonlyArray<FilterTabOption<ToolsFilterSlug>>
  >(
    () =>
      TOOLS_FILTER_ORDER.map((slug) => ({
        slug,
        label: TOOLS_FILTER_LABELS[slug],
      })),
    [],
  );

  // === Styles ===========================================================
  const rootStyle: CSSProperties = {
    width: "100%",
    height: "100%",
    minHeight: 0,
    backgroundColor: BACKGROUND,
    color: TEXT_PRIMARY,
    boxSizing: "border-box",
    display: "flex",
    flexDirection: "column",
    overflow: "auto",
  };
  const containerStyle: CSSProperties = {
    width: "100%",
    maxWidth: 1180,
    margin: "0 auto",
    padding: "24px 28px 48px",
    boxSizing: "border-box",
    display: "flex",
    flexDirection: "column",
    gap: 16,
  };
  const toolbarStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 12,
    flexWrap: "wrap",
  };
  const searchInputStyle: CSSProperties = {
    flex: 1,
    minWidth: 220,
    height: 32,
    padding: "0 12px",
    borderRadius: "var(--radius-sm, 6px)",
    border: `1px solid ${BORDER}`,
    backgroundColor: "var(--color-surface, #16161a)",
    color: TEXT_PRIMARY,
    fontFamily: "inherit",
    fontSize: "var(--font-size-sm, 13px)",
    outline: "none",
  };
  const sortSelectStyle: CSSProperties = {
    height: 32,
    padding: "0 8px",
    borderRadius: "var(--radius-sm, 6px)",
    border: `1px solid ${BORDER}`,
    backgroundColor: "var(--color-surface, #16161a)",
    color: TEXT_PRIMARY,
    fontFamily: "inherit",
    fontSize: "var(--font-size-sm, 13px)",
  };
  const kindRowStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 6,
    flexWrap: "wrap",
  };
  const kindChipStyle = (active: boolean): CSSProperties => ({
    height: 24,
    padding: "0 10px",
    borderRadius: "var(--radius-full, 999px)",
    border: `1px solid ${active ? ACCENT : BORDER}`,
    background: active
      ? "color-mix(in srgb, var(--color-accent) 12%, transparent)"
      : "transparent",
    color: active ? ACCENT : TEXT_SECONDARY,
    fontSize: "var(--font-size-xs, 12px)",
    fontWeight: 600,
    cursor: "pointer",
    fontFamily: "inherit",
  });
  const tileGridStyle: CSSProperties = {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
    gap: 12,
    width: "100%",
    marginTop: 12,
  };
  const tileStyle: CSSProperties = {
    padding: 16,
    borderRadius: "var(--radius-md, 10px)",
    border: `1px solid ${BORDER}`,
    background: "var(--color-bg-elevated)",
    color: TEXT_PRIMARY,
    textAlign: "left",
    cursor: "pointer",
    fontFamily: "inherit",
    display: "flex",
    flexDirection: "column",
    gap: 8,
  };
  const tileIconStyle: CSSProperties = {
    width: 32,
    height: 32,
    borderRadius: "var(--radius-sm, 8px)",
    backgroundColor: "var(--color-surface, #16161a)",
    border: `1px solid ${BORDER}`,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    fontSize: 11,
    fontWeight: 700,
    color: TEXT_SECONDARY,
    letterSpacing: 0.4,
  };
  const tileTitleStyle: CSSProperties = {
    fontSize: "var(--font-size-sm, 14px)",
    fontWeight: 600,
    margin: 0,
  };
  const tileDescStyle: CSSProperties = {
    fontSize: "var(--font-size-xs, 12px)",
    color: TEXT_SECONDARY,
    margin: 0,
  };

  // === Render ===========================================================
  return (
    <section
      aria-label="Tools destination"
      data-testid="tools-destination"
      data-filter={filter}
      data-kind-filter={kindFilter ?? "all"}
      data-sort={sort}
      style={rootStyle}
    >
      <div style={containerStyle}>
        <PageHeader
          title="Tools"
          subtitle="Built-in, MCP, OpenAPI, code, and skill tools your team can call."
          primaryAction={
            onOnboard !== undefined
              ? { label: "Onboard tool", onClick: () => onOnboard() }
              : undefined
          }
        />

        {/* === Filter axis ============================================== */}
        <nav aria-label="Tools filter">
          <FilterTabs<ToolsFilterSlug>
            value={filter}
            onChange={handleFilter}
            options={filterOptions}
            ariaLabel="Tools filter"
            idPrefix="tools"
          />
        </nav>

        {/* === Kind narrow row (only visible under "By kind") ============ */}
        {filter === "by_kind" ? (
          <div
            role="group"
            aria-label="Tool kind"
            style={kindRowStyle}
            data-testid="tools-kind-row"
          >
            <button
              type="button"
              data-testid="tools-kind-chip-all"
              data-active={kindFilter === null ? "true" : "false"}
              aria-pressed={kindFilter === null}
              style={kindChipStyle(kindFilter === null)}
              onClick={() => handleKind(null)}
            >
              All
            </button>
            {TOOLS_KIND_ORDER.map((kind) => {
              const active = kindFilter === kind;
              return (
                <button
                  key={kind}
                  type="button"
                  data-testid={`tools-kind-chip-${kind}`}
                  data-active={active ? "true" : "false"}
                  aria-pressed={active}
                  style={kindChipStyle(active)}
                  onClick={() => handleKind(active ? null : kind)}
                >
                  {TOOLS_KIND_LABELS[kind]}
                </button>
              );
            })}
          </div>
        ) : null}

        {/* === Toolbar (search + sort) =================================== */}
        <div style={toolbarStyle} data-testid="tools-toolbar">
          <input
            type="search"
            data-testid="tools-search"
            aria-label="Search tools"
            placeholder="Search tools"
            value={search}
            onChange={(e) => handleSearch(e.target.value)}
            style={searchInputStyle}
          />
          <label
            style={{
              fontSize: "var(--font-size-xs, 12px)",
              color: TEXT_SECONDARY,
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
            }}
          >
            <span>Sort</span>
            <select
              data-testid="tools-sort"
              aria-label="Sort tools"
              value={sort}
              onChange={(e) => handleSort(e.target.value as ToolsSortSlug)}
              style={sortSelectStyle}
            >
              {TOOLS_SORT_ORDER.map((slug) => (
                <option key={slug} value={slug}>
                  {TOOLS_SORT_LABELS[slug]}
                </option>
              ))}
            </select>
          </label>
        </div>

        {/* === Body ====================================================== */}
        {visible.length === 0 ? (
          tools.length === 0 ? (
            // Catalog empty — render the SP-1 EmptyState plus the 4
            // onboarding tiles below (tools-prd §7.4). This is the "no
            // tools at all" path, distinct from "filtered to zero".
            <div data-testid="tools-empty-onboard">
              <EmptyState
                title="No tools yet"
                body="Onboard your first tool — pick a starting point below."
              />
              <div style={tileGridStyle} data-testid="tools-onboard-tiles">
                {ONBOARD_KIND_TILES.map((tile) => (
                  <button
                    key={tile.kind}
                    type="button"
                    data-testid={`tools-onboard-tile-${tile.kind}`}
                    data-onboard-kind={tile.kind}
                    onClick={() => {
                      if (onOnboard !== undefined) onOnboard(tile.kind);
                    }}
                    style={tileStyle}
                  >
                    <span style={tileIconStyle} aria-hidden="true">
                      {tile.icon}
                    </span>
                    <h4 style={tileTitleStyle}>{tile.label}</h4>
                    <p style={tileDescStyle}>{tile.description}</p>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            // Catalog has tools, but the current filter/search narrowed to
            // nothing — different empty state. Keeps the user oriented.
            <EmptyState
              title="No tools match"
              body="Adjust the filter or search query above."
            />
          )
        ) : (
          <CardGrid
            ariaLabel="Tools catalog"
            minCardWidth={280}
            gap={12}
            className="tools-catalog-grid"
          >
            {visible.map((tool) => (
              <ToolCard
                key={tool.id}
                tool={tool}
                onOpen={onOpenTool}
                now={now}
              />
            ))}
          </CardGrid>
        )}
      </div>
    </section>
  );
}
