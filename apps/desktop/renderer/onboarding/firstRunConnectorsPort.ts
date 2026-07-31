// Desktop `FirstRunConnectorsPort` — the MCP connector surface for the first-run
// Tools popover over the Transport (PRD-P4 §"Connector-aware Tools popover").
//
// chat-surface stays substrate-clean (it never calls IPC/fetch); this host
// implementation performs the MCP-facade calls the port contract describes
// (NOT rebuilt — the existing `/v1/mcp/*` routes):
//   • listServers()        → GET  /v1/mcp/servers                 (McpServerListResponse)
//   • listCatalog()        → GET  /v1/mcp/catalog                 (McpCatalogResponse)
//   • installFromCatalog() → POST /v1/mcp/servers/install         (McpServer)
//   • beginAuth(serverId)  → POST /v1/mcp/servers/{id}/auth/start (McpAuthStartResponse)
//
// Identity is server-derived (the facade injects org/user from the bearer), so —
// like the desktop `FirstRunRunsPort` — install/create bodies carry NO identity.
//
// `beginAuth` prepares the OAuth flow server-side and returns the `auth_url`.
// On desktop the renderer CANNOT open an external URL (main denies
// `window.open` and exposes no generic `openExternal` channel), so the FTUE
// binder's featured 1-click connect instead routes through the main-brokered
// `CONNECTOR_CHANNELS.connect` (system browser). This port method stays wired to
// the real endpoint for the popover's own catalog install → authorize path.

import type { Transport } from "@0x-copilot/chat-transport";
import type { FirstRunConnectorsPort } from "@0x-copilot/chat-surface";
import type {
  McpAuthStartResponse,
  McpCatalogEntry,
  McpCatalogResponse,
  McpOAuthClientConfigRequest,
  McpServer,
  McpServerListResponse,
} from "@0x-copilot/api-types";

/**
 * Build the desktop `FirstRunConnectorsPort` bound to a Transport. All reads
 * degrade to an empty list on a null/absent response so the popover shows its
 * empty state rather than throwing.
 */
export function createFirstRunConnectorsPort(
  transport: Transport,
): FirstRunConnectorsPort {
  return {
    async listServers(): Promise<readonly McpServer[]> {
      const res = await transport.request<McpServerListResponse | null>({
        method: "GET",
        path: "/v1/mcp/servers",
      });
      return res?.servers ?? [];
    },

    async listCatalog(): Promise<readonly McpCatalogEntry[]> {
      const res = await transport.request<McpCatalogResponse | null>({
        method: "GET",
        path: "/v1/mcp/catalog",
      });
      return res?.entries ?? [];
    },

    async installFromCatalog(
      slug: string,
      oauthClient?: McpOAuthClientConfigRequest,
    ): Promise<McpServer> {
      const body: Record<string, unknown> = { slug };
      if (oauthClient !== undefined) {
        body.oauth_client = oauthClient;
      }
      return transport.request<McpServer>({
        method: "POST",
        path: "/v1/mcp/servers/install",
        body,
      });
    },

    async deleteServer(serverId: string): Promise<void> {
      await transport.request<null>({
        method: "DELETE",
        path: `/v1/mcp/servers/${encodeURIComponent(serverId)}`,
      });
    },

    async beginAuth(serverId: string): Promise<void> {
      // Prepare the OAuth flow server-side (returns `auth_url`). The renderer
      // cannot open the external browser directly on desktop; the featured
      // connect path routes through main instead (see the binder).
      await transport.request<McpAuthStartResponse>({
        method: "POST",
        path: `/v1/mcp/servers/${encodeURIComponent(serverId)}/auth/start`,
        body: {},
      });
    },
  };
}
