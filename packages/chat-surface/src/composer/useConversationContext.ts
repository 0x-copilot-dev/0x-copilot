// useConversationContext — the ONE fetch behind the composer's context meter.
//
// It lives in this package rather than in each host binder for the same reason
// `contextPillView.ts` does: `apps/* -> apps/*` is a hard boundary, so a fetch
// written per host is a fetch that drifts per host. Both hosts already provide
// a `Transport` port, and the cockpit already fetches through it
// (`useRunSession`), so there is nothing substrate-specific left to own.
//
// WHY POLLING IS NOT NEEDED, AND WHY THE REFETCH KEY IS THE RUN STATUS.
// Occupancy is measured per model call, but the question the meter answers —
// "can I send this, or do I compact first?" — is asked BETWEEN turns. Refetching
// when the bound run reaches a terminal state gives the meter its answer exactly
// when the user is next in a position to act on it, at one request per turn.
// A `context_occupancy` run event exists in the vocabulary and would make this
// live mid-run, but nothing emits it today (see `contextPillView.ts`).

import { useEffect, useState } from "react";

import type {
  ConversationContextOccupancyResponse,
  ConversationContextResponse,
} from "@0x-copilot/api-types";

import { useTransport } from "../providers/TransportProvider";
import { buildContextPillView, type ContextPillView } from "./contextPillView";

export interface UseConversationContextInput {
  /** `null` disables the hook — a cockpit with no conversation has no window. */
  readonly conversationId: string | null;
  /**
   * Refetch trigger. Pass the bound run's status (or any value that changes
   * once per turn); the meter re-reads whenever it changes, so a turn that just
   * added 20k of tool results is reflected before the next send.
   */
  readonly refetchKey?: string | null;
}

export interface UseConversationContextResult {
  /** `null` until something is measured — pass it straight through, and let the
   *  composer render no meter rather than a zeroed one. */
  readonly view: ContextPillView | null;
}

export function useConversationContext({
  conversationId,
  refetchKey = null,
}: UseConversationContextInput): UseConversationContextResult {
  const transport = useTransport();
  const [view, setView] = useState<ContextPillView | null>(null);

  useEffect(() => {
    if (conversationId === null) {
      setView(null);
      return;
    }
    let cancelled = false;

    // Both endpoints are read-only sub-resources of the same conversation and
    // neither is required: `/context` alone still gives a headroom percent, and
    // `/context/occupancy` alone still gives a decomposition. So they settle
    // INDEPENDENTLY — one 404 (a conversation with no runs, a model absent from
    // pricing) must not blank a meter the other could still draw.
    const contextRequest = transport
      .request<ConversationContextResponse>({
        method: "GET",
        path: `/v1/agent/conversations/${conversationId}/context`,
      })
      .catch(() => null);

    const occupancyRequest = transport
      .request<ConversationContextOccupancyResponse>({
        method: "GET",
        path: `/v1/agent/conversations/${conversationId}/context/occupancy`,
      })
      .catch(() => null);

    void Promise.all([contextRequest, occupancyRequest]).then(
      ([context, occupancy]) => {
        if (cancelled) return;
        setView(buildContextPillView({ context, occupancy }));
      },
    );

    return () => {
      cancelled = true;
    };
    // `refetchKey` is a trigger, not a value this effect reads — it is in the
    // deps precisely so a turn boundary re-runs the fetch.
  }, [transport, conversationId, refetchKey]);

  return { view };
}
