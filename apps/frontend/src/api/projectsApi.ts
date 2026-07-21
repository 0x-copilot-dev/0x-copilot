// Typed wrappers for the Phase 6 Projects destination.
//
// Surfaces (sub-PRD §4.2):
//   1. `fetchProjects(identity, opts)`              — GET /v1/projects.
//   2. `fetchProject(identity, id)`                 — GET /v1/projects/{id}.
//   3. `createProject / patchProject / deleteProject`
//                                                   — CRUD on a single project.
//   4. `archiveProject / activateProject`           — status transitions.
//   5. `starProject / unstarProject`                — viewer-relative star.
//   6. `transferProjectOwnership`                   — owner reassignment.
//   7. `fetchProjectMembers / addProjectMember / patchProjectMember /
//       removeProjectMember`                         — membership CRUD.
//   8. `fetchProjectActivity`                       — cross-destination event stream.
//   9. `streamProjectEvents({...})`                 — SSE durable channel.
//
// Network rule (CLAUDE.md / `apps/frontend/CLAUDE.md`): apps call the
// **facade** only (`/v1/*`). Never `backend:8100` or `ai-backend:8000`
// directly. The transport singleton enforces this via the same-origin
// Vite proxy → facade.
//
// Wire types are the canonical Projects contract from
// `@0x-copilot/api-types` (`packages/api-types/src/projects.ts`).

import type { RequestIdentity } from "./config";
import { httpDelete, httpGet, httpPatchQuery, httpPostQuery } from "./http";
import { getAppTransport } from "./transport";
import type {
  AddMemberRequest as AddProjectMemberRequest,
  ChangeRoleRequest as UpdateProjectMemberRequest,
  CreateProjectRequest,
  ListProjectsFilters,
  Project,
  ProjectActivityListResponse,
  ProjectId,
  ProjectListResponse,
  ProjectMembership,
  ProjectMembershipListResponse,
  ProjectSortKey,
  ProjectStreamEnvelope,
  TransferOwnershipRequest as TransferProjectOwnershipRequest,
  UpdateProjectRequest,
  UserId,
} from "@0x-copilot/api-types";

const SSE_EVENT_NAME = "project_event";

// ===========================================================================
// LIST
// ===========================================================================

export interface FetchProjectsOptions {
  readonly filters?: ListProjectsFilters;
  readonly q?: string;
  readonly sort?: ProjectSortKey;
  /** Opaque cursor from a previous response's `next_cursor`. */
  readonly after?: string;
  /** 1..200; server default 50. */
  readonly limit?: number;
}

/**
 * GET /v1/projects with allowlisted filters + cursor pagination
 * (sub-PRD §4.2, §4.4, §8). Filter encoding mirrors the routines / inbox
 * APIs — `filter[<axis>]=<value>` keys, single value per axis (the
 * server's allowlist disallows repeated axes per cross-audit §1.5).
 */
export function fetchProjects(
  identity: RequestIdentity,
  options: FetchProjectsOptions = {},
): Promise<ProjectListResponse> {
  return httpGet<ProjectListResponse>(
    "/v1/projects",
    identity,
    encodeListParams(options),
  );
}

// ===========================================================================
// DETAIL
// ===========================================================================

/** GET /v1/projects/{id}. */
export function fetchProject(
  identity: RequestIdentity,
  id: ProjectId,
): Promise<Project> {
  return httpGet<Project>(`/v1/projects/${encodeURIComponent(id)}`, identity);
}

// ===========================================================================
// MUTATIONS
// ===========================================================================

/** POST /v1/projects — create. Creator auto-added as owner-membership row. */
export function createProject(
  identity: RequestIdentity,
  body: CreateProjectRequest,
): Promise<Project> {
  return httpPostQuery<Project>("/v1/projects", body, identity);
}

/** PATCH /v1/projects/{id} — owner-only writes (sub-PRD §7.2). */
export function patchProject(
  identity: RequestIdentity,
  id: ProjectId,
  body: UpdateProjectRequest,
): Promise<Project> {
  return httpPatchQuery<Project>(
    `/v1/projects/${encodeURIComponent(id)}`,
    body,
    identity,
  );
}

