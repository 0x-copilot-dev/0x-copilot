import type {
  McpOAuthClientConfigRequest,
  McpServer,
} from "@enterprise-search/api-types";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { RequestIdentity } from "../../api/config";
import {
  createMcpServer,
  deleteMcpServer,
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
  removeServer: (serverId: string) => Promise<void>;
  setEnabled: (serverId: string, enabled: boolean) => Promise<void>;
  authenticate: (serverId: string) => Promise<void>;
  skipAuth: (serverId: string) => Promise<void>;
}

export function useConnectors(
  identity: RequestIdentity | null,
): ConnectorState {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (identity === null) {
      setLoading(false);
      return;
    }
    try {
      setLoading(true);
      setServers(await listMcpServers(identity));
      setError(null);
    } catch (err) {
      setError(errorMessage(err, "Could not load connectors"));
    } finally {
      setLoading(false);
    }
  }, [identity]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const actions = useMemo(
    () => ({
      refresh,
      async addServer(
        url: string,
        oauthClient?: McpOAuthClientConfigRequest,
      ): Promise<void> {
        const currentIdentity = requireIdentity(identity);
        await createMcpServer(url, currentIdentity, oauthClient);
        await refresh();
      },
      async removeServer(serverId: string): Promise<void> {
        const currentIdentity = requireIdentity(identity);
        await deleteMcpServer(serverId, currentIdentity);
        await refresh();
      },
      async setEnabled(serverId: string, enabled: boolean): Promise<void> {
        const currentIdentity = requireIdentity(identity);
        await updateMcpServer(serverId, { enabled }, currentIdentity);
        await refresh();
      },
      async authenticate(serverId: string): Promise<void> {
        const currentIdentity = requireIdentity(identity);
        const auth = await startMcpAuth(serverId, currentIdentity);
        window.location.href = auth.auth_url;
      },
      async skipAuth(serverId: string): Promise<void> {
        const currentIdentity = requireIdentity(identity);
        await skipMcpAuth(serverId, currentIdentity);
        await refresh();
      },
    }),
    [identity, refresh],
  );

  return {
    servers,
    loading,
    error,
    ...actions,
  };
}

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

function requireIdentity(identity: RequestIdentity | null): RequestIdentity {
  if (identity === null) {
    throw new Error("Session identity is not loaded.");
  }
  return identity;
}
