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
  useMemo,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactElement,
  type ReactNode,
} from "react";

import type {
  Connector,
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
    onRetry,
    now,
    renderIcon,
  } = props;

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
      aria-label="Connectors"
      data-component="connectors-destination"
      style={rootStyle}
    >
      <div style={innerStyle}>
        <PageHeader
          title="Connectors"
          subtitle="Authenticated bridges to your SaaS sources."
          primaryAction={
            onConnect !== undefined
              ? { label: "Connect a connector", onClick: onConnect }
              : undefined
          }
        />
        <FilterTabs<ConnectorsFilterSlug>
          value={filter}
          onChange={handleFilterChange}
          options={filterOptions}
          ariaLabel="Connectors filter"
          idPrefix="connectors-filter"
        />
        <div
          id={`connectors-filter-panel-${filter}`}
          role="tabpanel"
          aria-labelledby={`connectors-filter-tab-${filter}`}
          style={bodyStyle}
          data-testid="connectors-body"
        >
          {renderBody({
            items,
            filter,
            onRetry,
            onConnect,
            onOpenConnector,
            onOpenCatalogEntry,
            onReconnect,
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
        body="Custom OAuth connectors land here once Atlas supports user-installed bundles."
      />
    );
  }

  if (filter === "available") {
    if (data.available.length === 0) {
      return (
        <EmptyState
          title="No more catalog entries"
          body="You've installed every connector Atlas currently knows about."
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
        title="Connect your first SaaS source"
        body="Authorize Gmail, Slack, Salesforce, or any other connector to bring real data into Atlas."
        action={
          onConnect !== undefined
            ? { label: "Connect a connector", onClick: onConnect }
            : undefined
        }
      />
    );
  }
  return (
    <CardGrid ariaLabel="Connected connectors">
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
