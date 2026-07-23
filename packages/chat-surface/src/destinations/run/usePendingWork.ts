// usePendingWork — cross-run pending-work hydration (Generative Surfaces v2, PRD-E2).
//
// The open run's pending cards come live from `projectPendingCards` over the
// SAME `session.events` array (the one-projector invariant). Everything the user
// still has to decide in OTHER runs is not on this stream, so this hook fetches
// the cross-run aggregate `GET /v1/agent/pending-work` through the Transport port
// (substrate rule: no bare `fetch` in the package) and MERGES it with the live
// cards — the SSE-fed open-run cards always win (they are fresher).
//
// The direct precedent is `useSurfacesV2`: a Transport-fed GET that refetches
// when a watermark advances (here `refreshKey`, the open run's last ledger seq),
// coalescing concurrent advances into exactly one follow-up, plus a manual
// `refresh()` (wired to Approvals-tab activation). It fails soft — an HTTP error
// keeps the last data (`status:"error"`), never throws into React, and never
// retries in a storm. No timers, no polling in v0; cross-run PUSH is an E3
// concern (the inbox stream carries only v1 approvals today).

import { useCallback, useEffect, useRef, useState } from "react";

import type {
  PendingAgentRow,
  PendingWorkItem,
  PendingWorkResponse,
} from "@0x-copilot/api-types";
import { isPendingWorkResponse } from "@0x-copilot/api-types";
import type { Transport } from "@0x-copilot/chat-transport";

import type { PendingCard } from "./pendingCardsProjection";

export interface UsePendingWorkResult {
  /** Merged: the live open-run cards + the fetched other-run cards. */
  readonly cards: readonly PendingCard[];
  readonly agents: readonly PendingAgentRow[];
  readonly status: "idle" | "loading" | "ready" | "error";
  readonly refresh: () => void;
}

const NO_AGENTS: readonly PendingAgentRow[] = [];

export function usePendingWork(
  transport: Transport,
  enabled: boolean,
  currentRunId: string | null,
  liveCards: readonly PendingCard[],
  refreshKey: number,
): UsePendingWorkResult {
  const [fetched, setFetched] = useState<PendingWorkResponse | null>(null);
  const [status, setStatus] = useState<UsePendingWorkResult["status"]>("idle");
  const [manualKey, setManualKey] = useState(0);

  // Hook-lifetime refs (survive effect re-runs).
  const mountedRef = useRef(true);
  const inFlightRef = useRef(false);
  const queuedRef = useRef(false);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (!enabled) return;

    const runFetch = (): void => {
      inFlightRef.current = true;
      if (mountedRef.current) setStatus("loading");
      void transport
        .request<PendingWorkResponse>({
          method: "GET",
          path: "/v1/agent/pending-work",
        })
        .then((res) => {
          if (!mountedRef.current) return;
          // Defensive: an unexpected shape keeps the last good data.
          if (isPendingWorkResponse(res)) {
            setFetched(res);
            setStatus("ready");
          } else {
            setStatus("error");
          }
        })
        .catch(() => {
          // Fail soft — keep the last data, no retry storm (the open-run cards
          // still render live; a later refresh point retries). PRD-E2 §4.
          if (mountedRef.current) setStatus("error");
        })
        .finally(() => {
          inFlightRef.current = false;
          // Exactly one coalesced follow-up if a refresh landed mid-flight.
          if (queuedRef.current && mountedRef.current) {
            queuedRef.current = false;
            runFetch();
          }
        });
    };

    if (inFlightRef.current) {
      queuedRef.current = true;
    } else {
      runFetch();
    }
    // `refreshKey` (open-run activity) + `manualKey` (tab activation / refresh())
    // are the only refetch triggers; a run switch changes `currentRunId`.
  }, [transport, enabled, refreshKey, manualKey, currentRunId]);

  const refresh = useCallback(() => {
    setManualKey((k) => k + 1);
  }, []);

  const cards = mergeCards(fetched, currentRunId, liveCards);
  const agents = fetched?.agents ?? NO_AGENTS;

  return { cards, agents, status, refresh };
}

/**
 * Merge the fetched cross-run cards with the live open-run cards. The open run's
 * fetched items are REPLACED by `liveCards` (SSE is fresher); dedupe key is
 * `runId + (gateId ?? stageId)`. Live cards lead (the open run is the focus),
 * then the other runs' fetched cards in server order (newest-first).
 */
function mergeCards(
  fetched: PendingWorkResponse | null,
  currentRunId: string | null,
  liveCards: readonly PendingCard[],
): readonly PendingCard[] {
  const seen = new Set<string>();
  const out: PendingCard[] = [];
  for (const card of liveCards) {
    const key = dedupeKey(card.runId, card.gateId, card.stageId);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(card);
  }
  for (const item of fetched?.items ?? []) {
    if (currentRunId !== null && item.run_id === currentRunId) {
      // Replaced by the live cards above — the open run streams fresher.
      continue;
    }
    const key = dedupeKey(item.run_id, item.gate_id, item.stage_id);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(itemToCard(item));
  }
  return out;
}

function dedupeKey(
  runId: string,
  gateId: string | null,
  stageId: string | null,
): string {
  return `${runId}::${gateId ?? stageId ?? ""}`;
}

function itemToCard(item: PendingWorkItem): PendingCard {
  return {
    itemKind: item.item_kind,
    runId: item.run_id,
    gateId: item.gate_id,
    stageId: item.stage_id,
    surfaceId: item.surface_id,
    title: item.title,
    connector: item.connector,
    ledgerId: item.ledger_id,
    openedSeq: item.opened_sequence_no,
    rowsPending: item.rows_pending,
    rowsTotal: item.rows_total,
  };
}
