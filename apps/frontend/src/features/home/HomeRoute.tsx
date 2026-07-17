// HomeRoute — Phase 9 data binder for the Home destination.
//
// Responsibilities (sub-PRD §3.3 + §3.6):
//   1. Fetch `GET /v1/home` via `homeApi.fetchHome` on mount; cache the
//      `HomePayload` locally.
//   2. Open `GET /v1/home/stream` via `homeApi.openHomeStream`; merge
//      typed `HomeStreamEnvelope` events into the cached payload using
//      pure reducers from `./adapters`.
//   3. Track the highest received `sequence_no` so the next reconnect
//      resumes with `?after_sequence=N` and the user does not see
//      duplicate rows.
//   4. Silent reconnect with exponential backoff 1s → 2s → 4s → … capped
//      at 30s (cross-audit §1.4). No "paused" chip — sub-PRD §3.6.
//   5. Hand the payload off to `<HomeDestination>` (main) and
//      `<HomePanel>` (right rail) via the v2 prop interface (sub-PRD
//      §3.1 / §3.2). The shells own the render — this route owns the
//      data flow only.
//
// Auth: 401 handling is wired globally in `AuthContext` (the transport's
// `configureUnauthorizedHandler` mints a fresh dev bearer or flips the
// session to anonymous). The route only needs to surface the user-facing
// error message; the bearer-refresh path is substrate-agnostic.
//
// Mount-once-per-route: identity is stable for the lifetime of an Atlas
// shell mount, so the fetch + SSE both live inside one effect cycle each
// (gated on `identity`). Route remounts (e.g. navigating away and back)
// re-fetch + reopen — exactly as sub-PRD §3.6 requires.

import { useEffect, useReducer, useRef, type ReactElement } from "react";

import { HomeDestination, HomePanel } from "@0x-copilot/chat-surface";
import type { HomePayload } from "@0x-copilot/api-types";

import type { RequestIdentity } from "../../api/config";
import {
  fetchHome,
  openHomeStream,
  type HomeStream,
  type HomeStreamEnvelope,
} from "../../api/homeApi";
import { errorMessage } from "../../utils/errors";
import { applyHomeStreamEvent } from "./adapters";

// === SSE reconnect schedule (cross-audit §1.4) =============================
// Silent exponential backoff: 1s → 2s → 4s → 8s → 16s → 30s cap.
const RECONNECT_BACKOFF_MIN_MS = 1_000;
const RECONNECT_BACKOFF_MAX_MS = 30_000;

interface HomeRouteProps {
  readonly identity: RequestIdentity;
}

// === Local state machine ==================================================

type State =
  | { readonly kind: "loading" }
  | { readonly kind: "error"; readonly message: string }
  | {
      readonly kind: "ready";
      readonly payload: HomePayload;
      /** Highest applied `sequence_no` — used for `?after_sequence=N` resume. */
      readonly lastSequenceNo: number;
    };

type Action =
  | { readonly type: "fetch_started" }
  | { readonly type: "fetch_succeeded"; readonly payload: HomePayload }
  | { readonly type: "fetch_failed"; readonly message: string }
  | { readonly type: "stream_event"; readonly envelope: HomeStreamEnvelope };

function reduce(state: State, action: Action): State {
  switch (action.type) {
    case "fetch_started":
      return { kind: "loading" };
    case "fetch_succeeded":
      return {
        kind: "ready",
        payload: action.payload,
        lastSequenceNo: 0,
      };
    case "fetch_failed":
      return { kind: "error", message: action.message };
    case "stream_event": {
      if (state.kind !== "ready") return state;
      const nextPayload = applyHomeStreamEvent(state.payload, action.envelope);
      const nextSeq = Math.max(
        state.lastSequenceNo,
        action.envelope.sequence_no,
      );
      if (nextPayload === state.payload && nextSeq === state.lastSequenceNo) {
        return state;
      }
      return {
        kind: "ready",
        payload: nextPayload,
        lastSequenceNo: nextSeq,
      };
    }
  }
}