/** DELETE /v1/projects/{id} — soft delete (sub-PRD §5.3 tombstone). */
export function deleteProject(
  identity: RequestIdentity,
  id: ProjectId,
): Promise<void> {
  return httpDelete(`/v1/projects/${encodeURIComponent(id)}`, identity);
}

/** POST /v1/projects/{id}/archive — owner-only. */
export function archiveProject(
  identity: RequestIdentity,
  id: ProjectId,
): Promise<Project> {
  return httpPostQuery<Project>(
    `/v1/projects/${encodeURIComponent(id)}/archive`,
    {},
    identity,
  );
}

/** POST /v1/projects/{id}/activate — owner-only. */
export function activateProject(
  identity: RequestIdentity,
  id: ProjectId,
): Promise<Project> {
  return httpPostQuery<Project>(
    `/v1/projects/${encodeURIComponent(id)}/activate`,
    {},
    identity,
  );
}

/** POST /v1/projects/{id}/star — viewer-relative; any member. */
export function starProject(
  identity: RequestIdentity,
  id: ProjectId,
): Promise<Project> {
  return httpPostQuery<Project>(
    `/v1/projects/${encodeURIComponent(id)}/star`,
    {},
    identity,
  );
}

/** POST /v1/projects/{id}/unstar — viewer-relative; any member. */
export function unstarProject(
  identity: RequestIdentity,
  id: ProjectId,
): Promise<Project> {
  return httpPostQuery<Project>(
    `/v1/projects/${encodeURIComponent(id)}/unstar`,
    {},
    identity,
  );
}

/** POST /v1/projects/{id}/transfer — owner reassignment (sub-PRD §3.5.3). */
export function transferProjectOwnership(
  identity: RequestIdentity,
  id: ProjectId,
  body: TransferProjectOwnershipRequest,
): Promise<Project> {
  return httpPostQuery<Project>(
    `/v1/projects/${encodeURIComponent(id)}/transfer`,
    body,
    identity,
  );
}

// ===========================================================================
// MEMBERS (sub-PRD §3.5)
// ===========================================================================

/** GET /v1/projects/{id}/members — members-only read. */
export function fetchProjectMembers(
  identity: RequestIdentity,
  id: ProjectId,
): Promise<ProjectMembershipListResponse> {
  return httpGet<ProjectMembershipListResponse>(
    `/v1/projects/${encodeURIComponent(id)}/members`,
    identity,
  );
}

/** POST /v1/projects/{id}/members — owner-only. */
export function addProjectMember(
  identity: RequestIdentity,
  id: ProjectId,
  body: AddProjectMemberRequest,
): Promise<ProjectMembership> {
  return httpPostQuery<ProjectMembership>(
    `/v1/projects/${encodeURIComponent(id)}/members`,
    body,
    identity,
  );
}

/** PATCH /v1/projects/{id}/members/{user_id} — owner-only role change. */
export function patchProjectMember(
  identity: RequestIdentity,
  id: ProjectId,
  userId: UserId,
  body: UpdateProjectMemberRequest,
): Promise<ProjectMembership> {
  return httpPatchQuery<ProjectMembership>(
    `/v1/projects/${encodeURIComponent(id)}/members/${encodeURIComponent(userId)}`,
    body,
    identity,
  );
}

/**
 * DELETE /v1/projects/{id}/members/{user_id} — owner-only, or self-remove
 * via `/members/me`. Cannot remove owner; caller must transfer first
 * (sub-PRD §3.5.4).
 */
export function removeProjectMember(
  identity: RequestIdentity,
  id: ProjectId,
  userId: UserId | "me",
): Promise<void> {
  return httpDelete(
    `/v1/projects/${encodeURIComponent(id)}/members/${encodeURIComponent(userId)}`,
    identity,
  );
}

// ===========================================================================
// ACTIVITY (sub-PRD §3.6)
// ===========================================================================

export interface FetchProjectActivityOptions {
  /** ItemKind filter (OR per cross-audit §1.5). */
  readonly kind?: string;
  readonly after?: string;
  readonly limit?: number;
}

