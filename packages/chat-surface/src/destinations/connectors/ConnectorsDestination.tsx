// Connectors destination shell (Tools) — PRD-11.
//
// Source: design-kit/app-v3 ConnectorsSurface (copilot-app.jsx:95-160) +
// docs/plan/design-parity-remediation/PRD-11-tools-surface.md.
//
// The design is a single hairline ROW LIST, not a card grid:
//   1. <PageLead> — one muted lead paragraph carrying the policy hand-off link
//      (the rail already labels the screen — no 22px page title).
//   2. <SectionHeader> — the mono `Connected · N` eyebrow, with the primary
//      "Connect a tool" CTA (+ optional Webhooks ghost) in its action slot.
//   3. <RowList> of <Row>s — each connector = a 30px neutral identity tile
//      (AppIcon, keyed on slug), a 12.5px name, a mono sub-line, and the
//      per-connector <AccessModeSegment> in the trailing `.lrow__act` cell.
//
// PRD-06 owns `accessPort` + the optimistic-apply / revert / error-banner state
// machine below; PRD-11 does not touch it. Pure presentation: no fetch, no
// router, no SSE — the host (apps/frontend ConnectorsRoute / apps/desktop
// ConnectorsBinder) wires those and mounts the <ConnectModal> connect flow.

import {
  useCallback,
  useState,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

import type {
  Connector,
  ConnectorAccessMode,
  ConnectorId,
  ConnectorSlug,
  ConnectorStatus,
  SectionResult,
} from "@0x-copilot/api-types";
import { AppIcon, Button } from "@0x-copilot/design-system";

import { PageLead, RowList, Row, SectionHeader } from "../_shared";
import { EmptyState } from "../../shell/EmptyState";
import { StatusPill, type StatusTone } from "../../shell/StatusPill";

import { AccessModeSegment } from "./AccessModeSegment";
import type { ConnectorAccessPort } from "./ports/ConnectorAccessPort";

// ===========================================================================
// Copy (DESIGN-SPEC §3 — "Tools = connectors") — exported so the host + tests
// assert the exact strings rather than re-typing them.
// ===========================================================================

/** Page lead — thesis sentence framing Tools as a destination, not a settings
 *  tab. The policy hand-off link (below) is appended inline. */
export const TOOLS_LEAD_COPY =
  "The apps the agent can read from and act through — a destination, not a settings tab. Per-tool access lives here; the agent's approval policy lives in ";

/** The inline policy-note link label (FR-4.25). */
export const TOOLS_POLICY_NOTE_COPY = "Settings → Model & behavior";

// Retained for callers/tests that imported the old subtitle constant. The
// thesis now lives in the lead paragraph (TOOLS_LEAD_COPY).
export const TOOLS_SUBTITLE =
  "The apps the agent can read from and act through.";

// Only broken connectors carry a status chip — a list whose membership already
// means "connected" needs no CONNECTED pill on every row (PRD-11: chip moves to
// error/expired/disconnected only).
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

export interface ConnectorsDestinationProps {
  /**
   * Server-projected list payload. `null` = loading skeleton; the
   * SectionResult wrapper lets the destination render a uniform error branch.
   * `available` (the catalog) is carried for the host's ConnectModal but is no
   * longer rendered on the surface itself (PRD-11 D2 — the catalog lives only
   * in the modal).
   */
  readonly items?: SectionResult<{
    readonly connectors: ReadonlyArray<Connector>;
    readonly available: ReadonlyArray<unknown>;
  }> | null;

  /** Primary CTA — opens the host's connect modal. */
  readonly onConnect?: () => void;

  /** Row-title click — host wires to open the connector detail route. */
  readonly onOpenConnector?: (id: ConnectorId) => void;

  /** Pivot into the webhooks sub-destination (ghost button in the header). */
  readonly onOpenWebhooks?: () => void;

  /** Inline reconnect from a connector whose status is `error` or `expired`. */
  readonly onReconnect?: (id: ConnectorId) => void;

  /**
   * Host-injected per-connector access-mode writer (PRD-06 D4). When wired,
   * the destination OWNS the optimistic apply, the revert-on-rejection, and
   * the inline error banner — the host supplies only the PATCH I/O.
   */
  readonly accessPort?: ConnectorAccessPort;

  /**
   * Opens Settings → Model & behavior from the approval-policy link. When
   * omitted the link renders as plain text.
   */
  readonly onOpenApprovalSettings?: () => void;

  /** Retry callback when items.status === "error". */
  readonly onRetry?: () => void;

  /** Test seam for relative-time formatting (kept for host parity). */
  readonly now?: number;

  /**
   * Host-provided icon override — a host with `logo_url` may want it. When
   * absent the destination renders the default neutral `AppIcon` tile keyed on
   * `connector.slug` (PRD-11 D3), so the tile no longer depends on a binding.
   */
  readonly renderIcon?: (slug: ConnectorSlug) => ReactNode;
}

export function ConnectorsDestination(
  props: ConnectorsDestinationProps = {},
): ReactElement {
  const {
    items = null,
    onConnect,
    onOpenConnector,
    onOpenWebhooks,
    onReconnect,
    accessPort,
    onOpenApprovalSettings,
    onRetry,
    renderIcon,
  } = props;

  // PRD-06 D4 — the optimistic-apply / revert / error-banner state machine
  // lives ONCE here. `overrides` holds the in-flight optimistic mode per
  // connector; `accessError` flags a failed PATCH so the inline banner renders.
  const [overrides, setOverrides] = useState<
    Readonly<Record<string, ConnectorAccessMode>>
  >({});
  const [accessError, setAccessError] = useState<boolean>(false);

  const handleAccessModeChange = useCallback(
    (id: ConnectorId, mode: ConnectorAccessMode): void => {
      if (accessPort === undefined) return;
      setOverrides((prev) => ({ ...prev, [id]: mode }));
      setAccessError(false);
      accessPort.setAccessMode(id, mode).then(
        (connector) => {
          setOverrides((prev) => ({ ...prev, [id]: connector.access_mode }));
        },
        () => {
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

  const connectCta =
    onConnect !== undefined ? (
      <Button
        variant="primary"
        size="sm"
        onClick={onConnect}
        data-testid="connectors-connect-cta"
      >
        Connect a tool
      </Button>
    ) : null;

  const headerAction =
    onConnect !== undefined || onOpenWebhooks !== undefined ? (
      <>
        {onOpenWebhooks !== undefined ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={onOpenWebhooks}
            data-testid="connectors-webhooks"
          >
            Webhooks
          </Button>
        ) : null}
        {connectCta}
      </>
    ) : undefined;

  const connectorCount =
    items !== null && items !== undefined && items.status === "ok"
      ? (items.data?.connectors.length ?? 0)
      : 0;

  return (
    <section
      role="region"
      aria-label="Tools"
      data-component="connectors-destination"
      style={rootStyle}
    >
      <div style={innerStyle}>
        <ToolsLead onOpenApprovalSettings={onOpenApprovalSettings} />
        <SectionHeader
          action={headerAction}
          data-testid="connectors-section-header"
        >
          Connected · {connectorCount}
        </SectionHeader>
        {accessError ? (
          <div
            role="alert"
            data-testid="connectors-access-mode-error"
            style={accessErrorStyle}
          >
            Couldn&apos;t update the tool&apos;s access mode. Please try again.
          </div>
        ) : null}
        {renderBody({
          items,
          onRetry,
          onConnect,
          onOpenConnector,
          onReconnect,
          accessPort,
          overrides,
          onAccessModeChange: handleAccessModeChange,
          renderIcon,
        })}
      </div>
    </section>
  );
}

interface BodyArgs {
  readonly items: ConnectorsDestinationProps["items"];
  readonly onRetry: ConnectorsDestinationProps["onRetry"];
  readonly onConnect: ConnectorsDestinationProps["onConnect"];
  readonly onOpenConnector: ConnectorsDestinationProps["onOpenConnector"];
  readonly onReconnect: ConnectorsDestinationProps["onReconnect"];
  readonly accessPort: ConnectorsDestinationProps["accessPort"];
  readonly overrides: Readonly<Record<string, ConnectorAccessMode>>;
  readonly onAccessModeChange: (
    id: ConnectorId,
    mode: ConnectorAccessMode,
  ) => void;
  readonly renderIcon: ConnectorsDestinationProps["renderIcon"];
}

function renderBody(args: BodyArgs): ReactElement {
  const {
    items,
    onRetry,
    onConnect,
    onOpenConnector,
    onReconnect,
    accessPort,
    overrides,
    onAccessModeChange,
    renderIcon,
  } = args;

  if (items === null || items === undefined) {
    return <SkeletonList />;
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
  const connectors = items.data?.connectors ?? [];
  if (connectors.length === 0) {
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
    <RowList
      ariaLabel="Connected tools"
      items={connectors}
      keyFor={(c) => c.id}
      renderRow={(c) => (
        <ConnectorRow
          connector={c}
          accessMode={overrides[c.id] ?? c.access_mode}
          onOpenConnector={onOpenConnector}
          onReconnect={onReconnect}
          onAccessModeChange={
            accessPort !== undefined
              ? (mode) => onAccessModeChange(c.id, mode)
              : undefined
          }
          renderIcon={renderIcon}
        />
      )}
    />
  );
}

// --- One connector row ----------------------------------------------------

interface ConnectorRowProps {
  readonly connector: Connector;
  readonly accessMode?: ConnectorAccessMode;
  readonly onOpenConnector?: (id: ConnectorId) => void;
  readonly onReconnect?: (id: ConnectorId) => void;
  readonly onAccessModeChange?: (mode: ConnectorAccessMode) => void;
  readonly renderIcon?: (slug: ConnectorSlug) => ReactNode;
}

function ConnectorRow({
  connector: c,
  accessMode,
  onOpenConnector,
  onReconnect,
  onAccessModeChange,
  renderIcon,
}: ConnectorRowProps): ReactElement {
  const needsReconnect = c.status === "error" || c.status === "expired";
  const broken = needsReconnect || c.status === "disconnected";

  // PRD-11 D3 — the default identity tile no longer depends on a host binding:
  // AppIcon keyed on the slug, neutral tone, 30px squircle. A host may override
  // via `renderIcon` (e.g. a server-supplied logo_url).
  const icon =
    renderIcon !== undefined ? (
      renderIcon(c.slug)
    ) : (
      <AppIcon name={c.slug} size="tile" tone="neutral" />
    );

  // The design row is `cursor: default` and non-navigable — the TITLE carries
  // the detail affordance, not the row (PRD-11 non-goal note).
  const title =
    onOpenConnector !== undefined ? (
      <button
        type="button"
        style={titleButtonStyle}
        onClick={() => onOpenConnector(c.id)}
        data-testid="connector-open"
      >
        {c.display_name}
      </button>
    ) : (
      c.display_name
    );

  return (
    <Row
      icon={icon}
      iconSize={30}
      subFont="mono"
      title={title}
      sub={
        c.description !== undefined && c.description.length > 0
          ? c.description
          : undefined
      }
      chip={
        broken ? (
          <StatusPill
            status={STATUS_TONE[c.status]}
            label={STATUS_LABEL[c.status]}
          />
        ) : undefined
      }
      meta={
        // The trailing `.lrow__act` cell: the per-connector access segment, and
        // a reconnect affordance for a broken connector. Isolate its clicks /
        // keys from any ancestor row activation.
        <span
          style={actCellStyle}
          data-testid="connector-card-access"
          onClick={(e) => e.stopPropagation()}
          onKeyDown={(e) => e.stopPropagation()}
        >
          {needsReconnect && onReconnect !== undefined ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onReconnect(c.id)}
              data-testid="connector-reconnect"
            >
              Reconnect
            </Button>
          ) : null}
          {accessMode !== undefined ? (
            <AccessModeSegment
              value={accessMode}
              onChange={(mode) => onAccessModeChange?.(mode)}
              ariaLabel={`Access mode for ${c.display_name}`}
            />
          ) : null}
        </span>
      }
      data-connector-id={c.id}
      data-status={c.status}
      data-testid="connector-row"
    />
  );
}

// --- Page lead + policy note (FR-4.25) ------------------------------------

function ToolsLead({
  onOpenApprovalSettings,
}: {
  readonly onOpenApprovalSettings?: () => void;
}): ReactElement {
  return (
    <PageLead data-testid="tools-policy-note">
      {TOOLS_LEAD_COPY}
      {onOpenApprovalSettings !== undefined ? (
        <button
          type="button"
          onClick={onOpenApprovalSettings}
          style={policyLinkStyle}
          data-testid="tools-policy-note-link"
        >
          {TOOLS_POLICY_NOTE_COPY}
        </button>
      ) : (
        <span data-testid="tools-policy-note-copy">
          {TOOLS_POLICY_NOTE_COPY}
        </span>
      )}
      .
    </PageLead>
  );
}

// --- Skeleton --------------------------------------------------------------

function SkeletonList(): ReactElement {
  return (
    <RowList
      ariaLabel="Loading connectors"
      data-testid="connectors-skeleton"
      items={[0, 1, 2, 3, 4, 5]}
      keyFor={(i) => String(i)}
      renderRow={() => (
        <Row
          icon={<span style={skeletonTileStyle} aria-hidden="true" />}
          iconSize={30}
          title={<span style={skeletonBarStyle(120)} aria-hidden="true" />}
          sub={<span style={skeletonBarStyle(180)} aria-hidden="true" />}
          data-testid="connectors-skeleton-row"
        />
      )}
    />
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

const accessErrorStyle: CSSProperties = {
  margin: "0 0 8px",
  padding: "8px 12px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border-strong, #3a3a3d)",
  background: "var(--color-bg-elevated, #18181b)",
  color: "var(--color-text, #ededee)",
  fontSize: "var(--font-size-sm, 13px)",
};

// The design's `.lrow__act` — flex, right-end, gap 9px (copilot.css:1650-1655).
// It sits in <Row>'s `meta` slot, whose wrapper forces the mono time face +
// subtle colour; the design's act cell is plain body/--tx, so counteract the
// wrapper here (the segment's own buttons already own their styles, but the
// cell + group inherit from this span).
const actCellStyle: CSSProperties = {
  flex: "none",
  display: "inline-flex",
  alignItems: "center",
  gap: 9,
  fontFamily: "var(--font-sans)",
  color: "var(--color-text)",
};

const titleButtonStyle: CSSProperties = {
  appearance: "none",
  background: "transparent",
  border: "none",
  padding: 0,
  margin: 0,
  font: "inherit",
  color: "inherit",
  cursor: "pointer",
  textAlign: "left",
};

// Plain accent link, NO underline (design `.pg-lead a`, copilot.css:127-130).
const policyLinkStyle: CSSProperties = {
  background: "transparent",
  border: "none",
  padding: 0,
  margin: 0,
  font: "inherit",
  color: "var(--color-accent, #d97757)",
  textDecoration: "none",
  cursor: "pointer",
};

const skeletonTileStyle: CSSProperties = {
  display: "inline-block",
  width: 30,
  height: 30,
  borderRadius: "var(--radius-md, 8px)",
  background: "var(--color-surface-elevated, #232325)",
};

function skeletonBarStyle(width: number): CSSProperties {
  return {
    display: "inline-block",
    width,
    maxWidth: "100%",
    height: 10,
    borderRadius: 4,
    background: "var(--color-border, #232325)",
  };
}
