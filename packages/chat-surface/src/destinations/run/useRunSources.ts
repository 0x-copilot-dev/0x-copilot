// useRunSources — the Run cockpit's Sources tab, as a projection.
//
// The Sources tab was permanently empty: RunDestination mounted the rail without
// a `sources` prop, so it fell back to EMPTY_SOURCES — even though the backend
// ingests citations and the legacy web chat shows them. This binder closes that
// gap the same way `useRunTranscript` closed the streaming gap.
//
// TWO SOURCES (sources are conversation-scoped, the run stream is run-scoped):
//   - the persisted seed (GET /sources) owns every doc cited in prior turns;
//   - the live fold (applySourceEvent over the active run's `source_ingested` /
//     `sources_ingested` events) owns docs the running run is citing now.
// History re-seeds on run boundary + terminal (the run's docs are now
// persisted); the live overlay drops once the run settles so a doc cited during
// the run isn't counted twice.

import { useEffect, useMemo, useState } from "react";

import type {
  AgentRunStatus,
  RuntimeEventEnvelope,
  SourceListResponse,
} from "@0x-copilot/api-types";

import { useTransport } from "../../providers/TransportProvider";
import {
  applySourceEvent,
  emptySourceMap,
  seedSourceMap,
  type SourceEntryMap,
} from "../../workspace/workspaceHelpers";

// Non-terminal run states; anything else means the run's citations are
// persisted. Mirrors useRunTranscript / useRunSession.
const ACTIVE_RUN_STATUSES: ReadonlySet<AgentRunStatus> = new Set([
  "queued",
  "running",
  "waiting_for_approval",
  "cancelling",
]);

export interface UseRunSourcesOptions {
  readonly conversationId: string;
  readonly runId: string | null;
  readonly runStatus: AgentRunStatus | null;
  readonly events: readonly RuntimeEventEnvelope[];
}

export interface UseRunSourcesResult {
  readonly sources: SourceEntryMap;
  readonly loading: boolean;
  readonly error: string | null;
}

export function useRunSources(
  options: UseRunSourcesOptions,
): UseRunSourcesResult {
  const { conversationId, runId, runStatus, events } = options;
  const transport = useTransport();

  const [seed, setSeed] = useState<SourceEntryMap>(emptySourceMap);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // The runId whose terminal re-seed has landed; while this !== runId the live
  // event fold is applied on top of the seed.
  const [settledRunId, setSettledRunId] = useState<string | null>(null);

  // Seed / re-seed on conversation or run change.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void transport
      .request<SourceListResponse>({
        method: "GET",
        path: `/v1/agent/conversations/${conversationId}/sources`,
      })
      .then((res) => {
        if (cancelled) return;
        setSeed(seedSourceMap(res.sources ?? []));
        setError(null);
      })
      .catch(() => {
        if (!cancelled) setError("Couldn't load sources.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [transport, conversationId, runId]);

  // On terminal, re-seed so the run's now-persisted citations enter the map,
  // then settle so the live fold drops (it would otherwise double-count them).
  useEffect(() => {
    if (
      runId === null ||
      runStatus === null ||
      ACTIVE_RUN_STATUSES.has(runStatus)
    ) {
      return;
    }
    let cancelled = false;
    void transport
      .request<SourceListResponse>({
        method: "GET",
        path: `/v1/agent/conversations/${conversationId}/sources`,
      })
      .then((res) => {
        if (cancelled) return;
        setSeed(seedSourceMap(res.sources ?? []));
        setSettledRunId(runId);
      })
      .catch(() => {
        // Leave the live fold in place if the settle fetch fails.
      });
    return () => {
      cancelled = true;
    };
  }, [transport, conversationId, runId, runStatus]);

  const sources = useMemo<SourceEntryMap>(() => {
    const overlay = runId !== null && settledRunId !== runId;
    return overlay ? events.reduce(applySourceEvent, seed) : seed;
  }, [seed, events, runId, settledRunId]);

  return { sources, loading, error };
}
