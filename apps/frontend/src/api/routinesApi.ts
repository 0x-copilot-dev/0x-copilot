// Typed wrappers for the Phase 5 Routines destination.
//
// Surfaces (sub-PRD §4.2):
//   1. `fetchRoutines(identity, opts)`        — GET /v1/routines.
//   2. `fetchRoutine(identity, id)`           — GET /v1/routines/{id}.
//   3. `createRoutine / patchRoutine / dismissRoutine`
//                                             — CRUD on a single routine.
//   4. `activateRoutine / pauseRoutine`       — state transitions.
//   5. `runRoutineNow`                        — POST /v1/routines/{id}/run
//                                              (manual fire).
//   6. `streamRoutineEvents({...})`           — SSE durable channel
//                                              (sub-PRD §4.2).
//
// Network rule (CLAUDE.md / `apps/frontend/CLAUDE.md`): apps call the
// **facade** only (`/v1/*`). Never `backend:8100` or `ai-backend:8000`
// directly. The transport singleton enforces this via the same-origin
// Vite proxy → facade.
//
// Wire types live in `./_routines-stub` until P5-A's
// `@enterprise-search/api-types/src/routines.ts` lands on main.
//
// TODO(merge): swap every `./_routines-stub` import for
// `@enterprise-search/api-types`.

import type { RequestIdentity } from "./config";
import { httpDelete, httpGet, httpPatchQuery, httpPostQuery } from "./http";
import { getAppTransport } from "./transport";
import type {
  CreateRoutineRequest,
  ListRoutinesFilters,
  ListRoutinesResponse,
  ManualFireResponse,
  PauseRoutineRequest,
  Routine,
  RoutineId,
  RoutineSortKey,
  RoutineStreamEnvelope,
  UpdateRoutineRequest,
} from "./_routines-stub";

const SSE_EVENT_NAME = "routine_event";

// ===========================================================================
// LIST
// ===========================================================================

export interface FetchRoutinesOptions {
  readonly filters?: ListRoutinesFilters;
  readonly q?: string;
  readonly sort?: RoutineSortKey;
  /** Opaque cursor from a previous response's `next_cursor`. */
  readonly after?: string;
  /** 1..200; server default 50. */
  readonly limit?: number;
}

/**
 * GET /v1/routines with allowlisted filters + cursor pagination
 * (sub-PRD §4.2, §4.5, §8). Filter encoding mirrors `todosApi.fetchTodos`
 * / `inboxApi.fetchInbox` — `filter[<axis>]=<value>` keys, single value
 * per axis (the server's allowlist disallows repeated axes per
 * cross-audit §1.5).
 */
export function fetchRoutines(
  identity: RequestIdentity,
  options: FetchRoutinesOptions = {},
): Promise<ListRoutinesResponse> {
  return httpGet<ListRoutinesResponse>(
    "/v1/routines",
    identity,
    encodeListParams(options),
  );
}

// ===========================================================================
// DETAIL
// ===========================================================================

/** GET /v1/routines/{id}. Secrets are server-masked per sub-PRD §7.5. */
export function fetchRoutine(
  identity: RequestIdentity,
  id: RoutineId,
): Promise<Routine> {
  return httpGet<Routine>(`/v1/routines/${encodeURIComponent(id)}`, identity);
}

// ===========================================================================
// MUTATIONS
// ===========================================================================

/** POST /v1/routines — create a draft (or active) routine. */
export function createRoutine(
  identity: RequestIdentity,
  body: CreateRoutineRequest,
): Promise<Routine> {
  return httpPostQuery<Routine>("/v1/routines", body, identity);
}

/** PATCH /v1/routines/{id} — owner-only writes (sub-PRD §7). */
export function patchRoutine(
  identity: RequestIdentity,
  id: RoutineId,
  body: UpdateRoutineRequest,
): Promise<Routine> {
  return httpPatchQuery<Routine>(
    `/v1/routines/${encodeURIComponent(id)}`,
    body,
    identity,
  );
}

/**
 * DELETE /v1/routines/{id} — soft delete. Tombstone retained per
 * sub-PRD §5.3 retention rules. Cascade behavior is owned by the
 * server (fires + triggers cascade ON DELETE).
 */
export function dismissRoutine(
  identity: RequestIdentity,
  id: RoutineId,
): Promise<void> {
  return httpDelete(`/v1/routines/${encodeURIComponent(id)}`, identity);
}

/**
 * POST /v1/routines/{id}/activate — sub-PRD §4.2.
 *
 * Server validates triggers + permissions and recomputes
 * `next_fire_at`. Returns the routine in its new state.
 */
export function activateRoutine(
  identity: RequestIdentity,
  id: RoutineId,
): Promise<Routine> {
  return httpPostQuery<Routine>(
    `/v1/routines/${encodeURIComponent(id)}/activate`,
    {},
    identity,
  );
}

/**
 * POST /v1/routines/{id}/pause — sub-PRD §4.2.
 *
 * Optional `pause_reason` is persisted on the routine row so list /
 * detail views can render *why* a routine is paused (manual pause vs
 * scheduler-triggered auto-pause per sub-PRD §3.10 / §7.4).
 */
