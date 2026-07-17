// RoutinesRoute — data binder for the Phase 5 Routines destination (the
// 12th destination per `docs/atlas-new-design/destinations/routines-prd.md`).
//
// Mirrors the P4-C InboxRoute pattern:
//   1. Fetches `GET /v1/routines` via `routinesApi` and owns
//      loading / error / ready states.
//   2. Opens the `/v1/routines/stream` SSE channel (sub-PRD §4.2) with
//      exponential-backoff reconnect, tracking the highest
//      `sequence_no` for `?after_sequence=N` resume.
//   3. Proxies state changes (activate / pause / dismiss) and manual
//      fire ("Run now" — sub-PRD §3.11) back to the backend, optimistically
//      driving the SSE-merged local list while the server confirms.
//   4. Renders a host-side scaffolding today; the P5-B
//      `<RoutinesDestination>` from `@0x-copilot/chat-surface`
//      will replace the inner shell at merge — see `TODO(merge)`.
//
// Why a feature-level wrapper, not props on `<RoutinesDestination>`
// today: P5-B has not landed in the package yet, and this wave runs in
// parallel. Owning the data flow + state mutation here lets P5-B
// reshape the controlled component without forcing an App.tsx-level
// rewrite — same compromise the InboxRoute / TodosRoute waves made
// (sub-PRD §15.1 Impl-B pattern).

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactElement,
} from "react";

import type { RequestIdentity } from "../../api/config";
import {
  activateRoutine,
  dismissRoutine,
  fetchRoutines,
  pauseRoutine,
  runRoutineNow,
  streamRoutineEvents,
} from "../../api/routinesApi";
import type {
  ListRoutinesResponse,
  ManualFireResponse,
  Routine,
  RoutineId,
  RoutineStreamEnvelope,
} from "../../api/_routines-stub";
import { errorMessage } from "../../utils/errors";

/** Reconnect backoff bounds (mirrors InboxRoute / sub-PRD §3.6 conventions). */
const RECONNECT_BACKOFF_MIN_MS = 1_000;
const RECONNECT_BACKOFF_MAX_MS = 30_000;

interface RoutinesRouteProps {
  readonly identity: RequestIdentity;
}

type ViewState =
  | { readonly kind: "loading" }
  | { readonly kind: "error"; readonly message: string }
  | {
      readonly kind: "ready";
      readonly items: ReadonlyArray<Routine>;
      readonly highestSequenceNo: number;
    };

/**
 * Apply one durable SSE envelope to the local routine list. Mirrors
 * the shape of `applyInboxEnvelope` in InboxRoute but supports the
 * five routine event types (sub-PRD §4.2).
 *
 * Pure function so a test can drive it without a mounted component.
 *
 * Semantics:
 * - `routine_created`           → prepend if new; replace if id seen (idempotency).
 * - `routine_updated` / `routine_paused` / `routine_fired`
 *                                → in-place replace by id.
 * - `routine_deleted`           → drop the matching id.
 */
export function applyRoutineEnvelope(
  items: ReadonlyArray<Routine>,
  envelope: RoutineStreamEnvelope,
): ReadonlyArray<Routine> {
  const idx = items.findIndex((r) => r.id === envelope.routine.id);
  if (envelope.event_type === "routine_deleted") {
    if (idx === -1) return items;
    return items.slice(0, idx).concat(items.slice(idx + 1));
  }
  if (
    envelope.event_type === "routine_updated" ||
    envelope.event_type === "routine_paused" ||
    envelope.event_type === "routine_fired"
  ) {
    if (idx === -1) return items;
    const next = items.slice();
    next[idx] = envelope.routine;
    return next;
  }
  // routine_created — prepend if new; treat as update when the producer
  // re-emits an existing id (idempotency at the wire — cross-audit §5.2).
  if (idx !== -1) {
    const next = items.slice();
    next[idx] = envelope.routine;
    return next;
  }
  return [envelope.routine, ...items];
}

