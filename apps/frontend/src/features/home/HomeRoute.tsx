// HomeRoute — data binder for the Home destination.
//
// Responsibilities:
//   1. Read the per-user `home.activity_window_hours` KV setting (default
//      24h; allowed values 6/12/24/48/168 per cross-audit §9.5).
//   2. Fetch `GET /v1/home` via `homeApi` and own loading + error state.
//   3. Open the `/v1/home/stream` SSE channel and merge live
//      `AgentActivityEntry` events into the local payload (sub-PRD §3.5).
//   4. Manage SSE reconnect with exponential backoff 1s → 30s
//      (sub-PRD §16 Q8, cross-audit §9.5 deviation — silent retry,
//      no "paused" indicator).
//   5. Hand the payload off to `<HomeDestination>` from chat-surface
//      (the presentational component). On error, render a local
//      error state; during load, the destination renders its own
//      skeleton.
//
// Why a feature-level wrapper, not props on <HomeDestination> today:
// the current `<HomeDestination>` in chat-surface still owns its own
// fetch (the Wave-1 seed). P2-B1 rewrites it into a controlled
// component that accepts the `HomeResponse` as a prop. Until then,
// HomeRoute owns the data flow + error state without forcing a
// breaking change on the package boundary. The orchestrator rewires
// the prop hand-off at merge — see TODO(merge) markers.

import { useEffect, useRef, useState, type ReactElement } from "react";

import {
  HomeDestination,
  useKeyValueStore,
} from "@enterprise-search/chat-surface";

import { fetchHome, streamHomeActivity } from "../../api/homeApi";
import {
  HOME_ACTIVITY_WINDOW_HOURS_ALLOWED,
  HOME_ACTIVITY_WINDOW_HOURS_DEFAULT,
  type HomeActivityWindowHours,
} from "../../api/homeApi";
import type { RequestIdentity } from "../../api/config";
import type { AgentActivityEntry, HomeResponse } from "../../api/_home-stub";
import { errorMessage } from "../../utils/errors";

/**
 * KV key for the per-user activity-window length. Cross-audit §9.5
 * names it `home.activity_window_hours`; this constant is the single
 * source of truth for spelling on the frontend.
 */
export const HOME_ACTIVITY_WINDOW_HOURS_KEY = "home.activity_window_hours";

/** Activity-feed cap from sub-PRD §3.1.2. */
const ACTIVITY_FEED_CAP = 15;

/** Reconnect backoff bounds from cross-audit §9.5 deviation on Q8. */
const RECONNECT_BACKOFF_MIN_MS = 1_000;
const RECONNECT_BACKOFF_MAX_MS = 30_000;

interface HomeRouteProps {
  readonly identity: RequestIdentity;
}

type ViewState =
  | { readonly kind: "loading" }
  | { readonly kind: "error"; readonly message: string }
  | { readonly kind: "ready"; readonly payload: HomeResponse };

/**
 * Read the per-user activity-window from KV. Defaults to 24h, refuses
 * any value not in the allowlist (defence-in-depth: a corrupted KV
 * value never leaks into the backend's window filter).
 */
function readActivityWindowHours(store: {
  get(key: string): string | null;
}): HomeActivityWindowHours {
  const raw = store.get(HOME_ACTIVITY_WINDOW_HOURS_KEY);
  if (raw === null) {
    return HOME_ACTIVITY_WINDOW_HOURS_DEFAULT;
  }
  const parsed = Number.parseInt(raw, 10);
  for (const allowed of HOME_ACTIVITY_WINDOW_HOURS_ALLOWED) {
    if (parsed === allowed) {
      return allowed;
    }
  }
  return HOME_ACTIVITY_WINDOW_HOURS_DEFAULT;
}

/**
 * Prepend a live `AgentActivityEntry` to the feed, deduplicating by
 * `id` and capping at 15 entries (sub-PRD §3.1.2). Returns the updated
 * payload, or the original reference when the entry is a duplicate
 * (so React skips the re-render).
 */
