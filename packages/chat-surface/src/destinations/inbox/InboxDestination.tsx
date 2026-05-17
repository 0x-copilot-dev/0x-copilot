// Inbox — destination shell (P4-B1).
//
// Pure-presentation layout + section bucketing scaffolding. Three
// interactive bodies — detail/reply/snooze (P4-B2), 960px split-pane
// responsive styles (P4-B3), and the producer/SSE wiring (P4-A*) — ship
// as separate files / phases. The shell owns:
//
//   1. Section bucketing (inbox-prd §3.2 + brief — Unread / Snoozed /
//      Read (last 7d) / Dismissed). Client-side per cross-audit decision
//      §9.6 (Todos) extended to Inbox: the server returns a flat list,
//      the shell buckets in render so a status mutation doesn't force a
//      refetch.
//   2. Empty-when-zero rendering — sections with no rows are not
//      rendered; if every section is empty, a single `<EmptyState>`
//      stands in ("Inbox zero").
//   3. Bulk-select toolbar — a sticky surface using `<StatusPill>` to
//      announce the selection count, with bulk-mark-read / bulk-snooze
//      / bulk-dismiss actions.
//   4. Per-row primitives — `<DocList items renderRow>` for stable
//      virtualisable rendering; `<ItemLink ref={item.links[0]}>` for
//      primary navigation; `<StatusPill>` for kind/priority chips.
//   5. Render-prop seam for P4-B2: when the host supplies
//      `renderDetail` and the destination is rendering a focused item,
//      the slot replaces the list body. P4-B3 layers a side-by-side
//      style on top above the 960px breakpoint.
//
// `_inbox-stub.ts` carries wire-types until P4-A1's api-types land.
// Every import is marked `TODO(merge): rewire to "@enterprise-search/api-types"`.

