// Projects destination (Phase 6) — CRUD + membership + ownership transfer
// + activity stream wire contract.
//
// Source: docs/atlas-new-design/destinations/projects-prd.md §3 (architecture)
// + §4 (wire contracts) + §6 (audit) + §7 (canonical ACL); and
// docs/atlas-new-design/cross-audit.md §1.1 (ItemRef incl. kind="project"),
// §1.3 (project-scoped ACL master rule — Projects owns the canonical
// resolver), §1.4 (audit context), §1.5 (multi-value OR filter axes),
// §2.1 (branded ProjectId), §3.3 (ItemLink registry), §5.3 (cascade →
// dead-link default).
//
// Wire-only file: no business logic, no HTTP client, no view models.
// The server is the source of truth; this package mirrors the public
// payloads exactly as the facade serves them. Internal
// `/internal/v1/projects/*` membership-resolution contracts are NOT
// mirrored here — those live behind the service boundary.
//
// Per cross-audit §1.3 the predicate `is_member(tenant, project, user)`
// has ONE implementation. The wire shape exposes the *result* of that
// check via `viewer_role` on `Project` / `ProjectSummary`; the predicate
// itself never crosses the wire.

import type { ItemKind, ItemRef } from "./refs";
import type { AgentId, ProjectId, TenantId, UserId } from "./brands";

// ---------------------------------------------------------------------------
// Phase 6.5 §5 — Connector inheritance.
// ---------------------------------------------------------------------------

/**
 * Connector "kind" — `"salesforce"`, `"gmail"`, etc. Distinct from
 * `ConnectorId` (a specific OAuth grant). Allowlists travel as kinds so
 * a re-grant does not invalidate the rule.
 */
export type ConnectorSlug = string;

// ---------------------------------------------------------------------------
// Primitive enums
// ---------------------------------------------------------------------------

/**
 * Project lifecycle status. Single bit — `active` or `archived`. No
 * `draft` / `paused`. cross-audit §1.6 status taxonomy.
 *
 *   active ──archive──▶ archived ──activate──▶ active
 *
 * Mutations on `archived` projects return 409 (must activate first). In-flight
 * Routine fires complete; new fires pause at fire time per projects-prd
 * §11.3 (Q4 product decision: complete-not-halt).
 */
export type ProjectStatus = "active" | "archived";

/**
 * Project-local role. Distinct from the tenant role
 * (`owner` / `admin` / `member` / `guest`); project roles do not change
 * the user's tenant role.
 *
 *  - `owner` — full write authority on the project + members + transfer.
 *    Exactly ONE per project (PARTIAL UNIQUE on `(project_id) WHERE
 *    role='owner'`).
 *  - `editor` — read + write own child resources (own todos, own routines,
 *    etc); NO membership / metadata writes on the project itself.
 *  - `viewer` — read-only on project + project-scoped child resources.
 *
 * projects-prd §7.2 — admin force-transfer is the only path that mutates
 * membership without owner consent; see `POST /v1/admin/projects/{id}/
 * force-transfer` and the `project.admin_force_transferred` audit action.
 */
export type ProjectRole = "owner" | "editor" | "viewer";

/** HSL hue (0–359). Lightness + saturation are design-system fixed. */
export type ProjectColorHue = number;

/**
 * Single emoji glyph (server-validated). Skin-tone variants per Unicode
 * emoji ZWJ rules are allowed; multi-codepoint strings beyond that are
 * rejected at create / update.
 */
export type ProjectIconEmoji = string;

// ---------------------------------------------------------------------------
// Activity counts (denormalized for list-view perf)
// ---------------------------------------------------------------------------

/**
 * Per-project activity rollup. Refreshed by the projector on every
 * `project_activity` insert; reconciled nightly from authoritative tables
 * to repair drift (projects-prd §3.7 + §5.4 retention cron).
 *
 * `inbox_items` is the **viewer's** count — Inbox visibility is per-recipient,
 * so the rollup is filtered to the calling user (cross-audit §1.3 does NOT
 * override Inbox's recipient-only read).
 */
export interface ProjectActivityCounts {
  readonly chats: number;
  readonly todos_open: number;
  readonly todos_done: number;
  readonly inbox_items: number;
  readonly library_items: number;
  readonly routines_active: number;
  readonly members: number;
}

