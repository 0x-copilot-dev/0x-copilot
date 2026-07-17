// <ConnectorDetailView /> — tabbed detail surface for one connector.
//
// Source: connectors-prd §7.3 (detail view) + cross-audit §1.6 (tablist
// shape) + §1.1 (no direct router.navigate; every cross-ref hop is an
// ItemLink in the children Tab components).
//
// Tabs (5): Overview / Scope / Consumers / Audit / Settings.
// `Audit` is admin-gated (connectors-prd §6); non-admins still see the
// tab but the panel renders an explanatory empty state.
//
// Pure presentation. Every action (disconnect, refresh, scope-patch,
// pagination-load-more, csv-export) is a callback prop. The host owns
// transport, routing and confirmation dialogs.

import {
  useCallback,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactElement,
  type ReactNode,
} from "react";

import type {
  ConnectorAuditEntry,
  ConnectorDetailResponse,
  ConnectorScopeEntry,
  ConnectorStatus,
} from "@0x-copilot/api-types";

import { StatusPill, type StatusTone } from "../../shell/StatusPill";
import { formatRelativeTime } from "../../util/time";

import { ConsumersTab } from "./ConsumersTab";
import { ReadAuditTab } from "./ReadAuditTab";
import { ScopeReviewTab } from "./ScopeReviewTab";

// ===========================================================================
// Tabs
// ===========================================================================

export type ConnectorDetailTabId =
  | "overview"
  | "scope"
  | "consumers"
  | "audit"
  | "settings";

const TAB_ORDER: ReadonlyArray<ConnectorDetailTabId> = [
  "overview",
  "scope",
  "consumers",
  "audit",
  "settings",
];

const TAB_LABEL: Readonly<Record<ConnectorDetailTabId, string>> = {
  overview: "Overview",
  scope: "Scope",
  consumers: "Consumers",
  audit: "Audit",
  settings: "Settings",
};

const STATUS_TONE: Readonly<Record<ConnectorStatus, StatusTone>> = {
  connected: "ok",
  error: "error",
  expired: "warning",
  disconnected: "muted",
};

const STATUS_LABEL: Readonly<Record<ConnectorStatus, string>> = {
  connected: "Connected",
  error: "Error",
  expired: "Needs re-auth",
  disconnected: "Disconnected",
};

// ===========================================================================
// Props
// ===========================================================================

export interface ConnectorDetailViewProps {
  /** Server-projected detail. `null` = loading skeleton. */
  readonly detail: ConnectorDetailResponse | null;
  /** Admin scope drives whether the Audit tab renders rows. */
  readonly isAdmin: boolean;
  /** Audit page rows (host owns paging). */
  readonly auditEntries?: ReadonlyArray<ConnectorAuditEntry>;
  readonly auditNextCursor?: string | null;
  readonly onLoadMoreAudit?: (cursor: string) => void;
  readonly onExportAuditCsv?: () => void;
  /** Mutating callbacks. */
  readonly onPatchScopes?: (scopes: ReadonlyArray<ConnectorScopeEntry>) => void;
  readonly onRefresh?: () => void;
  readonly onDisconnect?: () => void;
  /** Host-provided icon (slug-based). */
  readonly icon?: ReactNode;
  /** Reference instant — test seam for relative-time formatting. */
  readonly now?: number;
  readonly initialTab?: ConnectorDetailTabId;
  readonly onTabChange?: (next: ConnectorDetailTabId) => void;
}

// ===========================================================================
// Component
// ===========================================================================