import {
  useCallback,
  useMemo,
  useState,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

import type { InboxItemId, SectionResult } from "@enterprise-search/api-types";

import { DocList } from "../../shell/DocList";
import { EmptyState } from "../../shell/EmptyState";
import { PageHeader } from "../../shell/PageHeader";
import { StatusPill, type StatusTone } from "../../shell/StatusPill";
import { ItemLink } from "../../refs/ItemLink";
import { formatRelativeTime } from "../../util/time";

// TODO(merge): rewire to "@enterprise-search/api-types"
import {
  READ_LOOKBACK_MS,
  type InboxItem,
  type InboxItemKind,
  type InboxItemPriority,
  type InboxItemStatus,
  type InboxSectionKey,
  type InboxSender,
  type InboxSenderKind,
  type InboxSystemOrigin,
} from "./_inbox-stub";

// TODO(merge): rewire to "@enterprise-search/api-types"
export type {
  InboxItem,
  InboxItemKind,
  InboxItemPriority,
  InboxItemStatus,
  InboxSectionKey,
  InboxSender,
  InboxSenderKind,
  InboxSystemOrigin,
};

// ===========================================================================
// §3.2 fixed section order
// ===========================================================================
//
// Unread first — brief: "client-side bucketing: Unread, Snoozed,
// Read (last 7d), Dismissed (collapsed)". Order mirrors the user's
// urgency gradient: things needing action -> things waiting -> things
// already triaged -> things done.

const SECTION_ORDER: ReadonlyArray<InboxSectionKey> = [
  "unread",
  "snoozed",
  "read",
  "dismissed",
];

const SECTION_HEADINGS: Readonly<Record<InboxSectionKey, string>> = {
  unread: "Unread",
  snoozed: "Snoozed",
  read: "Read",
  dismissed: "Dismissed",
};

const SECTION_TONE: Readonly<Record<InboxSectionKey, StatusTone>> = {
  unread: "info",
  snoozed: "warning",
  read: "muted",
  dismissed: "muted",
};

// ===========================================================================
// Public props
// ===========================================================================

/** Slot for P4-B2's detail/reply/snooze pane. Rendered in place of the
 *  list body when `focusedItemId` is supplied. P4-B3 layers the
 *  side-by-side breakpoint style on top of the same slot. */
export type RenderDetailSlot = (props: {
  readonly itemId: InboxItemId;
  readonly onClose: () => void;
}) => ReactNode;

export interface InboxDestinationProps {
  /**
   * Server-projected list result. `null` = loading skeleton; `error`
   * shows the destination-level error empty-state with retry; `ok`
   * buckets rows into sections.
   *
   * `items` is wrapped in `SectionResult` even though `/v1/inbox` is
   * a non-aggregating endpoint (cross-audit §2.3 only mandates the
   * wrapper for aggregators) — same rationale as todos-shell: a uniform
   * "couldn't load" branch without inventing a second error path.
   */
  readonly items?: SectionResult<ReadonlyArray<InboxItem>> | null;

  /** Unread count rendered on the PageHeader as a `<StatusPill>` badge.
   *  Host derives from the SectionResult or the rail's `BadgePort`. */
  readonly unreadCount?: number;

  /** Row-level handlers. Each takes the InboxItemId and (where relevant)
   *  the new state. Optimistic UI + error handling live in the host
   *  (apps/frontend P4-C) — the shell stays pure presentation. */
  readonly onMarkRead?: (id: InboxItemId) => void;
  readonly onSnooze?: (id: InboxItemId) => void;
  readonly onDismiss?: (id: InboxItemId) => void;

  /** Bulk-action handlers (brief). Called with the selected ids. */
  readonly onBulkMarkRead?: (ids: ReadonlyArray<InboxItemId>) => void;
  readonly onBulkSnooze?: (ids: ReadonlyArray<InboxItemId>) => void;
  readonly onBulkDismiss?: (ids: ReadonlyArray<InboxItemId>) => void;
  readonly onBulkClear?: () => void;

  /** Retry callback when `items.status === "error"`. */
  readonly onRetry?: () => void;

  /** P4-B2 detail slot. When supplied AND `focusedItemId` is set, the
   *  slot replaces the list body. */
  readonly renderDetail?: RenderDetailSlot;
  readonly focusedItemId?: InboxItemId | null;
  readonly onCloseDetail?: () => void;

  /** Reference instant — test seam for the Read (last 7d) cut-off. */
  readonly now?: number;

  /** Initial state of the Dismissed section: collapsed by default per
   *  the brief. Tests can flip this. */
  readonly initialDismissedCollapsed?: boolean;
}

// ===========================================================================
// Top-level shell
// ===========================================================================

export function InboxDestination(
  props: InboxDestinationProps = {},
): ReactElement {
  const {
    items = null,
    unreadCount,
    onMarkRead,
    onSnooze,
    onDismiss,
    onBulkMarkRead,
    onBulkSnooze,
    onBulkDismiss,
    onBulkClear,
    onRetry,
    renderDetail,
    focusedItemId = null,
    onCloseDetail,
    now,
    initialDismissedCollapsed = true,
  } = props;

  // === Bulk-select state ================================================
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<InboxItemId>>(
    () => new Set<InboxItemId>(),
  );
  const selectedCount = selectedIds.size;

  // === Dismissed-section collapse state ================================
  const [dismissedCollapsed, setDismissedCollapsed] = useState<boolean>(
    initialDismissedCollapsed,
  );

  const toggleSelected = useCallback((id: InboxItemId) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const clearSelection = useCallback(() => {
    setSelectedIds(new Set<InboxItemId>());
    if (onBulkClear !== undefined) onBulkClear();
  }, [onBulkClear]);

  // === Bucket the flat list (client-side per the brief) ================
  const buckets = useMemo(
    () => bucketInbox(items, now ?? Date.now()),
    [items, now],
  );

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
    maxWidth: 920, // matches todos shell; P4-B3 widens for split-pane.
    margin: "0 auto",
    padding: "24px 28px 96px", // bottom pad so the bulk bar doesn't cover content.
    boxSizing: "border-box",
    display: "flex",
    flexDirection: "column",
    gap: 16,
  };
  const sectionGridStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 20,
  };

  // === Loading state ====================================================
  if (items === null) {
    return (
      <section
        aria-label="Inbox destination"
        data-testid="inbox-destination"
        data-state="loading"
        style={rootStyle}
      >
        <div style={containerStyle}>
          <PageHeader title="Inbox" subtitle="Loading…" />
          <div
            style={sectionGridStyle}
            data-testid="inbox-sections"
            data-state="loading"
            aria-hidden="true"
          >
            {Array.from({ length: 3 }).map((_, i) => (
              <SectionSkeleton key={i} />
            ))}
          </div>
        </div>
      </section>
    );
  }

  // === Error state (whole-list) =========================================
  if (items.status === "error") {
    return (
      <section
        aria-label="Inbox destination"
        data-testid="inbox-destination"
        data-state="error"
        style={rootStyle}
      >
        <div style={containerStyle}>
          <PageHeader title="Inbox" />
          <EmptyState
            title="Could not load inbox"
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
        aria-label="Inbox destination"
        data-testid="inbox-destination"
        data-state="unavailable"
        style={rootStyle}
      >
        <div style={containerStyle}>
          <PageHeader title="Inbox" />
          <EmptyState
            title="Inbox unavailable"
            body={
              items.error ??
              "This destination is not enabled for your workspace."
            }
          />
        </div>
      </section>
    );
  }

  // === Detail-pane render-prop seam (P4-B2 slot) ========================
  //
  // When the host supplies `renderDetail` and a focused id, the slot
  // replaces the list body. P4-B3 will style a side-by-side layout via
  // the responsive breakpoint without touching this file. The shell
  // still owns the PageHeader + unread badge so the chrome is
  // consistent.
  const showingDetail = renderDetail !== undefined && focusedItemId !== null;

  // === Ready state ======================================================

  const hasAnyItems = SECTION_ORDER.some(
    (k) => (buckets.get(k) ?? []).length > 0,
  );

  // PageHeader subtitle: total unread + read summary
  const unreadInList = (buckets.get("unread") ?? []).length;
  const computedUnread = unreadCount ?? unreadInList;
  const subtitle =
    computedUnread === 0 ? "Inbox zero" : `${computedUnread} unread`;

  const badges =
    computedUnread > 0 ? (
      <StatusPill status="info" label={`${computedUnread} unread`} />
    ) : undefined;

  return (
    <section
      aria-label="Inbox destination"
      data-testid="inbox-destination"
      data-state="ready"
      data-selection-count={selectedCount}
      data-focused-item-id={focusedItemId ?? undefined}
      style={rootStyle}
    >
      <div style={containerStyle}>
        <PageHeader title="Inbox" subtitle={subtitle} badges={badges} />

        {showingDetail ? (
          <div
            data-testid="inbox-detail-slot"
            data-focused-item-id={focusedItemId!}
          >
            {renderDetail!({
              itemId: focusedItemId!,
              onClose: () => {
                if (onCloseDetail !== undefined) onCloseDetail();
              },
            })}
          </div>
        ) : !hasAnyItems ? (
          <EmptyState
            title="Inbox zero"
            body="Nothing addressed to you right now. Mentions, approvals routed cross-thread, and connector errors will land here."
          />
        ) : (
          <div
            style={sectionGridStyle}
            data-testid="inbox-sections"
            data-state="ready"
          >
            {SECTION_ORDER.map((sectionKey) => {
              const rows = buckets.get(sectionKey) ?? [];
              if (rows.length === 0) return null;

              const collapsed =
                sectionKey === "dismissed" && dismissedCollapsed;
              return (
                <Section
                  key={sectionKey}
                  sectionKey={sectionKey}
                  rows={rows}
                  selectedIds={selectedIds}
                  toggleSelected={toggleSelected}
                  onMarkRead={onMarkRead}
                  onSnooze={onSnooze}
                  onDismiss={onDismiss}
                  collapsed={collapsed}
                  onToggleCollapsed={
                    sectionKey === "dismissed"
                      ? () => setDismissedCollapsed((v) => !v)
                      : undefined
                  }
                  now={now ?? Date.now()}
                />
              );
            })}
          </div>
        )}
      </div>

      {selectedCount > 0 ? (
        <BulkActionBar
          count={selectedCount}
          selectedIds={selectedIds}
          onMarkRead={onBulkMarkRead}
          onSnooze={onBulkSnooze}
          onDismiss={onBulkDismiss}
          onClear={clearSelection}
        />
      ) : null}
    </section>
  );
}

