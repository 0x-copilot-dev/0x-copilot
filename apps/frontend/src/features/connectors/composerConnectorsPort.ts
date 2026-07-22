// Web `ComposerConnectorsPort` for the shared inline Tools popover (chat + run).
//
// `ComposerConnectorsPort` is the neutral barrel alias for `FirstRunConnectorsPort`
// — the host-injected MCP surface the `ToolsPopover` reads (web-search toggle +
// Connected rows + 1-click Installable + Custom-MCP). The FTUE built this shape
// for onboarding; chat/run reuse the SAME popover, so they need the same port.
//
// This factory is a superset-thin wrapper over the existing connectors API layer
// (`api/mcpApi`) — the same facade routes `features/connectors/useConnectors.ts`
// (`listServers` / `installFromCatalog` / `addServer` / `authenticate`) and
// `useMcpCatalog.ts` (`listCatalog`) drive. We wrap the typed api module rather
// than the hooks because the port is a plain object of async methods the popover
// calls itself (it owns its own load-once state); wiring it over the api layer
// keeps every read fresh and avoids coupling the popover to a component's hook
// state. Custom-MCP registration reuses the same `POST /v1/mcp/servers` route the
// JSON-config editor posts to (`features/connectors/jsonConfig.ts`).

import type {
  McpCatalogEntry,
  McpOAuthClientConfigRequest,
  McpServer,
} from "@0x-copilot/api-types";
import type { ComposerConnectorsPort } from "@0x-copilot/chat-surface";

import type { RequestIdentity } from "../../api/config";
import {
  createMcpServer,
  installMcpServer,
  listMcpCatalog,
  listMcpServers,
  startMcpAuth,
} from "../../api/mcpApi";

/**
 * Build the web `ComposerConnectorsPort` over `api/mcpApi`. `beginAuth` mirrors
 * `useConnectors.authenticate` — start the OAuth round-trip and full-page-redirect
 * the browser to the returned `auth_url` (the desktop binder opens it externally
 * instead; the surface stays agnostic to how the redirect happens).
 */
export function createComposerConnectorsPort(
  identity: RequestIdentity,
): ComposerConnectorsPort {
  return {
    async listServers(): Promise<readonly McpServer[]> {
      return listMcpServers(identity);
    },
    async listCatalog(): Promise<readonly McpCatalogEntry[]> {
      return (await listMcpCatalog()).entries;
    },
    installFromCatalog(
      slug: string,
      oauthClient?: McpOAuthClientConfigRequest,
    ): Promise<McpServer> {
      return installMcpServer(slug, identity, oauthClient);
    },
    addCustomServer(
      url: string,
      oauthClient?: McpOAuthClientConfigRequest,
    ): Promise<McpServer> {
      return createMcpServer(url, identity, oauthClient);
    },
    async beginAuth(serverId: string): Promise<void> {
      const auth = await startMcpAuth(serverId, identity);
      window.location.href = auth.auth_url;
    },
  };
}
