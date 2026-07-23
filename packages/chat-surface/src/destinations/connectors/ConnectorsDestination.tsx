// Connectors destination shell (P11-B).
//
// Source: connectors-prd §7 + cross-audit §1.6.
//
// Composition:
//   1. <PageHeader> — title + "Connect a connector" primary CTA.
//   2. <FilterTabs> — three slugs (Connected / Available / Custom) per
//      §U1 + §7.2. "Custom" is the user-installed-not-from-catalog
//      bucket; v1 leaves it empty but the slug stays so the wire shape
//      is stable.
//   3. Body: <CardGrid> of <ConnectorCard>s (Connected) OR catalog
//      entries (Available) OR an <EmptyState> for Custom.
//
// Pure presentation: no fetch, no router, no SSE. The host (apps/
// frontend P11-C) wires those.

import {
  useCallback,
  useMemo,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactElement,
  type ReactNode,
} from "react";

import type {
  Connector,
  ConnectorAccessMode,
  ConnectorCatalogEntry,
  ConnectorId,
  ConnectorSlug,
  SectionResult,
} from "@0x-copilot/api-types";

import { CardGrid } from "../../shell/CardGrid";
import { EmptyState } from "../../shell/EmptyState";
import { FilterTabs, type FilterTabOption } from "../../shell/FilterTabs";
import { PageHeader } from "../../shell/PageHeader";

import { ConnectorCard } from "./ConnectorCard";
import type { ConnectorAccessPort } from "./ports/ConnectorAccessPort";

// ===========================================================================
// Copy (DESIGN-SPEC §3 — "Tools = connectors") — exported so the host + tests
// assert the exact strings rather than re-typing them. Generic-SaaS-first;
// Safe/Dune are ordinary catalog entries, never defaults (FR-4.24).
// ===========================================================================

/** Page subtitle. Frames Tools as a destination, not a settings tab. */
export const TOOLS_SUBTITLE =
  "The apps the agent can read from and act through.";

/**
 * Note that the approval *policy* is separate from per-connector access mode
 * (FR-4.25). Rendered as an inline link that invokes `onOpenApprovalSettings`
 * (host → Settings → Model & behavior); plain text when no handler is wired.
 */
export const TOOLS_POLICY_NOTE_COPY =
  "The approval policy lives in Settings → Model & behavior.";

export type ConnectorsFilterSlug = "connected" | "available" | "custom";

const FILTER_ORDER: ReadonlyArray<ConnectorsFilterSlug> = [
  "connected",
  "available",
  "custom",
];

const FILTER_LABEL: Readonly<Record<ConnectorsFilterSlug, string>> = {
  connected: "Connected",
  available: "Available",
  custom: "Custom",
};

export type ConnectorsFilterCounts = Readonly<
  Record<ConnectorsFilterSlug, number>
>;

export interface ConnectorsDestinationProps {
  /**
   * Server-projected list payload. `null` = loading skeleton; the
   * SectionResult wrapper lets the destination render a uniform error
   * branch even though `/v1/connectors` is a non-aggregating endpoint
   * (mirrors Routines + Inbox rationale).
   */
  readonly items?: SectionResult<{
    readonly connectors: ReadonlyArray<Connector>;
    readonly available: ReadonlyArray<ConnectorCatalogEntry>;
  }> | null;

  /** Active filter slug. Defaults to "connected". */
  readonly filter?: ConnectorsFilterSlug;
  readonly onFilterChange?: (next: ConnectorsFilterSlug) => void;

  /** Per-tab counts (chip on each FilterTab). */
  readonly counts?: ConnectorsFilterCounts;

  /** Primary CTA — pivots the host into the connect flow. */
  readonly onConnect?: () => void;

  /** Card click — host wires to `router.navigate({kind:"connector",id})`
   *  via the ItemLink registry. */
  readonly onOpenConnector?: (id: ConnectorId) => void;
  /** Catalog card click — host opens the install flow for the slug. */
  readonly onOpenCatalogEntry?: (slug: ConnectorSlug) => void;

  /** Inline reconnect from a Connected card whose status is `error` or
   *  `expired`. Host kicks off the re-OAuth flow. */
  readonly onReconnect?: (id: ConnectorId) => void;

  /**
   * Host-injected per-connector access-mode writer (PRD-06 D4). When wired,
   * the destination OWNS the optimistic apply, the revert-on-rejection, and
   * the inline error banner — the host supplies only the PATCH I/O. When
   * omitted the segments render read-only (no `onChange` fires). This
   * replaces the old per-connector change callback, whose shape forced each
   * host to re-implement the same state machine.
   */
  readonly accessPort?: ConnectorAccessPort;