const INITIAL_STATE: State = { kind: "loading" };

// === Component =============================================================

export function HomeRoute({ identity }: HomeRouteProps): ReactElement {
  const [state, dispatch] = useReducer(reduce, INITIAL_STATE);

  // Track lastSequenceNo via a ref so the SSE effect can read the latest
  // value when it re-opens without listing `state` as a dependency
  // (which would tear down + reopen the stream on every event).
  const lastSequenceNoRef = useRef(0);
  if (state.kind === "ready") {
    lastSequenceNoRef.current = state.lastSequenceNo;
  }

  // ---- HTTP fetch (one-shot per mount or identity change) ---------------
  useEffect(() => {
    let cancelled = false;
    dispatch({ type: "fetch_started" });
    fetchHome(identity)
      .then((payload) => {
        if (cancelled) return;
        dispatch({ type: "fetch_succeeded", payload });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        dispatch({
          type: "fetch_failed",
          message: errorMessage(error, "Could not load home."),
        });
      });
    return () => {
      cancelled = true;
    };
  }, [identity]);

  // ---- SSE subscription with silent exponential-backoff reconnect ------
  //
  // Stream is bound to (identity); the backoff doubles on every failure,
  // clamps at 30s, and resets to 1s on the next successful `onOpen`.
  // The reducer uses the latest `lastSequenceNoRef.current` on reconnect
  // so duplicates are avoided after a transient drop.
  const isReady = state.kind === "ready";
  useEffect(() => {
    if (!isReady) {
      return;
    }
    let cancelled = false;
    let activeHandle: HomeStream | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let backoffMs = RECONNECT_BACKOFF_MIN_MS;

    const open = (): void => {
      if (cancelled) return;
      activeHandle = openHomeStream({
        identity,
        afterSequence: lastSequenceNoRef.current,
        onOpen: () => {
          backoffMs = RECONNECT_BACKOFF_MIN_MS;
        },
        onEvent: (envelope) => {
          if (cancelled) return;
          dispatch({ type: "stream_event", envelope });
        },
        onError: () => {
          if (cancelled) return;
          activeHandle?.close();
          activeHandle = null;
          const delay = backoffMs;
          backoffMs = Math.min(backoffMs * 2, RECONNECT_BACKOFF_MAX_MS);
          reconnectTimer = setTimeout(open, delay);
        },
      });
    };

    open();

    return () => {
      cancelled = true;
      if (reconnectTimer !== null) {
        clearTimeout(reconnectTimer);
      }
      activeHandle?.close();
    };
    // `isReady` gates open(); depending on the boolean (not the full
    // `state` object) means SSE-driven payload merges do NOT tear down
    // + reopen the stream.
  }, [identity, isReady]);

  // ---- Render ----------------------------------------------------------
  if (state.kind === "error") {
    return (
      <section
        aria-label="Home destination"
        data-testid="home-route"
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
          data-testid="home-route-error"
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
            Could not load home
          </div>
          <div
            style={{ fontSize: 13, color: "var(--color-text-muted)" }}
            data-testid="home-route-error-message"
          >
            {state.message}
          </div>
          <button
            type="button"
            data-testid="home-route-retry"
            onClick={() => dispatch({ type: "fetch_started" })}
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

  // Loading + ready both render <HomeDestination> + <HomePanel>; the
  // shells render their own skeleton when `homeResponse` is null.
  const homeResponse: HomePayload | null =
    state.kind === "ready" ? state.payload : null;

  return (
    <>
      <section
        aria-label="Home destination"
        data-testid="home-route"
        data-state={state.kind}
        style={{ height: "100%", width: "100%", overflow: "auto" }}
      >
        <HomeDestination homeResponse={homeResponse} />
      </section>
      <section
        aria-label="Home context panel"
        data-testid="home-context-panel"
        style={{ display: "contents" }}
      >
        <HomePanel homeResponse={homeResponse} />
      </section>
    </>
  );
}