export function ConnectorDetailView(
  props: ConnectorDetailViewProps,
): ReactElement {
  const {
    detail,
    isAdmin,
    auditEntries = [],
    auditNextCursor,
    onLoadMoreAudit,
    onExportAuditCsv,
    onPatchScopes,
    onRefresh,
    onDisconnect,
    icon,
    now,
    initialTab = "overview",
    onTabChange,
  } = props;

  const [activeTab, setActiveTab] = useState<ConnectorDetailTabId>(initialTab);
  const tabRefs = useRef<
    Record<ConnectorDetailTabId, HTMLButtonElement | null>
  >({
    overview: null,
    scope: null,
    consumers: null,
    audit: null,
    settings: null,
  });

  const switchTab = useCallback(
    (next: ConnectorDetailTabId) => {
      setActiveTab(next);
      onTabChange?.(next);
    },
    [onTabChange],
  );

  const focusTab = useCallback(
    (next: ConnectorDetailTabId) => {
      switchTab(next);
      tabRefs.current[next]?.focus();
    },
    [switchTab],
  );

  const handleTabKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLDivElement>) => {
      const idx = TAB_ORDER.indexOf(activeTab);
      if (idx < 0) return;
      if (event.key === "ArrowRight") {
        event.preventDefault();
        focusTab(TAB_ORDER[(idx + 1) % TAB_ORDER.length]!);
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        focusTab(TAB_ORDER[(idx - 1 + TAB_ORDER.length) % TAB_ORDER.length]!);
      } else if (event.key === "Home") {
        event.preventDefault();
        focusTab(TAB_ORDER[0]!);
      } else if (event.key === "End") {
        event.preventDefault();
        focusTab(TAB_ORDER[TAB_ORDER.length - 1]!);
      }
    },
    [activeTab, focusTab],
  );

  const lastSyncLabel = useMemo(() => {
    if (detail === null) return "";
    const ts = detail.connector.last_sync_at;
    return ts === null
      ? "Never synced"
      : `Last sync ${formatRelativeTime(ts, now)}`;
  }, [detail, now]);

  if (detail === null) {
    return (
      <article
        data-testid="connector-detail-view-skeleton"
        style={containerStyle}
        aria-busy="true"
      >
        <div style={skeletonHeroStyle}>
          <span style={skeletonBlock(48, 48)} aria-hidden="true" />
          <span style={skeletonBlock(180, 18)} aria-hidden="true" />
        </div>
      </article>
    );
  }

  const c = detail.connector;
  const needsReconnect = c.status === "error" || c.status === "expired";

  return (
    <article
      data-testid="connector-detail-view"
      data-connector-id={c.id}
      data-status={c.status}
      data-active-tab={activeTab}
      style={containerStyle}
    >
      {/* Header --------------------------------------------------------- */}
      <header style={heroStyle}>
        {icon !== undefined ? (
          <span aria-hidden="true" style={iconStyle}>
            {icon}
          </span>
        ) : null}
        <div style={heroBodyStyle}>
          <h1 style={titleStyle} data-testid="connector-detail-name">
            {c.display_name}
          </h1>
          <p style={slugStyle} data-testid="connector-detail-slug">
            {c.slug}
          </p>
          <div style={pillRowStyle}>
            <StatusPill
              status={STATUS_TONE[c.status]}
              label={STATUS_LABEL[c.status]}
            />
            <span
              style={lastSyncStyle}
              data-testid="connector-detail-last-sync"
            >
              {lastSyncLabel}
            </span>
          </div>
        </div>
      </header>

      {c.description.length > 0 ? (
        <p style={descriptionStyle} data-testid="connector-detail-description">
          {c.description}
        </p>
      ) : null}

      {/* Tablist -------------------------------------------------------- */}
      <div
        role="tablist"
        aria-label="Connector detail"
        style={tabStripStyle}
        onKeyDown={handleTabKeyDown}
      >
        {TAB_ORDER.map((tab) => (
          <button
            key={tab}
            ref={(node) => {
              tabRefs.current[tab] = node;
            }}
            type="button"
            role="tab"
            id={`connector-detail-tab-${tab}`}
            aria-selected={activeTab === tab}
            aria-controls={`connector-detail-tabpanel-${tab}`}
            tabIndex={activeTab === tab ? 0 : -1}
            onClick={() => switchTab(tab)}
            data-testid={`connector-detail-tab-${tab}`}
            style={tabButtonStyle(activeTab === tab)}
          >
            {TAB_LABEL[tab]}
          </button>
        ))}
      </div>

      <div
        role="tabpanel"
        id={`connector-detail-tabpanel-${activeTab}`}
        aria-labelledby={`connector-detail-tab-${activeTab}`}
        data-testid={`connector-detail-tabpanel-${activeTab}`}
        style={panelStyle}
      >
        {activeTab === "overview" ? (
          <OverviewPanel
            detail={detail}
            needsReconnect={needsReconnect}
            onRefresh={onRefresh}
          />
        ) : null}
        {activeTab === "scope" ? (
          <ScopeReviewTab scopes={c.scopes} onSave={onPatchScopes} />
        ) : null}
        {activeTab === "consumers" ? (
          <ConsumersTab consumers={detail.consumers} />
        ) : null}
        {activeTab === "audit" ? (
          <ReadAuditTab
            isAdmin={isAdmin}
            entries={auditEntries}
            nextCursor={auditNextCursor}
            onLoadMore={onLoadMoreAudit}
            onExportCsv={onExportAuditCsv}
            now={now}
          />
        ) : null}
        {activeTab === "settings" ? (
          <SettingsPanel
            onDisconnect={onDisconnect}
            disconnectDisabled={c.status === "disconnected"}
          />
        ) : null}
      </div>
    </article>
  );
}

