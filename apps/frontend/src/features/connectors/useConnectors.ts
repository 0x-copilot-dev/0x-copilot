import type {
  McpOAuthClientConfigRequest,
  McpServer,
} from "@0x-copilot/api-types";
import { useMemo } from "react";
import type { RequestIdentity } from "../../api/config";
import { classifyMcpError } from "../../api/mcpErrors";
import { requireIdentity, useResource } from "../../api/useResource";
import {
  createMcpServer,
  deleteMcpServer,
  installMcpServer,
  listMcpServers,
  skipMcpAuth,
  startMcpAuth,
  updateMcpServer,
} from "../../api/mcpApi";
import { notifyWorkspaceConnectorsChanged } from "./invalidation";

export interface ConnectorState {
  servers: McpServer[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  addServer: (
    url: string,
    oauthClient?: McpOAuthClientConfigRequest,
  ) => Promise<McpServer>;
  installFromCatalog: (
    slug: string,
    oauthClient?: McpOAuthClientConfigRequest,
  ) => Promise<McpServer>;
  removeServer: (serverId: string) => Promise<void>;
  setEnabled: (serverId: string, enabled: boolean) => Promise<void>;
  /**
   * PR 4.4.7 — rename a connector. The backend's
   * ``PATCH /v1/mcp/servers/{id}`` already accepts ``display_name``;
   * the JSON editor and the future visual rename affordance share
   * this hook so they stay in sync. Returns the server with its
   * updated display_name.
   */
  setDisplayName: (serverId: string, displayName: string) => Promise<McpServer>;
  authenticate: (serverId: string) => Promise<void>;
  skipAuth: (serverId: string) => Promise<void>;
}

export function useConnectors(
  identity: RequestIdentity | null,
): ConnectorState {
  const { data, loading, error, refresh } = useResource<McpServer>(
    identity,
    listMcpServers,
    "Could not load connectors",
  );

  const actions = useMemo(
    () => ({
      async addServer(
        url: string,
        oauthClient?: McpOAuthClientConfigRequest,
      ): Promise<McpServer> {
        const server = await createMcpServer(
          url,
          requireIdentity(identity),
          oauthClient,
        );
        await refresh();
        notifyWorkspaceConnectorsChanged();
        return server;
      },
      async installFromCatalog(
        slug: string,
        oauthClient?: McpOAuthClientConfigRequest,
      ): Promise<McpServer> {
        try {
          const server = await installMcpServer(
            slug,
            requireIdentity(identity),
            oauthClient,
          );
          await refresh();
          notifyWorkspaceConnectorsChanged();
          return server;
        } catch (err) {
          throw classifyMcpError({ kind: "slug", slug }, err);
        }
      },
      async removeServer(serverId: string): Promise<void> {
        await deleteMcpServer(serverId, requireIdentity(identity));
        await refresh();
        notifyWorkspaceConnectorsChanged();
      },
      async setEnabled(serverId: string, enabled: boolean): Promise<void> {
        await updateMcpServer(serverId, { enabled }, requireIdentity(identity));
        await refresh();
        notifyWorkspaceConnectorsChanged();
      },
      async setDisplayName(
        serverId: string,
        displayName: string,
      ): Promise<McpServer> {
        const server = await updateMcpServer(
          serverId,
          { display_name: displayName },
          requireIdentity(identity),
        );
        await refresh();
        notifyWorkspaceConnectorsChanged();
        return server;
      },
      async authenticate(serverId: string): Promise<void> {
        try {
          const auth = await startMcpAuth(serverId, requireIdentity(identity));
          window.location.href = auth.auth_url;
        } catch (err) {
          throw classifyMcpError({ kind: "server", serverId }, err);
        }
      },
      async skipAuth(serverId: string): Promise<void> {
        await skipMcpAuth(serverId, requireIdentity(identity));
        await refresh();
        notifyWorkspaceConnectorsChanged();
      },
    }),
    [identity, refresh],
  );

  return { servers: data, loading, error, refresh, ...actions };
}
