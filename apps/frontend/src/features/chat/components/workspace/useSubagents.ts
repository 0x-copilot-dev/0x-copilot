// PR 3.2 — archive-merge hook for the Agents tab.
//
// Mirrors `useArchivedSources` (PR 3.1): one GET on conversation switch
// to seed the snapshot, then a setter the live event reducer (owned by
// ChatScreen) folds `subagent_started/progress/completed` events into.
// Live writes win on conflict because `applySubagentEvent` upgrades
// state monotonically (running → completed/failed/cancelled).

import type { SubagentEntry } from "@enterprise-search/api-types";
import { useCallback, useEffect, useState } from "react";

import { listSubagents } from "../../../../api/agentApi";
import type { RequestIdentity } from "../../../../api/config";
import {
  emptySubagentMap,
  seedSubagentMap,
  type SubagentSnapshotMap,
} from "../../chatModel/subagentReducer";
import { errorMessage } from "../../../../utils/errors";

export interface SubagentsState {
  subagents: SubagentSnapshotMap;
  setSubagents: (
    next:
      | SubagentSnapshotMap
      | ((current: SubagentSnapshotMap) => SubagentSnapshotMap),
  ) => void;
  loading: boolean;
  error: string | null;
  reseed: (entries: readonly SubagentEntry[]) => void;
}

export function useSubagents(
  conversationId: string | null,
  identity: RequestIdentity | null,
): SubagentsState {
  const [subagents, setSubagents] =
    useState<SubagentSnapshotMap>(emptySubagentMap);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reseed = useCallback((entries: readonly SubagentEntry[]) => {
    setSubagents(seedSubagentMap(entries));
    setError(null);
  }, []);

  useEffect(() => {
    if (conversationId === null || identity === null) {
      setSubagents(emptySubagentMap());
      setError(null);
      return undefined;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    void listSubagents(conversationId, identity)
      .then((response) => {
        if (cancelled) {
          return;
        }
        // Replace, not merge — conversation switch hands us the full
        // archive truth. Live events overlay subsequent state.
        setSubagents(seedSubagentMap(response.subagents));
      })
      .catch((err: unknown) => {
        if (cancelled) {
          return;
        }
        setError(errorMessage(err, "Could not load subagents"));
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [conversationId, identity]);

  return { subagents, setSubagents, loading, error, reseed };
}