// ---------------------------------------------------------------------------
// Core entity shapes
// ---------------------------------------------------------------------------

/**
 * Full project payload. `viewer_role` and `viewer_starred` are
 * caller-relative — set by the server from the verified bearer's identity,
 * never accepted from the client.
 *
 * `archived_at` is present iff `status === "archived"` (server-enforced
 * invariant; cross-audit §3.4 timeline rule).
 *
 * `last_activity_at` is denormalized — projector advances it on every
 * `project_activity` insert.
 */
export interface Project {
  readonly id: ProjectId;
  readonly tenant_id: TenantId;
  readonly owner_user_id: UserId;
  /** ≤ 80 chars; UNIQUE per `(tenant_id, lower(name))`. */
  readonly name: string;
  /** ≤ 400 chars; defaults to ''. */
  readonly description: string;
  readonly icon_emoji: ProjectIconEmoji;
  readonly color_hue: ProjectColorHue;
  readonly status: ProjectStatus;
  /** ISO-8601 UTC. Present iff status='archived'. */
  readonly archived_at: string | null;
  /** ISO-8601 UTC. */
  readonly created_at: string;
  /** ISO-8601 UTC. */
  readonly updated_at: string;
  /** ISO-8601 UTC. Denormalized; advanced by the activity projector. */
  readonly last_activity_at: string | null;
  readonly counts: ProjectActivityCounts;
  /**
   * Caller-relative — present iff the caller is a member (or owner).
   * `null` means "caller has admin-compliance read but is not a member"
   * (tenant-admin reading a non-member project; audited at the route
   * layer).
   */
  readonly viewer_role: ProjectRole | null;
  readonly viewer_starred: boolean;
  /**
   * Phase 6.5 §5 — connector allowlist for new chats / routines created
   * with this project's id. `null` (or absent) means "inherit owner
   * defaults at create time"; `[]` is explicit deny; `[...]` is the
   * allowlist of connector kinds. Owner-only field; readable by every
   * project member.
   */
  readonly default_connector_allowlist?: ReadonlyArray<ConnectorSlug> | null;
  /**
   * Phase 8 §1.5 / §8 — default Agent for new chats created under this
   * project. `null` (or absent) means "no project default; the chat
   * composer falls back to the user's workspace default agent". When
   * set, new chats pre-fill `Conversation.agent_id = default_agent_id`
   * at create time; an explicit picker on the composer overrides
   * per-chat (agents-prd §1.5 U5). Owner-only writable; readable by
   * every project member. Applies the same create-time inheritance
   * pattern as `default_connector_allowlist` (projects-prd §12 Q3
   * resolution).
   *
   * If the referenced agent is uninstalled or disabled at chat-create
   * time, the pre-fill is skipped (agents-prd §4.6 cascade); the field
   * is preserved on the project so re-install restores the behavior.
   */
  readonly default_agent_id?: AgentId | null;
}

// ---------------------------------------------------------------------------
// Phase 6.5 §3 — Liveness aggregator wire shape (returned embedded in
// the 409 archive response body; never exposed standalone to the FE).
// ---------------------------------------------------------------------------

export type LivenessDetailSource =
  | "ai_backend.runs"
  | "ai_backend.approvals"
  | "backend.routines"
  | "backend.inbox";

export interface LivenessDetail {
  readonly source: LivenessDetailSource;
  readonly count: number;
  readonly is_alive: boolean;
  readonly error: string | null;
  readonly fetched_at: string;
}

export interface LivenessReport {
  readonly project_id: ProjectId;
  readonly tenant_id: TenantId;
  readonly is_alive: boolean;
  readonly active_runs: number;
  readonly pending_approvals: number;
  readonly active_routines: number;
  readonly in_flight_inbox: number;
  readonly details: ReadonlyArray<LivenessDetail>;
  readonly computed_at: string;
  readonly cache_hit: boolean;
}

/**
 * 409 body shape returned by `DELETE /v1/projects/{id}` when the
 * pre-archive liveness check finds the project alive (§6.1).
 */
export interface ProjectArchiveBlockedResponse {
  readonly error: "project_archive_blocked_live_work";
  readonly message: string;
  readonly liveness: LivenessReport;
}