  /**
   * Opens Settings → Model & behavior from the approval-policy note
   * (FR-4.25). When omitted the note renders as plain text.
   */
  readonly onOpenApprovalSettings?: () => void;

  /** Retry callback when items.status === "error". */
  readonly onRetry?: () => void;

  /** Test seam for relative-time formatting. */
  readonly now?: number;

  /** Host-provided icon resolver — keeps the shell free of the design-
   *  system's brand-glyph catalog while letting the host wire whichever
   *  source it prefers. */
  readonly renderIcon?: (slug: ConnectorSlug) => ReactNode;
}

export function ConnectorsDestination(
  props: ConnectorsDestinationProps = {},
): ReactElement {
  const {
    items = null,
    filter = "connected",
    onFilterChange,
    counts,
    onConnect,
    onOpenConnector,
    onOpenCatalogEntry,
    onReconnect,
    accessPort,
    onOpenApprovalSettings,
    onRetry,
    now,
    renderIcon,
  } = props;

  // PRD-06 D4 — the optimistic-apply / revert / error-banner state machine
  // lives ONCE here (was duplicated per host). `overrides` holds the
  // in-flight optimistic mode per connector; `accessError` flags a failed
  // PATCH so the inline banner renders.
  const [overrides, setOverrides] = useState<
    Readonly<Record<string, ConnectorAccessMode>>
  >({});
  const [accessError, setAccessError] = useState<boolean>(false);

  const handleAccessModeChange = useCallback(
    (id: ConnectorId, mode: ConnectorAccessMode): void => {
      if (accessPort === undefined) return;
      // Optimistic apply.
      setOverrides((prev) => ({ ...prev, [id]: mode }));
      setAccessError(false);
      accessPort.setAccessMode(id, mode).then(
        (connector) => {
          // Reconcile against the authoritative server row.
          setOverrides((prev) => ({
            ...prev,
            [id]: connector.access_mode,
          }));
        },
        () => {
          // Revert to the pre-optimistic (server) mode + surface the error.
          setOverrides((prev) => {
            const next = { ...prev };
            delete next[id];
            return next;
          });
          setAccessError(true);
        },
      );
    },
    [accessPort],
  );

  const filterOptions = useMemo<
    ReadonlyArray<FilterTabOption<ConnectorsFilterSlug>>
  >(
    () =>
      FILTER_ORDER.map((slug) => ({
        slug,
        label: FILTER_LABEL[slug],
        count: counts?.[slug],
      })),
    [counts],
  );

  const handleFilterChange = (next: ConnectorsFilterSlug): void => {
    if (onFilterChange !== undefined) onFilterChange(next);
  };

  return (
    <section
      role="region"
      aria-label="Tools"
      data-component="connectors-destination"
      style={rootStyle}
    >
      <div style={innerStyle}>
        <PageHeader
          title="Tools"
          subtitle={TOOLS_SUBTITLE}
          primaryAction={
            onConnect !== undefined
              ? { label: "Connect a tool", onClick: onConnect }
              : undefined
          }
        />
        <ApprovalPolicyNote onOpenApprovalSettings={onOpenApprovalSettings} />
        <FilterTabs<ConnectorsFilterSlug>
          value={filter}
          onChange={handleFilterChange}
          options={filterOptions}
          ariaLabel="Tools filter"
          idPrefix="connectors-filter"
        />
        <div
          id={`connectors-filter-panel-${filter}`}
          role="tabpanel"
          aria-labelledby={`connectors-filter-tab-${filter}`}
          style={bodyStyle}
          data-testid="connectors-body"
        >
          {accessError ? (
            <div
              role="alert"
              data-testid="connectors-access-mode-error"
              style={accessErrorStyle}
            >
              Couldn&apos;t update the tool&apos;s access mode. Please try
              again.
            </div>
          ) : null}
          {renderBody({
            items,
            filter,
            onRetry,
            onConnect,
            onOpenConnector,
            onOpenCatalogEntry,
            onReconnect,
            accessPort,
            overrides,
            onAccessModeChange: handleAccessModeChange,
            now,
            renderIcon,
          })}
        </div>
      </div>
    </section>
  );
}

