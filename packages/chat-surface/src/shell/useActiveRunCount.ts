// useActiveRunCount (PRD-12 D1) — the ONE active-run-count hook.
//
// Reads the server projection `GET /v1/agent/runs/active_count` through the
// `Transport` port (the precedent is `useRunSession`, which also fetches through
// the port inside this package). `ShellGrid` calls it and feeds the number to
// the rail's Run badge — so the count has a single owner both hosts inherit,
// and no host can pass a competing value (the host badge prop is deleted).
//
// Revalidation is SIGNAL-DRIVEN, not a poll:
//   * mount — one fetch.
//   * a `runActivityBus` publish (250ms trailing debounce) — the user's own run
//     starting / finishing / cancelling, the common case; kills the old 30s lag.
//   * a `PresenceSignal` hidden→visible transition — catch up on focus.
//   * a 30s interval, but ONLY while visible — the safety net for runs started
//     on another device / tab. One indexed COUNT, strictly less work than the
//     deleted web hook's 100-conversation page with per-row latest-run lookups.
//
// Error handling is deliberate: an `UnauthorizedError` (expired session) sets
// the count to 0 so the badge goes dark rather than freezing lit forever; any
// other error keeps the last known value (a transient network blip must not
// blank a real count). Today's deleted bare `catch {}` could not tell those apart.

import { UnauthorizedError } from "@0x-copilot/chat-transport";
import { useCallback, useEffect, useRef, useState } from "react";

import { usePresenceSignal } from "../providers/PresenceSignalProvider";
import { useTransport } from "../providers/TransportProvider";

import { useRunActivityBus } from "./runActivityBus";

const ACTIVE_RUN_COUNT_PATH = "/v1/agent/runs/active_count";
const BUS_DEBOUNCE_MS = 250;
const VISIBLE_POLL_MS = 30_000;

// The wire shape of `GET /v1/agent/runs/active_count`. The public contract is
// `ActiveRunCountResponse` in `@0x-copilot/api-types`; this local mirror keeps
// the hook decoupled from that package for a one-field read (chat-surface reads
// through the substrate-agnostic `Transport` port, not typed clients).
interface ActiveRunCountBody {
  readonly active_run_count: number;
}

export function useActiveRunCount(): number {
  const transport = useTransport();
  const presence = usePresenceSignal();
  const bus = useRunActivityBus();
  const [count, setCount] = useState(0);
  // "Latest wins" token: ignore a response from a superseded request so a slow
  // in-flight fetch can't clobber a fresher one (or a 401 from an old fetch
  // can't blank a value a newer success already set).
  const requestIdRef = useRef(0);

  const revalidate = useCallback(() => {
    const requestId = ++requestIdRef.current;
    void transport
      .request<ActiveRunCountBody>({
        method: "GET",
        path: ACTIVE_RUN_COUNT_PATH,
      })
      .then((response) => {
        if (requestId !== requestIdRef.current) return; // superseded
        setCount(Math.max(0, response.active_run_count ?? 0));
      })
      .catch((error: unknown) => {
        if (requestId !== requestIdRef.current) return; // superseded
        if (error instanceof UnauthorizedError) {
          // Session expired: the badge must go dark, not freeze on its last value.
          setCount(0);
        }
        // Any other error: keep the last known count (transient blip).
      });
  }, [transport]);

  // Mount: one fetch.
  useEffect(() => {
    revalidate();
  }, [revalidate]);

  // Bus publishes → one debounced revalidation. A burst of run transitions
  // coalesces to a single round-trip.
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const unsubscribe = bus.subscribe(() => {
      if (timer !== null) clearTimeout(timer);
      timer = setTimeout(() => {
        timer = null;
        revalidate();
      }, BUS_DEBOUNCE_MS);
    });
    return () => {
      if (timer !== null) clearTimeout(timer);
      unsubscribe();
    };
  }, [bus, revalidate]);

  // Presence: poll every 30s ONLY while visible; on hidden→visible, catch up
  // immediately and resume the poll. No interval ever runs while hidden.
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
