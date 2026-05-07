// PR 4.4.6 — MCP catalog hook.
//
// The catalog is org-agnostic and small (~13 entries). Fetched once on
// mount of the consumer (typically the McpOverlay modal). Refresh is
// exposed for the modal's manual Refresh button. Identity is **not**
// required — the endpoint is org-agnostic — but we keep the hook on
// the same useResource shape as ``useConnectors`` so it composes the
// same way in tests.

import type { McpCatalogEntry } from "@enterprise-search/api-types";
import { useCallback, useEffect, useState } from "react";
import { listMcpCatalog } from "../../api/mcpApi";

export interface CatalogState {
  entries: McpCatalogEntry[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

export function useMcpCatalog(): CatalogState {
  const [entries, setEntries] = useState<McpCatalogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setLoading(true);
      const response = await listMcpCatalog();
      setEntries([...response.entries]);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load catalog.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { entries, loading, error, refresh };
}
