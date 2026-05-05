// PR 3.4 — projection from `(McpServer[], conversationScopes, viewer)` into
// the small presentational shape the ConnectorPopover renders. Pure;
// table-tested in isolation.
//
// The four-state vocabulary maps the three-layer connector model from the
// design doc (workspace-installed → user-authenticated → active for this
// chat) onto a single attribute the chip + row visuals key off.

import type {
  ConversationConnectorScopes,
  McpServer,
} from "@enterprise-search/api-types";

export type ConnectorRowState =
  | "active" // workspace-installed + user-authenticated + per-chat scope ≠ null
  | "paused" // workspace-installed + user-authenticated + per-chat scope === null
  | "disconnected" // workspace-installed + user NOT authenticated
  | "workspace_off"; // workspace-disabled / not installed

export interface ConnectorRow {
  server_id: string;
  display_name: string;
  /** Visual attribute on `<ConnectorChip data-state>`. */
  state: ConnectorRowState;
  /** Active scopes when `state === "active"`. `null` while paused / not active. */
  current_scopes: readonly string[] | null;
  /**
   * Server-provided default scopes used as the resume target. The MCP
   * server contract doesn't expose per-server defaults today, so we fall
   * back to an empty array — PR 1.2's PATCH semantics treat `[]` as
   * "active with no extra scopes; let server defaults apply." A future
   * PR (4.4 — MCP catalog overhaul) may surface real per-server defaults.
   */
  default_scopes: readonly string[];
}

const RESUME_DEFAULT: readonly string[] = [];

export function projectConnectors(
  servers: readonly McpServer[],
  scopes: ConversationConnectorScopes | undefined,
): ConnectorRow[] {
  return servers.map((server) => {
    const installed = server.enabled === true;
    const authenticated = server.auth_state === "authenticated";
    const override = scopes?.[server.server_id];

    let state: ConnectorRowState;
    let currentScopes: readonly string[] | null;
    if (!installed) {
      state = "workspace_off";
      currentScopes = null;
    } else if (!authenticated) {
      state = "disconnected";
      currentScopes = null;
    } else if (override === null) {
      state = "paused";
      currentScopes = null;
    } else {
      state = "active";
      // Active with explicit override → honour the override; active with no
      // override → empty scopes (server applies its defaults).
      currentScopes = Array.isArray(override) ? override : RESUME_DEFAULT;
    }

    return {
      server_id: server.server_id,
      display_name: server.display_name || server.name || server.url,
      state,
      current_scopes: currentScopes,
      default_scopes: RESUME_DEFAULT,
    };
  });
}

/** Convenience: count rows that are active in the projection. */
export function activeCount(rows: readonly ConnectorRow[]): number {
  return rows.reduce((n, row) => (row.state === "active" ? n + 1 : n), 0);
}
