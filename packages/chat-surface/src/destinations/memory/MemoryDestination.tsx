// Memory — destination shell (P12-B2).
//
// Source:
//   docs/atlas-new-design/destinations/team-memory-cmdk-prd.md §1.2
//     (what Memory is), §2 Memory journeys (U-M1..U-M5), §7.2
//     (`<PageHeader>` + `<FilterTabs>` (Skills / Facts / Preferences) +
//     `<DocList>`).
//
// Invariants:
//   - Pure presentation. Search, filter, kind/scope changes lift through
//     callback props; the host (apps/frontend P12-C2) owns the transport
//     call.
//   - SP-1 primitives only — PageHeader / FilterTabs / DocList /
//     EmptyState / StatusPill. `<ItemLink>` for every cross-destination
//     ref (created-by-agent, source chats). Direct `router.navigate(…)`
//     from rows is forbidden (cross-audit §1.1 + §3.3).
//   - Wire types from `@0x-copilot/api-types/memory` only — no
//     local-stub copy (the wire types already shipped on main via
//     P12-A1).
//   - `formatRelativeTime` from `../../util/time` is the canonical
//     relative-time helper (cross-audit §3.4); no inline re-implementation.
//   - Backward-compat: the destination still mounts with no props
//     (`apps/frontend/src/app/App.tsx` registers it as a placeholder
//     until the data binder lands), in which case it renders an
//     empty-state explaining what Memory is.

