// Library — destination shell (P7-B1).
//
// Pure-presentation list view per library-prd §3.2:
//
//   1. PageHeader (cross-audit §1.6 shape) — title "Your knowledge
//      library", subtitle with counts, primary action "Upload".
//      Side action "New page" optional (host-supplied).
//   2. Search bar — large, autofocused on first paint per library-prd §9.
//      Debouncing + result rendering is the host's job (P7-C); the shell
//      just emits onSearchChange. When `searchValue` is non-empty the
//      host swaps the body slot for its own results view via
//      `renderSearchResults`.
//   3. FilterTabs — All / Files / Pages / Datasets (library-prd §3.1).
//      "All" subsumes the kind axis — selecting it clears the filter.
//   4. View-toggle (CardGrid / DocList) — default CardGrid per
//      library-prd §3.2.1 (recognition-first browsing). Toggle is a
//      header action; persistence is the host's job.
//   5. Recently-accessed strip — horizontal scroller, shown only on
//      "all" view AND when payload carries items (library-prd §3.2 #2).
//      Each card is an <ItemLink> chip.
//   6. List body — CardGrid (default) or DocList (opt-in) — pure
//      presentation; rows render <ItemLink> for cross-refs.
//
// Empty state = tutorial card with three CTAs: Upload / New page /
// Connect a source (library-prd §3.7 list-empty-all).
//
// Hard correctness rules:
//   - SP-1 primitives only (PageHeader / FilterTabs / CardGrid / DocList /
//     StatusPill / EmptyState / ItemLink). No custom buttons except the
//     view-toggle (which is a tablist-style switch, identical to other
//     destinations' view toggles — kept inline since SP-1 has no
//     dedicated <ViewToggle> primitive).
//   - ItemLink for every cross-destination ref (the item itself, the
//     project chip, the source attribution chip).
//   - Pure presentation: no fetch, no router calls, no SSE — the host
//     (apps/frontend P7-C) wires those.
//   - Search input autofocuses on mount per library-prd §9 a11y.