export function RoutinesRoute({ identity }: RoutinesRouteProps): ReactElement {
  const [state, setState] = useState<ViewState>({ kind: "loading" });
  const [reloadToken, setReloadToken] = useState(0);
  const [pendingError, setPendingError] = useState<string | null>(null);

  // ---- Initial fetch ------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });

    fetchRoutines(identity, { limit: 50 })
      .then((list: ListRoutinesResponse) => {
        if (cancelled) return;
        setState({
          kind: "ready",
          items: list.items,
          highestSequenceNo: 0,
        });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setState({
          kind: "error",
          message: errorMessage(error, "Could not load routines."),
        });
      });

    return () => {
      cancelled = true;
    };
  }, [identity, reloadToken]);

  // ---- SSE subscription with exponential-backoff reconnect ---------
  const backoffRef = useRef(RECONNECT_BACKOFF_MIN_MS);
  useEffect(() => {
    if (state.kind !== "ready") {
      return;
    }
    let cancelled = false;
    let activeHandle: { close(): void } | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    backoffRef.current = RECONNECT_BACKOFF_MIN_MS;

    function open(): void {
      if (cancelled) return;
      // Read the highest seen seq from the latest state snapshot via
      // setState's updater so reconnect resumes from the right point
      // even after several deltas have landed since the last open.
      let afterSequence = 0;
      setState((prev) => {
        if (prev.kind === "ready") afterSequence = prev.highestSequenceNo;
        return prev;
      });

      activeHandle = streamRoutineEvents({
        identity,
        afterSequence: afterSequence > 0 ? afterSequence : undefined,
        onOpen: () => {
          backoffRef.current = RECONNECT_BACKOFF_MIN_MS;
        },
        onEvent: (envelope) => {
          if (cancelled) return;
          setState((prev) => {
            if (prev.kind !== "ready") return prev;
            const items = applyRoutineEnvelope(prev.items, envelope);
            const highestSequenceNo = Math.max(
              prev.highestSequenceNo,
              envelope.sequence_no,
            );
            return { kind: "ready", items, highestSequenceNo };
          });
        },
        onError: () => {
          if (cancelled) return;
          activeHandle?.close();
          activeHandle = null;
          const delay = backoffRef.current;
          backoffRef.current = Math.min(
            backoffRef.current * 2,
            RECONNECT_BACKOFF_MAX_MS,
          );
          reconnectTimer = setTimeout(open, delay);
        },
      });
    }

    open();

    return () => {
      cancelled = true;
      if (reconnectTimer !== null) {
        clearTimeout(reconnectTimer);
      }
      activeHandle?.close();
    };
    // `state.kind` gates open(); we depend on it (not the full `state`
    // object) so an SSE-driven merge does NOT tear down + reopen the
    // stream.
  }, [identity, state.kind]);

  // ---- Mutation helpers (activate / pause / delete / run-now) ------
  //
  // Each helper replaces the local row optimistically when the server
  // acknowledges, then lets the next SSE delta confirm. Errors surface
  // as a non-fatal pendingError banner — the list keeps rendering, the
  // user can retry. Mirrors the InboxRoute reply / patch pattern.

  const handleActivate = useCallback(
    async (id: RoutineId): Promise<void> => {
      setPendingError(null);
      try {
        const updated = await activateRoutine(identity, id);
        setState((prev) => mergeUpdated(prev, updated));
      } catch (error: unknown) {
        setPendingError(errorMessage(error, "Could not activate routine."));
      }
    },
    [identity],
  );

  const handlePause = useCallback(
    async (id: RoutineId, reason?: string): Promise<void> => {
      setPendingError(null);
      try {
        const body = reason !== undefined ? { pause_reason: reason } : {};
        const updated = await pauseRoutine(identity, id, body);
        setState((prev) => mergeUpdated(prev, updated));
      } catch (error: unknown) {
        setPendingError(errorMessage(error, "Could not pause routine."));
      }
    },
    [identity],
  );

  const handleDelete = useCallback(
    async (id: RoutineId): Promise<void> => {
      setPendingError(null);
      try {
        await dismissRoutine(identity, id);
        setState((prev) => removeById(prev, id));
      } catch (error: unknown) {
        setPendingError(errorMessage(error, "Could not delete routine."));
      }
    },
    [identity],
  );

  const handleRunNow = useCallback(
    async (id: RoutineId): Promise<ManualFireResponse | null> => {
      setPendingError(null);
      try {
        return await runRoutineNow(identity, id);
      } catch (error: unknown) {
        setPendingError(errorMessage(error, "Could not start routine."));
        return null;
      }
    },
    [identity],
  );

  // ---- Render -------------------------------------------------------
  if (state.kind === "error") {
    return (
      <section
        aria-label="Routines destination"
        data-testid="routines-route"
        data-state="error"
        style={{
          height: "100%",
          width: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: 24,
          boxSizing: "border-box",
          backgroundColor: "var(--color-bg)",
          color: "var(--color-text)",
        }}
      >
        <div
          role="alert"
          data-testid="routines-route-error"
          style={{
            border: "1px solid var(--color-border)",
            borderRadius: 12,
            backgroundColor: "var(--color-surface)",
            padding: 32,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 12,
            maxWidth: 480,
          }}
        >
          <div style={{ fontSize: 14, fontWeight: 600 }}>
            Could not load routines
          </div>
          <div
            style={{ fontSize: 13, color: "var(--color-text-muted)" }}
            data-testid="routines-route-error-message"
          >
            {state.message}
          </div>
          <button
            type="button"
            data-testid="routines-route-retry"
            onClick={() => setReloadToken((t) => t + 1)}
            style={{
              height: 32,
              padding: "0 14px",
              borderRadius: 8,
              border: "1px solid var(--color-border-strong)",
              backgroundColor: "transparent",
              color: "var(--color-accent)",
              fontSize: 13,
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            Retry
          </button>
        </div>
      </section>
    );
  }

  const items = state.kind === "ready" ? state.items : [];

  // TODO(merge): once P5-B's `<RoutinesDestination>` lands in
  // `@0x-copilot/chat-surface`, swap the inner host-side list
  // for the package-shipped destination:
  //
  //   <RoutinesDestination
  //     items={items}
  //     identity={identity}
  //     onActivate={handleActivate}
  //     onPause={handlePause}
  //     onDelete={handleDelete}
  //     onRunNow={handleRunNow}
  //     onRetry={() => setReloadToken((t) => t + 1)}
  //   />
  return (
    <section
      aria-label="Routines destination"
      data-testid="routines-route"
      data-state={state.kind}
      data-item-count={items.length}
      style={{
        height: "100%",
        width: "100%",
        overflow: "auto",
        padding: 24,
        boxSizing: "border-box",
      }}
    >
      {pendingError !== null && (
        <div
          role="status"
          data-testid="routines-route-pending-error"
          style={{
            marginBottom: 16,
            padding: 12,
            border: "1px solid var(--color-border-strong)",
            borderRadius: 8,
            backgroundColor: "var(--color-surface)",
            fontSize: 13,
          }}
        >
          {pendingError}
        </div>
      )}
      {state.kind === "loading" ? (
        <div data-testid="routines-route-loading" style={{ fontSize: 13 }}>
          Loading routines…
        </div>
      ) : items.length === 0 ? (
        <div
          data-testid="routines-route-empty"
          style={{ fontSize: 13, color: "var(--color-text-muted)" }}
        >
          No routines yet.
        </div>
      ) : (
        <ul
          data-testid="routines-route-list"
          style={{ listStyle: "none", margin: 0, padding: 0 }}
        >
          {items.map((routine) => (
            <li
              key={routine.id}
              data-testid="routines-route-row"
              data-routine-id={routine.id}
              data-routine-status={routine.status}
              style={{
                padding: "12px 0",
                borderBottom: "1px solid var(--color-border)",
                display: "flex",
                gap: 12,
                alignItems: "center",
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 14, fontWeight: 600 }}>
                  {routine.name}
                </div>
                <div style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
                  {routine.status}
                  {routine.next_fire_at
                    ? ` · next ${routine.next_fire_at}`
                    : null}
                </div>
              </div>
              <button
                type="button"
                data-testid="routines-route-run-now"
                data-routine-id={routine.id}
                onClick={() => {
                  void handleRunNow(routine.id);
                }}
              >
                Run now
              </button>
              {routine.status === "active" ? (
                <button
                  type="button"
                  data-testid="routines-route-pause"
                  data-routine-id={routine.id}
                  onClick={() => {
                    void handlePause(routine.id);
                  }}
                >
                  Pause
                </button>
              ) : (
                <button
                  type="button"
                  data-testid="routines-route-activate"
                  data-routine-id={routine.id}
                  onClick={() => {
                    void handleActivate(routine.id);
                  }}
                >
                  Activate
                </button>
              )}
              <button
                type="button"
                data-testid="routines-route-delete"
                data-routine-id={routine.id}
                onClick={() => {
                  void handleDelete(routine.id);
                }}
              >
                Delete
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// ===========================================================================
// State reducers — extracted so they remain pure + testable.
// ===========================================================================

function mergeUpdated(prev: ViewState, updated: Routine): ViewState {
  if (prev.kind !== "ready") return prev;
  const idx = prev.items.findIndex((r) => r.id === updated.id);
  if (idx === -1) {
    return { ...prev, items: [updated, ...prev.items] };
  }
  const next = prev.items.slice();
  next[idx] = updated;
  return { ...prev, items: next };
}

function removeById(prev: ViewState, id: RoutineId): ViewState {
  if (prev.kind !== "ready") return prev;
  const idx = prev.items.findIndex((r) => r.id === id);
  if (idx === -1) return prev;
  return {
    ...prev,
    items: prev.items.slice(0, idx).concat(prev.items.slice(idx + 1)),
  };
}
