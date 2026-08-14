// useHostWrites — the binder for "what did this run write to my disk, and put
// one of them back".
//
// The two routes have existed and been reachable by nothing:
//   GET  /v1/agent/runs/{run_id}/host-writes
//   POST /v1/agent/runs/{run_id}/host-writes/revert
// Both are proxied by the facade, so this goes through the `Transport` port
// like every other cockpit binder — a bare `fetch` is eslint-banned here.
//
// WHEN IT FETCHES. On run binding, and again when the run reaches a terminal
// status — the same shape `useRunSources` uses, and for the same reason: the
// journal grows while the run is executing, so the listing taken at bind time
// is a prefix. There is no poll. A running agent writing a file the user wants
// back is a case the composer's pause answers; a list that re-fetched on a
// timer would flicker rows under a cursor that is about to press Undo.
//
// 503 IS NOT AN ERROR. Every non-desktop image composes no object store, so
// nothing was ever captured and the routes answer 503 by design
// (`HostWriteUndoRoutes._service`). That is a CAPABILITY answer, not a failure,
// and it is surfaced as `unavailable` so the panel can say "this deployment
// does not capture agent writes" rather than showing a red error over a
// deployment behaving exactly as intended.
//
// A REVERT IS NEVER SILENT. The POST returns a per-path report and this hook
// keeps it, keyed by the group that asked. The server audits the act — an
// undo that restored nothing is written to `runtime_audit_log` precisely so an
// operator can see it — and dropping the report on the client would leave the
// user with the only copy of that fact being a log they cannot read.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type {
  AgentRunStatus,
  HostWriteRevertReport,
  HostWriteUndoListing,
} from "@0x-copilot/api-types";
import { isTransportHttpError } from "@0x-copilot/chat-transport";

import { useTransport } from "../../providers/TransportProvider";
import {
  groupHostWrites,
  summariseRevert,
  type HostWriteGroup,
  type HostWriteRevertSummary,
} from "./hostWrites";

/** Non-terminal run states — mirrors `useRunSources` / `useRunTranscript`. */
const ACTIVE_RUN_STATUSES: ReadonlySet<AgentRunStatus> = new Set([
  "queued",
  "running",
  "waiting_for_approval",
  "cancelling",
]);

/** Where one group's undo has got to. Absent ⇒ `idle`. */
export type HostWriteRevertState = "reverting" | "reverted" | "failed";

export interface UseHostWritesOptions {
  readonly runId: string | null;
  readonly runStatus: AgentRunStatus | null;
}

export interface HostWritesController {
  /** One entry per tool call, oldest first. Empty when the run wrote nothing. */
  readonly groups: readonly HostWriteGroup[];
  readonly loading: boolean;
  /** A real failure to read the journal. Null while unavailable — see below. */
  readonly error: string | null;
  /**
   * This deployment does not capture agent writes (the routes answered 503).
   * Distinct from `error` so the panel states a capability rather than a fault.
   */
  readonly unavailable: boolean;
  /** Per-group undo state, keyed by `HostWriteGroup.key`. */
  readonly states: Readonly<Record<string, HostWriteRevertState>>;
  /** Per-group receipt — what the undo actually did, one row per path. */
  readonly reports: Readonly<Record<string, HostWriteRevertSummary>>;
  /** Per-group failure sentence, shown verbatim on a `failed` group. */
  readonly failures: Readonly<Record<string, string>>;
  /**
   * Undo exactly one tool call. Takes the GROUP, not a raw id, so a caller
   * cannot reach the route with `tool_call_id` omitted — which is the whole-run
   * revert, an action no control on this surface offers.
   */
  readonly revert: (group: HostWriteGroup) => void;
}

const NO_GROUPS: readonly HostWriteGroup[] = [];
const NO_STATES: Readonly<Record<string, HostWriteRevertState>> = {};
const NO_REPORTS: Readonly<Record<string, HostWriteRevertSummary>> = {};
const NO_FAILURES: Readonly<Record<string, string>> = {};

