// Connectors — left rail context panel (P11-B).
//
// Source: connectors-prd §7.2 (left rail filters) + cross-audit §1.6
// (ContextPanel shape). Carries:
//
//   1. Filter chips mirroring the destination's FilterTabs vocabulary
//      (Connected / Available / Custom). One source of truth — the
//      destination owns the slug type; the panel re-uses it.
//   2. "Webhooks" link — pivots into the /connectors/webhooks
//      sub-destination (host wires the callback).
//   3. "Connect" CTA — primary call-to-action; mirrors the destination's
//      header primaryAction so the rail also surfaces it.
//
// Pure presentation; no fetch, no router.

import { type CSSProperties, type ReactElement } from "react";

import { ContextPanel } from "../../shell/ContextPanel";
import { FilterTabs, type FilterTabOption } from "../../shell/FilterTabs";

import type {
  ConnectorsFilterCounts,
  ConnectorsFilterSlug,
} from "./ConnectorsDestination";

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

export interface ConnectorsPanelProps {
  readonly filter?: ConnectorsFilterSlug;
  readonly onFilterChange?: (next: ConnectorsFilterSlug) => void;
  readonly counts?: ConnectorsFilterCounts;
  /** Primary CTA — pivots the host into the connect flow. */
  readonly onConnect?: () => void;
  /** Pivot into the webhook manager sub-destination. */
  readonly onOpenWebhooks?: () => void;
}

export function ConnectorsPanel({
  filter = "connected",
  onFilterChange,
  counts,
  onConnect,
  onOpenWebhooks,
}: ConnectorsPanelProps): ReactElement {
  const filterOptions: ReadonlyArray<FilterTabOption<ConnectorsFilterSlug>> =
    FILTER_ORDER.map((slug) => ({
      slug,
      label: FILTER_LABEL[slug],
      count: counts?.[slug],
    }));

  const handleFilterChange = (next: ConnectorsFilterSlug): void => {
    if (onFilterChange !== undefined) onFilterChange(next);
  };

  return (
    <ContextPanel
      title="Connectors"
      destination="connectors"
      primaryAction={
        onConnect !== undefined
          ? { label: "Connect a connector", onClick: onConnect }
          : undefined
      }
    >
      <div data-testid="connectors-panel" style={bodyStyle}>
        <div style={sectionStyle}>
          <div style={sectionTitleStyle}>Status</div>
          <FilterTabs<ConnectorsFilterSlug>
            value={filter}
            onChange={handleFilterChange}
            options={filterOptions}
            ariaLabel="Connectors filter (panel)"
            idPrefix="connectors-panel-filter"
          />
        </div>
        <div style={sectionStyle}>
          <div style={sectionTitleStyle}>Manage</div>
          <button
            type="button"
            onClick={onOpenWebhooks}
            disabled={onOpenWebhooks === undefined}
            style={linkButtonStyle}
            data-testid="connectors-panel-webhooks"
          >
            Webhooks
          </button>
        </div>
      </div>
    </ContextPanel>
  );
}

// === Styles ============================================================

const bodyStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 12,
  padding: "8px 12px",
};

const sectionStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  padding: "8px 0",
};

const sectionTitleStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: 600,
  textTransform: "uppercase",
  letterSpacing: 0.6,
  color: "var(--color-text-subtle, #7e7e84)",
};

const linkButtonStyle: CSSProperties = {
  height: 30,
  padding: "0 10px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid transparent",
  background: "transparent",
  color: "var(--color-accent, #d97757)",
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  textAlign: "left",
  cursor: "pointer",
};

const ctaSectionStyle: CSSProperties = {
  padding: "8px 0",
};

const primaryButtonStyle: CSSProperties = {
  width: "100%",
  height: 32,
  padding: "0 12px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-accent, #d97757)",
  background: "var(--color-accent, #d97757)",
  color: "var(--color-accent-contrast, #1a0f0a)",
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  cursor: "pointer",
};
