// Team destination (Phase 12) — wire contract.
//
// Source: docs/atlas-new-design/destinations/team-memory-cmdk-prd.md
//   §3.1 (Team wire shapes), §4.1 (endpoints), §6.1 (ACL),
//   §7.1 (frontend surface — TeamInviteWizard + OffboardingWizard).
//
// Built on the existing `users` + `tenant_memberships` tables (no new
// identity). Adds: presence, agent ownership lens, invite workflow,
// role management, person-detail audit (admin).
//
// Single declaration site for: TeamRole, Presence, Person,
// TeamListResponse, PersonDetailResponse, InviteRequest,
// OffboardingRequest, plus the SSE envelope and filter/sort axis tokens.
//
// Brand types live in ./brands.ts (canonical site); cross-destination
// refs live in ./refs.ts. This file ONLY composes them — zero new
// `__brand:` declarations (DRY rule, cross-audit §2.1).

import type { ProjectId, TenantId, UserId } from "./brands";
import type { ItemRef } from "./refs";

// ---------------------------------------------------------------------------
// Enums
// ---------------------------------------------------------------------------

/**
 * Workspace role on the tenant. Sourced from `tenant_memberships.role`.
 *
 *   * `owner`  — the founding admin; cannot be demoted while sole owner
 *     (§6.1 — "cannot remove sole owner" invariant).
 *   * `admin`  — full read/write on tenant assets + invite + role +
 *     offboarding endpoints.
 *   * `member` — default; reads tenant, writes own assets.
 *   * `guest`  — limited; read-only on the workspace catalog, write only
 *     on project rows where they hold `is_project_member` (cross-audit
 *     §1.3).
 */
export type TeamRole = "owner" | "admin" | "member" | "guest";

/**
 * Last-known presence state. Volatile — sourced from `presence_state` KV
 * (Redis in prod, in-proc dict in dev — sub-PRD §5.2). The wire value is
 * a best-effort projection; `last_seen_at` is the audit-trail truth.
 */
export type Presence = "active" | "away" | "in_meeting" | "offline";

// ---------------------------------------------------------------------------
// Composite types
// ---------------------------------------------------------------------------

/**
 * Canonical Person record. Returned by `GET /v1/team` (list rows) and
 * embedded in `PersonDetailResponse.person` for `GET /v1/team/{id}`.
 *
 * The `agents_count` / `projects_count` fields are denormalized read-time
 * projections — never write-paths; refreshed by per-destination LISTEN/
 * NOTIFY triggers (same pattern as `palette_index`).
 *
 * `is_self` is a server hint so the caller can early-render a "(you)"
 * suffix without a separate identity round-trip. Treat as untrusted —
 * the canonical identity is the verified session bearer, not this flag.
 */
export interface Person {
  readonly id: UserId;
  readonly tenant_id: TenantId;
  readonly display_name: string;
  readonly email: string;
  readonly avatar_url?: string;
  readonly role: TeamRole;
  readonly presence: Presence;
  /** ISO8601; null when the user has never connected. */
  readonly last_seen_at: string | null;
  /** ISO8601 — join time on this tenant (not IdP signup). */
  readonly joined_at: string;
  /** Projection over agents owned by this user. */
  readonly agents_count: number;
  /** Projection over projects this user owns. */
  readonly projects_count: number;
  /** Server hint — `true` if the caller's `user_id` matches `id`. */
  readonly is_self: boolean;
}

/**
 * Cursor-paginated team listing. `next_cursor` is an opaque token; the
 * client passes it back as `?cursor=…` to continue.
 */
export interface TeamListResponse {
  readonly people: ReadonlyArray<Person>;
  readonly next_cursor: string | null;
}

/**
 * `GET /v1/team/{id}` — person detail.
 *
 * `recent_activity` is admin-only (§6.1) — non-admin callers receive an
 * empty array (404-not-403 is reserved for the row itself; the field is
 * just silently empty so the UI can render the tab without branching).
 * The `target` is an `ItemRef` so the row links cross-destination via
 * the canonical resolver registry (cross-audit §1.1).
 */
export interface PersonDetailResponse {
  readonly person: Person;
  /** ItemRefs narrowed to `kind: "agent"` by the server. */
  readonly agents: ReadonlyArray<ItemRef>;
  /** ItemRefs narrowed to `kind: "project"` by the server. */
  readonly projects: ReadonlyArray<ItemRef>;
  /** Admin-only — empty array for non-admin callers. */
  readonly recent_activity: ReadonlyArray<PersonActivityEntry>;
}

/**
 * One row of the admin-only person activity feed (`recent_activity`).
 * Joined server-side from `runtime_run_usage` + `runtime_tool_invocations`
 * (sub-PRD §5.1).
 */
