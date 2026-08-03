// usePendingApprovalCount — how much work is parked ACROSS conversations.
//
// This number used to live in the Run cockpit's header as a "N waiting" chip,
// beside a posture chip, inside a surface scoped to ONE conversation. That was
// the wrong home twice over: it read as a sentence about the run on screen
// ("Writes wait for you · 2 waiting") when every item could be parked in some
// other chat, and it forced the Approvals panel to stack a cross-run list above
// a "nothing pending in this conversation" empty state to explain itself.
//
// A global count belongs on the nav rail, where a global number is what a badge
// means everywhere else in the product. It rides the `chats` destination — the
// one that owns "your other conversations" — so clicking through lands you where
// the parked work actually is.
//
// Modelled on `useActiveRunCount` deliberately: same Transport-port read, same
// signal-driven revalidation, same error posture. A second, differently-behaved
// badge hook would drift from the first one within a release.

import { UnauthorizedError } from "@0x-copilot/chat-transport";
import { useCallback, useEffect, useRef, useState } from "react";

import { usePresenceSignal } from "../providers/PresenceSignalProvider";
import { useTransport } from "../providers/TransportProvider";

import { useRunActivityBus } from "./runActivityBus";

// The v1 route, deliberately. `/v1/agent/pending-work-v2` is gated behind the
// ai-backend's workspace-enforcement flag and 404s with it off — which is the
// default, so a hook pointed there returns 0 forever and the badge never
// appears. Verified live against the running app: v2 → HTTP 404, v1 → the
// queue. `usePendingWork` (which fed the cross-run cards this badge replaces)
// reads the same route.
const PENDING_WORK_PATH = "/v1/agent/pending-work";
const BUS_DEBOUNCE_MS = 250;
const VISIBLE_POLL_MS = 30_000;

/**
 * The one field this badge needs off `GET /v1/agent/pending-work`.
 *
 * The route answers `{ v, items[], agents[] }`; `items` is the pending queue.
 * `total` is honoured first in case a later revision adds it, so a paged
 * response would under-count rather than over-count — the right way round for a
 * badge, which may say "at least this much is waiting" but never more.
 */
interface PendingWorkCountBody {
  readonly total?: number;
  readonly items?: readonly unknown[];
}

function readCount(response: unknown): number {
  if (typeof response !== "object" || response === null) return 0;
  const body = response as PendingWorkCountBody;
  if (typeof body.total === "number" && Number.isFinite(body.total)) {
    return Math.max(0, Math.trunc(body.total));
  }
  return Array.isArray(body.items) ? body.items.length : 0;
}

export function usePendingApprovalCount(): number {
  const transport = useTransport();
  const presence = usePresenceSignal();
  const bus = useRunActivityBus();
  const [count, setCount] = useState(0);
  // "Latest wins": a slow in-flight fetch must not clobber a fresher one.
  const requestIdRef = useRef(0);

  const revalidate = useCallback(() => {
    const requestId = ++requestIdRef.current;
    void transport
      .request<unknown>({ method: "GET", path: PENDING_WORK_PATH })
      .then((response) => {
        if (requestId !== requestIdRef.current) return; // superseded
        setCount(readCount(response));
      })
      .catch((error: unknown) => {
        if (requestId !== requestIdRef.current) return; // superseded
        // An expired session must DARKEN the badge, not freeze it lit forever.
        // Any other error keeps the last known value: a network blip must not
        // blank a real count.
        if (error instanceof UnauthorizedError) setCount(0);
      });
  }, [transport]);

  useEffect(() => {
    revalidate();
  }, [revalidate]);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const unsubscribe = bus.subscribe(() => {
      if (timer !== null) clearTimeout(timer);
      timer = setTimeout(revalidate, BUS_DEBOUNCE_MS);
    });
    return () => {
      if (timer !== null) clearTimeout(timer);
      unsubscribe();
    };
  }, [bus, revalidate]);

  useEffect(() => {
    let interval: ReturnType<typeof setInterval> | null = null;
    const startPoll = (): void => {
      if (interval === null) {
        interval = setInterval(revalidate, VISIBLE_POLL_MS);
      }
    };
    const stopPoll = (): void => {
      if (interval !== null) {
        clearInterval(interval);
        interval = null;
      }
    };
    if (presence.current() === "visible") {
      startPoll();
    }
    const unsubscribe = presence.subscribe((state) => {
      if (state === "visible") {
        revalidate();
        startPoll();
      } else {
        stopPoll();
      }
    });
    return () => {
      stopPoll();
      unsubscribe();
    };
  }, [presence, revalidate]);

  return count;
}
