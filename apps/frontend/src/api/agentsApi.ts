// Typed wrappers for the Phase 8 Agents destination.
//
// Surfaces (sub-PRD §4):
//   1. `fetchAgents(identity, opts)`              — GET /v1/agents.
//   2. `fetchAgent(identity, id)`                 — GET /v1/agents/{id}.
//   3. `createAgent / patchAgent`                 — custom agent CRUD.
//   4. `installAgent / uninstallAgent`            — per-user install state.
//   5. `snapshotAgentVersion`                     — POST /v1/agents/{id}/versions.
//   6. `fetchAgentVersions`                       — GET /v1/agents/{id}/versions.
//   7. `duplicateAgent`                           — fork to custom.
//   8. `fetchAgentUsage`                          — usage projection.
//   9. `streamAgentEvents({...})`                 — SSE durable channel.
//
// Network rule (CLAUDE.md / `apps/frontend/CLAUDE.md`): apps call the
// **facade** only (`/v1/*`). Never `backend:8100` or `ai-backend:8000`
// directly. The transport singleton enforces this via the same-origin
// Vite proxy → facade.
//
// Wire types live in `./_agents-stub` until P8-A's
// `@enterprise-search/api-types/src/agents.ts` lands on main.
//
// TODO(merge): swap every `./_agents-stub` import for
// `@enterprise-search/api-types`.

import type { RequestIdentity } from "./config";
import { httpGet, httpPatchQuery, httpPostQuery } from "./http";
import { getAppTransport } from "./transport";
import type {
  Agent,
  AgentId,
  AgentListResponse,
  AgentSortKey,
  AgentStreamEnvelope,
  AgentUsageResponse,
  AgentVersion,
  AgentVersionListResponse,
  CreateAgentRequest,
  DuplicateAgentRequest,
  InstallAgentRequest,
  ListAgentsFilters,
  SnapshotAgentVersionRequest,
  UninstallAgentRequest,
  UpdateAgentRequest,
  UsagePeriod,
} from "./_agents-stub";

const SSE_EVENT_NAME = "agent_event";

// ===========================================================================
// LIST
// ===========================================================================

export interface FetchAgentsOptions {
  readonly filters?: ListAgentsFilters;
  readonly q?: string;
  readonly sort?: AgentSortKey;
  /** Opaque cursor from a previous response's `next_cursor`. */
  readonly after?: string;
  /** 1..200; server default 50. */
  readonly limit?: number;
}

/**
 * GET /v1/agents with allowlisted filters + cursor pagination
 * (sub-PRD §4.1, §4.13). Filter encoding mirrors the projects / routines /
 * inbox APIs — `filter[<axis>]=<value>` keys, single value per axis (the
 * server's allowlist disallows repeated axes per cross-audit §1.5).
 */
export function fetchAgents(
  identity: RequestIdentity,
  options: FetchAgentsOptions = {},
): Promise<AgentListResponse> {
  return httpGet<AgentListResponse>(
    "/v1/agents",
    identity,
    encodeListParams(options),
  );
}

// ===========================================================================
// DETAIL
// ===========================================================================

/** GET /v1/agents/{id}. Returns the merged-overrides view (sub-PRD §3.3). */
export function fetchAgent(
  identity: RequestIdentity,
  id: AgentId,
): Promise<Agent> {
  return httpGet<Agent>(`/v1/agents/${encodeURIComponent(id)}`, identity);
}

// ===========================================================================
// MUTATIONS — create / patch
// ===========================================================================

/** POST /v1/agents — create custom agent (sub-PRD §4.3). */
export function createAgent(
  identity: RequestIdentity,
  body: CreateAgentRequest,
): Promise<Agent> {
  return httpPostQuery<Agent>("/v1/agents", body, identity);
}

/**
 * PATCH /v1/agents/{id} — edit live record (sub-PRD §4.4).
 *
 * 409 with `{ error: "agent_origin_immutable" }` when origin is
 * "system" or "community"; caller must duplicate first.
 */