export interface PersonActivityEntry {
  /** ISO8601 — when the action occurred. */
  readonly at: string;
  /** Human-readable summary; never an exception trace. */
  readonly summary: string;
  /** Cross-destination ref the activity touched. */
  readonly target: ItemRef;
}

// ---------------------------------------------------------------------------
// Request bodies
// ---------------------------------------------------------------------------

/**
 * Body for `POST /v1/team/invite` (admin only — §6.1). The server
 * re-uses the existing identity invite path (magic-link IdP). The
 * optional `note` is shown verbatim in the invite email.
 */
export interface InviteRequest {
  readonly email: string;
  readonly role: TeamRole;
  /** Optional welcome note shown in the invite email. */
  readonly note?: string;
}

/**
 * Body for `POST /v1/team/{id}/offboard` — admin offboarding wizard.
 *
 * Sub-PRD §6.1 + Routines §9.7 Q12 (re-evaluation): naive force-transfer
 * stays deferred (cross-audit §9.8 Q1). Offboarding requires explicit
 * per-asset reassignment via the existing PATCH endpoints — this body
 * is just the orchestration plan the server applies in one transaction.
 *
 * Each `asset` is narrowed by the server to one of `"agent"`, `"project"`,
 * `"tool"`, `"connector"`. Memory / chat history is NOT reassigned —
 * private memories are soft-deleted per tenant policy (sub-PRD §6.1).
 */
export interface OffboardingRequest {
  readonly target_user_id: UserId;
  readonly reassignments: ReadonlyArray<OffboardingReassignment>;
}

export interface OffboardingReassignment {
  /** ItemRef narrowed by the server to "agent"|"project"|"tool"|"connector". */
  readonly asset: ItemRef;
  readonly new_owner_user_id: UserId;
}

/**
 * Body for `PATCH /v1/team/{id}/role` — admin role change.
 *
 * Server-side invariants (§6.1): cannot demote self, cannot remove the
 * sole owner. Reject with 409 on violation.
 */
export interface UpdateTeamRoleRequest {
  readonly role: TeamRole;
}

// ---------------------------------------------------------------------------
// Filter axis + sort tokens (cross-audit §1.5 reproduction so the FE can
// statically narrow `filter[<axis>]` keys at the call site).
// ---------------------------------------------------------------------------

/** Allowed `filter[<axis>]` keys on `GET /v1/team`. */
export type TeamListFilterAxis = "role" | "presence" | "q";

/** Allowed sort tokens — `filter[sort]=…`. */
export type TeamListSort =
  | "display_name:asc"
  | "display_name:desc"
  | "last_seen:desc"
  | "joined_at:desc";

// ---------------------------------------------------------------------------
// Filter axis allowlist — admin recent-activity filter (§U-T4).
// ---------------------------------------------------------------------------

/** Allowed `filter[<axis>]` keys on `GET /v1/team/{id}` recent_activity. */
export type PersonActivityFilterAxis = "project_id" | "since" | "kind";

// ---------------------------------------------------------------------------
// SSE — `GET /v1/team/stream` (sub-PRD §4.1)
// ---------------------------------------------------------------------------

/**
 * Team SSE event types.
 *
 *   * `team.presence_changed` — user `presence` / `last_seen_at` updated.
 *   * `team.role_changed`     — admin patched a member's role.
 *   * `team.invited`          — invite created (admin-visible only).
 *   * `team.joined`           — invitee accepted (admin-visible only).
 *   * `team.offboarded`       — offboarding wizard completed.
 *   * `heartbeat`             — SSE keepalive comment frame.
 */
export type TeamStreamEventType =
  | "team.presence_changed"
  | "team.role_changed"
  | "team.invited"
  | "team.joined"
  | "team.offboarded"
  | "heartbeat";

/**
 * SSE envelope mirroring the inbox / home / connectors streams. Monotonic
 * `sequence_no` per `(tenant_id, user_id)` channel; reconnect via
 * `Last-Event-ID`.
 *
 * The `person` payload is present on every non-heartbeat frame so the
 * client can patch its in-memory row without a follow-up GET. For
 * `offboarded` the payload reflects the post-offboard state (role
 * unchanged, `last_seen_at` may be the offboard timestamp), used for
 * "person X was offboarded" toast strings.
 *
 * Project scope filter (`?project_id=…`) is allowed when admin and
 * already gates on `is_project_member` (cross-audit §1.3).
 */
export interface TeamStreamEnvelope {
  readonly event_id: string;
  readonly sequence_no: number;
  readonly event_type: TeamStreamEventType;
  /** Patched person row; absent on `heartbeat` frames. */
  readonly person?: Person;
  /** Optional — present on `team.offboarded` for the new owner per asset. */
  readonly offboarding?: {
    readonly target_user_id: UserId;
    readonly reassignments_count: number;
  };
  /** Optional — project this event is scoped to (Q4 admin filter). */
  readonly project_id?: ProjectId;
  readonly created_at: string;
}
