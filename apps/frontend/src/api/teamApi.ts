// Typed wrappers for the Phase 12 Team destination
// (sub-PRD `team-memory-cmdk-prd.md` §4.1).
//
// Endpoints:
//   * `fetchTeam(identity, opts)`              — GET    /v1/team
//   * `fetchPerson(identity, id)`              — GET    /v1/team/{id}
//   * `invitePerson(identity, body)`           — POST   /v1/team/invite
//   * `patchPersonRole(identity, id, body)`    — PATCH  /v1/team/{id}/role
//   * `offboardPerson(identity, id, body)`     — POST   /v1/team/{id}/offboard
//   * `streamTeamEvents({...})`                — SSE    /v1/team/stream
//
// Network rule: apps call the **facade** only (`/v1/*`). The transport
// singleton enforces this via the same-origin Vite proxy → facade
// (`CLAUDE.md` / `apps/frontend/CLAUDE.md`).
//
// Mirrors the routinesApi / toolsApi / connectorsApi shape — pure
// adapter functions, presentation lives elsewhere.

import type {
  InviteRequest,
  OffboardingRequest,
  Person,
  PersonDetailResponse,
  Presence,
  ProjectId,
  TeamListResponse,
  TeamListSort,
  TeamRole,
  TeamStreamEnvelope,
  UpdateTeamRoleRequest,
  UserId,
} from "@enterprise-search/api-types";

import type { RequestIdentity } from "./config";
import { httpGet, httpPatchQuery, httpPostQuery } from "./http";
import { getAppTransport } from "./transport";

const SSE_EVENT_NAME = "team_event";

// ===========================================================================
// LIST
// ===========================================================================

export interface FetchTeamOptions {
  readonly role?: TeamRole;
  readonly presence?: Presence;
  readonly q?: string;
  readonly sort?: TeamListSort;
  readonly after?: string;
  /** 1..200; server default 50. */
  readonly limit?: number;
  /** Admin-only — project scope filter (cross-audit §1.3). */
  readonly project_id?: ProjectId;
}

/**
 * GET /v1/team — list workspace members. Filter axes mirror sub-PRD §4.1
 * (`role`, `presence`, `q`) — single value per axis per the cross-audit
 * §1.5 allowlist convention.
 */
export function fetchTeam(
  identity: RequestIdentity,
  options: FetchTeamOptions = {},
): Promise<TeamListResponse> {
  return httpGet<TeamListResponse>(
    "/v1/team",
    identity,
    encodeListParams(options),
  );
}

// ===========================================================================
// DETAIL
// ===========================================================================

/**
 * GET /v1/team/{id} — person detail. `recent_activity` is admin-only;
 * non-admins receive an empty array (sub-PRD §6.1 — 404-not-403 is
 * reserved for the row itself).
 */
export function fetchPerson(
  identity: RequestIdentity,
  id: UserId,
): Promise<PersonDetailResponse> {
  return httpGet<PersonDetailResponse>(
    `/v1/team/${encodeURIComponent(id)}`,
    identity,
  );
}

// ===========================================================================
// MUTATIONS
// ===========================================================================

/** POST /v1/team/invite — admin only (sub-PRD §6.1). */
export function invitePerson(
  identity: RequestIdentity,
  body: InviteRequest,
): Promise<Person> {
  return httpPostQuery<Person>("/v1/team/invite", body, identity);
}

/**
 * PATCH /v1/team/{id}/role — admin only. Server-side invariants reject
 * with 409 on "cannot demote self" / "cannot remove sole owner".
 */
export function patchPersonRole(
  identity: RequestIdentity,
  id: UserId,
  body: UpdateTeamRoleRequest,
): Promise<Person> {
  return httpPatchQuery<Person>(
    `/v1/team/${encodeURIComponent(id)}/role`,
    body,
    identity,
  );
}

/**
 * POST /v1/team/{id}/offboard — admin offboarding wizard. Server
 * applies the per-asset reassignments in one transaction (sub-PRD §6.1).
 */
export function offboardPerson(
  identity: RequestIdentity,
  id: UserId,
  body: OffboardingRequest,
): Promise<PersonDetailResponse> {
  return httpPostQuery<PersonDetailResponse>(
    `/v1/team/${encodeURIComponent(id)}/offboard`,
    body,
    identity,
  );
}

// ===========================================================================
// SSE — `GET /v1/team/stream`
// ===========================================================================

export interface TeamEventsStream {
  close(): void;
}

/**
 * Open the durable team-events SSE channel (sub-PRD §4.1). Each frame
 * carries one `TeamStreamEnvelope`; clients track the highest
 * `sequence_no` and reconnect with `?after_sequence=N` to resume
 * without dropping events (cross-audit §5.2). Mirrors
 * `streamRoutineEvents` / `streamConnectorEvents`.
 */
export function streamTeamEvents({
  identity,
  afterSequence,
  projectId,
  onEvent,
  onError,
  onOpen,
}: {
  readonly identity: RequestIdentity;
  readonly afterSequence?: number;
  /** Optional admin project-scope filter (sub-PRD §4.1 / cross-audit §1.3). */
  readonly projectId?: ProjectId;
  readonly onEvent: (envelope: TeamStreamEnvelope) => void;
  readonly onError: (err: Event) => void;
  readonly onOpen?: () => void;
}): TeamEventsStream {
  return getAppTransport().subscribeServerSentEvents({
    path: "/v1/team/stream",
    query: streamQueryFor(identity, afterSequence, projectId),
    eventName: SSE_EVENT_NAME,
    onOpen,
    onError: (_err) => onError(streamErrorEvent()),
    onMessage: (data) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(data) as unknown;
      } catch {
        return;
      }
      if (isTeamStreamEnvelope(parsed)) {
        onEvent(parsed);
      }
    },
  });
}

// ===========================================================================
// Helpers
// ===========================================================================

function encodeListParams(
  options: FetchTeamOptions,
): Record<string, string | undefined> {
  const params: Record<string, string | undefined> = {};
  const { role, presence, q, sort, after, limit, project_id } = options;
  if (role !== undefined) params["filter[role]"] = role;
  if (presence !== undefined) params["filter[presence]"] = presence;
  if (project_id !== undefined) params["filter[project_id]"] = project_id;
  if (q !== undefined && q.length > 0) params.q = q;
  if (sort !== undefined) params.sort = sort;
  if (after !== undefined) params.after = after;
  if (limit !== undefined) params.limit = String(limit);
  return params;
}

function streamQueryFor(
  identity: RequestIdentity,
  afterSequence: number | undefined,
  projectId: ProjectId | undefined,
): Record<string, string> {
  const out: Record<string, string> = {
    org_id: identity.orgId,
    user_id: identity.userId,
  };
  if (afterSequence !== undefined) {
    out.after_sequence = String(afterSequence);
  }
  if (projectId !== undefined) {
    out.project_id = projectId;
  }
  return out;
}

function isTeamStreamEnvelope(value: unknown): value is TeamStreamEnvelope {
  if (value === null || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.event_id === "string" &&
    typeof v.sequence_no === "number" &&
    typeof v.event_type === "string" &&
    typeof v.created_at === "string"
  );
}

function streamErrorEvent(): Event {
  if (typeof Event === "function") {
    return new Event("error");
  }
  return { type: "error" } as unknown as Event;
}
