// Typed wrapper for the Phase 2 Home destination.
//
// Two surfaces:
//   1. `fetchHome(identity, opts)` — one-shot `GET /v1/home` for the
//      morning briefing payload (sub-PRD §3.3).
//   2. `streamHomeActivity({...})` — SSE subscription for live agent
//      activity (sub-PRD §3.5). Reconnect with exponential backoff is
//      managed by `HomeRoute`; this wrapper exposes the same
//      `subscribeServerSentEvents` shape as `agentApi.streamRunEvents`
//      so the only difference between callers is the path + event name.
//
// Network rule (CLAUDE.md): apps call the **facade** only (`/v1/*`),
// never `backend:8100` or `ai-backend:8000` directly. The transport
// singleton enforces this via the same-origin Vite proxy → facade.
//
// Wire types come from the canonical `@enterprise-search/api-types`
// package once P2-A1 lands. Today they live in `./_home-stub` so the
// frontend wave can run in parallel.
//
// TODO(merge): swap `./_home-stub` imports for `@enterprise-search/api-types`.

import type { RequestIdentity } from "./config";
import { httpGet } from "./http";
import { getAppTransport } from "./transport";
import type { AgentActivityEntry, HomeResponse } from "./_home-stub";

const SSE_EVENT_NAME = "home_activity";

/**
 * Allowed values for the per-user `home.activity_window_hours` KV
 * setting (cross-audit §9.5 — three-deviation product decision on top
 * of sub-PRD §16 Q1). The backend rejects any other value; the client
 * keeps the same allowlist so a corrupted KV entry never leaks an
 * arbitrary query-param value through.
 */
export const HOME_ACTIVITY_WINDOW_HOURS_ALLOWED = [6, 12, 24, 48, 168] as const;
export type HomeActivityWindowHours =
  (typeof HOME_ACTIVITY_WINDOW_HOURS_ALLOWED)[number];

/** Default window when no KV value is set (sub-PRD §16 Q1). */
export const HOME_ACTIVITY_WINDOW_HOURS_DEFAULT: HomeActivityWindowHours = 24;

export interface FetchHomeOptions {
  /**
   * Per-user activity-window length in hours. Read from KV by the
   * caller, defaulted to 24. Passed as the `activity_window_hours`
   * query param so the backend filter applies in one place.
   */
  readonly activityWindowHours?: HomeActivityWindowHours;
  /**
   * Per-section cache-bust hint for the "Retry section" affordance
   * (sub-PRD §12.6). Forwarded as `refresh_section=<name>` so the
   * backend bypasses cache for that one section only.
   */
  readonly refreshSection?: string;
}

/**
 * One-shot fetch of the morning-briefing payload. Resolves with the
 * full `HomeResponse` (including per-section status flags so partial
 * upstream failures surface as `status: "error"`, not as a top-level
 * 5xx — sub-PRD §3.3 partial-failure rule).
 */
export function fetchHome(
  identity: RequestIdentity,
  options: FetchHomeOptions = {},
): Promise<HomeResponse> {
  const params: Record<string, string | undefined> = {
    activity_window_hours: String(
      options.activityWindowHours ?? HOME_ACTIVITY_WINDOW_HOURS_DEFAULT,
    ),
  };
  if (options.refreshSection !== undefined) {
    params.refresh_section = options.refreshSection;
  }
  return httpGet<HomeResponse>("/v1/home", identity, params);
}

/** Closeable handle for a running home-activity SSE subscription. */
export interface HomeActivityStream {
  close(): void;
}

/**
 * Open the home-activity SSE stream. Each frame carries one
 * `AgentActivityEntry` as its data payload (sub-PRD §3.5 — the SSE
 * event name is `home_activity`). The stream is the SAME shape as
 * `streamRunEvents` (agentApi.ts:584) — fetch-based, bearer-aware,
 * cancellable via the returned handle — so a single SSE pattern lives
 * across the codebase.
 *
 * Reconnect policy (sub-PRD §16 Q8, cross-audit §9.5 deviation): silent
 * exponential backoff 1s → 30s. The policy itself is owned by
 * `HomeRoute` (caller-side, so tests can drive timing deterministically);
 * this wrapper exposes one connection attempt + a stable error hook.
 */
export function streamHomeActivity({
  identity,
  activityWindowHours,
  onEvent,
  onError,
  onOpen,
}: {
  readonly identity: RequestIdentity;
  readonly activityWindowHours?: HomeActivityWindowHours;
  readonly onEvent: (event: AgentActivityEntry) => void;
  readonly onError: (err: Event) => void;
  readonly onOpen?: () => void;
}): HomeActivityStream {
  return getAppTransport().subscribeServerSentEvents({
    path: "/v1/home/stream",
    query: homeSseQueryFor(identity, activityWindowHours),
    eventName: SSE_EVENT_NAME,
    onOpen,
    onError: (_err) => onError(streamErrorEvent()),
    onMessage: (data) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(data) as unknown;
      } catch {
        // Malformed JSON: the stream contract is JSON-per-frame; we
        // drop the frame rather than crash the route. The caller has
        // `onError` for the broader "stream broken" signal — a single
        // bad frame doesn't end the connection.
        return;
      }
      if (isAgentActivityEntry(parsed)) {
        onEvent(parsed);
      }
    },
  });
}

/**
 * Loose structural check on the SSE payload. The full type
 * (`AgentActivityEntry`) is a discriminated union with kind-specific
 * extra fields; this guard only validates the discriminator core so
 * the kind-dispatching renderer in chat-surface can do the rest. Same
 * pattern as `isRuntimeEventEnvelope` in api-types (`brands.ts`).
 */
function isAgentActivityEntry(value: unknown): value is AgentActivityEntry {
  if (value === null || typeof value !== "object") {
    return false;
  }
  const v = value as Record<string, unknown>;
  return (
    typeof v.id === "string" &&
    typeof v.kind === "string" &&
    typeof v.agent_id === "string" &&
    typeof v.agent_name === "string" &&
    typeof v.summary === "string" &&
    typeof v.created_at === "string" &&
    typeof v.tone === "string" &&
    typeof v.target === "object" &&
    v.target !== null
  );
}

// Identity + window in the shape `Transport.subscribeServerSentEvents`
// wants. Keeps the query-param composition in one place so the SSE
// callers can't drift in how `activity_window_hours` is encoded.
function homeSseQueryFor(
  identity: RequestIdentity,
  activityWindowHours: HomeActivityWindowHours | undefined,
): Record<string, string> {
  const out: Record<string, string> = {
    org_id: identity.orgId,
    user_id: identity.userId,
    activity_window_hours: String(
      activityWindowHours ?? HOME_ACTIVITY_WINDOW_HOURS_DEFAULT,
    ),
  };
  return out;
}

// The legacy onError signature was modelled after EventSource's bare
// Event — callers only react to "stream broken" and reconnect.
// Preserve that contract here so the home-activity stream shares the
// same error-handling shape as `streamRunEvents`.
function streamErrorEvent(): Event {
  if (typeof Event === "function") {
    return new Event("error");
  }
  return { type: "error" } as unknown as Event;
}
