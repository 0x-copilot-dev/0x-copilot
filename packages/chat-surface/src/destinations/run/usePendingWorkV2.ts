// usePendingWorkV2 — canonical v2.1 pending-work hydration (E1 D6).
//
// The aggregate is identity-authorised by the facade/backend. The client still
// protects its own lifecycle boundary: a run or Transport change invalidates
// prior pages, and late responses are ignored. Requests go exclusively through
// the injected Transport port; an absent rollout endpoint (404/cohort-off) is
// an honest fail-soft state, never a fabricated empty queue.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  isPendingWorkV2Response,
  type PendingWorkV2Response,
} from "@0x-copilot/api-types";
import type { Transport } from "@0x-copilot/chat-transport";

import {
  projectPendingWorkV2,
  type PendingWorkCardV2,
} from "./pendingWorkV2Projection";

export interface UsePendingWorkV2Result {
  readonly cards: readonly PendingWorkCardV2[];
  readonly status: "idle" | "loading" | "ready" | "error";
  /** True when the authorised aggregate deliberately omitted one or more runs. */
  readonly hasOmittedRuns: boolean;
  readonly hasMore: boolean;
  readonly loadMore: () => void;
  readonly refresh: () => void;
}

interface PendingWorkV2State {
  readonly items: readonly PendingWorkV2Response["items"][number][];
  readonly nextCursor: string | null;
  readonly hasMore: boolean;
  readonly hasOmittedRuns: boolean;
}

const EMPTY_ITEMS: readonly PendingWorkV2Response["items"][number][] = [];
const EMPTY_STATE: PendingWorkV2State = {
  items: EMPTY_ITEMS,
  nextCursor: null,
  hasMore: false,
  hasOmittedRuns: false,
};

/**
 * Fetch a bounded, cursor-paginated canonical pending-work queue.
 *
 * `runScopeId` is not sent to the server. It is a local race boundary: changing
 * the active run clears old cards and causes late prior-run responses to be
 * dropped, so Review never targets the wrong current cockpit.
 */
export function usePendingWorkV2(
  transport: Transport,
  enabled: boolean,
  runScopeId: string | null,
  refreshKey: number,
): UsePendingWorkV2Result {
  const [state, setState] = useState<PendingWorkV2State>(EMPTY_STATE);
  const [status, setStatus] =
    useState<UsePendingWorkV2Result["status"]>("idle");
  const [manualRefresh, setManualRefresh] = useState(0);
  const [queuedRefresh, setQueuedRefresh] = useState(0);

  const mountedRef = useRef(true);
  const inFlightRef = useRef(false);
  const refreshQueuedRef = useRef(false);
  const generationRef = useRef(0);
  const runScopeRef = useRef<string | null>(runScopeId);
  const transportRef = useRef<Transport>(transport);
  const enabledRef = useRef(enabled);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const requestPage = useCallback(
    (cursor: string | null, append: boolean): void => {
      if (!enabled || runScopeRef.current === null || inFlightRef.current) {
        return;
      }
      const generation = generationRef.current;
      const expectedScope = runScopeRef.current;
      const expectedTransport = transportRef.current;
      inFlightRef.current = true;
      if (mountedRef.current) setStatus("loading");

      void transport
        .request<unknown>({
          method: "GET",
          path: "/v1/agent/pending-work-v2",
          ...(cursor === null ? {} : { query: { cursor } }),
        })
        .then((response) => {
          if (
            !mountedRef.current ||
            generation !== generationRef.current ||
            expectedScope !== runScopeRef.current ||
            expectedTransport !== transportRef.current
          ) {
            return;
          }
          if (!isPendingWorkV2Response(response)) {
            setStatus("error");
            return;
          }
          setState((previous) => {
            const items = append
              ? [...previous.items, ...response.items]
              : [...response.items];
            return {
              items,
              // A malformed impossible combination must not expose a dead
              // "load more" control. The strict response guard still owns the
              // envelope validation; this is presentation conservatism.
              nextCursor: response.has_more ? response.next_cursor : null,
              hasMore: response.has_more && response.next_cursor !== null,
              // Omission markers contain only an opaque run id. Keep the
              // information as a boolean so the UI can be honest without
              // exposing that id or implying that this page is exhaustive.
              hasOmittedRuns:
                (append && previous.hasOmittedRuns) ||
                response.warnings.length > 0,
            };
          });
          setStatus("ready");
        })
        .catch(() => {
          if (
            mountedRef.current &&
            generation === generationRef.current &&
            expectedScope === runScopeRef.current &&
            expectedTransport === transportRef.current
          ) {
            // Keep the last verified page(s). A 404 during staged rollout is
            // deliberately distinguishable from an empty queue by the caller.
            setStatus("error");
          }
        })
        .finally(() => {
          inFlightRef.current = false;
          if (mountedRef.current && refreshQueuedRef.current) {
            // A scope/Transport change increments the generation while the old
            // request is still in flight. Its response is discarded above, but
            // the queued *current* scope must still get its own request once
            // this global single-flight slot is released.
            refreshQueuedRef.current = false;
            setQueuedRefresh((value) => value + 1);
          }
        });
    },
    [enabled, transport],
  );

  useEffect(() => {
    const scopeChanged =
      runScopeRef.current !== runScopeId || transportRef.current !== transport;
    const becameDisabled = enabledRef.current && !enabled;
    runScopeRef.current = runScopeId;
    transportRef.current = transport;
    enabledRef.current = enabled;

    if (scopeChanged || becameDisabled) {
      generationRef.current += 1;
      refreshQueuedRef.current = false;
      setState(EMPTY_STATE);
      setStatus("idle");
    }
    if (!enabled || runScopeId === null) return;
    if (inFlightRef.current) {
      // Collapses a stream of ledger refreshes into exactly one newest request.
      refreshQueuedRef.current = true;
      return;
    }
    requestPage(null, false);
  }, [
    enabled,
    manualRefresh,
    queuedRefresh,
    refreshKey,
    requestPage,
    runScopeId,
    transport,
  ]);

  const loadMore = useCallback(() => {
    if (!state.hasMore || state.nextCursor === null || inFlightRef.current) {
      return;
    }
    requestPage(state.nextCursor, true);
  }, [requestPage, state.hasMore, state.nextCursor]);

  const refresh = useCallback(() => {
    setManualRefresh((value) => value + 1);
  }, []);

  const cards = useMemo(() => projectPendingWorkV2(state.items), [state.items]);
  return {
    cards,
    status,
    hasOmittedRuns: state.hasOmittedRuns,
    hasMore: state.hasMore,
    loadMore,
    refresh,
  };
}