export function pauseRoutine(
  identity: RequestIdentity,
  id: RoutineId,
  body: PauseRoutineRequest = {},
): Promise<Routine> {
  return httpPostQuery<Routine>(
    `/v1/routines/${encodeURIComponent(id)}/pause`,
    body,
    identity,
  );
}

/**
 * POST /v1/routines/{id}/run — sub-PRD §3.11 / §4.2 manual fire
 * ("Run now"). ACL is enforced server-side (`permissions.manual_fire`
 * = owner / project_members / tenant). Returns the new run ref so the
 * caller can navigate to the run timeline.
 */
export function runRoutineNow(
  identity: RequestIdentity,
  id: RoutineId,
): Promise<ManualFireResponse> {
  return httpPostQuery<ManualFireResponse>(
    `/v1/routines/${encodeURIComponent(id)}/run`,
    {},
    identity,
  );
}

// ===========================================================================
// SSE (durable routine channel — sub-PRD §4.2)
// ===========================================================================

/** Closeable handle for a running routine-events SSE subscription. */
export interface RoutineEventsStream {
  close(): void;
}

/**
 * Open the durable routine-events SSE stream (sub-PRD §4.2). Each
 * frame carries one `RoutineStreamEnvelope`; the client tracks the
 * highest `sequence_no` and reconnects with `?after_sequence=N` to
 * resume without dropping events (cross-audit §5.2).
 *
 * Reconnect policy is owned caller-side (mirrors `streamInboxEvents` /
 * `streamHomeActivity`) — the wrapper exposes one connection attempt
 * plus a stable error hook so tests can drive the timing
 * deterministically.
 */
export function streamRoutineEvents({
  identity,
  afterSequence,
  onEvent,
  onError,
  onOpen,
}: {
  readonly identity: RequestIdentity;
  /** Highest `sequence_no` already applied; backend replays everything strictly greater. */
  readonly afterSequence?: number;
  readonly onEvent: (envelope: RoutineStreamEnvelope) => void;
  readonly onError: (err: Event) => void;
  readonly onOpen?: () => void;
}): RoutineEventsStream {
  return getAppTransport().subscribeServerSentEvents({
    path: "/v1/routines/stream",
    query: routineSseQueryFor(identity, afterSequence),
    eventName: SSE_EVENT_NAME,
    onOpen,
    onError: (_err) => onError(streamErrorEvent()),
    onMessage: (data) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(data) as unknown;
      } catch {
        // Malformed JSON — drop the frame. Mirrors inboxApi behavior:
        // a single bad frame must not tear down the connection; the
        // caller has `onError` for the broader "stream broken" signal.
        return;
      }
      if (isRoutineStreamEnvelope(parsed)) {
        onEvent(parsed);
      }
    },
  });
}

// ===========================================================================
// Helpers
// ===========================================================================

function encodeListParams(
  options: FetchRoutinesOptions,
): Record<string, string | undefined> {
  const params: Record<string, string | undefined> = {};
  const { filters, q, sort, after, limit } = options;

  if (filters?.status !== undefined) {
    params["filter[status]"] = filters.status;
  }
  if (filters?.owner_user_id !== undefined) {
    params["filter[owner_user_id]"] = filters.owner_user_id;
  }
  if (filters?.project_id !== undefined) {
    params["filter[project_id]"] = filters.project_id;
  }
  if (filters?.trigger_kind !== undefined) {
    params["filter[trigger_kind]"] = filters.trigger_kind;
  }
  if (q !== undefined && q.length > 0) {
    params.q = q;
  }
  if (sort !== undefined) {
    params.sort = sort;
  }
  if (after !== undefined) {
    params.after = after;
  }
  if (limit !== undefined) {
    params.limit = String(limit);
  }
  return params;
}

function routineSseQueryFor(
  identity: RequestIdentity,
  afterSequence: number | undefined,
): Record<string, string> {
  const out: Record<string, string> = {
    org_id: identity.orgId,
    user_id: identity.userId,
  };
  if (afterSequence !== undefined) {
    out.after_sequence = String(afterSequence);
  }
  return out;
}

/**
 * Loose structural check on the SSE envelope. Matches the
 * discriminator fields from sub-PRD §4.2 — `sequence_no` (number),
 * `event_type` (string), `routine` (object), `emitted_at` (string).
 * Same pattern as `isInboxStreamEnvelope` in inboxApi.ts.
 */
function isRoutineStreamEnvelope(
  value: unknown,
): value is RoutineStreamEnvelope {
  if (value === null || typeof value !== "object") {
    return false;
  }
  const v = value as Record<string, unknown>;
  return (
    typeof v.sequence_no === "number" &&
    typeof v.event_type === "string" &&
    typeof v.emitted_at === "string" &&
    typeof v.routine === "object" &&
    v.routine !== null
  );
}

// The legacy onError signature was modelled after EventSource's bare
// Event — callers only react to "stream broken" and reconnect. Mirrors
// `streamInboxEvents` / `streamHomeActivity` / `streamRunEvents`.
function streamErrorEvent(): Event {
  if (typeof Event === "function") {
    return new Event("error");
  }
  return { type: "error" } as unknown as Event;
}