interface BodyArgs {
  readonly items: ConnectorsDestinationProps["items"];
  readonly filter: ConnectorsFilterSlug;
  readonly onRetry: ConnectorsDestinationProps["onRetry"];
  readonly onConnect: ConnectorsDestinationProps["onConnect"];
  readonly onOpenConnector: ConnectorsDestinationProps["onOpenConnector"];
  readonly onOpenCatalogEntry: ConnectorsDestinationProps["onOpenCatalogEntry"];
  readonly onReconnect: ConnectorsDestinationProps["onReconnect"];
  readonly accessPort: ConnectorsDestinationProps["accessPort"];
  readonly overrides: Readonly<Record<string, ConnectorAccessMode>>;
  readonly onAccessModeChange: (
    id: ConnectorId,
    mode: ConnectorAccessMode,
  ) => void;
  readonly now: ConnectorsDestinationProps["now"];
  readonly renderIcon: ConnectorsDestinationProps["renderIcon"];
}

function renderBody(args: BodyArgs): ReactElement {
  const {
    items,
    filter,
    onRetry,
    onConnect,
    onOpenConnector,
    onOpenCatalogEntry,
    onReconnect,
    accessPort,
    overrides,
    onAccessModeChange,
    now,
    renderIcon,
  } = args;

  if (items === null || items === undefined) {
    return <SkeletonGrid />;
  }
  if (items.status === "error" || items.status === "unavailable") {
    return (
      <EmptyState
        title={
          items.status === "unavailable"
            ? "Connectors unavailable"
            : "Couldn't load connectors"
        }
        body={items.error ?? undefined}
        action={
          onRetry !== undefined
            ? { label: "Retry", onClick: onRetry }
            : undefined
        }
      />
    );
  }
  const data = items.data ?? { connectors: [], available: [] };

  if (filter === "custom") {
    return (
      <EmptyState
        title="No custom connectors yet"
        body="Custom OAuth connectors land here once Copilot supports user-installed bundles."
      />
    );
  }

  if (filter === "available") {
    if (data.available.length === 0) {
      return (
        <EmptyState
          title="No more catalog entries"
          body="You've installed every connector Copilot currently knows about."
        />
      );
    }
    return (
      <CardGrid ariaLabel="Available connectors">
        {data.available.map((entry) => (
          <CatalogCard
            key={entry.slug}
            entry={entry}
            icon={renderIcon !== undefined ? renderIcon(entry.slug) : undefined}
            onClick={
              onOpenCatalogEntry !== undefined
                ? () => onOpenCatalogEntry(entry.slug)
                : undefined
            }
          />
        ))}
      </CardGrid>
    );
  }

  // filter === "connected"
  if (data.connectors.length === 0) {
    return (
      <EmptyState
        title="Connect your first tool"
        body="Authorize Gmail, Slack, Notion, or any other app to let the agent read from and act through it."
        action={
          onConnect !== undefined
            ? { label: "Connect a tool", onClick: onConnect }
            : undefined
        }
      />
    );
  }
  return (
    <CardGrid ariaLabel="Connected tools">
      {data.connectors.map((c) => {
        const needsReconnect = c.status === "error" || c.status === "expired";
        return (
          <ConnectorCard
            key={c.id}
            id={c.id}
            displayName={c.display_name}
            description={c.description}
            status={c.status}
            lastSyncIso={c.last_sync_at}
            icon={renderIcon !== undefined ? renderIcon(c.slug) : undefined}
            now={now}
            // Required wire field (PRD-06): the server always emits it. The
            // in-flight optimistic override wins over the server row until the
            // PATCH settles/reverts.
            accessMode={overrides[c.id] ?? c.access_mode}
            onAccessModeChange={
              accessPort !== undefined
                ? (mode) => onAccessModeChange(c.id, mode)
                : undefined
            }
            onClick={
              onOpenConnector !== undefined
                ? () => onOpenConnector(c.id)
                : undefined
            }
            action={
              needsReconnect && onReconnect !== undefined
                ? { label: "Reconnect", onClick: () => onReconnect(c.id) }
                : undefined
            }
          />
        );
      })}
    </CardGrid>
  );
}

// --- Approval-policy note (FR-4.25) --------------------------------------

function ApprovalPolicyNote({
  onOpenApprovalSettings,
}: {
  readonly onOpenApprovalSettings?: () => void;
}): ReactElement {
  return (
    <p style={policyNoteStyle} data-testid="tools-policy-note">
      {onOpenApprovalSettings !== undefined ? (
        <button
          type="button"
          onClick={onOpenApprovalSettings}
          style={policyNoteLinkStyle}
          data-testid="tools-policy-note-link"
        >
          {TOOLS_POLICY_NOTE_COPY}
        </button>
      ) : (
        <span data-testid="tools-policy-note-copy">
          {TOOLS_POLICY_NOTE_COPY}
        </span>
      )}
    </p>
  );
}

// --- Available-catalog card (lightweight; no status pill) ----------------

