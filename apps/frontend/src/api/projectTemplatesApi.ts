// Typed wrappers for the Phase 6.5 Project Templates surface.
//
// Surfaces (sub-PRD `projects-extensions-prd.md` §7.3):
//   1. `fetchProjectTemplates(identity, opts)` — GET /v1/project-templates.
//   2. `fetchProjectTemplate(identity, id)`    — GET /v1/project-templates/{id}.
//   3. `forkProjectTemplate(identity, id, body)`
//                                              — POST /v1/project-templates/{id}/fork.
//   4. `patchProjectTemplate(identity, id, body)`
//                                              — PATCH /v1/project-templates/{id}
//                                                (name + description only — snapshot is
//                                                immutable per §7.5).
//   5. `deleteProjectTemplate(identity, id)`   — DELETE /v1/project-templates/{id}
//                                                (soft-delete; 90d retention).
//   6. `saveProjectAsTemplate(identity, projectId, body)`
//                                              — POST /v1/projects/{id}/save-as-template
//                                                (the inverse: snapshot an existing
//                                                project as a new template).
//
// Network rule (CLAUDE.md / `apps/frontend/CLAUDE.md`): apps call the
// **facade** only (`/v1/*`). Never `backend:8100` or `ai-backend:8000`
// directly. The transport singleton enforces this via the same-origin
// Vite proxy → facade.
//
// Wire types are declared in-file (no `_project-templates-stub.ts`
// sibling) because P6.5-A1 has not yet landed
// `packages/api-types/src/project-templates.ts` on main and this file is
// the only consumer. The shapes mirror sub-PRD §7.2 verbatim.
//
// TODO(merge): once `@0x-copilot/api-types/src/project-templates.ts`
// lands on main, delete the local type declarations and re-export from
// `@0x-copilot/api-types`.

import type { ProjectId, TenantId, UserId } from "@0x-copilot/api-types";

import type { RequestIdentity } from "./config";
import { httpDelete, httpGet, httpPatchQuery, httpPostQuery } from "./http";

// ===========================================================================
// Wire types (sub-PRD §7.2 — `packages/api-types/src/project-templates.ts`
// shape, mirrored locally pending P6.5-A1 merge).
// ===========================================================================

/**
 * Local stub for the not-yet-exported `ProjectTemplateId` brand.
 * Matches the existing `*Id` brand pattern in
 * `packages/api-types/src/brands.ts`.
 *
 * TODO(merge): replace with the canonical brand once P6.5-A1 adds it.
 */
export type ProjectTemplateId = string & {
  readonly __brand: "ProjectTemplateId";
};

/** Connector slug (e.g. `"google_drive"`). Plain string at the wire. */
export type ConnectorSlug = string;

export interface ProjectTemplateSeededTodo {
  readonly text: string;
  readonly priority: "low" | "normal" | "high" | null;
  readonly relative_due_days: number | null;
  readonly labels: ReadonlyArray<string>;
}

export interface ProjectTemplateSeededRoutineTrigger {
  readonly kind: "schedule" | "manual";
  readonly cron?: string;
  readonly tz?: string;
}

export interface ProjectTemplateSeededRoutine {
  readonly name: string;
  readonly description: string;
  readonly instructions_template: string;
  readonly triggers: ReadonlyArray<ProjectTemplateSeededRoutineTrigger>;
}

export interface ProjectTemplateSnapshot {
  readonly default_member_user_ids: ReadonlyArray<UserId>;
  readonly default_connector_allowlist: ReadonlyArray<ConnectorSlug> | null;
  readonly color_hue: number | null;
  readonly icon_emoji: string | null;
  readonly seeded_todos: ReadonlyArray<ProjectTemplateSeededTodo>;
  readonly seeded_routines: ReadonlyArray<ProjectTemplateSeededRoutine>;
}