// ===========================================================================
// Subpanels
// ===========================================================================

interface OverviewPanelProps {
  readonly detail: ConnectorDetailResponse;
  readonly needsReconnect: boolean;
  readonly onRefresh?: () => void;
}

function OverviewPanel({
  detail,
  needsReconnect,
  onRefresh,
}: OverviewPanelProps): ReactElement {
  const c = detail.connector;
  return (
    <div data-testid="connector-detail-overview">
      <dl style={factsGridStyle}>
        <Fact
          label="Owner"
          value={c.owner_user_id}
          testId="connector-detail-fact-owner"
        />
        <Fact
          label="Scopes granted"
          value={String(c.scopes.filter((s) => s.granted).length)}
          testId="connector-detail-fact-scopes"
        />
        <Fact
          label="Created"
          value={c.created_at}
          testId="connector-detail-fact-created"
        />
        <Fact
          label="Updated"
          value={c.updated_at}
          testId="connector-detail-fact-updated"
        />
      </dl>
      {c.status_reason !== undefined && c.status_reason.length > 0 ? (
        <p
          style={reasonStyle}
          data-testid="connector-detail-status-reason"
          role="status"
        >
          {c.status_reason}
        </p>
      ) : null}
      {needsReconnect ? (
        <p style={reauthHintStyle} role="alert">
          This connector needs re-authorization. The host wires the re-OAuth
          flow.
        </p>
      ) : null}
      <div style={actionRowStyle}>
        {onRefresh !== undefined ? (
          <button
            type="button"
            onClick={onRefresh}
            style={secondaryButtonStyle}
            data-testid="connector-detail-refresh"
          >
            Refresh
          </button>
        ) : null}
      </div>
    </div>
  );
}

interface SettingsPanelProps {
  readonly onDisconnect?: () => void;
  readonly disconnectDisabled: boolean;
}

function SettingsPanel({
  onDisconnect,
  disconnectDisabled,
}: SettingsPanelProps): ReactElement {
  return (
    <div data-testid="connector-detail-settings">
      <p style={hintStyle}>
        Disconnecting revokes the OAuth token and stops every consumer from
        reading. Dependent tools are disabled (not deleted) and can be
        re-enabled after you reconnect.
      </p>
      <div style={actionRowStyle}>
        {onDisconnect !== undefined ? (
          <button
            type="button"
            onClick={onDisconnect}
            disabled={disconnectDisabled}
            style={dangerButtonStyle}
            data-testid="connector-detail-disconnect"
          >
            Disconnect
          </button>
        ) : null}
      </div>
    </div>
  );
}

interface FactProps {
  readonly label: string;
  readonly value: string;
  readonly testId: string;
}

function Fact(props: FactProps): ReactElement {
  return (
    <div style={factStyle} data-testid={props.testId}>
      <dt style={factLabelStyle}>{props.label}</dt>
      <dd style={factValueStyle}>{props.value}</dd>
    </div>
  );
}

