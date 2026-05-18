// Local stub for the Phase 6 Projects wire contract.
//
// The canonical types live in `@enterprise-search/api-types`
// (`packages/api-types/src/projects.ts`), authored by the parallel
// Phase 6 P6-A backend-types agent. This frontend wave (P6-C) runs in
// parallel against the same sub-PRD spec and cannot import a type that
// is not yet on `main`, so this stub mirrors the shapes in
// `docs/atlas-new-design/destinations/projects-prd.md` §4.1.
//
// `ProjectId`, `TenantId`, `UserId`, and the cross-destination
// `ItemRef` / `ItemKind` union already live in
// `@enterprise-search/api-types` — re-export from there so the
// `<ItemLink>` registry stays a single source of truth even before
// the rest of the Projects contract merges.
//
// TODO(merge): delete this file. Replace every `_projects-stub` import
// with `@enterprise-search/api-types` once P6-A's
// `packages/api-types/src/projects.ts` lands on main.

import type {
  ItemKind,
  ItemRef,
  ProjectId,
  TenantId,
  UserId,
} from "@enterprise-search/api-types";

export type { ItemKind, ItemRef, ProjectId, TenantId, UserId };

// ===========================================================================
// Enums (§4.1)
// ===========================================================================

export type ProjectStatus = "active" | "archived";

export type ProjectRole = "owner" | "editor" | "viewer";

/** Color hue (HSL 0–359). Lightness + saturation are design-system fixed. */
export type ProjectColorHue = number;

/** Validated server-side as a single emoji glyph. */
export type ProjectIconEmoji = string;

// ===========================================================================
// Counts (§4.1)
// ===========================================================================

export interface ProjectActivityCounts {
  readonly chats: number;
  readonly todos_open: number;
  readonly todos_done: number;
  readonly inbox_items: number;
  readonly library_items: number;
  readonly routines_active: number;
  readonly members: number;
}

// ===========================================================================
// Project (§4.1)
// ===========================================================================

export interface Project {
  readonly id: ProjectId;
  readonly tenant_id: TenantId;
  readonly owner_user_id: UserId;
  readonly name: string;
  readonly description: string;
  readonly icon_emoji: ProjectIconEmoji;
  readonly color_hue: ProjectColorHue;
  readonly status: ProjectStatus;
  readonly archived_at: string | null;
  readonly created_at: string;
  readonly updated_at: string;
  readonly last_activity_at: string | null;
  readonly counts: ProjectActivityCounts;
  readonly viewer_role: ProjectRole | null;
  readonly viewer_starred: boolean;
}

export interface ProjectSummary {
  readonly id: ProjectId;
  readonly tenant_id: TenantId;
  readonly name: string;
  readonly description: string;
  readonly icon_emoji: ProjectIconEmoji;
  readonly color_hue: ProjectColorHue;
  readonly status: ProjectStatus;
  readonly owner_user_id: UserId;
  readonly viewer_role: ProjectRole | null;
  readonly viewer_starred: boolean;
  readonly counts: ProjectActivityCounts;
  readonly last_activity_at: string | null;
  readonly updated_at: string;
}

export interface ProjectMembership {
  readonly project_id: ProjectId;
  readonly user_id: UserId;
  readonly role: ProjectRole;
  readonly added_at: string;
  readonly added_by: UserId;
}

export interface ProjectActivity {
  readonly id: string;
  readonly tenant_id: TenantId;
  readonly project_id: ProjectId;
  readonly actor_user_id: UserId | null;
  readonly actor_display_name: string;
  readonly action: string;
  readonly kind: ItemKind;
  readonly ref: ItemRef;
  readonly preview: string;
  readonly occurred_at: string;
}

// ===========================================================================
// List filters + sort (§4.4)
// ===========================================================================

export type ProjectSortKey =
  | "updated_at:desc"
  | "updated_at:asc"
  | "name:asc"
  | "name:desc"
  | "created_at:desc"
  | "last_activity_at:desc";

export interface ListProjectsFilters {
  readonly status?: ProjectStatus;
  readonly owner_user_id?: UserId;
  readonly member_user_id?: UserId;
  readonly starred?: boolean;
}

// ===========================================================================
// List responses (§4.1)
// ===========================================================================

export interface ProjectListResponse {
  readonly items: ReadonlyArray<ProjectSummary>;
  readonly next_cursor: string | null;
}

export interface ProjectMembershipListResponse {
  readonly items: ReadonlyArray<ProjectMembership>;
  readonly next_cursor: string | null;
}

export interface ProjectActivityListResponse {
  readonly items: ReadonlyArray<ProjectActivity>;
  readonly next_cursor: string | null;
}

// ===========================================================================
// Mutations (§4.2)
// ===========================================================================

/** Body for `POST /v1/projects`. */
export interface CreateProjectRequest {
  readonly name: string;
  readonly description?: string;
  readonly icon_emoji: ProjectIconEmoji;
  readonly color_hue: ProjectColorHue;
}

/** Body for `PATCH /v1/projects/{id}`. */
export interface UpdateProjectRequest {
  readonly name?: string;
  readonly description?: string;
  readonly icon_emoji?: ProjectIconEmoji;
  readonly color_hue?: ProjectColorHue;
  readonly status?: ProjectStatus;
}

/** Body for `POST /v1/projects/{id}/members`. */
export interface AddProjectMemberRequest {
  readonly user_id: UserId;
  readonly role: ProjectRole;
}

/** Body for `PATCH /v1/projects/{id}/members/{user_id}`. */
export interface UpdateProjectMemberRequest {
  readonly role: ProjectRole;
}

/** Body for `POST /v1/projects/{id}/transfer`. */
export interface TransferProjectOwnershipRequest {
  readonly new_owner_user_id: UserId;
  readonly previous_owner_new_role?: ProjectRole;
}

// ===========================================================================
// SSE envelope (§4.1 — durable stream)
// ===========================================================================

export type ProjectStreamEventType =
  | "project_created"
  | "project_updated"
  | "project_archived"
  | "project_activated"
  | "project_deleted"
  | "project_member_added"
  | "project_member_removed"
  | "project_member_role_changed"
  | "project_ownership_transferred"
  | "project_activity_appended";

/**
 * Discriminated payload union per sub-PRD §4.1. Membership / state-change
 * envelopes carry only the small descriptor — clients refetch the full
 * project on `project_member_added` for the current user (auto-add to rail).
 */
export type ProjectStreamPayload =
  | ProjectSummary
  | Project
  | ProjectMembership
  | ProjectActivity
  | {
      readonly project_id: ProjectId;
      readonly user_id?: UserId;
      readonly archived_at?: string;
      readonly activated_at?: string;
      readonly from_user_id?: UserId;
      readonly to_user_id?: UserId;
      readonly previous_owner_new_role?: ProjectRole;
    };

export interface ProjectStreamEnvelope {
  readonly sequence_no: number;
  readonly event_type: ProjectStreamEventType;
  readonly project_id: ProjectId;
  readonly payload: ProjectStreamPayload;
  readonly emitted_at: string;
}
