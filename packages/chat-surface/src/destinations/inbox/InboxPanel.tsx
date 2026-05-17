// Inbox — right-side context panel (P4-B1).
//
// Per inbox-prd §3.3 the panel carries filter chips (All / Mentions /
// Approvals / Errors), an unread-count badge, search, and sender /
// project / saved-search sections. P4-B1 ships the chips + unread
// badge slice; search, sender groups, and saved-search CRUD are
// follow-ups (P4-B2/P4-B3 don't own them — see brief: "filter chips
// (All / Unread / Mentions / Errors) + unread count badge").
//
// The brief's four filter slugs:
//   - "all"        — every inbox row (default)
//   - "unread"     — status === "unread"
//   - "mentions"   — kind === "mention"
//   - "errors"     — kind === "error"
//
// The panel keeps no fetch logic — the host supplies the active filter
// and per-filter counts and receives a change callback. Substrate-
// agnostic (web + desktop).
//
// Saved-search / sender / project sections are deliberately omitted at
// this phase: they're feature areas, not part of the shell carve-out.
// The PRD calls them out and a follow-up agent will extend the panel
// without touching the destination.

import {
  useMemo,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

import { ContextPanel } from "../../shell/ContextPanel";
import { FilterTabs, type FilterTabOption } from "../../shell/FilterTabs";
import { StatusPill } from "../../shell/StatusPill";

// ===========================================================================
// Filter contract
// ===========================================================================

/**
 * Slug shape for the panel filter chips. Encoded as a string so the
 * `<FilterTabs>` typed generic compiles cleanly.
 *
 * P4-C wires these to `GET /v1/inbox?filter[…]=…` per inbox-prd §4.4:
 *   - "all"      -> no filter
 *   - "unread"   -> filter[status]=unread
 *   - "mentions" -> filter[kind]=mention
 *   - "errors"   -> filter[kind]=error
 */
export type InboxPanelFilterSlug = "all" | "unread" | "mentions" | "errors";

/** Counts per filter slug — driven by the host from the same query
 *  result used to render the main list. */
export type InboxPanelCounts = Readonly<Record<InboxPanelFilterSlug, number>>;

export interface InboxPanelProps {
  /** Currently active filter slug. Defaults to "all". */
  readonly filter?: InboxPanelFilterSlug;
  readonly onFilterChange?: (next: InboxPanelFilterSlug) => void;

  /** Per-filter counts. When omitted, chips render without count chips. */
  readonly counts?: InboxPanelCounts;

  /** Convenience badge — total unread, surfaced at the top of the
   *  panel head. Single source of truth (mirrors rail badge). */
  readonly unreadCount?: number;

  /** Optional footer slot — host may surface "Edit inbox rules" link
   *  out to agents/policies per inbox-prd §3.3 footer. */
  readonly footer?: ReactNode;
}

// ===========================================================================
// Top-level panel
// ===========================================================================

export function InboxPanel(props: InboxPanelProps = {}): ReactElement {
  const { filter = "all", onFilterChange, counts, unreadCount, footer } = props;

  const filterOptions = useMemo<
    ReadonlyArray<FilterTabOption<InboxPanelFilterSlug>>
  >(
    () => [
      {
        slug: "all" as const,
        label: "All",
        count: counts?.all,
      },
      {
        slug: "unread" as const,
        label: "Unread",
        count: counts?.unread ?? unreadCount,
      },
      {
        slug: "mentions" as const,
        label: "Mentions",
        count: counts?.mentions,
      },
      {
        slug: "errors" as const,
        label: "Errors",
        count: counts?.errors,
      },
    ],
    [counts, unreadCount],
  );

  const handleChange = (next: InboxPanelFilterSlug): void => {
    if (onFilterChange !== undefined) onFilterChange(next);
  };

  const subtitle =
    unreadCount !== undefined && unreadCount > 0
      ? `${unreadCount} unread`
      : undefined;

  return (
    <ContextPanel title="Inbox" subtitle={subtitle} destination="inbox">
      <div data-testid="inbox-panel">
        {/* === Unread badge — visible single source of truth === */}
        {unreadCount !== undefined && unreadCount > 0 ? (
          <PanelSectionWrapper
            testId="inbox-panel-section-unread-badge"
            title="Status"
          >
            <div data-testid="inbox-panel-unread-badge">
              <StatusPill status="info" label={`${unreadCount} unread`} />
            </div>
          </PanelSectionWrapper>
        ) : null}

        {/* === Filter chips === */}
        <PanelSectionWrapper
          testId="inbox-panel-section-filters"
          title="Filter"
        >
          <FilterTabs<InboxPanelFilterSlug>
            value={filter}
            onChange={handleChange}
            options={filterOptions}
            ariaLabel="Inbox filter"
            idPrefix="inbox-panel"
          />
        </PanelSectionWrapper>

        {footer !== undefined ? (
          <PanelSectionWrapper
            testId="inbox-panel-section-footer"
            title="Settings"
          >
            <div data-testid="inbox-panel-footer">{footer}</div>
          </PanelSectionWrapper>
        ) : null}
      </div>
    </ContextPanel>
  );
}

// ===========================================================================
// PanelSectionWrapper — local clone of the TodosPanel section frame.
// ===========================================================================
//
// Inlining (rather than promoting to shell/) keeps the small visual
// frame an internal concern of the panel; if a second destination wants
// the same look it would be promoted then. The wrapper is
// intentionally identical in shape to the Todos panel for visual
// consistency across context-panel destinations.

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