export interface ProjectTemplate {
  readonly id: ProjectTemplateId;
  readonly tenant_id: TenantId;
  readonly owner_user_id: UserId;
  readonly name: string;
  readonly description: string;
  readonly snapshot: ProjectTemplateSnapshot;
  readonly source_project_id: ProjectId | null;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface ProjectTemplateListResponse {
  readonly items: ReadonlyArray<ProjectTemplate>;
  readonly next_cursor: string | null;
}

// ---- Request bodies (§7.3) -----------------------------------------------

export type ProjectTemplateSortKey = "created_at:desc" | "created_at:asc";

export interface ListProjectTemplatesFilters {
  /** Owner of the template; one user id per call (cross-audit §1.5). */
  readonly owner_user_id?: UserId;
}

export interface ForkProjectTemplateRequest {
  readonly name: string;
  readonly description?: string;
  readonly color_hue?: number;
  readonly icon_emoji?: string;
  /** Optional override of `snapshot.default_member_user_ids`. */
  readonly member_overrides?: ReadonlyArray<UserId>;
  /** Optional override of `snapshot.default_connector_allowlist`. */
  readonly connector_overrides?: ReadonlyArray<ConnectorSlug>;
}

export interface UpdateProjectTemplateRequest {
  /** Editable metadata (sub-PRD §7.5 — snapshot is immutable). */
  readonly name?: string;
  readonly description?: string;
}

export interface SaveProjectAsTemplateRequest {
  /** Defaults server-side to the source project's name + " (template)". */
  readonly name?: string;
  readonly description?: string;
}

/**
 * Response of `POST /v1/project-templates/{id}/fork` — the newly created
 * project. Wire shape is the standard `Project` from the Projects API.
 *
 * Typed as `{ id: ProjectId }` plus an index signature here so callers
 * can navigate to the new project without forcing a `_projects-stub`
 * import in this file. The Projects API surface lifts the full row.
 */
export interface ProjectTemplateForkResponse {
  readonly id: ProjectId;
  readonly [key: string]: unknown;
}

// ===========================================================================
// LIST
// ===========================================================================

export interface FetchProjectTemplatesOptions {
  readonly filters?: ListProjectTemplatesFilters;
  /** Full-text search across name + description (§7.3). */
  readonly q?: string;
  readonly sort?: ProjectTemplateSortKey;
  readonly after?: string;
  readonly limit?: number;
}

/**
 * GET /v1/project-templates with allowlisted filters + cursor pagination
 * (sub-PRD §7.3). Tenant-scoped: any tenant member can read every template
 * in their tenant; the server enforces the tenant boundary against the
 * caller's identity.
 *
 * Mirrors the `fetchProjects` filter-encoding contract (`filter[<axis>]=...`,
 * one value per axis).
 */
export function fetchProjectTemplates(
  identity: RequestIdentity,
  options: FetchProjectTemplatesOptions = {},
): Promise<ProjectTemplateListResponse> {
  return httpGet<ProjectTemplateListResponse>(
    "/v1/project-templates",
    identity,
    encodeListParams(options),
  );
}

// ===========================================================================
// DETAIL
// ===========================================================================

/** GET /v1/project-templates/{id}. */
export function fetchProjectTemplate(
  identity: RequestIdentity,
  id: ProjectTemplateId,
): Promise<ProjectTemplate> {
  return httpGet<ProjectTemplate>(
    `/v1/project-templates/${encodeURIComponent(id)}`,
    identity,
  );
}

// ===========================================================================
// MUTATIONS
// ===========================================================================

/**
 * PATCH /v1/project-templates/{id} — owner-only metadata edit. The
 * snapshot itself is immutable; PATCH on `snapshot` returns 422 server-side
 * (sub-PRD §7.5).
 */
export function patchProjectTemplate(
  identity: RequestIdentity,
  id: ProjectTemplateId,
  body: UpdateProjectTemplateRequest,
): Promise<ProjectTemplate> {
  return httpPatchQuery<ProjectTemplate>(
    `/v1/project-templates/${encodeURIComponent(id)}`,
    body,
    identity,
  );
}

/**
 * DELETE /v1/project-templates/{id} — soft-delete; 90d retention then
 * hard-delete (sub-PRD §7.8).
 */
export function deleteProjectTemplate(
  identity: RequestIdentity,
  id: ProjectTemplateId,
): Promise<void> {
  return httpDelete(
    `/v1/project-templates/${encodeURIComponent(id)}`,
    identity,
  );
}

/**
 * POST /v1/project-templates/{id}/fork — instantiate a new Project from
 * the template. Caller becomes the new project's owner regardless of who
 * authored the template (sub-PRD §7.3 ACL).
 */
export function forkProjectTemplate(
  identity: RequestIdentity,
  id: ProjectTemplateId,
  body: ForkProjectTemplateRequest,
): Promise<ProjectTemplateForkResponse> {
  return httpPostQuery<ProjectTemplateForkResponse>(
    `/v1/project-templates/${encodeURIComponent(id)}/fork`,
    body,
    identity,
  );
}

/**
 * POST /v1/projects/{id}/save-as-template — snapshot the source project's
 * current configuration into a new template. Caller must be the source
 * project's owner (sub-PRD §7.3 ACL).
 */
export function saveProjectAsTemplate(
  identity: RequestIdentity,
  projectId: ProjectId,
  body: SaveProjectAsTemplateRequest = {},
): Promise<ProjectTemplate> {
  return httpPostQuery<ProjectTemplate>(
    `/v1/projects/${encodeURIComponent(projectId)}/save-as-template`,
    body,
    identity,
  );
}

// ===========================================================================
// Helpers
// ===========================================================================

function encodeListParams(
  options: FetchProjectTemplatesOptions,
): Record<string, string | undefined> {
  const params: Record<string, string | undefined> = {};
  const { filters, q, sort, after, limit } = options;

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