// ===========================================================================
// Section — one bucket render (heading + rows)
// ===========================================================================

interface SectionProps {
  readonly sectionKey: InboxSectionKey;
  readonly rows: ReadonlyArray<InboxItem>;
  readonly selectedIds: ReadonlySet<InboxItemId>;
  readonly toggleSelected: (id: InboxItemId) => void;
  readonly onMarkRead?: (id: InboxItemId) => void;
  readonly onSnooze?: (id: InboxItemId) => void;
  readonly onDismiss?: (id: InboxItemId) => void;
  readonly collapsed: boolean;
  readonly onToggleCollapsed?: () => void;
  readonly now: number;
}

function Section({
  sectionKey,
  rows,
  selectedIds,
  toggleSelected,
  onMarkRead,
  onSnooze,
  onDismiss,
  collapsed,
  onToggleCollapsed,
  now,
}: SectionProps): ReactElement {
  const headingId = `inbox-section-${sectionKey}-heading`;
  const wrapperStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 10,
  };
  const headerRowStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 10,
  };
  const headingStyle: CSSProperties = {
    fontSize: "var(--font-size-md, 14px)",
    fontWeight: 600,
    color: "var(--color-text)",
    margin: 0,
    flex: 1,
  };
  const collapseButtonStyle: CSSProperties = {
    background: "transparent",
    border: "1px solid var(--color-border, #232325)",
    color: "var(--color-text-muted, #b4b4b8)",
    borderRadius: "var(--radius-sm, 6px)",
    height: 24,
    padding: "0 8px",
    fontSize: "var(--font-size-xs, 12px)",
    cursor: "pointer",
  };

  return (
    <section
      aria-labelledby={headingId}
      data-testid={`inbox-section-${sectionKey}`}
      data-section-key={sectionKey}
      data-row-count={rows.length}
      style={wrapperStyle}
    >
      <div style={headerRowStyle}>
        <h2 id={headingId} style={headingStyle}>
          {SECTION_HEADINGS[sectionKey]}
        </h2>
        <StatusPill
          status={SECTION_TONE[sectionKey]}
          label={String(rows.length)}
        />
        {onToggleCollapsed !== undefined ? (
          <button
            type="button"
            data-testid={`inbox-section-${sectionKey}-collapse`}
            onClick={onToggleCollapsed}
            aria-expanded={!collapsed}
            aria-controls={`inbox-section-${sectionKey}-body`}
            style={collapseButtonStyle}
          >
            {collapsed ? "Show" : "Hide"}
          </button>
        ) : null}
      </div>

      {!collapsed ? (
        <div
          id={`inbox-section-${sectionKey}-body`}
          data-testid={`inbox-section-${sectionKey}-body`}
        >
          <DocList<InboxItem>
            ariaLabel={SECTION_HEADINGS[sectionKey]}
            items={rows}
            keyFor={(it) => it.id}
            renderRow={(item) => (
              <InboxRow
                item={item}
                selected={selectedIds.has(item.id)}
                onToggleSelected={() => toggleSelected(item.id)}
                onMarkRead={onMarkRead}
                onSnooze={onSnooze}
                onDismiss={onDismiss}
                now={now}
              />
            )}
          />
        </div>
      ) : null}
    </section>
  );
}