export function patchAgent(
  identity: RequestIdentity,
  id: AgentId,
  body: UpdateAgentRequest,
): Promise<Agent> {
  return httpPatchQuery<Agent>(
    `/v1/agents/${encodeURIComponent(id)}`,
    body,
    identity,
  );
}

// ===========================================================================
// INSTALL / UNINSTALL
// ===========================================================================

/**
 * POST /v1/agents/{id}/install — per-user install, or admin-only tenant
 * install (sub-PRD §4.5). Idempotent on user scope.
 */
export function installAgent(
  identity: RequestIdentity,
  id: AgentId,
  body: InstallAgentRequest = {},
): Promise<Agent> {
  return httpPostQuery<Agent>(
    `/v1/agents/${encodeURIComponent(id)}/install`,
    body,
    identity,
  );
}

/**
 * POST /v1/agents/{id}/uninstall — drop the install row + per-user
 * overrides (sub-PRD §4.6). Does NOT cascade-delete pinned Routines or
 * Project defaults — see §4.6 for the dead-link semantics.
 */
export function uninstallAgent(
  identity: RequestIdentity,
  id: AgentId,
  body: UninstallAgentRequest = {},
): Promise<Agent> {
  return httpPostQuery<Agent>(
    `/v1/agents/${encodeURIComponent(id)}/uninstall`,
    body,
    identity,
  );
}

// ===========================================================================
// VERSIONS — snapshot + list
// ===========================================================================

/**
 * POST /v1/agents/{id}/versions — snapshot the current live config into
 * an immutable `AgentVersion` and bump `Agent.version` (sub-PRD §4.7).
 */
export function snapshotAgentVersion(
  identity: RequestIdentity,
  id: AgentId,
  body: SnapshotAgentVersionRequest = {},
): Promise<AgentVersion> {
  return httpPostQuery<AgentVersion>(
    `/v1/agents/${encodeURIComponent(id)}/versions`,
    body,
    identity,
  );
}

export interface FetchAgentVersionsOptions {
  readonly after?: string;
  readonly limit?: number;
}

/** GET /v1/agents/{id}/versions — paginated version history (sub-PRD §4.8). */
export function fetchAgentVersions(
  identity: RequestIdentity,
  id: AgentId,
  options: FetchAgentVersionsOptions = {},
): Promise<AgentVersionListResponse> {
  const params: Record<string, string | undefined> = {};
  if (options.after !== undefined) {
    params.after = options.after;
  }
  if (options.limit !== undefined) {
    params.limit = String(options.limit);
  }
  return httpGet<AgentVersionListResponse>(
    `/v1/agents/${encodeURIComponent(id)}/versions`,
    identity,
    params,
  );
}

// ===========================================================================
// DUPLICATE
// ===========================================================================

/**
 * POST /v1/agents/{id}/duplicate — fork to a custom-origin agent owned
 * by the caller (sub-PRD §4.10). Caller must be able to read the source.
 */
export function duplicateAgent(
  identity: RequestIdentity,
  id: AgentId,
  body: DuplicateAgentRequest = {},
): Promise<Agent> {
  return httpPostQuery<Agent>(
    `/v1/agents/${encodeURIComponent(id)}/duplicate`,
    body,
    identity,
  );
}

// ===========================================================================
// USAGE — read-only projection (sub-PRD §3.4, §4.9)
// ===========================================================================

export interface FetchAgentUsageOptions {
  /** `day` / `week` / `month`. Server default `week`. */
  readonly period?: UsagePeriod;
  /** ISO8601 lower bound. Server default = `now - 30d`. */
  readonly since?: string;
}

/**
 * GET /v1/agents/{id}/usage — read-only projection over
 * `runtime_model_call_usage` (cross-audit §5.5 single-tracker invariant).
 * Caller must be able to read the agent (§4.9 ACL).
 */
export function fetchAgentUsage(
  identity: RequestIdentity,
  id: AgentId,
  options: FetchAgentUsageOptions = {},
): Promise<AgentUsageResponse> {
  const params: Record<string, string | undefined> = {};
  if (options.period !== undefined) {
    params.period = options.period;
  }
  if (options.since !== undefined) {
    params.since = options.since;
  }
  return httpGet<AgentUsageResponse>(
    `/v1/agents/${encodeURIComponent(id)}/usage`,
    identity,
    params,
  );
}