interface CatalogCardProps {
  readonly entry: ConnectorCatalogEntry;
  readonly icon?: ReactNode;
  readonly onClick?: () => void;
}

function CatalogCard({ entry, icon, onClick }: CatalogCardProps): ReactElement {
  const handleKey = (e: ReactKeyboardEvent<HTMLDivElement>): void => {
    if (onClick === undefined) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onClick();
    }
  };
  return (
    <div
      role="listitem"
      tabIndex={onClick !== undefined ? 0 : -1}
      data-testid="connector-catalog-card"
      data-slug={entry.slug}
      onClick={onClick}
      onKeyDown={onClick !== undefined ? handleKey : undefined}
      style={catalogCardStyle}
      aria-label={`${entry.display_name} — available to connect`}
    >
      <div style={catalogHeaderStyle}>
        {icon !== undefined ? <span aria-hidden="true">{icon}</span> : null}
        <h3 style={catalogTitleStyle}>{entry.display_name}</h3>
      </div>
      <p style={catalogBodyStyle}>{entry.description}</p>
      <div style={catalogFooterStyle}>
        <span>Available</span>
      </div>
    </div>
  );
}

// --- Skeleton --------------------------------------------------------------

function SkeletonGrid(): ReactElement {
  return (
    <CardGrid ariaLabel="Loading connectors">
      {[0, 1, 2, 3, 4, 5].map((i) => (
        <div
          key={i}
          data-testid="connectors-skeleton-card"
          style={skeletonCardStyle}
        >
          <span style={skeletonBarStyle(60)} aria-hidden="true" />
          <span style={skeletonBarStyle(40)} aria-hidden="true" />
          <span style={skeletonBarStyle(75)} aria-hidden="true" />
        </div>
      ))}
    </CardGrid>
  );
}

// === Styles ==============================================================

const rootStyle: CSSProperties = {
  width: "100%",
  height: "100%",
  minHeight: 0,
  background: "var(--color-bg, #131316)",
  color: "var(--color-text, #ededee)",
  boxSizing: "border-box",
  display: "flex",
  flexDirection: "column",
  overflow: "auto",
};

const innerStyle: CSSProperties = {
  width: "100%",
  maxWidth: 1080,
  margin: "0 auto",
  padding: "16px 20px 32px",
  boxSizing: "border-box",
  display: "flex",
  flexDirection: "column",
  gap: 12,
};

const bodyStyle: CSSProperties = {
  flex: 1,
  minHeight: 0,
  padding: "8px 0",
};

const accessErrorStyle: CSSProperties = {
  margin: "0 0 8px",
  padding: "8px 12px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border-strong, #3a3a3d)",
  background: "var(--color-bg-elevated, #18181b)",
  color: "var(--color-text, #ededee)",
  fontSize: "var(--font-size-sm, 13px)",
};

const policyNoteStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-sm, 13px)",
  lineHeight: 1.5,
  color: "var(--color-text-muted, #b4b4b8)",
  maxWidth: 620,
};

const policyNoteLinkStyle: CSSProperties = {
  background: "transparent",
  border: "none",
  padding: 0,
  margin: 0,
  font: "inherit",
  color: "var(--color-accent, #d97757)",
  textDecoration: "underline",
  textUnderlineOffset: 2,
  cursor: "pointer",
};

const catalogCardStyle: CSSProperties = {
  padding: 14,
  background: "var(--color-bg-elevated, #18181b)",
  border: "1px solid var(--color-border, #232325)",
  borderRadius: "var(--radius-md, 12px)",
  display: "flex",
  flexDirection: "column",
  gap: 8,
  cursor: "pointer",
  minHeight: 120,
};

const catalogHeaderStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 10,
};

const catalogTitleStyle: CSSProperties = {
  fontSize: "var(--font-size-md, 14px)",
  fontWeight: 600,
  margin: 0,
};

const catalogBodyStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-sm, 13px)",
  color: "var(--color-text-muted, #b4b4b8)",
};

const catalogFooterStyle: CSSProperties = {
  marginTop: "auto",
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-subtle, #7e7e84)",
};

const skeletonCardStyle: CSSProperties = {
  padding: 14,
  background: "var(--color-bg-elevated, #18181b)",
  border: "1px solid var(--color-border, #232325)",
  borderRadius: "var(--radius-md, 12px)",
  display: "flex",
  flexDirection: "column",
  gap: 10,
  minHeight: 120,
};

function skeletonBarStyle(widthPercent: number): CSSProperties {
  return {
    display: "inline-block",
    width: `${widthPercent}%`,
    height: 10,
    borderRadius: 4,
    background: "var(--color-border, #232325)",
  };
}