function applyActivityEvent(
  payload: HomeResponse,
  entry: AgentActivityEntry,
): HomeResponse {
  if (payload.agent_activity.status !== "ok") {
    // Section is in error/unavailable; don't merge live events into a
    // section that has no `data` array we can trust.
    return payload;
  }
  const existing = payload.agent_activity.data ?? [];
  if (existing.some((e) => e.id === entry.id)) {
    return payload;
  }
  const next = [entry, ...existing].slice(0, ACTIVITY_FEED_CAP);
  return {
    ...payload,
    agent_activity: { ...payload.agent_activity, data: next },
  };
}

export function HomeRoute({ identity }: HomeRouteProps): ReactElement {
  const keyValueStore = useKeyValueStore();
  const [state, setState] = useState<ViewState>({ kind: "loading" });
  const [reloadToken, setReloadToken] = useState(0);

  // Read the per-user window once per (re)load so a Settings change
  // that lands while Home is mounted picks up on the next refresh.
  const activityWindowHours = readActivityWindowHours(keyValueStore);

  // ---- HTTP fetch ---------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });
    fetchHome(identity, { activityWindowHours })
      .then((payload) => {
        if (cancelled) return;
        setState({ kind: "ready", payload });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setState({
          kind: "error",
          message: errorMessage(error, "Could not load home."),
        });
      });
    return () => {
      cancelled = true;
    };
  }, [identity, activityWindowHours, reloadToken]);

  // ---- SSE subscription with exponential-backoff reconnect ---------
  //
  // The stream is bound to (identity, window) — both are stable for
  // the lifetime of an effect cycle, so the reconnect loop lives
  // entirely inside one useEffect. The backoff doubles on every
  // failure, clamps at 30s, and resets to 1s on the next successful
  // `onOpen`.
  const backoffRef = useRef(RECONNECT_BACKOFF_MIN_MS);
  useEffect(() => {
    // Only open the stream once the initial fetch succeeded — the
    // feed will be merged into `state.payload`, so we'd have nowhere
    // to put events before the payload arrives.
    if (state.kind !== "ready") {
      return;
    }
    let cancelled = false;
    let activeHandle: { close(): void } | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    backoffRef.current = RECONNECT_BACKOFF_MIN_MS;

    function open(): void {
      if (cancelled) return;
      activeHandle = streamHomeActivity({
        identity,
        activityWindowHours,
        onOpen: () => {
          backoffRef.current = RECONNECT_BACKOFF_MIN_MS;
        },
        onEvent: (entry) => {
          if (cancelled) return;
          setState((prev) => {
            if (prev.kind !== "ready") return prev;
            const next = applyActivityEvent(prev.payload, entry);
            return next === prev.payload
              ? prev
              : { kind: "ready", payload: next };
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
    // `state.kind` gates open(); we depend on it (not on the full
    // `state` object) so an SSE-driven payload merge does NOT tear
    // down + reopen the stream. The activity-window dep covers
    // KV-driven window changes.
  }, [identity, activityWindowHours, state.kind]);

  // ---- Render -------------------------------------------------------
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

  // Loading + ready both render <HomeDestination />. Today (Wave 1
  // seed) `<HomeDestination>` does its own fetch and shows its own
  // skeleton, so passing the payload through is a no-op — but the
  // wrapper is here so P2-B1's controlled rewrite of HomeDestination
  // can accept `payload` (and `onRetrySection` callback) without any
  // App.tsx-level rewiring.
  //
  // TODO(merge): once P2-B1's HomeDestination accepts props, pass:
  //   <HomeDestination
  //     payload={state.kind === "ready" ? state.payload : null}
  //     activityWindowHours={activityWindowHours}
  //     onRetrySection={(section) =>
  //       fetchHome(identity, { activityWindowHours, refreshSection: section })
  //     }
  //     /* HomePanel mounts in App.tsx's ContextPanel slot */
  //   />
  return (
    <section
      aria-label="Home destination"
      data-testid="home-route"
      data-state={state.kind}
      data-activity-window-hours={activityWindowHours}
      style={{ height: "100%", width: "100%", overflow: "auto" }}
    >
      <HomeDestination />
    </section>
  );
}