// ===========================================================================
// SSE — durable agents channel (sub-PRD §4.12)
// ===========================================================================

/** Closeable handle for a running agents-events SSE subscription. */
export interface AgentEventsStream {
  close(): void;
}

/**
 * Open the durable agents-events SSE stream (sub-PRD §4.12). Each frame
 * carries one `AgentStreamEnvelope`; the client tracks the highest
 * `sequence_no` and reconnects with `?after_sequence=N` to resume
 * without dropping events (cross-audit §5.2).
 *
 * Reconnect policy is owned caller-side (mirrors `streamProjectEvents` /
 * `streamRoutineEvents`) — the wrapper exposes one connection attempt
 * plus a stable error hook so tests can drive the timing
 * deterministically.
 */
export function streamAgentEvents({
  identity,
  afterSequence,
  onEvent,
  onError,
  onOpen,
}: {
  readonly identity: RequestIdentity;
  /** Highest `sequence_no` already applied; backend replays everything strictly greater. */
  readonly afterSequence?: number;
  readonly onEvent: (envelope: AgentStreamEnvelope) => void;
  readonly onError: (err: Event) => void;
  readonly onOpen?: () => void;
}): AgentEventsStream {
  return getAppTransport().subscribeServerSentEvents({
    path: "/v1/agents/stream",
    query: agentSseQueryFor(identity, afterSequence),
    eventName: SSE_EVENT_NAME,
    onOpen,
    onError: (_err) => onError(streamErrorEvent()),
    onMessage: (data) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(data) as unknown;
      } catch {
        // Malformed JSON — drop the frame. Mirrors projectsApi / routinesApi
        // behavior: a single bad frame must not tear down the connection;
        // the caller has `onError` for the broader "stream broken" signal.
        return;
      }
      if (isAgentStreamEnvelope(parsed)) {
        onEvent(parsed);
      }
    },
  });
}

// ===========================================================================
// Helpers
// ===========================================================================

function encodeListParams(
  options: FetchAgentsOptions,
): Record<string, string | undefined> {
  const params: Record<string, string | undefined> = {};
  const { filters, q, sort, after, limit } = options;

  if (filters?.origin !== undefined) {
    params["filter[origin]"] = filters.origin;
  }
  if (filters?.status !== undefined) {
    params["filter[status]"] = filters.status;
  }
  if (filters?.skill_id !== undefined) {
    params["filter[skill_id]"] = filters.skill_id;
  }
  if (filters?.connector_id !== undefined) {
    params["filter[connector_id]"] = filters.connector_id;
  }
  if (filters?.owner_user_id !== undefined) {
    params["filter[owner_user_id]"] = filters.owner_user_id;
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

function agentSseQueryFor(
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
 * Loose structural check on the SSE envelope. Matches the discriminator
 * fields from sub-PRD §4.12 — `sequence_no` (number), `event_type`
 * (string), `agent_id` (string), `payload` (object), `emitted_at`
 * (string). Same pattern as `isProjectStreamEnvelope` /
 * `isRoutineStreamEnvelope`.
 */
function isAgentStreamEnvelope(value: unknown): value is AgentStreamEnvelope {
  if (value === null || typeof value !== "object") {
    return false;
  }
  const v = value as Record<string, unknown>;
  return (
    typeof v.sequence_no === "number" &&
    typeof v.event_type === "string" &&
    typeof v.agent_id === "string" &&
    typeof v.emitted_at === "string" &&
    typeof v.payload === "object" &&
    v.payload !== null
  );
}

// The legacy onError signature was modelled after EventSource's bare
// Event — callers only react to "stream broken" and reconnect. Mirrors
// `streamProjectEvents` / `streamRoutineEvents`.
function streamErrorEvent(): Event {
  if (typeof Event === "function") {
    return new Event("error");
  }
  return { type: "error" } as unknown as Event;
}
