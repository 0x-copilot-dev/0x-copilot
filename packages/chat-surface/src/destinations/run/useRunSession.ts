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
  AGENT_RUN_STATUSES,
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

/** Non-terminal run states — used to prefer a "live" run when auto-resolving. */
const NON_TERMINAL_STATUSES: readonly AgentRunStatus[] = [
  "queued",
  "running",
  "waiting_for_approval",
  "cancelling",
];

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
}

const EMPTY_EVENTS: readonly RuntimeEventEnvelope[] = [];
const EMPTY_RUNS: readonly RunListItem[] = [];

export function useRunSession(options: UseRunSessionOptions): RunSession {
  const {
    conversationId,
    runId: explicitRunId = null,
    enabled = true,
  } = options;
  const transport = useTransport();

  // ---- run resolution -----------------------------------------------------
  const [runs, setRuns] = useState<readonly RunListItem[]>(EMPTY_RUNS);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
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

  const autoResolvedRunId = useMemo(() => pickActiveRunId(runs), [runs]);
  // Precedence: explicit user selection > explicit prop > auto-resolved latest.
  const activeRunId = selectedRunId ?? explicitRunId ?? autoResolvedRunId;

  // Switching conversation clears any selection + stale run list so a stale run
  // is never streamed against the new conversation.
  useEffect(() => {
    setSelectedRunId(null);
    setRuns(EMPTY_RUNS);
    setResolveError(null);
  }, [conversationId]);

  // Resolve the conversation's run list via the Transport port. Runs in the
  // background even when an explicit `runId` is supplied, so the multi-run
  // selector still has data.
  useEffect(() => {
    if (!enabled) {
      return;
    }
    let cancelled = false;
    setIsResolving(true);
    setResolveError(null);
    void transport
      .request<unknown>({
        method: "GET",
        path: "/v1/agent/runs",
        query: { conversation_id: conversationId },
      })
      .then((payload) => {
        if (cancelled) {
          return;
        }
        setRuns(parseRunList(payload));
      })
      .catch((err: unknown) => {
        if (cancelled) {
          return;
        }
        // The run list is a best-effort enhancement — it only backs the
        // multi-run selector. Some deployments do not expose a run-list
        // endpoint (there is no `GET /v1/agent/runs`), so a failure here MUST
        // degrade to "no prior runs" (the empty/idle cockpit), never a blocking
        // error: starting a run (POST) and streaming it (GET …/stream) are
        // independent of listing.
        void err;
        setRuns(EMPTY_RUNS);
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

  const selectRun = useCallback((next: string) => {
    setSelectedRunId(next);
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

function pickActiveRunId(runs: readonly RunListItem[]): string | null {
  if (runs.length === 0) {
    return null;
  }
  const live = runs.filter(
    (run) => run.status !== null && NON_TERMINAL_STATUSES.includes(run.status),
  );
  const pool = live.length > 0 ? live : runs;
  let best = pool[0];
  let bestMillis = startedAtMillis(best);
  for (let i = 1; i < pool.length; i += 1) {
    const millis = startedAtMillis(pool[i]);
    if (millis >= bestMillis) {
      best = pool[i];
      bestMillis = millis;
    }
  }
  return best.runId;
}

function startedAtMillis(item: RunListItem): number {
  if (item.startedAt === null) {
    return Number.NEGATIVE_INFINITY;
  }
  const parsed = Date.parse(item.startedAt);
  return Number.isNaN(parsed) ? Number.NEGATIVE_INFINITY : parsed;
}

// Tolerant run-list parser: accepts a bare array, `{ runs: [...] }`, or
// `{ items: [...] }`, and snake_case or camelCase item fields.
function parseRunList(payload: unknown): readonly RunListItem[] {
  const raw = runListArray(payload);
  const out: RunListItem[] = [];
  for (const entry of raw) {
    const item = parseRunListItem(entry);
    if (item !== null) {
      out.push(item);
    }
  }
  return out;
}

function runListArray(payload: unknown): readonly unknown[] {
  if (Array.isArray(payload)) {
    return payload;
  }
  const record = asRecord(payload);
  if (record === null) {
    return [];
  }
  if (Array.isArray(record.runs)) {
    return record.runs;
  }
  if (Array.isArray(record.items)) {
    return record.items;
  }
  return [];
}

function parseRunListItem(value: unknown): RunListItem | null {
  const record = asRecord(value);
  if (record === null) {
    return null;
  }
  const runId = asString(record.run_id) ?? asString(record.runId);
  if (runId === null) {
    return null;
  }
  return {
    runId,
    goal:
      asString(record.goal) ??
      asString(record.title) ??
      asString(record.summary),
    status: asRunStatus(record.status),
    startedAt:
      asString(record.started_at) ??
      asString(record.startedAt) ??
      asString(record.created_at),
  };
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null
    ? (value as Record<string, unknown>)
    : null;
}

function asString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function asRunStatus(value: unknown): AgentRunStatus | null {
  return typeof value === "string" &&
    (AGENT_RUN_STATUSES as readonly string[]).includes(value)
    ? (value as AgentRunStatus)
    : null;
}

function toError(value: unknown): Error {
  if (value instanceof Error) {
    return value;
  }
  return new Error(typeof value === "string" ? value : "run resolution failed");
}