// ===========================================================================
// InboxRow — one item row
// ===========================================================================

interface InboxRowProps {
  readonly item: InboxItem;
  readonly selected: boolean;
  readonly onToggleSelected: () => void;
  readonly onMarkRead?: (id: InboxItemId) => void;
  readonly onSnooze?: (id: InboxItemId) => void;
  readonly onDismiss?: (id: InboxItemId) => void;
  readonly now: number;
}

function InboxRow({
  item,
  selected,
  onToggleSelected,
  onMarkRead,
  onSnooze,
  onDismiss,
  now,
}: InboxRowProps): ReactElement {
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
  const checkboxStyle: CSSProperties = {
    width: 14,
    height: 14,
    accentColor: "var(--color-accent, #d97757)",
    cursor: "pointer",
    flexShrink: 0,
    opacity: 0.7,
  };
  const subjectIsUnread = item.status === "unread";
  const subjectStyle: CSSProperties = {
    flex: 1,
    minWidth: 0,
    fontSize: "var(--font-size-sm, 13px)",
    fontWeight: subjectIsUnread ? 600 : 500,
    color:
      item.status === "done"
        ? "var(--color-text-muted, #b4b4b8)"
        : "var(--color-text, #ededee)",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };
  const metaStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 8,
    flexWrap: "wrap",
    fontSize: "var(--font-size-xs, 12px)",
    color: "var(--color-text-muted, #b4b4b8)",
  };
  const previewStyle: CSSProperties = {
    fontSize: "var(--font-size-xs, 12px)",
    color: "var(--color-text-muted, #b4b4b8)",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };
  const actionButtonStyle: CSSProperties = {
    background: "transparent",
    border: "none",
    color: "var(--color-text-subtle, #7e7e84)",
    cursor: "pointer",
    fontSize: "var(--font-size-xs, 12px)",
    padding: "2px 6px",
  };

  // Primary ref — first entry in `links` is the canonical navigation
  // target. If the host hasn't populated `links` (e.g. an early-stage
  // fixture), we fall back to a synthetic `{ kind: "inbox_item", id }`.
  // The brief mandates ItemLink for primary navigation; the shell does
  // not call `router.navigate` directly.
  const primaryRef = item.links[0] ?? {
    kind: "inbox_item" as const,
    id: item.id,
  };
  const sender = senderDisplay(item.sender);
  const showHigh = item.priority === "high";

  return (
    <div
      style={wrapStyle}
      data-testid="inbox-row"
      data-item-id={item.id}
      data-status={item.status}
      data-kind={item.kind}
      data-priority={item.priority}
      data-selected={selected ? "true" : "false"}
    >
      <div style={headStyle}>
        {/* Bulk-select checkbox */}
        <input
          type="checkbox"
          aria-label={`Select inbox item ${item.subject}`}
          data-testid="inbox-row-select"
          checked={selected}
          onChange={onToggleSelected}
          style={checkboxStyle}
        />

        {/* Primary navigation: ItemLink on the FIRST item ref. The
            link's label is the subject so users see the headline as
            the click target, not an opaque kind chip. */}
        <span style={{ flex: 1, minWidth: 0 }}>
          <ItemLink
            ref={primaryRef}
            deletedLabel={`(deleted) ${item.subject}`}
            className="inbox-row-primary-link"
          />
        </span>

        {/* Per-row actions (visible always; hover affordance is a host
            CSS concern). All are no-ops when handler absent. */}
        {onMarkRead !== undefined && item.status === "unread" ? (
          <button
            type="button"
            data-testid="inbox-row-mark-read"
            onClick={() => onMarkRead(item.id)}
            style={actionButtonStyle}
            aria-label={`Mark ${item.subject} as read`}
          >
            Mark read
          </button>
        ) : null}
        {onSnooze !== undefined ? (
          <button
            type="button"
            data-testid="inbox-row-snooze"
            onClick={() => onSnooze(item.id)}
            style={actionButtonStyle}
            aria-label={`Snooze ${item.subject}`}
          >
            Snooze
          </button>
        ) : null}
        {onDismiss !== undefined ? (
          <button
            type="button"
            data-testid="inbox-row-dismiss"
            onClick={() => onDismiss(item.id)}
            style={actionButtonStyle}
            aria-label={`Dismiss ${item.subject}`}
          >
            Dismiss
          </button>
        ) : null}
      </div>

      {/* Subject is a secondary visible line ABOVE preview, because the
          primary ItemLink renders the resolved label (sender or kind);
          keeping the subject text visible matters when the link is
          loading or the resolver returns a generic label. */}
      <div style={subjectStyle} data-testid="inbox-row-subject">
        {item.subject}
      </div>
      <div style={previewStyle} data-testid="inbox-row-preview">
        {item.preview}
      </div>

      <div style={metaStyle} data-testid="inbox-row-meta">
        <StatusPill status={kindTone(item.kind)} label={kindLabel(item.kind)} />
        {showHigh ? <StatusPill status="error" label="High" /> : null}
        {item.labels.map((lab) => (
          <StatusPill key={lab} status="muted" label={lab} />
        ))}
        <span data-testid="inbox-row-sender">{sender}</span>
        <span data-testid="inbox-row-time">
          {formatRelativeTime(item.updated_at, now)}
        </span>
        {/* Additional cross-destination chips — any `links` past the
            first slot. Filtered to skip a duplicate primary ref. */}
        {item.links.slice(1).map((ref, idx) => (
          <ItemLink key={`${ref.kind}-${idx}`} ref={ref} />
        ))}
        {item.status === "snoozed" && item.snoozed_until !== undefined ? (
          <span data-testid="inbox-row-snoozed-until">
            until {formatRelativeTime(item.snoozed_until, now)}
          </span>
        ) : null}
      </div>
    </div>
  );
}