export function useHostWrites(
  options: UseHostWritesOptions,
): HostWritesController {
  const { runId, runStatus } = options;
  const transport = useTransport();

  const [entries, setEntries] = useState<HostWriteUndoListing["entries"]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [states, setStates] =
    useState<Readonly<Record<string, HostWriteRevertState>>>(NO_STATES);
  const [reports, setReports] =
    useState<Readonly<Record<string, HostWriteRevertSummary>>>(NO_REPORTS);
  const [failures, setFailures] =
    useState<Readonly<Record<string, string>>>(NO_FAILURES);

  // The run this hook is bound to, read inside the async revert so a POST that
  // returns after a rebind cannot write a receipt onto the wrong run's panel.
  const boundRunRef = useRef<string | null>(runId);
  boundRunRef.current = runId;

  const settled = runStatus !== null && !ACTIVE_RUN_STATUSES.has(runStatus);

  useEffect(() => {
    if (runId === null || runId === "") {
      setEntries([]);
      setError(null);
      setUnavailable(false);
      setStates(NO_STATES);
      setReports(NO_REPORTS);
      setFailures(NO_FAILURES);
      return;
    }
    let cancelled = false;
    setLoading(true);
    void transport
      .request<HostWriteUndoListing>({
        method: "GET",
        path: `/v1/agent/runs/${encodeURIComponent(runId)}/host-writes`,
      })
      .then((res) => {
        if (cancelled) return;
        setEntries(res.entries ?? []);
        setError(null);
        setUnavailable(false);
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        // 503 says the capability is absent, not that the read failed. Clearing
        // the entries as well, because "unavailable" and "here is a stale list"
        // must never be on screen together.
        if (isTransportHttpError(cause) && cause.status === 503) {
          setEntries([]);
          setError(null);
          setUnavailable(true);
          return;
        }
        setUnavailable(false);
        setError("Couldn't read what this run changed on disk.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // `settled` rather than `runStatus`: the listing is re-read once, when the
    // run stops growing the journal. Depending on the raw status would refetch
    // on every queued→running→waiting hop and churn the list mid-decision.
  }, [transport, runId, settled]);

  const groups = useMemo(
    () => (entries.length === 0 ? NO_GROUPS : groupHostWrites(entries)),
    [entries],
  );

  const revert = useCallback(
    (group: HostWriteGroup): void => {
      const activeRunId = boundRunRef.current;
      const toolCallId = group.toolCallId;
      if (activeRunId === null || activeRunId === "" || toolCallId === null) {
        // Unreachable from the UI — `undoable` already withholds the control
        // for an unbound group. Guarded anyway so a host wiring the callback by
        // hand cannot turn a per-call undo into the whole-run one by passing a
        // group the route cannot address.
        return;
      }
      setStates((prev) => ({ ...prev, [group.key]: "reverting" }));
      setFailures((prev) => dropKey(prev, group.key));
      void transport
        .request<HostWriteRevertReport>({
          method: "POST",
          path: `/v1/agent/runs/${encodeURIComponent(activeRunId)}/host-writes/revert`,
          body: { tool_call_id: toolCallId },
        })
        .then((report) => {
          if (boundRunRef.current !== activeRunId) return;
          setReports((prev) => ({
            ...prev,
            [group.key]: summariseRevert(report),
          }));
          // `reverted` means the server answered, NOT that every path came
          // back. Whether it did is the receipt's business, and the receipt is
          // rendered — collapsing "the call succeeded" into "your files are
          // back" is exactly the silent mutation this affordance must not be.
          setStates((prev) => ({ ...prev, [group.key]: "reverted" }));
        })
        .catch((cause: unknown) => {
          if (boundRunRef.current !== activeRunId) return;
          setStates((prev) => ({ ...prev, [group.key]: "failed" }));
          setFailures((prev) => ({ ...prev, [group.key]: messageOf(cause) }));
        });
    },
    [transport],
  );

  return {
    groups,
    loading,
    error,
    unavailable,
    states,
    reports,
    failures,
    revert,
  };
}

function dropKey<T>(
  record: Readonly<Record<string, T>>,
  key: string,
): Readonly<Record<string, T>> {
  if (record[key] === undefined) {
    return record;
  }
  return Object.fromEntries(
    Object.entries(record).filter(([existing]) => existing !== key),
  );
}

/** A thrown transport becomes a sentence — never a row that just stops moving. */
function messageOf(cause: unknown): string {
  if (isTransportHttpError(cause) && cause.status === 503) {
    return "Agent-write undo is not available on this deployment.";
  }
  if (cause instanceof Error && cause.message.trim().length > 0) {
    return cause.message;
  }
  return "The undo did not complete. Nothing is confirmed to have changed.";
}