// ===========================================================================
// Styles
// ===========================================================================

const containerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 12,
  padding: 16,
  background: "var(--color-bg, #131316)",
  color: "var(--color-text, #ededee)",
  border: "1px solid var(--color-border, #232325)",
  borderRadius: 10,
  boxSizing: "border-box",
};

const heroStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 12,
};

const iconStyle: CSSProperties = {
  display: "inline-flex",
  flexShrink: 0,
};

const heroBodyStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 4,
  minWidth: 0,
};

const titleStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-lg, 18px)",
  fontWeight: 600,
  color: "var(--color-text, #ededee)",
};

const slugStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-muted, #b4b4b8)",
  fontFamily:
    "var(--font-family-mono, ui-monospace, SFMono-Regular, monospace)",
};

const pillRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  flexWrap: "wrap",
  marginTop: 4,
};

const lastSyncStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-subtle, #7e7e84)",
};

const descriptionStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-sm, 13px)",
  lineHeight: 1.55,
  color: "var(--color-text, #ededee)",
};

const tabStripStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 4,
  flexWrap: "wrap",
  borderBottom: "1px solid var(--color-border, #232325)",
  paddingBottom: 4,
};

function tabButtonStyle(active: boolean): CSSProperties {
  return {
    height: 32,
    padding: "0 12px",
    borderRadius: "var(--radius-sm, 6px)",
    border: "1px solid transparent",
    background: active ? "var(--color-bg-elevated, #18181b)" : "transparent",
    color: active
      ? "var(--color-text, #ededee)"
      : "var(--color-text-muted, #b4b4b8)",
    fontSize: "var(--font-size-sm, 13px)",
    fontWeight: 600,
    cursor: "pointer",
  };
}

const panelStyle: CSSProperties = {
  paddingTop: 12,
  display: "flex",
  flexDirection: "column",
  gap: 12,
};

const factsGridStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
  gap: 10,
  margin: 0,
  padding: 0,
};

const factStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 2,
  padding: "8px 10px",
  background: "var(--color-bg-elevated, #18181b)",
  borderRadius: "var(--radius-sm, 6px)",
};

const factLabelStyle: CSSProperties = {
  fontSize: "var(--font-size-2xs, 11px)",
  color: "var(--color-text-muted, #b4b4b8)",
  textTransform: "uppercase",
  letterSpacing: 0.4,
  margin: 0,
};

const factValueStyle: CSSProperties = {
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  color: "var(--color-text, #ededee)",
  margin: 0,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const reasonStyle: CSSProperties = {
  margin: "8px 0 0 0",
  padding: "8px 10px",
  background: "var(--color-warning-bg, #322615)",
  border: "1px solid var(--color-warning, #d9a857)",
  borderRadius: "var(--radius-sm, 6px)",
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text, #ededee)",
};

const reauthHintStyle: CSSProperties = {
  margin: "8px 0 0 0",
  padding: "8px 10px",
  background: "var(--color-danger-bg, #321a1a)",
  border: "1px solid var(--color-danger, #d97777)",
  borderRadius: "var(--radius-sm, 6px)",
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text, #ededee)",
};

const hintStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-sm, 13px)",
  color: "var(--color-text-muted, #b4b4b8)",
};

const actionRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  marginTop: 12,
};

const secondaryButtonStyle: CSSProperties = {
  height: 30,
  padding: "0 12px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border-strong, #2a2a2c)",
  background: "transparent",
  color: "var(--color-text, #ededee)",
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  cursor: "pointer",
};

const dangerButtonStyle: CSSProperties = {
  height: 30,
  padding: "0 12px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-danger, #d97777)",
  background: "transparent",
  color: "var(--color-danger, #d97777)",
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  cursor: "pointer",
};

const skeletonHeroStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 12,
};

function skeletonBlock(width: number, height: number): CSSProperties {
  return {
    display: "inline-block",
    width,
    height,
    borderRadius: 6,
    background: "var(--color-border, #232325)",
  };
}
