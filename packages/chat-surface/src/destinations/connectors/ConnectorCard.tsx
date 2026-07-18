// <ConnectorCard> — one card in the Connectors destination grid.
//
// Source: connectors-prd §7.2 — "icon + name + status pill + last-sync".
// Pure presentation; all actions are callbacks. The host wires
// click → router.navigate(...) for "open detail" — the card itself
// does not own routing (cross-audit §1.1).
//
// Status tone derivation maps `ConnectorStatus` to <StatusPill> tones:
//
//   connected     → "ok"      ("Connected")
//   error         → "error"   ("Error")
//   expired       → "warning" ("Needs re-auth")
//   disconnected  → "muted"   ("Disconnected")
//
// `lastSyncIso` is rendered via `formatRelativeTime` so the same
// vocabulary as Home / Inbox / Library shows on every card.

import type {
  CSSProperties,
  KeyboardEvent as ReactKeyboardEvent,
  ReactElement,
  ReactNode,
} from "react";

import type {
  ConnectorAccessMode,
  ConnectorStatus,
} from "@0x-copilot/api-types";

import { StatusPill, type StatusTone } from "../../shell/StatusPill";
import { formatRelativeTime } from "../../util/time";

import { AccessModeSegment } from "./AccessModeSegment";

export interface ConnectorCardProps {
  /** Stable identity (used in test ids + as the React key by the host). */
  readonly id: string;
  readonly displayName: string;
  readonly description?: string;
  readonly status: ConnectorStatus;
  /** ISO-8601; `null` = never synced. */
  readonly lastSyncIso: string | null;
  /** Pre-rendered icon (host decides icon source — slug-based glyph,
   *  remote logo, letter fallback). */
  readonly icon?: ReactNode;
  /** Right-aligned secondary action button content (e.g. "Reconnect").
   *  Host owns the click handler; the card stops propagation so the
   *  card-level click doesn't fire. */
  readonly action?: {
    readonly label: string;
    readonly onClick: () => void;
  };
  /** Card click — host wires this to open the detail route. Keyboard
   *  Enter / Space also activate. */
  readonly onClick?: () => void;
  /** Reference instant — test seam for relative-time formatting. */
  readonly now?: number;
  /**
   * Current per-connector access mode. When provided, the card renders the
   * 3-way `AccessModeSegment` (Read / Read & act / Off) — FR-4.21. Absent =
   * no segment (e.g. non-connected contexts). The destination defaults an
   * omitted wire `access_mode` to least privilege (`off`) before passing it.
   */
  readonly accessMode?: ConnectorAccessMode;
  /**
   * Fired when the user picks a new access mode. The card owns nothing but
   * the click → callback wiring; the destination maps this to
   * `onSetAccessMode(id, mode)` and the host persists it (FR-4.22).
   */
  readonly onAccessModeChange?: (mode: ConnectorAccessMode) => void;
}

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

export function ConnectorCard({
  id,
  displayName,
  description,
  status,
  lastSyncIso,
  icon,
  action,
  onClick,
  now,
  accessMode,
  onAccessModeChange,
}: ConnectorCardProps): ReactElement {
  const handleClick = (): void => {
    if (onClick !== undefined) onClick();
  };
  const handleKey = (e: ReactKeyboardEvent<HTMLDivElement>): void => {
    if (onClick === undefined) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onClick();
    }
  };
  const lastSyncLabel =
    lastSyncIso === null
      ? "Never synced"
      : `Last sync ${formatRelativeTime(lastSyncIso, now)}`;

  return (
    <div
      role="listitem"
      tabIndex={onClick !== undefined ? 0 : -1}
      data-testid="connector-card"
      data-connector-id={id}
      data-status={status}
      onClick={onClick !== undefined ? handleClick : undefined}
      onKeyDown={onClick !== undefined ? handleKey : undefined}
      style={cardStyle}
      aria-label={`${displayName} — ${STATUS_LABEL[status]}`}
    >
      <div style={headerRowStyle}>
        {icon !== undefined ? (
          <span style={iconStyle} aria-hidden="true">
            {icon}
          </span>
        ) : null}
        <h3 style={titleStyle} data-testid="connector-card-name">
          {displayName}
        </h3>
        <StatusPill status={STATUS_TONE[status]} label={STATUS_LABEL[status]} />
      </div>
      {description !== undefined && description.length > 0 ? (
        <p style={descriptionStyle} data-testid="connector-card-description">
          {description}
        </p>
      ) : null}
      {accessMode !== undefined ? (
        // The segment is interactive inside a clickable card — stop clicks /
        // keys from bubbling to the card-level open handler.
        <div
          style={accessRowStyle}
          data-testid="connector-card-access"
          onClick={(e) => e.stopPropagation()}
          onKeyDown={(e) => e.stopPropagation()}
        >
          <span style={accessLabelStyle}>Agent access</span>
          <AccessModeSegment
            value={accessMode}
            onChange={(mode) => onAccessModeChange?.(mode)}
            ariaLabel={`Access mode for ${displayName}`}
          />
        </div>
      ) : null}
      <div style={footerRowStyle}>
        <span data-testid="connector-card-last-sync">{lastSyncLabel}</span>
        {action !== undefined ? (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              action.onClick();
            }}
            style={actionButtonStyle}
            data-testid="connector-card-action"
          >
            {action.label}
          </button>
        ) : null}
      </div>
    </div>
  );
}

// === Styles ============================================================

const cardStyle: CSSProperties = {
  padding: 14,
  background: "var(--color-bg-elevated, #18181b)",
  border: "1px solid var(--color-border, #232325)",
  borderRadius: "var(--radius-md, 12px)",
  display: "flex",
  flexDirection: "column",
  gap: 8,
  cursor: "pointer",
  minHeight: 132,
};

const headerRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 10,
};

const iconStyle: CSSProperties = {
  display: "inline-flex",
  flexShrink: 0,
};

const titleStyle: CSSProperties = {
  fontSize: "var(--font-size-md, 14px)",
  fontWeight: 600,
  color: "var(--color-text, #ededee)",
  margin: 0,
  flex: 1,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const descriptionStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-sm, 13px)",
  color: "var(--color-text-muted, #b4b4b8)",
  display: "-webkit-box",
  WebkitLineClamp: 2,
  WebkitBoxOrient: "vertical",
  overflow: "hidden",
};

const accessRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 10,
  flexWrap: "wrap",
};

const accessLabelStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: 500,
  color: "var(--color-text-subtle, #7e7e84)",
};

const footerRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-subtle, #7e7e84)",
  marginTop: "auto",
};

const actionButtonStyle: CSSProperties = {
  height: 26,
  padding: "0 10px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border-strong, #2a2a2c)",
  background: "transparent",
  color: "var(--color-accent, #d97757)",
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: 600,
  cursor: "pointer",
};