// ---------------------------------------------------------------------------
// Phase 6.5 §7 — Project templates.
// ---------------------------------------------------------------------------

export interface ProjectTemplateSeededTodo {
  readonly text: string;
  readonly priority: "low" | "normal" | "high" | null;
  readonly relative_due_days: number | null;
  readonly labels: ReadonlyArray<string>;
}

export interface ProjectTemplateSeededRoutine {
  readonly name: string;
  readonly description: string;
  readonly instructions_template: string;
  readonly triggers: ReadonlyArray<{
    readonly kind: "schedule" | "manual";
    readonly cron?: string;
    readonly tz?: string;
  }>;
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
  readonly id: string;
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

export interface SaveAsTemplateRequest {
  readonly name: string;
  readonly description?: string;
  readonly seeded_todos?: ReadonlyArray<ProjectTemplateSeededTodo>;
  readonly seeded_routines?: ReadonlyArray<ProjectTemplateSeededRoutine>;
}

export interface ForkProjectTemplateRequest {
  readonly name: string;
  readonly description?: string;
  readonly color_hue?: number;
  readonly icon_emoji?: string;
  readonly member_overrides?: ReadonlyArray<UserId>;
  readonly connector_overrides?: ReadonlyArray<ConnectorSlug>;
}

export interface UpdateProjectTemplateRequest {
  readonly name?: string;
  readonly description?: string;
}

/**
 * Lightweight projection for list endpoints, ItemLink resolvers, and the
 * composer's project mention popover. Drops the full `archived_at` /
 * `created_at` metadata triplet in favor of the fields a list row needs.
 *
 * projects-prd §3.9 / §10 — the composer `<MentionPopover>` reads from
 * `GET /v1/projects?q=…&limit=20` and renders one row per `ProjectSummary`.
 */
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
  /**
   * Denormalized owner display name — server-projected for list-view perf
   * so a row can render "owned by X" without a second identity lookup.
   * Absent when the projection is unavailable (older payloads / stores that
   * don't populate it); the UI degrades to no owner label.
   */
  readonly owner_display_name?: string;
}

/**
 * One membership row. `role` is project-local (`owner` / `editor` /
 * `viewer`) — distinct from the tenant role.
 *
 * Email addresses are NOT carried (projects-prd §7.6); the frontend
 * resolves them via the Team destination, which has its own visibility
 * rules.
 */
export interface ProjectMembership {
  readonly project_id: ProjectId;
  readonly user_id: UserId;
  readonly role: ProjectRole;
  /** ISO-8601 UTC. */
  readonly added_at: string;
  readonly added_by: UserId;
}

/**
 * One projected activity row. Server-side projector copies the
 * discriminating fields (actor + action + ref + occurred_at) from the
 * source audit row, keyed by `project_id` so the activity tab list is
 * O(rows) not O(destinations × rows).
 *
 * `actor_display_name` is denormalized at projection time; user rename
 * does NOT retroactively rewrite (a small 24h refresh cron handles
 * recent rows; deeper history is the historical name — a feature for
 * forensics, not a bug).
 */
export interface ProjectActivity {
  readonly id: string;
  readonly tenant_id: TenantId;
  readonly project_id: ProjectId;
  /** `null` = system / automation actor. */
  readonly actor_user_id: UserId | null;
  readonly actor_display_name: string;
  /** Dotted form mirroring audit (e.g. `"todo.created"`). */
  readonly action: string;
  readonly kind: ItemKind;
  readonly ref: ItemRef;
  /** ≤ 200 chars; denormalized title / summary. */
  readonly preview: string;
  /** ISO-8601 UTC. */
  readonly occurred_at: string;
}

// ---------------------------------------------------------------------------
// List query axes (sort + filter)
// ---------------------------------------------------------------------------

/**
 * Sort axis for `GET /v1/projects`. `field:direction`; the server keyset-
 * paginates on the chosen axis and encodes the pointer into `next_cursor`.
 */
export type ProjectSortKey =
  | "updated_at:desc"
  | "updated_at:asc"
  | "name:asc"
  | "name:desc"
  | "created_at:desc"
  | "last_activity_at:desc";

/**
 * Multi-axis filter for `GET /v1/projects`. Each axis narrows the result
 * set (AND across axes); cross-audit §1.5 multi-value OR semantics apply
 * within an axis when the server accepts repeated values.
 */
