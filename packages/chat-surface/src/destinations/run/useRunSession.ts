// useRunSession — the Run cockpit's host hook (PR-3.3).
//
// One hook owns the *live run session* for the Run destination: it resolves
// which run the cockpit should show for a conversation, subscribes to that
// run's event stream through the Transport port, and exposes an append-only,
// referentially-stable event array plus the session lifecycle status. The
// RunDestination (PR-3.5) feeds `events` into `useEventProjector` and renders
// `status` / `error` / the multi-run selector; this hook renders no UI.
//
// Why it lives here (not in ThreadCanvas or the frontend):
//   - chat-surface stays framework-agnostic. All network I/O goes through the
//     Transport port (`request` for run resolution, `subscribeServerSentEvents`
//     for the SSE tail) — never bare `fetch`/`EventSource`. The desktop webview
//     and the browser both satisfy the port, so the cockpit ships once.
//   - The streaming model is cursor-based: a subscription opens at
//     `?after_sequence=N` and, on reconnect, resumes from the highest
//     `sequence_no` already rendered — no replay. `retry()` re-subscribes from
//     that cursor while preserving the last-projected events (FR-3.32).
//
// Two event sources coexist in Phase 3 (documented as convergence risk R4):
// this hook owns the canonical array fed to `ThreadCanvas.events`, while
// `TcSwimlanes` keeps its own incremental subscription for lane liveness. Both
// are keyed off the same `runId`, so their beads stay in parity.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  isRuntimeEventEnvelope,
  type AgentRunStatus,
  type RuntimeApiEventType,
  type RuntimeEventEnvelope,
} from "@0x-copilot/api-types";

import { useTransport } from "../../providers/TransportProvider";

// The backend tags every run event frame with `event: runtime_event`. The web
// SSE reader defaults an omitted `eventName` to "message", which would silently
// match nothing against the real stream — so the name is passed explicitly.
const RUNTIME_EVENT_NAME = "runtime_event";

/**
 * Session lifecycle phase, independent of the run's own {@link AgentRunStatus}.
 * This is what the cockpit switches its per-pane loading / error chrome on
 * (FR-3.33): `connecting` is the "subscribed, no event yet" window that shows
 * "Loading messages…" / "Listening for run events…" before the first bead.
 */
export type RunSessionStatus =
  | "idle" // nothing to show — no run resolved and none is being resolved
  | "resolving" // fetching the conversation's run list to pick a run
  | "connecting" // subscribed to the run stream, no event projected yet
  | "streaming" // at least one event received; live tail
  | "error"; // run stream (or, when no run is selected, resolution) failed

/**
 * A run in the conversation, for the multi-run selector (US-3.9 / FR-3.26).
 * Shapes are parsed tolerantly from the run-list response so the hook does not
 * pin an exact server contract this phase.
 */
export interface RunListItem {
  readonly runId: string;
  readonly goal: string | null;
  readonly status: AgentRunStatus | null;
  readonly startedAt: string | null;
}

export interface UseRunSessionOptions {
  /** Conversation whose runs are resolved and streamed. */
  readonly conversationId: string;
  /**
   * Explicit target run. Wins over auto-resolution and is streamed even if it
   * is not yet present in the fetched run list — this is how the empty→live
   * transition binds to a freshly-created `runId` without a shell remount
   * (FR-3.25).
   */
  readonly runId?: string | null;
  /**
   * Gate the whole session. When `false`, the hook neither resolves nor
   * subscribes and reports `idle` (e.g. Run is not the active destination).
   * Defaults to `true`.
   */
  readonly enabled?: boolean;
}

export interface RunSession {
  readonly conversationId: string;
  /** The active/selected run, or `null` when the conversation has no run. */
  readonly runId: string | null;
  /** All runs resolved for the conversation (for the multi-run selector). */
  readonly runs: readonly RunListItem[];
  /** Session lifecycle status (see {@link RunSessionStatus}). */
  readonly status: RunSessionStatus;
  /**
   * The active run's own status: the latest value derived from stream events,
   * falling back to the run-list entry, or `null` when unknown.
   */
  readonly runStatus: AgentRunStatus | null;
  /** Append-only, referentially-stable event array (grows by new reference). */
  readonly events: readonly RuntimeEventEnvelope[];
  /** Highest `sequence_no` received — the resume cursor. */
  readonly latestSequenceNo: number;
  /** SSE (or, when no run is selected, resolution) failure; `null` otherwise. */
  readonly error: Error | null;
  /** Re-resolve the run list and re-subscribe from the resume cursor. */
  readonly retry: () => void;
  /** Bind the cockpit to a different run from {@link RunSession.runs}. */
  readonly selectRun: (runId: string) => void;
  /**
   * The single run-binding sink (desktop-run-identity §D3). The cockpit's one
   * dispatch calls this with the freshly-created run id so a send — turn 1 or
   * turn N — always binds + streams; passing `null` unbinds. Every binding path
   * (dispatch / selectRun / deep-link / head) funnels through here.
   */
  readonly bindRun: (runId: string | null) => void;
}

