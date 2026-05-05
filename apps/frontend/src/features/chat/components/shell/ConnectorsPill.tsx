import { AppIcon, classNames } from "@enterprise-search/design-system";
import type { McpServer } from "@enterprise-search/api-types";
import type { ReactElement } from "react";

export interface ActiveConnectorGlyph {
  id: string;
  name: string;
  color?: string;
}

export interface ConnectorsPillProps {
  active: ActiveConnectorGlyph[];
  onOpen: () => void;
  open?: boolean;
  disabled?: boolean;
}

const MAX_GLYPHS = 4;

/**
 * Topbar connectors pill — shows up to 4 app glyphs in a stack, with
 * "+N" when more are active. Clicking opens the per-chat ConnectorPopover
 * (PR 3.4 ships the popover body). The pill itself is a presentational
 * component — the parent owns the open/close state and the popover host.
 */
export function ConnectorsPill({
  active,
  onOpen,
  open,
  disabled,
}: ConnectorsPillProps): ReactElement {
  const visible = active.slice(0, MAX_GLYPHS);
  const overflow = Math.max(0, active.length - MAX_GLYPHS);
  const label =
    active.length === 0
      ? "Connectors — none active for this chat"
      : `Connectors — ${active.length} active for this chat`;
  return (
    <button
      type="button"
      className={classNames(
        "ui-icon-button",
        "ui-icon-button--ghost",
        "atlas-connectors-pill",
      )}
      onClick={onOpen}
      disabled={disabled}
      aria-haspopup="menu"
      aria-expanded={open ?? false}
      aria-label={label}
      data-tooltip="Per-chat connectors"
      data-tooltip-placement="bottom"
    >
      <span className="atlas-connectors-pill__stack" aria-hidden="true">
        {visible.length === 0 ? (
          <span className="atlas-connectors-pill__empty">All paused</span>
        ) : (
          visible.map((connector) => (
            <AppIcon
              key={connector.id}
              name={connector.name}
              color={connector.color}
              size="sm"
              className="atlas-connectors-pill__glyph"
            />
          ))
        )}
        {overflow > 0 ? (
          <span className="atlas-connectors-pill__overflow">+{overflow}</span>
        ) : null}
      </span>
      <span aria-hidden="true" className="atlas-connectors-pill__caret">
        ▾
      </span>
    </button>
  );
}

/**
 * Project a list of MCP servers + per-chat scopes into the small
 * presentational shape the pill renders. `null` scope means paused.
 * Servers that aren't enabled at the workspace level or aren't
 * authenticated never appear in the active set.
 */
export function activeConnectorsFromScopes(
  servers: readonly McpServer[],
  scopes: Record<string, readonly string[] | null> | undefined,
): ActiveConnectorGlyph[] {
  return servers
    .filter((server) => server.enabled && server.auth_state === "authenticated")
    .filter((server) => {
      const override = scopes?.[server.server_id];
      // No override → use server-default availability.
      // Array override → active.
      // null override → paused for this chat.
      return override !== null;
    })
    .map((server) => ({
      id: server.server_id,
      name: server.display_name || server.url,
    }));
}