import {
  useMemo,
  useState,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

import type {
  MemoryItem,
  MemoryKind,
  MemoryScope,
  SectionResult,
} from "@0x-copilot/api-types";

import { ItemLink } from "../../refs/ItemLink";
import { DocList } from "../../shell/DocList";
import { EmptyState } from "../../shell/EmptyState";
import { FilterTabs, type FilterTabOption } from "../../shell/FilterTabs";
import { PageHeader } from "../../shell/PageHeader";
import { StatusPill, type StatusTone } from "../../shell/StatusPill";
import { formatRelativeTime } from "../../util/time";

// ===========================================================================
// Filter slugs
// ===========================================================================
//
// The primary filter axis is `kind`. The PRD lists Skills / Facts /
// Preferences as the visible tabs; we add an "All" slug to mirror the
// no-filter case (matches Routines / Inbox shell shape).

export type MemoryKindFilterSlug = "all" | MemoryKind;

const KIND_ORDER: ReadonlyArray<MemoryKindFilterSlug> = [
  "all",
  "skill",
  "fact",
  "preference",
];

const KIND_LABEL: Readonly<Record<MemoryKindFilterSlug, string>> = {
  all: "All",
  skill: "Skills",
  fact: "Facts",
  preference: "Preferences",
};

const KIND_TONE: Readonly<Record<MemoryKind, StatusTone>> = {
  skill: "info",
  fact: "ok",
  preference: "muted",
};

/** Pretty-print one memory kind for the row chip. */
const KIND_ROW_LABEL: Readonly<Record<MemoryKind, string>> = {
  skill: "Skill",
  fact: "Fact",
  preference: "Preference",
};

/** Per-kind counts driven by the host. */
export type MemoryKindFilterCounts = Readonly<
  Record<MemoryKindFilterSlug, number>
>;

/**
 * Sub-filter axis: My (scope=user) / Workspace (scope=workspace) / All.
 * Encoded as a separate slug so it composes with `<FilterTabs>` cleanly.
 */
export type MemoryScopeFilterSlug = "all" | MemoryScope;

const SCOPE_ORDER: ReadonlyArray<MemoryScopeFilterSlug> = [
  "all",
  "user",
  "workspace",
];

const SCOPE_LABEL: Readonly<Record<MemoryScopeFilterSlug, string>> = {
  all: "All",
  user: "My",
  workspace: "Workspace",
};

// ===========================================================================
// Public props
// ===========================================================================

/** Slot for the editor / detail pane. When supplied AND `focusedMemoryId`
 *  is set, the slot replaces the list body. */
export type RenderMemoryDetailSlot = (props: {
  readonly memoryId: MemoryItem["id"];
  readonly onClose: () => void;
}) => ReactNode;

export interface MemoryDestinationProps {
  /**
   * Server-projected list result. `null` = loading skeleton; `error`
   * shows the destination-level error empty-state with retry; `ok`
   * renders the filtered list. When the prop is omitted entirely we
   * fall back to the "no data wired yet" empty-state so the
   * destination still mounts (App.tsx registers the component as a
   * placeholder until the host binder lands).
   */
  readonly items?: SectionResult<ReadonlyArray<MemoryItem>> | null;

  /** Active kind filter slug. Defaults to "all". */
  readonly filter?: MemoryKindFilterSlug;
  readonly onFilterChange?: (next: MemoryKindFilterSlug) => void;
  readonly counts?: MemoryKindFilterCounts;

  /** Active scope sub-filter slug. Defaults to "all". */
  readonly scopeFilter?: MemoryScopeFilterSlug;
  readonly onScopeFilterChange?: (next: MemoryScopeFilterSlug) => void;

  /** Free-text search; debouncing lives in the host. */
  readonly search?: string;
  readonly onSearch?: (next: string) => void;

  /** "Add memory" CTA → host pivots to MemoryEditor (create flow). */
  readonly onCreateMemory?: () => void;

  /** Row interactions — lifted to the host. */
  readonly onOpenMemory?: (id: MemoryItem["id"]) => void;
  readonly onEditMemory?: (id: MemoryItem["id"]) => void;
  readonly onDeleteMemory?: (id: MemoryItem["id"]) => void;

  /** Retry callback when `items.status === "error"`. */
  readonly onRetry?: () => void;

  /** Detail slot. When supplied AND `focusedMemoryId` is set, the slot
   *  replaces the list body. */
  readonly renderDetail?: RenderMemoryDetailSlot;
  readonly focusedMemoryId?: MemoryItem["id"] | null;
  readonly onCloseDetail?: () => void;

  /** Reference instant — test seam for relative-time formatting. */
  readonly now?: number;
}

// ===========================================================================
// Top-level shell
// ===========================================================================

export function MemoryDestination(
  props: MemoryDestinationProps = {},
): ReactElement {
  const {
    items,
    filter = "all",
    onFilterChange,
    counts,
    scopeFilter = "all",
    onScopeFilterChange,
    search,
    onSearch,
    onCreateMemory,
    onOpenMemory,
    onEditMemory,
    onDeleteMemory,
    onRetry,
    renderDetail,
    focusedMemoryId = null,
    onCloseDetail,
    now,
  } = props;

  // Local controlled-vs-uncontrolled search seam: when the host doesn't
  // supply `search`, the destination still echoes typed characters so
  // the test renderer behaves like a controlled input.
  const [internalSearch, setInternalSearch] = useState<string>("");
  const searchValue = search ?? internalSearch;

  const handleSearchChange = (next: string): void => {
    setInternalSearch(next);
    if (onSearch !== undefined) onSearch(next);
  };

  // === Kind filter chip options (single source of truth) =================
  const kindOptions = useMemo<
    ReadonlyArray<FilterTabOption<MemoryKindFilterSlug>>
  >(
    () =>
      KIND_ORDER.map((slug) => ({
        slug,
        label: KIND_LABEL[slug],
        count: counts?.[slug],
      })),
    [counts],
  );

  const scopeOptions = useMemo<
    ReadonlyArray<FilterTabOption<MemoryScopeFilterSlug>>
  >(
    () =>
      SCOPE_ORDER.map((slug) => ({
        slug,
        label: SCOPE_LABEL[slug],
      })),
    [],
  );

  const handleKindChange = (next: MemoryKindFilterSlug): void => {
    if (onFilterChange !== undefined) onFilterChange(next);
  };
  const handleScopeChange = (next: MemoryScopeFilterSlug): void => {
    if (onScopeFilterChange !== undefined) onScopeFilterChange(next);
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
    maxWidth: 920,
    margin: "0 auto",
    padding: "24px 28px 48px",
    boxSizing: "border-box",
    display: "flex",
    flexDirection: "column",
    gap: 16,
  };
  const searchRowStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 8,
  };
  const searchInputStyle: CSSProperties = {
    flex: 1,
    height: 32,
    padding: "0 10px",
    borderRadius: "var(--radius-sm, 6px)",
    border: "1px solid var(--color-border, #232325)",
    background: "var(--color-surface, #161617)",
    color: "var(--color-text, #ededee)",
    fontSize: "var(--font-size-sm, 13px)",
    outline: "none",
    minWidth: 0,
  };

  // === Unwired state ====================================================
  // Backward-compat for App.tsx registering the destination as a
  // placeholder until the data binder lands. We render a dignified
  // explanation rather than a stub — same vocabulary the host will use
  // once it wires through `items`.
  if (items === undefined) {
    return (
      <section
        aria-label="Memory destination"
        data-testid="memory-destination"
        data-state="unwired"
        style={rootStyle}
      >
        <div style={containerStyle}>
          <PageHeader
            title="Memory"
            subtitle="What Atlas remembers about you and your team"
          />
          <EmptyState
            title="What the agent remembers"
            body="Long-term memory across chats and runs — what you've taught the agent about your role, your projects, and the people you work with. Review, edit, and forget memories from one place."
          />
        </div>
      </section>
    );
  }

  // === Loading state ====================================================
  if (items === null) {
    return (
      <section
        aria-label="Memory destination"
        data-testid="memory-destination"
        data-state="loading"
        style={rootStyle}
      >
        <div style={containerStyle}>
          <PageHeader title="Memory" subtitle="Loading…" />
          <div
            data-testid="memory-skeleton"
            aria-hidden="true"
            style={{ display: "flex", flexDirection: "column", gap: 12 }}
          >
            {Array.from({ length: 3 }).map((_, i) => (
              <RowSkeleton key={i} />
            ))}
          </div>
        </div>
      </section>
    );
  }

  // === Error state ======================================================
  if (items.status === "error") {
    return (
      <section
        aria-label="Memory destination"
        data-testid="memory-destination"
        data-state="error"
        style={rootStyle}
      >
        <div style={containerStyle}>
          <PageHeader title="Memory" />
          <EmptyState
            title="Could not load memory"
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
        aria-label="Memory destination"
        data-testid="memory-destination"
        data-state="unavailable"
        style={rootStyle}
      >
        <div style={containerStyle}>
          <PageHeader title="Memory" />
          <EmptyState
            title="Memory unavailable"
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

  const subtitle =
    rows.length === 0
      ? "What Atlas remembers about you and your team"
      : `${rows.length} ${rows.length === 1 ? "memory" : "memories"}`;

  const showingDetail = renderDetail !== undefined && focusedMemoryId !== null;

  return (
    <section
      aria-label="Memory destination"
      data-testid="memory-destination"
      data-state="ready"
      data-focused-memory-id={focusedMemoryId ?? undefined}
      data-filter={filter}
      data-scope-filter={scopeFilter}
      style={rootStyle}
    >
      <div style={containerStyle}>
        <PageHeader
          title="Memory"
          subtitle={subtitle}
          primaryAction={
            onCreateMemory !== undefined
              ? { label: "Add memory", onClick: onCreateMemory }
              : undefined
          }
        />

        <FilterTabs<MemoryKindFilterSlug>
          value={filter}
          onChange={handleKindChange}
          options={kindOptions}
          ariaLabel="Memory kind filter"
          idPrefix="memory-kind"
        />

        <div style={searchRowStyle}>
          <FilterTabs<MemoryScopeFilterSlug>
            value={scopeFilter}
            onChange={handleScopeChange}
            options={scopeOptions}
            ariaLabel="Memory scope filter"
            idPrefix="memory-scope"
          />
          <input
            type="search"
            aria-label="Search memory"
            placeholder="Search memory…"
            value={searchValue}
            onChange={(e) => handleSearchChange(e.target.value)}
            style={searchInputStyle}
            data-testid="memory-search-input"
          />
        </div>

        {showingDetail ? (
          <div
            data-testid="memory-detail-slot"
            data-focused-memory-id={focusedMemoryId!}
          >
            {renderDetail!({
              memoryId: focusedMemoryId!,
              onClose: () => {
                if (onCloseDetail !== undefined) onCloseDetail();
              },
            })}
          </div>
        ) : rows.length === 0 ? (
          <EmptyState
            title="No memory yet"
            body="Teach Atlas something in a chat, or add a memory directly."
            action={
              onCreateMemory !== undefined
                ? { label: "Add memory", onClick: onCreateMemory }
                : undefined
            }
          />
        ) : (
          <DocList<MemoryItem>
            ariaLabel="Memory items"
            items={rows}
            keyFor={(m) => m.id}
            renderRow={(memory) => (
              <MemoryRow
                memory={memory}
                onOpen={onOpenMemory}
                onEdit={onEditMemory}
                onDelete={onDeleteMemory}
                now={now ?? Date.now()}
              />
            )}
          />
        )}
      </div>
    </section>
  );
}

// ===========================================================================
// MemoryRow — one item row
// ===========================================================================

interface MemoryRowProps {
  readonly memory: MemoryItem;
  readonly onOpen?: (id: MemoryItem["id"]) => void;
  readonly onEdit?: (id: MemoryItem["id"]) => void;
  readonly onDelete?: (id: MemoryItem["id"]) => void;
  readonly now: number;
}

function MemoryRow({
  memory,
  onOpen,
  onEdit,
  onDelete,
  now,
}: MemoryRowProps): ReactElement {
  const wrapStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 6,
    flex: 1,
    minWidth: 0,
  };
  const headStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 10,
    flex: 1,
    minWidth: 0,
  };
  const titleStyle: CSSProperties = {
    flex: 1,
    minWidth: 0,
    fontSize: "var(--font-size-sm, 13px)",
    fontWeight: 600,
    color: "var(--color-text, #ededee)",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
    background: "transparent",
    border: "none",
    textAlign: "left",
    cursor: onOpen !== undefined ? "pointer" : "default",
    padding: 0,
  };
  const metaStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 8,
    flexWrap: "wrap",
    fontSize: "var(--font-size-xs, 12px)",
    color: "var(--color-text-muted, #b4b4b8)",
  };
  const actionButtonStyle: CSSProperties = {
    background: "transparent",
    border: "none",
    color: "var(--color-text-subtle, #7e7e84)",
    cursor: "pointer",
    fontSize: "var(--font-size-xs, 12px)",
    padding: "2px 6px",
  };

  const lastUsedLabel =
    memory.last_used_at !== null
      ? `last used ${formatRelativeTime(memory.last_used_at, now)}`
      : "never used";

  return (
    <div
      style={wrapStyle}
      data-testid="memory-row"
      data-memory-id={memory.id}
      data-memory-kind={memory.kind}
      data-memory-scope={memory.scope}
    >
      <div style={headStyle}>
        <StatusPill
          status={KIND_TONE[memory.kind]}
          label={KIND_ROW_LABEL[memory.kind]}
        />
        <button
          type="button"
          style={titleStyle}
          onClick={() => {
            if (onOpen !== undefined) onOpen(memory.id);
          }}
          data-testid="memory-row-title"
          aria-label={`Open ${memory.title}`}
        >
          {memory.title}
        </button>
        {onEdit !== undefined ? (
          <button
            type="button"
            data-testid="memory-row-edit"
            onClick={() => onEdit(memory.id)}
            style={actionButtonStyle}
            aria-label={`Edit ${memory.title}`}
          >
            Edit
          </button>
        ) : null}
        {onDelete !== undefined ? (
          <button
            type="button"
            data-testid="memory-row-delete"
            onClick={() => onDelete(memory.id)}
            style={actionButtonStyle}
            aria-label={`Delete ${memory.title}`}
          >
            Delete
          </button>
        ) : null}
      </div>

      <div style={metaStyle} data-testid="memory-row-meta">
        <StatusPill
          status={memory.scope === "workspace" ? "info" : "muted"}
          label={memory.scope === "workspace" ? "Workspace" : "My"}
        />
        {memory.tags.map((tag) => (
          <StatusPill key={tag} status="muted" label={`#${tag}`} />
        ))}
        {memory.created_by.kind === "agent" ? (
          // The originating agent / chat is most useful when surfaced
          // as a cross-destination ItemLink. We don't have an exact
          // `kind: "agent"` ref here (the created_by carries an opaque
          // id) so we render a muted chip instead. Strict ItemLink
          // use is reserved for project_id / source refs that carry a
          // typed branded id (cross-audit §1.1).
          <StatusPill status="muted" label="auto-extracted" />
        ) : null}
        {memory.project_id !== undefined && memory.project_id !== null ? (
          <ItemLink ref={{ kind: "project", id: memory.project_id }} />
        ) : null}
        <span data-testid="memory-row-last-used">{lastUsedLabel}</span>
      </div>
    </div>
  );
}

// ===========================================================================
// RowSkeleton — loading placeholder
// ===========================================================================

function RowSkeleton(): ReactElement {
  const style: CSSProperties = {
    height: 56,
    borderRadius: "var(--radius-md, 12px)",
    border: "1px solid var(--color-border, #232325)",
    backgroundColor: "var(--color-surface-muted, #222224)",
    opacity: 0.5,
  };
  return (
    <div style={style} data-testid="memory-skeleton-row" aria-hidden="true" />
  );
}