const EMPTY_EVENTS: readonly RuntimeEventEnvelope[] = [];
const EMPTY_RUNS: readonly RunListItem[] = [];

/**
 * The conversation "head" projection this hook resolves the active run from
 * (desktop-run-identity §D2). `latest_run_id` is a live/non-terminal run only
 * (null once it completes); `latest_run_id_any_status` survives completion, so a
 * reopened finished conversation still hands us a run id to bind + stream.
 */
interface ConversationHead {
  readonly latest_run_id?: string | null;
  readonly latest_run_id_any_status?: string | null;
}

export function useRunSession(options: UseRunSessionOptions): RunSession {
  const {
    conversationId,
    runId: explicitRunId = null,
    enabled = true,
  } = options;
  const transport = useTransport();

  // ---- run resolution -----------------------------------------------------
  // The active run is a single ``boundRunId``, written ONLY through ``bindRun`` —
  // a fresh dispatch, a manual ``selectRun``, a deep-linked ``runId`` prop, or the
  // server-resolved conversation head. There is NO precedence coalescing in the
  // render path (the old ``selectedRunId ?? explicitRunId ?? autoResolved`` trap):
  // the last bind wins, so a fresh send after a manual selection is never shadowed
  // (desktop-run-identity §D3). ``runs`` backs the multi-run selector; it stays
  // empty until the runs-list endpoint lands (Phase 6) — the dead ``GET /v1/agent/
  // runs`` auto-resolve (that route is POST-only → 405) is gone.
  const [runs] = useState<readonly RunListItem[]>(EMPTY_RUNS);
  const [boundRunId, setBoundRunId] = useState<string | null>(null);
  const [isResolving, setIsResolving] = useState(false);
  const [resolveError, setResolveError] = useState<Error | null>(null);
  const [resolveNonce, setResolveNonce] = useState(0);

  // ---- live stream --------------------------------------------------------
  const [events, setEvents] =
    useState<readonly RuntimeEventEnvelope[]>(EMPTY_EVENTS);
  const [latestSequenceNo, setLatestSequenceNo] = useState(0);
  const [sseError, setSseError] = useState<Error | null>(null);
  const [runStatusFromEvents, setRunStatusFromEvents] =
    useState<AgentRunStatus | null>(null);
  const [connectNonce, setConnectNonce] = useState(0);

  const eventsRef = useRef<readonly RuntimeEventEnvelope[]>(EMPTY_EVENTS);
  const seenSequenceRef = useRef<Set<number>>(new Set());
  const latestSequenceRef = useRef(0);

  // The ONE sink. Every run binding (dispatch / selectRun / deep-link / head)
  // funnels through here, so the render path reads exactly ``boundRunId`` and a
  // new bind always wins over a stale one.
  const bindRun = useCallback((next: string | null): void => {
    setBoundRunId(next);
  }, []);

  const activeRunId = boundRunId;

  // Switching conversation clears the bound run so a stale run is never streamed
  // against the new conversation; the head-resolution effect below then binds this
  // conversation's own head run (if any).
  useEffect(() => {
    setBoundRunId(null);
    setResolveError(null);
  }, [conversationId]);

  // A deep-linked / host-supplied ``runId`` binds directly. Kept as an effect (not
  // render-path precedence) so it funnels through the one ``boundRunId`` sink.
  useEffect(() => {
    if (explicitRunId !== null) {
      setBoundRunId(explicitRunId);
    }
  }, [explicitRunId]);

  // Resolve the conversation's HEAD run from server truth (desktop-run-identity §D2)
  // — ``latest_run_id`` (a live, non-terminal run) else ``latest_run_id_any_status``
  // (survives completion). This replaces the dead ``GET /v1/agent/runs`` auto-resolve.
  // It binds ONLY when nothing is bound yet, so a dispatch or an explicit runId is
  // never clobbered by a late head resolution — and it lets reopening a FINISHED
  // conversation bind + stream its last run (kills the "NO ACTIVE RUN" reopen bug).
  useEffect(() => {
    if (!enabled) {
      return;
    }
    let cancelled = false;
    setIsResolving(true);
    void transport
      .request<ConversationHead>({
        method: "GET",
        path: `/v1/agent/conversations/${conversationId}`,
      })
      .then((conv) => {
        if (cancelled) {
          return;
        }
        const head =
          conv.latest_run_id ?? conv.latest_run_id_any_status ?? null;
        if (head !== null) {
          // Only when nothing has bound since the conversation switched — a
          // dispatch / selection / deep-link always wins over the head.
          setBoundRunId((prev) => prev ?? head);
        }
      })
      .catch((err: unknown) => {
        if (cancelled) {
          return;
        }
        // A never-run conversation returns 200 with a null head (→ idle composer,
        // no error). A genuine failure (network / 5xx / 404) surfaces a NON-blocking
        // retryable error banner while the empty composer stays available below — it
        // never blocks starting a run (retry() re-resolves + binds).
        setResolveError(toError(err));
      })
      .finally(() => {
        if (!cancelled) {
          setIsResolving(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [transport, conversationId, enabled, resolveNonce]);

  // Reset the accumulated stream whenever the active run changes. Keyed on the
  // run id only, so a `retry()` (which bumps `connectNonce`) resumes the same
  // run without discarding already-projected events.
  useEffect(() => {
    eventsRef.current = EMPTY_EVENTS;
    seenSequenceRef.current = new Set();
    latestSequenceRef.current = 0;
    setEvents(EMPTY_EVENTS);
    setLatestSequenceNo(0);
    setSseError(null);
    setRunStatusFromEvents(null);
  }, [activeRunId]);

  // Subscribe to the active run's SSE tail, resuming from the highest received
  // sequence number (`?after_sequence=N`).
  useEffect(() => {
    if (!enabled || activeRunId === null) {
      return;
    }
    setSseError(null);
    const subscription = transport.subscribeServerSentEvents({
      path: `/v1/agent/runs/${activeRunId}/stream`,
      query: { after_sequence: latestSequenceRef.current },
      eventName: RUNTIME_EVENT_NAME,
      onMessage: (raw) => {
        const envelope = parseEnvelope(raw);
        if (envelope === null || envelope.run_id !== activeRunId) {
          return;
        }
        if (seenSequenceRef.current.has(envelope.sequence_no)) {
          return; // dedupe — a resume can redeliver the boundary event
        }
        seenSequenceRef.current.add(envelope.sequence_no);
        const next = [...eventsRef.current, envelope];
        eventsRef.current = next;
        if (envelope.sequence_no > latestSequenceRef.current) {
          latestSequenceRef.current = envelope.sequence_no;
        }
        setEvents(next);
        setLatestSequenceNo(latestSequenceRef.current);
        const derived = runStatusFromEventType(envelope.event_type);
        if (derived !== null) {
          setRunStatusFromEvents(derived);
        }
      },
      onError: (err) => {
        setSseError(err);
      },
    });
    return () => subscription.close();
  }, [transport, activeRunId, enabled, connectNonce]);

  const status = useMemo<RunSessionStatus>(() => {
    if (!enabled) {
      return "idle";
    }
    if (activeRunId !== null) {
      if (sseError !== null) {
        return "error";
      }
      return events.length > 0 ? "streaming" : "connecting";
    }
    if (resolveError !== null) {
      return "error";
    }
    return isResolving ? "resolving" : "idle";
  }, [
    enabled,
    activeRunId,
    sseError,
    events.length,
    resolveError,
    isResolving,
  ]);

  const runStatus = useMemo<AgentRunStatus | null>(() => {
    if (runStatusFromEvents !== null) {
      return runStatusFromEvents;
    }
    const listed = runs.find((run) => run.runId === activeRunId);
    return listed?.status ?? null;
  }, [runStatusFromEvents, runs, activeRunId]);

  const error = sseError ?? (activeRunId === null ? resolveError : null);

  const retry = useCallback(() => {
    setSseError(null);
    setResolveError(null);
    setConnectNonce((nonce) => nonce + 1);
    setResolveNonce((nonce) => nonce + 1);
  }, []);

  // selectRun is the same sink as bindRun (a manual pick is just another bind).
  const selectRun = useCallback((next: string) => {
    setBoundRunId(next);
  }, []);

  return {
    conversationId,
    runId: activeRunId,
    runs,
    status,
    runStatus,
    events,
    latestSequenceNo,
    error,
    retry,
    selectRun,
    bindRun,
  };
}

// --- helpers ---------------------------------------------------------------

function parseEnvelope(raw: string): RuntimeEventEnvelope | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  return isRuntimeEventEnvelope(parsed) ? parsed : null;
}

// Map the run-lifecycle event types onto the run's AgentRunStatus. Non-status
// events (progress, tool, model, …) return null and leave the derived status
// untouched.
function runStatusFromEventType(
  eventType: RuntimeApiEventType,
): AgentRunStatus | null {
  switch (eventType) {
    case "run_queued":
      return "queued";
    case "run_started":
      return "running";
    case "run_cancelling":
      return "cancelling";
    case "run_cancelled":
      return "cancelled";
    case "run_completed":
      return "completed";
    case "run_failed":
    case "run_rejected":
      return "failed";
    case "approval_requested":
      return "waiting_for_approval";
    case "approval_resolved":
      return "running";
    default:
      return null;
  }
}

function toError(value: unknown): Error {
  if (value instanceof Error) {
    return value;
  }
  return new Error(typeof value === "string" ? value : "run resolution failed");
}