import {
  useEffect,
  useMemo,
  useRef,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

import type { ItemRef, SectionResult } from "@0x-copilot/api-types";

import { CardGrid } from "../../shell/CardGrid";
import { DocList } from "../../shell/DocList";
import { EmptyState } from "../../shell/EmptyState";
import { FilterTabs, type FilterTabOption } from "../../shell/FilterTabs";
import { PageHeader } from "../../shell/PageHeader";
import { StatusPill, type StatusTone } from "../../shell/StatusPill";
import { ItemLink } from "../../refs/ItemLink";
import { formatRelativeTime } from "../../util/time";

// TODO(merge): rewire to "@0x-copilot/api-types"
import type {
  LibraryIndexStatus,
  LibraryItemSummary,
  LibraryKindFilterCounts,
  LibraryKindFilterSlug,
  LibraryViewMode,
} from "./_library-stub";

// ===========================================================================
// Filter-tab vocabulary (single source of truth)
// ===========================================================================

const FILTER_ORDER: ReadonlyArray<LibraryKindFilterSlug> = [
  "all",
  "files",
  "pages",
  "datasets",
];

const FILTER_LABEL: Readonly<Record<LibraryKindFilterSlug, string>> = {
  all: "All",
  files: "Files",
  pages: "Pages",
  datasets: "Datasets",
};

// ===========================================================================
// Public props
// ===========================================================================

export interface LibraryDestinationProps {
  /**
   * Server-projected list result. `null` = loading skeleton; `error`
   * shows the destination-level error empty-state with retry; `ok`
   * renders the filtered list.
   *
   * `items` is wrapped in `SectionResult` for the uniform "couldn't
   * load" branch — same rationale as Projects / Routines / Inbox.
   */
  readonly items?: SectionResult<ReadonlyArray<LibraryItemSummary>> | null;

  /** Active kind-filter slug. Defaults to "all". */
  readonly filter?: LibraryKindFilterSlug;
  readonly onFilterChange?: (next: LibraryKindFilterSlug) => void;

  /** Per-filter counts. When omitted, chips render without count chips. */
  readonly counts?: LibraryKindFilterCounts;

  /** View toggle. Default "cards" per library-prd §3.2.1
   *  (recognition-first browsing). */
  readonly viewMode?: LibraryViewMode;
  readonly onViewModeChange?: (next: LibraryViewMode) => void;

  /** Search input — host owns the debounced fetch. */
  readonly searchValue?: string;
  readonly onSearchChange?: (next: string) => void;
  /**
   * When `searchValue` is non-empty AND `renderSearchResults` is supplied,
   * the body is replaced by host-rendered search results (library-prd
   * §3.5). Keeps the shell pure: it does not embed search-result row
   * styling.
   */
  readonly renderSearchResults?: (props: {
    readonly query: string;
  }) => ReactNode;

  /** Recently-accessed strip (library-prd §3.2 #2). Suppressed on
   *  kind-filtered views to keep the page calm. */
  readonly recents?: ReadonlyArray<LibraryItemSummary>;

  /** Primary action — "Upload" (library-prd §3.2 PageHeader). */
  readonly onUploadFile?: () => void;
  /** Side action — "New page" (library-prd §3.2 PageHeader actions). */
  readonly onNewPage?: () => void;
  /** Empty-state CTA — "Connect a source" (library-prd §3.7). */
  readonly onConnectSource?: () => void;

  /** Retry callback when `items.status === "error"`. */
  readonly onRetry?: () => void;

  /** Reference instant — test seam for relative-time formatting. */
  readonly now?: number;
}

// ===========================================================================
// Top-level shell
// ===========================================================================

export function LibraryDestination(
  props: LibraryDestinationProps = {},
): ReactElement {
  const {
    items = null,
    filter = "all",
    onFilterChange,
    counts,
    viewMode = "cards",
    onViewModeChange,
    searchValue = "",
    onSearchChange,
    renderSearchResults,
    recents,
    onUploadFile,
    onNewPage,
    onConnectSource,
    onRetry,
    now,
  } = props;

  // === Search input autofocus (library-prd §9 a11y) ===================
  const searchInputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    searchInputRef.current?.focus();
  }, []);

  // === Filter chip options ============================================
  const filterOptions = useMemo<
    ReadonlyArray<FilterTabOption<LibraryKindFilterSlug>>
  >(
    () =>
      FILTER_ORDER.map((slug) => ({
        slug,
        label: FILTER_LABEL[slug],
        count: counts?.[slug],
      })),
    [counts],
  );

  const handleFilterChange = (next: LibraryKindFilterSlug): void => {
    if (onFilterChange !== undefined) onFilterChange(next);
  };

  // === Styles ==========================================================
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

  // === Loading state ===================================================
  if (items === null) {
    return (
      <section
        aria-label="Library destination"
        data-testid="library-destination"
        data-state="loading"
        style={rootStyle}
      >
        <div style={containerStyle}>
          <PageHeader title="Your knowledge library" subtitle="Loading…" />
          <SearchBar
            inputRef={searchInputRef}
            value={searchValue}
            onChange={onSearchChange}
          />
          <CardGrid ariaLabel="Library loading skeleton">
            {Array.from({ length: 6 }).map((_, i) => (
              <CardSkeleton key={i} index={i} />
            ))}
          </CardGrid>
        </div>
      </section>
    );
  }

  // === Error / unavailable ============================================
  if (items.status === "error") {
    return (
      <section
        aria-label="Library destination"
        data-testid="library-destination"
        data-state="error"
        style={rootStyle}
      >
        <div style={containerStyle}>
          <PageHeader title="Your knowledge library" />
          <SearchBar
            inputRef={searchInputRef}
            value={searchValue}
            onChange={onSearchChange}
          />
          <EmptyState
            title="Could not load your library"
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
        aria-label="Library destination"
        data-testid="library-destination"
        data-state="unavailable"
        style={rootStyle}
      >
        <div style={containerStyle}>
          <PageHeader title="Your knowledge library" />
          <EmptyState
            title="Library unavailable"
            body={
              items.error ??
              "This destination is not enabled for your workspace."
            }
          />
        </div>
      </section>
    );
  }

  // === Ready state ====================================================
  const rows = items.data ?? [];
  const searching = searchValue.trim().length > 0;
  const showingRecents =
    !searching &&
    filter === "all" &&
    recents !== undefined &&
    recents.length > 0;

  const totalCount = rows.length;
  const subtitle =
    totalCount === 0
      ? "Files, pages, and datasets you've saved"
      : `${totalCount} item${totalCount === 1 ? "" : "s"}`;

  return (
    <section
      aria-label="Library destination"
      data-testid="library-destination"
      data-state="ready"
      data-filter={filter}
      data-view-mode={viewMode}
      style={rootStyle}
    >
      <div style={containerStyle}>
        <PageHeader
          title="Your knowledge library"
          subtitle={subtitle}
          primaryAction={
            onUploadFile !== undefined
              ? { label: "Upload", onClick: onUploadFile }
              : undefined
          }
          actions={
            <>
              {onNewPage !== undefined ? (
                <button
                  type="button"
                  onClick={onNewPage}
                  style={secondaryActionStyle}
                  data-testid="library-new-page-action"
                >
                  + New page
                </button>
              ) : null}
              <ViewToggle value={viewMode} onChange={onViewModeChange} />
            </>
          }
        />

        <SearchBar
          inputRef={searchInputRef}
          value={searchValue}
          onChange={onSearchChange}
        />

        <FilterTabs<LibraryKindFilterSlug>
          value={filter}
          onChange={handleFilterChange}
          options={filterOptions}
          ariaLabel="Library kind filter"
          idPrefix="library"
        />

        {showingRecents ? (
          <RecentsStrip items={recents!} now={now ?? Date.now()} />
        ) : null}

        {searching && renderSearchResults !== undefined ? (
          <div
            data-testid="library-search-results-slot"
            data-query={searchValue}
          >
            {renderSearchResults({ query: searchValue })}
          </div>
        ) : rows.length === 0 ? (
          filter === "all" ? (
            <TutorialCard
              onUploadFile={onUploadFile}
              onNewPage={onNewPage}
              onConnectSource={onConnectSource}
            />
          ) : (
            <EmptyState
              title={`No ${filterNoun(filter)} match these filters`}
              body="Try clearing a filter, or save your first item from a chat."
              action={
                onFilterChange !== undefined
                  ? {
                      label: "Clear filters",
                      onClick: () => onFilterChange("all"),
                    }
                  : undefined
              }
            />
          )
        ) : viewMode === "list" ? (
          <DocList
            ariaLabel="Library items"
            items={rows}
            keyFor={(row) => `${row.kind}:${row.id as unknown as string}`}
            renderRow={(row) => (
              <LibraryRow item={row} now={now ?? Date.now()} />
            )}
          />
        ) : (
          <CardGrid ariaLabel="Library items">
            {rows.map((row) => (
              <LibraryCard
                key={`${row.kind}:${row.id as unknown as string}`}
                item={row}
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
// SearchBar — large, autofocused input (library-prd §3.2 #3 + §9 a11y)
// ===========================================================================

interface SearchBarProps {
  readonly inputRef: React.RefObject<HTMLInputElement | null>;
  readonly value: string;
  readonly onChange?: (next: string) => void;
}

function SearchBar({
  inputRef,
  value,
  onChange,
}: SearchBarProps): ReactElement {
  const wrapperStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 8,
    width: "100%",
    height: 40,
    padding: "0 12px",
    borderRadius: "var(--radius-md, 12px)",
    border: "1px solid var(--color-border, #232325)",
    backgroundColor: "var(--color-surface, #1a1a1c)",
    color: "var(--color-text, #ededee)",
    boxSizing: "border-box",
  };
  const inputStyle: CSSProperties = {
    flex: 1,
    minWidth: 0,
    height: "100%",
    border: "none",
    background: "transparent",
    color: "var(--color-text, #ededee)",
    fontSize: "var(--font-size-sm, 13px)",
    outline: "none",
  };
  return (
    <div style={wrapperStyle} data-testid="library-search-bar">
      <span aria-hidden="true" style={{ opacity: 0.7 }}>
        🔍
      </span>
      <input
        ref={inputRef}
        type="search"
        value={value}
        onChange={(e) => {
          if (onChange !== undefined) onChange(e.target.value);
        }}
        placeholder="Search your library"
        aria-label="Search your library"
        style={inputStyle}
        data-testid="library-search-input"
      />
    </div>
  );
}

// ===========================================================================
// ViewToggle — CardGrid / DocList header switch
// ===========================================================================

interface ViewToggleProps {
  readonly value: LibraryViewMode;
  readonly onChange?: (next: LibraryViewMode) => void;
}

function ViewToggle({ value, onChange }: ViewToggleProps): ReactElement {
  const groupStyle: CSSProperties = {
    display: "inline-flex",
    height: 32,
    borderRadius: "var(--radius-sm, 6px)",
    border: "1px solid var(--color-border, #232325)",
    overflow: "hidden",
  };
  function tabStyle(active: boolean): CSSProperties {
    return {
      height: "100%",
      padding: "0 12px",
      border: "none",
      background: active
        ? "var(--color-surface-muted, #222224)"
        : "transparent",
      color: active
        ? "var(--color-text, #ededee)"
        : "var(--color-text-muted, #b4b4b8)",
      fontSize: "var(--font-size-sm, 13px)",
      fontWeight: active ? 600 : 500,
      cursor: "pointer",
    };
  }
  function handlePick(next: LibraryViewMode): void {
    if (onChange !== undefined && next !== value) onChange(next);
  }
  return (
    <div
      role="group"
      aria-label="Library view mode"
      style={groupStyle}
      data-testid="library-view-toggle"
      data-view-mode={value}
    >
      <button
        type="button"
        onClick={() => handlePick("cards")}
        style={tabStyle(value === "cards")}
        aria-pressed={value === "cards"}
        data-testid="library-view-toggle-cards"
      >
        Cards
      </button>
      <button
        type="button"
        onClick={() => handlePick("list")}
        style={tabStyle(value === "list")}
        aria-pressed={value === "list"}
        data-testid="library-view-toggle-list"
      >
        List
      </button>
    </div>
  );
}

// ===========================================================================
// TutorialCard — empty state with 3 CTAs (library-prd §3.7)
// ===========================================================================

interface TutorialCardProps {
  readonly onUploadFile?: () => void;
  readonly onNewPage?: () => void;
  readonly onConnectSource?: () => void;
}

function TutorialCard({
  onUploadFile,
  onNewPage,
  onConnectSource,
}: TutorialCardProps): ReactElement {
  const wrapperStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    textAlign: "center",
    gap: 14,
    padding: 48,
    border: "1px dashed var(--color-border-strong, #2a2a2c)",
    borderRadius: "var(--radius-md, 12px)",
    color: "var(--color-text, #ededee)",
  };
  const titleStyle: CSSProperties = {
    fontSize: "var(--font-size-lg, 16px)",
    fontWeight: 600,
    margin: 0,
  };
  const bodyStyle: CSSProperties = {
    fontSize: "var(--font-size-sm, 13px)",
    color: "var(--color-text-muted, #b4b4b8)",
    maxWidth: 440,
  };
  const ctaRowStyle: CSSProperties = {
    display: "flex",
    flexWrap: "wrap",
    justifyContent: "center",
    gap: 8,
    marginTop: 4,
  };
  const ctaStyle: CSSProperties = {
    height: 32,
    padding: "0 14px",
    borderRadius: "var(--radius-sm, 6px)",
    border: "1px solid var(--color-border-strong, #2a2a2c)",
    backgroundColor: "transparent",
    color: "var(--color-accent, #d97757)",
    fontSize: "var(--font-size-sm, 13px)",
    fontWeight: 600,
    cursor: "pointer",
  };
  return (
    <div role="status" style={wrapperStyle} data-testid="library-tutorial-card">
      <h3 style={titleStyle}>Your library is empty</h3>
      <div style={bodyStyle}>
        Library is where Atlas keeps your files, pages, and datasets. Save a
        tool result from a chat, write a knowledge page, or connect a source to
        pull docs in.
      </div>
      <div style={ctaRowStyle}>
        {onUploadFile !== undefined ? (
          <button
            type="button"
            onClick={onUploadFile}
            style={ctaStyle}
            data-testid="library-tutorial-cta-upload"
          >
            + Upload file
          </button>
        ) : null}
        {onNewPage !== undefined ? (
          <button
            type="button"
            onClick={onNewPage}
            style={ctaStyle}
            data-testid="library-tutorial-cta-new-page"
          >
            + New page
          </button>
        ) : null}
        {onConnectSource !== undefined ? (
          <button
            type="button"
            onClick={onConnectSource}
            style={ctaStyle}
            data-testid="library-tutorial-cta-connect"
          >
            + Connect a source
          </button>
        ) : null}
      </div>
    </div>
  );
}

// ===========================================================================
// RecentsStrip — horizontal scroller of recently-accessed items
// ===========================================================================

interface RecentsStripProps {
  readonly items: ReadonlyArray<LibraryItemSummary>;
  readonly now: number;
}

function RecentsStrip({ items, now }: RecentsStripProps): ReactElement {
  const wrapperStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 8,
  };
  const titleStyle: CSSProperties = {
    fontSize: "var(--font-size-xs, 12px)",
    fontWeight: 600,
    color: "var(--color-text-muted, #b4b4b8)",
    textTransform: "uppercase",
    letterSpacing: 0.4,
    margin: 0,
  };
  const scrollerStyle: CSSProperties = {
    display: "flex",
    gap: 8,
    overflowX: "auto",
    paddingBottom: 4,
  };
  const chipStyle: CSSProperties = {
    flexShrink: 0,
    display: "flex",
    flexDirection: "column",
    gap: 4,
    minWidth: 180,
    maxWidth: 220,
    padding: "8px 10px",
    borderRadius: "var(--radius-sm, 6px)",
    border: "1px solid var(--color-border, #232325)",
    backgroundColor: "var(--color-surface, #1a1a1c)",
    color: "var(--color-text, #ededee)",
    fontSize: "var(--font-size-sm, 13px)",
  };
  return (
    <section
      aria-label="Recently accessed"
      data-testid="library-recents-strip"
      style={wrapperStyle}
    >
      <h3 style={titleStyle}>Recently accessed</h3>
      <div style={scrollerStyle}>
        {items.map((item) => (
          <article
            key={`${item.kind}:${item.id as unknown as string}`}
            style={chipStyle}
            data-testid="library-recents-card"
            data-item-kind={item.kind}
            data-item-id={item.id as unknown as string}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span aria-hidden="true">{kindGlyph(item.kind)}</span>
              <ItemLink ref={toItemRef(item)} />
            </div>
            {item.last_accessed_at !== null ? (
              <span
                style={{
                  fontSize: "var(--font-size-xs, 12px)",
                  color: "var(--color-text-muted, #b4b4b8)",
                }}
              >
                {formatRelativeTime(item.last_accessed_at, now)}
              </span>
            ) : null}
          </article>
        ))}
      </div>
    </section>
  );
}

// ===========================================================================
// LibraryCard — one card in the CardGrid view
// ===========================================================================

interface LibraryCardProps {
  readonly item: LibraryItemSummary;
  readonly now: number;
}

function LibraryCard({ item, now }: LibraryCardProps): ReactElement {
  const cardStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 8,
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
    backgroundColor: "var(--color-surface-muted, #222224)",
    color: "var(--color-text, #ededee)",
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    fontSize: 14,
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
  const subtitleStyle: CSSProperties = {
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

  return (
    <article
      style={cardStyle}
      data-testid="library-card"
      data-item-kind={item.kind}
      data-item-id={item.id as unknown as string}
    >
      <div style={headStyle}>
        <span style={iconStyle} aria-hidden="true">
          {kindGlyph(item.kind)}
        </span>
        <span style={nameStyle} data-testid="library-card-name">
          {/* The item's own name is the canonical ItemLink for the card —
              clicking opens the per-kind detail (P7-B2). */}
          <ItemLink ref={toItemRef(item)} />
        </span>
        <StatusPill
          status={indexStatusTone(item.index_status)}
          label={indexStatusLabel(item.index_status)}
        />
      </div>

      {item.subtitle !== undefined && item.subtitle.length > 0 ? (
        <div style={subtitleStyle} data-testid="library-card-subtitle">
          {item.subtitle}
        </div>
      ) : null}

      <div style={metaStyle} data-testid="library-card-meta">
        {item.project_id !== null ? (
          <ItemLink ref={{ kind: "project", id: item.project_id }} />
        ) : null}
        <span data-testid="library-card-updated">
          {formatRelativeTime(item.updated_at, now)}
        </span>
        <span data-testid="library-card-source">{sourceLabel(item)}</span>
      </div>
    </article>
  );
}

// ===========================================================================
// LibraryRow — one row in the DocList view (opt-in scanning surface)
// ===========================================================================

function LibraryRow({
  item,
  now,
}: {
  readonly item: LibraryItemSummary;
  readonly now: number;
}): ReactElement {
  const wrapper: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 10,
    width: "100%",
    minWidth: 0,
  };
  const iconStyle: CSSProperties = {
    width: 22,
    height: 22,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    fontSize: 14,
    flexShrink: 0,
  };
  const nameStyle: CSSProperties = {
    flex: 1,
    minWidth: 0,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
    fontWeight: 600,
  };
  const metaStyle: CSSProperties = {
    fontSize: "var(--font-size-xs, 12px)",
    color: "var(--color-text-muted, #b4b4b8)",
    whiteSpace: "nowrap",
  };
  return (
    <div
      style={wrapper}
      data-testid="library-row"
      data-item-kind={item.kind}
      data-item-id={item.id as unknown as string}
    >
      <span style={iconStyle} aria-hidden="true">
        {kindGlyph(item.kind)}
      </span>
      <span style={nameStyle}>
        <ItemLink ref={toItemRef(item)} />
      </span>
      <StatusPill
        status={indexStatusTone(item.index_status)}
        label={indexStatusLabel(item.index_status)}
      />
      <span style={metaStyle} data-testid="library-row-updated">
        {formatRelativeTime(item.updated_at, now)}
      </span>
    </div>
  );
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
      data-testid="library-skeleton-card"
      data-skeleton-index={index}
      aria-hidden="true"
    />
  );
}

// ===========================================================================
// Helpers
// ===========================================================================

const secondaryActionStyle: CSSProperties = {
  height: 32,
  padding: "0 12px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border-strong, #2a2a2c)",
  backgroundColor: "transparent",
  color: "var(--color-text, #ededee)",
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 500,
  cursor: "pointer",
};

function kindGlyph(kind: LibraryItemSummary["kind"]): string {
  switch (kind) {
    case "file":
      return "📄";
    case "page":
      return "📝";
    case "dataset":
      return "📊";
  }
}

function filterNoun(slug: LibraryKindFilterSlug): string {
  switch (slug) {
    case "all":
      return "items";
    case "files":
      return "files";
    case "pages":
      return "pages";
    case "datasets":
      return "datasets";
  }
}

function toItemRef(item: LibraryItemSummary): ItemRef {
  switch (item.kind) {
    case "file":
      return { kind: "library_file", id: item.id };
    case "page":
      return { kind: "library_page", id: item.id };
    case "dataset":
      return { kind: "library_dataset", id: item.id };
  }
}

function sourceLabel(item: LibraryItemSummary): string {
  switch (item.source.kind) {
    case "user_upload":
      return "Uploaded";
    case "agent_save":
      return "Saved from chat";
    case "connector_sync":
      return "Synced";
  }
}

const INDEX_STATUS_TONE: Readonly<Record<LibraryIndexStatus, StatusTone>> = {
  pending: "muted",
  indexing: "info",
  indexed: "ok",
  failed: "error",
  skipped: "muted",
};

const INDEX_STATUS_LABEL: Readonly<Record<LibraryIndexStatus, string>> = {
  pending: "Pending",
  indexing: "Indexing",
  indexed: "Indexed",
  failed: "Failed",
  skipped: "Skipped",
};

function indexStatusTone(status: LibraryIndexStatus): StatusTone {
  return INDEX_STATUS_TONE[status];
}

function indexStatusLabel(status: LibraryIndexStatus): string {
  return INDEX_STATUS_LABEL[status];
}