// ===========================================================================
// BulkActionBar — sticky bottom-of-viewport surface
// ===========================================================================

interface BulkActionBarProps {
  readonly count: number;
  readonly selectedIds: ReadonlySet<InboxItemId>;
  readonly onMarkRead?: (ids: ReadonlyArray<InboxItemId>) => void;
  readonly onSnooze?: (ids: ReadonlyArray<InboxItemId>) => void;
  readonly onDismiss?: (ids: ReadonlyArray<InboxItemId>) => void;
  readonly onClear: () => void;
}

function BulkActionBar({
  count,
  selectedIds,
  onMarkRead,
  onSnooze,
  onDismiss,
  onClear,
}: BulkActionBarProps): ReactElement {
  const wrapperStyle: CSSProperties = {
    position: "sticky",
    bottom: 0,
    left: 0,
    right: 0,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    padding: "12px 16px",
    backgroundColor: "var(--color-bg-elevated, #161617)",
    borderTop: "1px solid var(--color-border, #232325)",
    boxShadow: "0 -2px 8px rgba(0,0,0,0.18)",
    zIndex: 10,
  };
  const innerStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 12,
    maxWidth: 920,
    width: "100%",
  };
  const buttonStyle: CSSProperties = {
    height: 30,
    padding: "0 12px",
    borderRadius: "var(--radius-sm, 6px)",
    border: "1px solid var(--color-border-strong, #2a2a2c)",
    background: "transparent",
    color: "var(--color-text, #ededee)",
    fontSize: "var(--font-size-sm, 13px)",
    cursor: "pointer",
  };
  const ids = Array.from(selectedIds);
  return (
    <div
      role="region"
      aria-label="Bulk actions"
      data-testid="inbox-bulk-bar"
      style={wrapperStyle}
    >
      <div style={innerStyle}>
        <StatusPill status="info" label={`${count} selected`} />
        <div style={{ flex: 1 }} />
        {onMarkRead !== undefined ? (
          <button
            type="button"
            data-testid="inbox-bulk-mark-read"
            onClick={() => onMarkRead(ids)}
            style={buttonStyle}
          >
            Mark read
          </button>
        ) : null}
        {onSnooze !== undefined ? (
          <button
            type="button"
            data-testid="inbox-bulk-snooze"
            onClick={() => onSnooze(ids)}
            style={buttonStyle}
          >
            Snooze
          </button>
        ) : null}
        {onDismiss !== undefined ? (
          <button
            type="button"
            data-testid="inbox-bulk-dismiss"
            onClick={() => onDismiss(ids)}
            style={buttonStyle}
          >
            Dismiss
          </button>
        ) : null}
        <button
          type="button"
          data-testid="inbox-bulk-clear"
          onClick={onClear}
          style={buttonStyle}
        >
          Done
        </button>
      </div>
    </div>
  );
}

