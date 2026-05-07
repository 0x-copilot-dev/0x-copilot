// PR 3.4 — projection from `(McpServer[], conversationScopes, viewer)` into
// the small presentational shape the ConnectorPopover renders. Pure;
// table-tested in isolation.
//
// PR 3.4.1 — projection now consumes server-supplied brand metadata
// (``logo_url`` / ``brand_color`` / ``scopes_summary``) and the
// resume-from-paused payload (``default_scopes``). Resuming a paused
// connector now restores real tool access instead of flipping it on
// with the empty fallback PR 3.4 used.
//
// PR 4.4.6 — the chat-screen popover renders **Connected** only:
// servers where the user has actually authorized access
// (``isAuthenticated(auth_state)``). Catalog availability and
// half-installed-not-yet-OAuthed rows belong in Settings → Manage MCP
// servers, not in the per-chat popover. The four-state vocabulary now
// collapses to two visual states for the chat surface: ``active`` and
// ``paused``. The other two (``disconnected``, ``workspace_off``)
// remain in the type so admin surfaces (Settings detail, future
// audit views) can still consume the projection without re-classifying.

import type {
  ConversationConnectorScopes,
  McpServer,
} from "@enterprise-search/api-types";
import { isAuthenticated } from "./authStateDisplay";

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
   * Server-supplied resume target. `PATCH /…/connectors` with this value
   * flips a paused row back to Active with the connector's full default
   * tool set. Empty array means "loaded with no extra scopes; let server
   * defaults apply" — same wire semantics as before, but now driven by
   * the server (PR 3.4.1) instead of an FE-side empty literal.
   */
  default_scopes: readonly string[];
  /** PR 3.4.1 — brand metadata. */
  logo_url: string | null;
  brand_color: string | null;
  scopes_summary: string | null;
  /** PR 3.4.1 — popover gates the Enable button for non-admin members
   *  when the workspace flagged the connector as admin-managed. */
  admin_managed: boolean;
}

const EMPTY_SCOPES: readonly string[] = Object.freeze([]);

export function projectConnectors(
  servers: readonly McpServer[],
  scopes: ConversationConnectorScopes | undefined,
): ConnectorRow[] {
  return servers.map((server) => {
    const installed = server.enabled === true;
    const authenticated = server.auth_state === "authenticated";
    const override = scopes?.[server.server_id];
    const defaults = server.default_scopes ?? EMPTY_SCOPES;

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
      // override → use server defaults (frozen-at-run-start materializer
      // applies the same set; see runtime_connector_scopes()).
      currentScopes = Array.isArray(override) ? override : defaults;
    }

    return {
      server_id: server.server_id,
      display_name: server.display_name || server.name || server.url,
      state,
      current_scopes: currentScopes,
      default_scopes: defaults,
      logo_url: server.logo_url ?? null,
      brand_color: server.brand_color ?? null,
      scopes_summary: server.scopes_summary ?? null,
      admin_managed: server.admin_managed === true,
    };
  });
}

/** Convenience: count rows that are active in the projection. */
export function activeCount(rows: readonly ConnectorRow[]): number {
  return rows.reduce((n, row) => (row.state === "active" ? n + 1 : n), 0);
}

/**
 * PR 4.4.6 — chat-popover projection.
 *
 * Filter the workspace's full server list to those that are actually
 * "Connected" — installed AND authorized AND not workspace-disabled.
 * Catalog availability (Install/Resume install) lives behind Settings →
 * Manage MCP servers; the per-chat popover only deals with active vs.
 * paused.
 *
 * The base ``projectConnectors`` is kept for surfaces that *do* need
 * the full four-state vocabulary (admin views, future audit panes).
 */
export function projectChatConnectors(
  servers: readonly McpServer[],
  scopes: ConversationConnectorScopes | undefined,
): ConnectorRow[] {
  const filtered = servers.filter(
    (server) => server.enabled === true && isAuthenticated(server.auth_state),
  );
  return projectConnectors(filtered, scopes);
}
