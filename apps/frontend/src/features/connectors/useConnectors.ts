import type { McpServer } from "@enterprise-search/api-types";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { RequestIdentity } from "../../api/config";
import { DEFAULT_IDENTITY } from "../../api/config";
import {
  createMcpServer,
  deleteMcpServer,
  listMcpServers,
  skipMcpAuth,
  startMcpAuth,
  updateMcpServer
} from "../../api/mcpApi";

export interface ConnectorState {
  servers: McpServer[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  addServer: (url: string) => Promise<void>;
  removeServer: (serverId: string) => Promise<void>;
  setEnabled: (serverId: string, enabled: boolean) => Promise<void>;
  authenticate: (serverId: string) => Promise<void>;
  skipAuth: (serverId: string) => Promise<void>;
}

export function useConnectors(identity: RequestIdentity = DEFAULT_IDENTITY): ConnectorState {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
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
      async addServer(url: string): Promise<void> {
        await createMcpServer(url, identity);
        await refresh();
      },
      async removeServer(serverId: string): Promise<void> {
        await deleteMcpServer(serverId, identity);
        await refresh();
      },
      async setEnabled(serverId: string, enabled: boolean): Promise<void> {
        await updateMcpServer(serverId, { enabled }, identity);
        await refresh();
      },
      async authenticate(serverId: string): Promise<void> {
        const auth = await startMcpAuth(serverId, identity);
        window.location.href = auth.auth_url;
      },
      async skipAuth(serverId: string): Promise<void> {
        await skipMcpAuth(serverId, identity);
        await refresh();
      }
    }),
    [identity, refresh]
  );

  return {
    servers,
    loading,
    error,
    ...actions
  };
}

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}
