import type {
  McpOAuthClientConfigRequest,
  McpServer,
} from "@enterprise-search/api-types";
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

export interface ConnectorState {
  servers: McpServer[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  addServer: (
    url: string,
    oauthClient?: McpOAuthClientConfigRequest,
  ) => Promise<void>;
  installFromCatalog: (
    slug: string,
    oauthClient?: McpOAuthClientConfigRequest,
  ) => Promise<McpServer>;
  removeServer: (serverId: string) => Promise<void>;
  setEnabled: (serverId: string, enabled: boolean) => Promise<void>;
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
      ): Promise<void> {
        await createMcpServer(url, requireIdentity(identity), oauthClient);
        await refresh();
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
          return server;
        } catch (err) {
          throw classifyMcpError({ kind: "slug", slug }, err);
        }
      },
      async removeServer(serverId: string): Promise<void> {
        await deleteMcpServer(serverId, requireIdentity(identity));
        await refresh();
      },
      async setEnabled(serverId: string, enabled: boolean): Promise<void> {
        await updateMcpServer(serverId, { enabled }, requireIdentity(identity));
        await refresh();
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
      },
    }),
    [identity, refresh],
  );

  return { servers: data, loading, error, refresh, ...actions };
}
