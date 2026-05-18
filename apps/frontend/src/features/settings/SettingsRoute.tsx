// SettingsRoute — P12 host-side data binder for the new settings
// pages (`/settings/notification-defaults`, `/settings/security/webhooks`,
// and the existing `/settings/profile` deep link).
//
// This route DOES NOT replace the existing `SettingsScreen` shell — the
// rail + admin sections + appearance / shortcuts / billing / etc. all
// stay there. Instead the P12 sub-PRD §7.4 surfaces (notification
// defaults + workspace defaults + webhook security) get a dedicated
// renderer here. `SettingsGateway` (below) picks which panel to render
// based on the active sub-path.

import type { ReactElement } from "react";

import type { RequestIdentity } from "../../api/config";
import { NotificationDefaultsPanel } from "./NotificationDefaultsPanel";
import { WebhookSecurityPanel } from "./WebhookSecurityPanel";

/** P12 settings sub-paths owned by SettingsGateway. */
export type SettingsP12SubPath = "notification-defaults" | "security-webhooks";

interface SettingsRouteProps {
  readonly identity: RequestIdentity;
  readonly isAdmin: boolean;
  readonly subPath: SettingsP12SubPath;
  readonly onBackToChat: () => void;
}

export function SettingsRoute({
  identity,
  isAdmin,
  subPath,
  onBackToChat,
}: SettingsRouteProps): ReactElement {
  return (
    <section
      aria-label="Settings (Phase 12)"
      data-testid="settings-route"
      data-sub-path={subPath}
      style={paneStyle}
    >
      <header style={headerStyle}>
        <button
          type="button"
          data-testid="settings-route-back"
          onClick={onBackToChat}
          style={backButtonStyle}
        >
          ← Back to Atlas
        </button>
      </header>
      {subPath === "notification-defaults" ? (
        <NotificationDefaultsPanel identity={identity} isAdmin={isAdmin} />
      ) : null}
      {subPath === "security-webhooks" ? (
        <WebhookSecurityPanel identity={identity} isAdmin={isAdmin} />
      ) : null}
    </section>
  );
}

const paneStyle = {
  height: "100%",
  padding: 16,
  boxSizing: "border-box",
  overflow: "auto",
  background: "var(--color-bg)",
  color: "var(--color-text)",
} as const;

const headerStyle = {
  marginBottom: 12,
} as const;

const backButtonStyle = {
  background: "transparent",
  border: "none",
  color: "var(--color-accent)",
  cursor: "pointer",
  fontSize: 13,
  padding: 0,
} as const;