export interface ListProjectsFilters {
  readonly status?: ProjectStatus;
  readonly owner_user_id?: UserId;
  readonly member_user_id?: UserId;
  readonly starred?: boolean;
}

// ---------------------------------------------------------------------------
// List / mutation payloads
// ---------------------------------------------------------------------------

/**
 * Cursor-paginated list response. `next_cursor` is opaque (server encodes
 * the keyset pointer per the sort axis); the client passes it back
 * verbatim. Absent / null means "no more pages".
 */
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

/**
 * POST `/v1/projects` body. Server stamps `id`, `tenant_id`,
 * `owner_user_id`, `status` (`'active'`), `created_at`, `updated_at`. The
 * creator is auto-added as the owner-membership row in the same
 * transaction.
 */
export interface CreateProjectRequest {
  readonly name: string;
  readonly description?: string;
  readonly icon_emoji: ProjectIconEmoji;
  readonly color_hue: ProjectColorHue;
}

/**
 * PATCH `/v1/projects/{id}` body. Every field optional. `status` accepts
 * `active` ↔ `archived` transitions; server stamps `archived_at` on
 * archive and clears it on activate. Owner-only.
 */
export interface UpdateProjectRequest {
  readonly name?: string;
  readonly description?: string;
  readonly icon_emoji?: ProjectIconEmoji;
  readonly color_hue?: ProjectColorHue;
  readonly status?: ProjectStatus;
}

/** POST `/v1/projects/{id}/members` body. Owner-only. */
export interface AddMemberRequest {
  readonly user_id: UserId;
  /** `owner` is not accepted here — use the transfer endpoint. */
  readonly role: Exclude<ProjectRole, "owner">;
}

/**
 * DELETE `/v1/projects/{id}/members/{user_id}` is the canonical remove
 * endpoint; this shape exists for parity with the public surface
 * (clients can pass `{ user_id: "me" }` to `DELETE …/members/me` for the
 * self-remove shortcut described in projects-prd §3.5.1).
 */
export interface RemoveMemberRequest {
  readonly user_id: UserId | "me";
}

/** PATCH `/v1/projects/{id}/members/{user_id}` body. Owner-only. */
export interface ChangeRoleRequest {
  /** `owner` not accepted here — use the transfer endpoint. */
  readonly role: Exclude<ProjectRole, "owner">;
}

/**
 * POST `/v1/projects/{id}/transfer` body. Owner-only (or admin-only via
 * `POST /v1/admin/projects/{id}/force-transfer` — different endpoint,
 * different audit action, same wire shape).
 *
 * `previous_owner_new_role` defaults to `"editor"` per projects-prd Q5
 * (orchestrator-approved). The transferor may set it to `viewer` or
 * `none` (= remove the previous owner entirely).
 */
export interface TransferOwnershipRequest {
  readonly new_owner_user_id: UserId;
  readonly previous_owner_new_role?: Exclude<ProjectRole, "owner"> | "none";
}

// ---------------------------------------------------------------------------
// SSE stream envelope
// ---------------------------------------------------------------------------

/**
 * Event types pushed on `GET /v1/projects/stream`. Per-recipient ACL is
 * enforced at fan-out — the same envelope is filtered by membership
 * before delivery (projects-prd §3.8).
 */
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
 * Discriminated payload union driven by `event_type`; producers are
 * responsible for matching the right shape. The union is permissive (open)
 * so adding a new event type stays forwards-compatible with old clients —
 * they surface the envelope as a generic "something changed" signal.
 * Membership / state-change envelopes carry only the small descriptor;
 * clients refetch the full project on `project_member_added` for the
 * current user (auto-add to rail).
 */
export type ProjectStreamPayload =
  | Project
  | ProjectSummary
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

/**
 * SSE envelope. `sequence_no` is monotonic per stream; clients reconnect
 * via `?after_sequence=N` to resume without replay (matches the runtime
 * agent-events stream pattern from cross-audit §5.2).
 */
export interface ProjectStreamEnvelope {
  readonly sequence_no: number;
  readonly event_type: ProjectStreamEventType;
  readonly project_id: ProjectId;
  readonly payload: ProjectStreamPayload;
  /** ISO-8601 UTC. */
  readonly emitted_at: string;
}
