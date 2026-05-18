// <WebhookCard /> — one card in the Webhooks sub-route list.
//
// Source: connectors-prd §7.3 (webhooks sub-destination) + §3.1 wire
// shape. Carries url + status pill + last fire + next rotation. Pure
// presentation; the host wires the click → router.navigate({kind:
// "webhook", id}) deep-link (or whatever ItemRef registry the host
// uses).

import type {
  CSSProperties,
  KeyboardEvent as ReactKeyboardEvent,
  ReactElement,
} from "react";

import type { Webhook, WebhookStatus } from "@enterprise-search/api-types";

import { StatusPill, type StatusTone } from "../../../shell/StatusPill";
import { formatRelativeTime } from "../../../util/time";

const STATUS_TONE: Readonly<Record<WebhookStatus, StatusTone>> = {
  active: "ok",
  paused: "muted",
};

const STATUS_LABEL: Readonly<Record<WebhookStatus, string>> = {
  active: "Active",
  paused: "Paused",
};

export interface WebhookCardProps {
  readonly webhook: Webhook;
  /** Card click — host wires this to open the detail route. */
  readonly onClick?: () => void;
  /** Reference instant — test seam for relative-time formatting. */
  readonly now?: number;
}

export function WebhookCard(props: WebhookCardProps): ReactElement {
  const { webhook, onClick, now } = props;

  const handleKey = (e: ReactKeyboardEvent<HTMLDivElement>): void => {
    if (onClick === undefined) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onClick();
    }
  };

  const lastFireLabel =
    webhook.last_fire_at === null
      ? "Never fired"
      : `Last fire ${formatRelativeTime(webhook.last_fire_at, now)}`;

  const rotatesAtLabel =
    webhook.rotates_at === null
      ? "Manual rotation"
      : `Rotates ${formatRelativeTime(webhook.rotates_at, now)}`;

  return (
    <div
      role="listitem"
      tabIndex={onClick !== undefined ? 0 : -1}
      data-testid="webhook-card"
      data-webhook-id={webhook.id}
      data-status={webhook.status}
      onClick={onClick}
      onKeyDown={onClick !== undefined ? handleKey : undefined}
      style={cardStyle}
      aria-label={`${webhook.url} — ${STATUS_LABEL[webhook.status]}`}
    >
      <div style={headerRowStyle}>
        <code style={urlStyle} data-testid="webhook-card-url">
          {webhook.url}
        </code>
        <StatusPill
          status={STATUS_TONE[webhook.status]}
          label={STATUS_LABEL[webhook.status]}
        />
      </div>
      <div style={metaRowStyle}>
        <span data-testid="webhook-card-last-fire">{lastFireLabel}</span>
        <span data-testid="webhook-card-rotates-at">{rotatesAtLabel}</span>
      </div>
      {webhook.last_status_code !== undefined ? (
        <div style={footerStyle}>
          <span data-testid="webhook-card-status-code">
            HTTP {webhook.last_status_code}
          </span>
        </div>
      ) : null}
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
  minHeight: 96,
};

const headerRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 10,
  justifyContent: "space-between",
};

const urlStyle: CSSProperties = {
  fontFamily:
    "var(--font-family-mono, ui-monospace, SFMono-Regular, monospace)",
  fontSize: "var(--font-size-sm, 13px)",
  color: "var(--color-text, #ededee)",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  flex: 1,
  minWidth: 0,
};

const metaRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-subtle, #7e7e84)",
  flexWrap: "wrap",
  gap: 8,
};

const footerStyle: CSSProperties = {
  display: "flex",
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-muted, #b4b4b8)",
};
