// PR 3.1 — archive seed for the Workspace pane Sources tab.
//
// PR 1.5 ships `GET /v1/agent/conversations/{id}/sources` (one row per
// unique source) plus the live event reducer in
// `chatModel/sourcesReducer.ts`. This hook is the FE bridge: on
// conversation switch it fetches the archive once, seeds the
// SourceEntryMap, and exposes a setter so the live reducer (owned by
// ChatScreen) can fold incoming `source_ingested` events into the same
// map. Live wins on conflict because PR 1.5's mergeIncoming is keyed by
// `(connector, doc_id)` and prefers the newer `last_cited_at`.
//
// Conversation switches cancel the in-flight fetch (StrictMode-safe) so
// stale rows from the previous chat never leak into the new one.

import { useCallback, useEffect, useState } from "react";

import { listSources } from "../../../../api/agentApi";
import type { RequestIdentity } from "../../../../api/config";
import {
  emptySourceMap,
  seedSourceMap,
  type SourceEntryMap,
} from "../../chatModel/sourcesReducer";
import { errorMessage } from "../../../../utils/errors";

export interface ArchivedSourcesState {
  sources: SourceEntryMap;
  loading: boolean;
  error: string | null;
  reload: () => void;
}

export function useArchivedSources(
  conversationId: string | null,
  identity: RequestIdentity | null,
): ArchivedSourcesState {
  const [sources, setSources] = useState<SourceEntryMap>(emptySourceMap);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  const reload = useCallback(() => {
    setReloadToken((token) => token + 1);
  }, []);

  useEffect(() => {
    if (conversationId === null || identity === null) {
      setSources(emptySourceMap());
      setError(null);
      return undefined;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    void listSources(conversationId, identity)
      .then((response) => {
        if (cancelled) {
          return;
        }
        // Replace, not merge: a conversation switch hands us the full
        // archive truth. The live reducer overlays subsequent events.
        setSources(seedSourceMap(response.sources));
      })
      .catch((err: unknown) => {
        if (cancelled) {
          return;
        }
        setError(errorMessage(err, "Could not load sources"));
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [conversationId, identity, reloadToken]);

  return { sources, loading, error, reload };
}
