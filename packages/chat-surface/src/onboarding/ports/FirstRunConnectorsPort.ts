// FirstRunConnectorsPort — the host-injected MCP connector surface for the
// first-run Tools popover (PRD-P4 §"Connector-aware Tools popover").
//
// chat-surface stays substrate-clean: it never calls `fetch`/IPC/`window`
// directly. The HOST implements this port over its Transport against the
// existing MCP facade routes (NOT rebuilt here):
//   • listServers()          → GET  /v1/mcp/servers            (McpServer[])
//   • listCatalog()          → GET  /v1/mcp/catalog            (McpCatalogEntry[])
//   • installFromCatalog()   → POST /v1/mcp/servers/install    (McpServer)
//   • addCustomServer()      → POST /v1/mcp/servers            (McpServer)
//   • beginAuth(serverId)    → host-owned redirect / external-open of the
//                              OAuth `auth_url` from
//                              POST /v1/mcp/servers/{id}/auth/start
//
// `beginAuth` is intentionally opaque: the web binder full-page-redirects
// (`location.href = auth_url`) while the desktop binder opens the URL in the
// EXTERNAL browser via the main process — the surface only exposes the call
// and never decides how the redirect happens.
//
// Identity is server-derived (the facade overrides org/user); the surface
// never sends identity. First-use *tool* consent stays the run-time
// `mcp_auth_required` HITL card — a "connect" here is workspace-authorize only.

import type {
  McpCatalogEntry,
  McpOAuthClientConfigRequest,
  McpServer,
} from "@0x-copilot/api-types";

export interface FirstRunConnectorsPort {
  /** All of the workspace's MCP servers for the current user. */
  listServers(): Promise<readonly McpServer[]>;
  /** The org-agnostic curated 1-click catalog. */
  listCatalog(): Promise<readonly McpCatalogEntry[]>;
  /**
   * 1-click install of a catalog entry by slug. Keyless install of an entry
   * flagged `requires_pre_registered_client` 422s — the popover routes those
   * to the custom-config form instead of calling this.
   */
  installFromCatalog(
    slug: string,
    oauthClient?: McpOAuthClientConfigRequest,
  ): Promise<McpServer>;
  /** Register a custom (non-catalog) MCP server from a URL. */
  addCustomServer(
    url: string,
    oauthClient?: McpOAuthClientConfigRequest,
  ): Promise<McpServer>;
  /**
   * Kick off OAuth for a freshly-installed server. Resolves once the host has
   * handed control to the redirect/external browser — the surface does not
   * await completion (the OAuth callback lands back in the host, not here).
   */
  beginAuth(serverId: string): Promise<void>;
}