// ===========================================================================
// SectionSkeleton — loading placeholder
// ===========================================================================

function SectionSkeleton(): ReactElement {
  const style: CSSProperties = {
    height: 120,
    borderRadius: "var(--radius-md, 12px)",
    border: "1px solid var(--color-border, #232325)",
    backgroundColor: "var(--color-surface-muted, #222224)",
    opacity: 0.5,
  };
  return (
    <div
      style={style}
      data-testid="inbox-skeleton-section"
      aria-hidden="true"
    />
  );
}

// ===========================================================================
// Section bucketing (client-side per brief)
// ===========================================================================

/**
 * Bucket a flat list of inbox items into the four sections defined by
 * the brief. Returns a Map keyed by `InboxSectionKey`.
 *
 * Bucket rules:
 *   - "unread"     : status === "unread"
 *   - "snoozed"    : status === "snoozed"
 *   - "read"       : status === "read" && updated_at >= now - 7d
 *   - "dismissed"  : status === "done" (or older read items)
 *
 * Each bucket is sorted by `updated_at` descending — most recent first.
 * The server may return a different stable order; the shell normalises.
 */
export function bucketInbox(
  items: SectionResult<ReadonlyArray<InboxItem>> | null,
  now: number,
): Map<InboxSectionKey, ReadonlyArray<InboxItem>> {
  const buckets = new Map<InboxSectionKey, InboxItem[]>([
    ["unread", []],
    ["snoozed", []],
    ["read", []],
    ["dismissed", []],
  ]);

  if (items === null || items.status !== "ok" || items.data === undefined) {
    return buckets;
  }

  const readCutoff = now - READ_LOOKBACK_MS;

  for (const it of items.data) {
    if (it.status === "unread") {
      buckets.get("unread")!.push(it);
      continue;
    }
    if (it.status === "snoozed") {
      buckets.get("snoozed")!.push(it);
      continue;
    }
    if (it.status === "done") {
      buckets.get("dismissed")!.push(it);
      continue;
    }
    // status === "read"
    const ts = Date.parse(it.updated_at);
    if (!Number.isNaN(ts) && ts >= readCutoff) {
      buckets.get("read")!.push(it);
    } else {
      // Older reads fold into Dismissed — they're triaged and out of
      // the active window. They remain reachable via the panel's
      // filter chips.
      buckets.get("dismissed")!.push(it);
    }
  }

  for (const [, arr] of buckets) {
    arr.sort((a, b) => {
      const ax = Date.parse(a.updated_at);
      const bx = Date.parse(b.updated_at);
      const aFinite = Number.isFinite(ax) ? ax : 0;
      const bFinite = Number.isFinite(bx) ? bx : 0;
      return bFinite - aFinite;
    });
  }

  const out = new Map<InboxSectionKey, ReadonlyArray<InboxItem>>();
  for (const [k, v] of buckets) out.set(k, v);
  return out;
}

// ===========================================================================
// Helpers
// ===========================================================================

function senderDisplay(sender: InboxSender): string {
  if (sender.kind === "user") return "Teammate";
  if (sender.kind === "agent") return sender.agent_name;
  return systemOriginLabel(sender.origin);
}

function systemOriginLabel(origin: InboxSystemOrigin): string {
  switch (origin) {
    case "connector_error":
      return "Connector";
    case "billing":
      return "Billing";
    case "retention_warning":
      return "Retention";
    case "admin_action":
      return "Admin";
  }
}

function kindLabel(kind: InboxItemKind): string {
  switch (kind) {
    case "mention":
      return "Mention";
    case "approval_request":
      return "Approval";
    case "error":
      return "Error";
    case "system":
      return "System";
  }
}

function kindTone(kind: InboxItemKind): StatusTone {
  switch (kind) {
    case "mention":
      return "info";
    case "approval_request":
      return "warning";
    case "error":
      return "error";
    case "system":
      return "muted";
  }
}
