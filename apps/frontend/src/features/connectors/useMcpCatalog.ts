// PR 4.4.6 — MCP catalog hook.
//
// The catalog is org-agnostic and small (~13 entries). Fetched once on
// mount of the consumer (typically the McpOverlay modal). Refresh is
// exposed for the modal's manual Refresh button. Identity is **not**
// required — the endpoint is org-agnostic — but we keep the shape
// aligned with the rest of the data hooks so consumers compose the
// same way in tests.

import type { McpCatalogEntry } from "@0x-copilot/api-types";
import { useCallback } from "react";

import { listMcpCatalog } from "../../api/mcpApi";
import { useRecord } from "../../api/useResource";

export interface CatalogState {
  entries: McpCatalogEntry[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

export function useMcpCatalog(): CatalogState {
  const fetcher = useCallback(
    async () => [...(await listMcpCatalog()).entries],
    [],
  );
  const { data, loading, error, refresh } = useRecord(
    fetcher,
    "Could not load catalog.",
  );
  return { entries: data ?? [], loading, error, refresh };
}