/** GET /v1/projects/{id}/activity — members + compliance admin. */
export function fetchProjectActivity(
  identity: RequestIdentity,
  id: ProjectId,
  options: FetchProjectActivityOptions = {},
): Promise<ProjectActivityListResponse> {
  const params: Record<string, string | undefined> = {};
  if (options.kind !== undefined) {
    params["filter[kind]"] = options.kind;
  }
  if (options.after !== undefined) {
    params.after = options.after;
  }
  if (options.limit !== undefined) {
    params.limit = String(options.limit);
  }
  return httpGet<ProjectActivityListResponse>(
    `/v1/projects/${encodeURIComponent(id)}/activity`,
    identity,
    params,
  );
}

// ===========================================================================
// SSE (durable project channel — sub-PRD §4.2)
// ===========================================================================

/** Closeable handle for a running project-events SSE subscription. */
export interface ProjectEventsStream {
  close(): void;
}

/**
 * Open the durable project-events SSE stream (sub-PRD §4.2). Each frame
 * carries one `ProjectStreamEnvelope`; the client tracks the highest
 * `sequence_no` and reconnects with `?after_sequence=N` to resume
 * without dropping events (cross-audit §5.2).
 *
 * Reconnect policy is owned caller-side (mirrors `streamInboxEvents` /
 * `streamRoutineEvents`) — the wrapper exposes one connection attempt
 * plus a stable error hook so tests can drive the timing
 * deterministically.
 */
export function streamProjectEvents({
  identity,
  afterSequence,
  onEvent,
  onError,
  onOpen,
}: {
  readonly identity: RequestIdentity;
  /** Highest `sequence_no` already applied; backend replays everything strictly greater. */
  readonly afterSequence?: number;
  readonly onEvent: (envelope: ProjectStreamEnvelope) => void;
  readonly onError: (err: Event) => void;
  readonly onOpen?: () => void;
}): ProjectEventsStream {
  return getAppTransport().subscribeServerSentEvents({
    path: "/v1/projects/stream",
    query: projectSseQueryFor(identity, afterSequence),
    eventName: SSE_EVENT_NAME,
    onOpen,
    onError: (_err) => onError(streamErrorEvent()),
    onMessage: (data) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(data) as unknown;
      } catch {
        // Malformed JSON — drop the frame. Mirrors inboxApi / routinesApi
        // behavior: a single bad frame must not tear down the connection;
        // the caller has `onError` for the broader "stream broken" signal.
        return;
      }
      if (isProjectStreamEnvelope(parsed)) {
        onEvent(parsed);
      }
    },
  });
}

// ===========================================================================
// Helpers
// ===========================================================================

function encodeListParams(
  options: FetchProjectsOptions,
): Record<string, string | undefined> {
  const params: Record<string, string | undefined> = {};
  const { filters, q, sort, after, limit } = options;

  if (filters?.status !== undefined) {
    params["filter[status]"] = filters.status;
  }
  if (filters?.owner_user_id !== undefined) {
    params["filter[owner_user_id]"] = filters.owner_user_id;
  }
  if (filters?.member_user_id !== undefined) {
    params["filter[member_user_id]"] = filters.member_user_id;
  }
  if (filters?.starred === true) {
    params["filter[starred]"] = "true";
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

function projectSseQueryFor(
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
 * fields from sub-PRD §4.1 — `sequence_no` (number), `event_type`
 * (string), `project_id` (string), `payload` (object), `emitted_at`
 * (string). Same pattern as `isInboxStreamEnvelope` /
 * `isRoutineStreamEnvelope`.
 */
function isProjectStreamEnvelope(
  value: unknown,
): value is ProjectStreamEnvelope {
  if (value === null || typeof value !== "object") {
    return false;
  }
  const v = value as Record<string, unknown>;
  return (
    typeof v.sequence_no === "number" &&
    typeof v.event_type === "string" &&
    typeof v.project_id === "string" &&
    typeof v.emitted_at === "string" &&
    typeof v.payload === "object" &&
    v.payload !== null
  );
}

// The legacy onError signature was modelled after EventSource's bare
// Event — callers only react to "stream broken" and reconnect. Mirrors
// `streamInboxEvents` / `streamRoutineEvents`.
function streamErrorEvent(): Event {
  if (typeof Event === "function") {
    return new Event("error");
  }
  return { type: "error" } as unknown as Event;
}
