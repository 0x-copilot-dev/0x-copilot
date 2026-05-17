// Routines destination (Phase 5) — CRUD + state machine + trigger
// wire contract.
//
// Source: docs/atlas-new-design/destinations/routines-prd.md §3 (item
// shape) + §4 (endpoints) + §7 (ACL) + §16 (open questions); and
// docs/atlas-new-design/cross-audit.md §1.1 (links via ItemRef),
// §1.3 (project-scoped ACL — owner writes, project-member reads,
// admin compliance reads, 404-not-403), §1.5 (multi-value OR filter
// axes), §2.1 (branded IDs), §2.4 (webhook security: rotating secret
// + IP allowlist), §9.7 (14 binding decisions — manual_fire ACL
// override, no auto-resume on permission restoration, fire_once
// missed-fire default, 100 active routines per USER, live agent
// re-resolve with optional agent_version_pin, code-routines wire
// shape now / executor later).
//
// Wire-only file: no business logic, no HTTP client, no view models.
// The server is the source of truth; this package mirrors the public
// payloads exactly as the facade serves them. Internal `/internal/v1/*`
// scheduler + webhook ingest contracts (P5-A2 / P5-A3) are NOT
// mirrored here — those live behind the service boundary.
//
// The shape is intentionally a subset of the PRD §4.1 wire spec. We
// land the fields P5-A1 owns (CRUD + ACL + trigger array + manual
// fire) plus the Wave-6 forwards-compatible `code?` field per
// cross-audit §9.7 Q1. Trigger validation, scheduler bookkeeping
// (claim_token, next_fire_at, etc.) and richer Behavior fields land
// alongside their siblings (P5-A2 scheduler, P5-A3 webhook, P5-A4
// permission intersection).

import type { AgentId, ProjectId, RoutineId, TenantId, UserId } from "./brands";
import type { ItemRef } from "./refs";

// ---------------------------------------------------------------------------
// Primitive enums
// ---------------------------------------------------------------------------

/**
 * Lifecycle state machine (cross-audit §3.2 — kept distinct from
 * `RecentRunStatus`; status describes the routine, not its last run).
 *
 *   draft ──activate──▶ active ──pause──▶ paused ──activate──▶ active
 *                          │                  ▲
 *                          ├──error──▶ errored ┘
 *                          │
 *  (any) ──fix──▶ draft   (errored → draft via PATCH so the owner can
 *                          edit the definition before re-activating;
 *                          pause is the "I'll come back to this"
 *                          path, errored is the "something broke" path)
 *
 *  Every transition writes one audit row. Manual fires don't change
 *  status; auto-pause from permission shrinkage (P5-A4) does and
 *  stamps `pause_reason = "permission_shrinkage"`.
 */
export type RoutineStatus = "draft" | "active" | "paused" | "errored";

/**
 * Why the routine is paused. `null` when not paused (or for legacy
 * rows pre-dating this field).
 *
 *  - `manual` — owner clicked pause; the routine resumes on owner
 *    activate.
 *  - `permission_shrinkage` — a tool / connector / skill the routine
 *    requires is no longer granted to the owner. P5-A4 sets this;
 *    the owner sees an Inbox CTA. cross-audit §9.7 Q4 + Q5 (no
 *    auto-resume — owner must explicitly re-activate after fixing).
 *  - `error` — the routine errored repeatedly past its retry budget.
 */
export type RoutinePauseReason = "manual" | "permission_shrinkage" | "error";

/**
 * What happens on the first activation after a scheduler outage /
 * pause that crossed a fire boundary. cross-audit §9.7 Q7:
 *
 *  - `fire_once` (DEFAULT) — catch up exactly once on resume; skip the
 *    rest of the backlog. Matches user expectations for "Daily
 *    standup at 9am" — they want today's run, not the three missed.
 *  - `fire_all` — fire every missed slot, in order. For low-frequency,
 *    high-criticality routines (weekly invoice digest).
 *  - `skip` — don't backfill; start the next scheduled fire.
 */
export type RoutineMissedFirePolicy = "fire_once" | "fire_all" | "skip";

/**
 * Manual-fire ACL override. cross-audit §9.7 Q2 — default `"owner"`
 * (sub-PRD recommendation); the override widens manual-fire to
 * project members or every tenant member.
 *
 *  - `"owner"` — only the routine's owner can manual-fire (default).
 *  - `"project_members"` — any member of `project_id` can manual-fire
 *    (only valid when the routine is filed under a project).
 *  - `"tenant"` — any tenant member can manual-fire (broadest).
 *
 * Note this is the only ACL the routine owner can widen; PATCH /
 * DELETE / ACTIVATE / PAUSE are always owner-only per routines-prd §7.2.
 */
export type RoutineManualFireScope = "owner" | "project_members" | "tenant";

/**
 * The four trigger kinds. cross-audit §9.7 Q6 — `webhook` accepts
 * a rotating secret today; HMAC-of-payload signature is the next
 * add (wire shape lands now via P5-A3's webhook router); mTLS is
 * deferred to Wave 5+.
 */
export type RoutineTriggerKind = "cron" | "event" | "webhook" | "manual";

// ---------------------------------------------------------------------------
// Trigger shape (discriminated union)
// ---------------------------------------------------------------------------

/**
 * Cron trigger. `spec` is a 5-field POSIX cron expression. `timezone`
 * is an IANA tz name (e.g. `"America/Los_Angeles"`); when absent the
 * server stamps `"UTC"`. P5-A2 (scheduler) resolves the next fire
 * time and populates the runtime's claim queue.
 */
export interface RoutineCronTrigger {
  readonly kind: "cron";
  readonly spec: string;
  readonly timezone?: string;
}

/**
 * Event trigger. `source` is one of the server-allowlisted event
 * sources (see `GET /v1/routines/event-sources`). `event_name` is
 * the specific event within that source. Wave-5 supports a small
 * allowlist; per-event filter expressions land alongside P5-A2.
 */
export interface RoutineEventTrigger {
  readonly kind: "event";
  readonly source: string;
  readonly event_name: string;
}

/**
 * Webhook trigger. `trigger_id` is the opaque per-trigger identifier
 * embedded in the public URL (`/v1/webhook/routines/{trigger_id}`).
 * Secrets are NEVER returned on read — `rotate-secret` returns the
 * cleartext exactly once. cross-audit §2.4 + §9.7 Q6.
 */
export interface RoutineWebhookTrigger {
  readonly kind: "webhook";
  readonly trigger_id: string;
}

export type RoutineTrigger =
  | RoutineCronTrigger
  | RoutineEventTrigger
  | RoutineWebhookTrigger;

// ---------------------------------------------------------------------------
// Permissions shape
// ---------------------------------------------------------------------------

/**
 * Per-routine ACL override surface. Only `manual_fire` widens beyond
 * the owner-only default today; richer per-tool / per-connector
 * permissions land in P5-A4 alongside the fire-time intersection
 * check.
 */
export interface RoutinePermissions {
  readonly manual_fire: RoutineManualFireScope;
}

// ---------------------------------------------------------------------------
// Code-routines forwards-compatible shape (Wave 6 executor)
// ---------------------------------------------------------------------------

/**
 * Reference to a user-supplied code bundle that runs as the routine
 * body (instead of, or in addition to, a prompt-driven agent run).
 * cross-audit §9.7 Q1 — Wave-6 executor + sandbox; wire shape lands
 * NOW (forwards-compatible) so the storage + facade don't need a
 * breaking-change migration when the executor goes live.
 *
 *  - `repo_ref` — git repository entity (a library_file or future
 *    library_dataset row carrying the cloneable URL + commit ref).
 *  - `env_ref` — environment / runtime image entity (a library_file
 *    carrying the dockerfile or pyproject manifest).
 *  - `entry` — entry point within the repo (e.g. `"src/main.py:main"`).
 *
 * Servers behind a feature flag drop this field today; the backend's
 * storage layer accepts and persists it but the executor pipeline
 * is not yet wired. Consumers MUST tolerate `code === undefined`.
 */
export interface RoutineCodeRef {
  readonly repo_ref: ItemRef;
  readonly env_ref: ItemRef;
  readonly entry: string;
}

// ---------------------------------------------------------------------------
// Canonical Routine shape
// ---------------------------------------------------------------------------

/**
 * One routine row. Triggers are inline because they share the
 * routine's lifecycle 1:1 today (P5-A1 lands array-on-routine; the
 * per-trigger `/v1/routines/{id}/triggers` endpoints in the PRD §4.2
 * are an editor-ergonomic surface that lands alongside P5-A2 webhook
 * + P5-B editor work).
 *
 * `agent_id` is required — every routine fires as an agent run, so
 * the agent is the load-bearing pointer. `agent_version_pin` is
 * optional per cross-audit §9.7 Q11: default behaviour is "live
 * re-resolve at fire time" so the latest definition runs; users
 * who want pinned behaviour set this to a specific version slug.
 *
 * `code` is forwards-compatible per cross-audit §9.7 Q1 (Wave 6).
 *
 * `pause_reason` is populated iff `status === "paused"`. Wiring
 * permission_shrinkage requires P5-A4 (intersection check); the
 * field exists today so frontend pause-banners can land before the
 * intersection check.
 *
 * `missed_fire_policy` default = `"fire_once"` per cross-audit §9.7 Q7.
 */
export interface Routine {
  readonly id: RoutineId;
  readonly tenant_id: TenantId;
  readonly owner_user_id: UserId;
  readonly project_id: ProjectId | null;
  readonly name: string;
  readonly instructions: string;
  readonly triggers: ReadonlyArray<RoutineTrigger>;
  /**
   * Per-routine connector scope (sparse: missing = inherit owner's
   * default scope at fire time per P5-A4). The map is server-validated
   * against the connector catalog; unknown keys are rejected on save.
   */
  readonly connectors_scope?: Readonly<Record<string, ReadonlyArray<string>>>;
  /**
   * Behaviour knobs (autonomy, retries, output target). Sparse on
   * P5-A1; P5-A2 + P5-A4 populate the full shape. Treated as opaque
   * JSON by the storage layer.
   */
  readonly behavior?: Readonly<Record<string, unknown>>;
  readonly permissions: RoutinePermissions;
  readonly agent_id: AgentId;
  readonly agent_version_pin?: string;
  readonly code?: RoutineCodeRef;
  readonly status: RoutineStatus;
  readonly pause_reason?: RoutinePauseReason;
  readonly missed_fire_policy: RoutineMissedFirePolicy;
  /** ISO-8601 UTC. */
  readonly created_at: string;
  /** ISO-8601 UTC. */
  readonly updated_at: string;
}

// ---------------------------------------------------------------------------
// List / mutation payloads
// ---------------------------------------------------------------------------

/**
 * Cursor-paginated list response. `next_cursor` is opaque (server
 * encodes the keyset pointer); the client passes it back verbatim.
 * Absent means "no more pages".
 */
export interface RoutineListResponse {
  readonly items: ReadonlyArray<Routine>;
  readonly next_cursor?: string;
}

/**
 * POST `/v1/routines` body. `triggers` is optional (a draft routine
 * with no triggers is valid — activation requires at least one).
 * Server stamps `id`, `tenant_id`, `owner_user_id`, `created_at`,
 * `updated_at`, and initial `status = "draft"`.
 *
 * `permissions.manual_fire` defaults to `"owner"` server-side when
 * the field is absent. cross-audit §9.7 Q2.
 */
export interface CreateRoutineRequest {
  readonly name: string;
  readonly instructions: string;
  readonly agent_id: AgentId;
  readonly project_id?: ProjectId | null;
  readonly triggers?: ReadonlyArray<RoutineTrigger>;
  readonly connectors_scope?: Readonly<Record<string, ReadonlyArray<string>>>;
  readonly behavior?: Readonly<Record<string, unknown>>;
  readonly permissions?: Partial<RoutinePermissions>;
  readonly agent_version_pin?: string;
  readonly code?: RoutineCodeRef;
  readonly missed_fire_policy?: RoutineMissedFirePolicy;
}

/**
 * PATCH `/v1/routines/{id}` body. Every field optional. `status`
 * obeys the state-machine (P5-A1 enforces transitions; invalid
 * moves return 409). `pause_reason` only meaningful when
 * `status === "paused"`.
 *
 * Owner-only writes (routines-prd §7.2). Quota gate on
 * draft → active transitions per cross-audit §9.7 Q8.
 */
export interface UpdateRoutineRequest {
  readonly name?: string;
  readonly instructions?: string;
  readonly agent_id?: AgentId;
  readonly project_id?: ProjectId | null;
  readonly triggers?: ReadonlyArray<RoutineTrigger>;
  readonly connectors_scope?: Readonly<Record<string, ReadonlyArray<string>>>;
  readonly behavior?: Readonly<Record<string, unknown>>;
  readonly permissions?: Partial<RoutinePermissions>;
  readonly agent_version_pin?: string;
  readonly code?: RoutineCodeRef;
  readonly missed_fire_policy?: RoutineMissedFirePolicy;
  readonly status?: RoutineStatus;
  readonly pause_reason?: RoutinePauseReason | null;
}

/**
 * POST `/v1/routines/{id}/run` response. Manual-fire records a
 * `routine_fires` row immediately and (in P5-A2) enqueues the
 * downstream ai-backend run; today the response carries just the
 * fire id so the frontend can poll / SSE for run completion.
 *
 * `run_id` is populated once the run-coordinator handoff lands
 * (P5-A2 deliverable); P5-A1 returns `null` for the initial wire.
 */
export interface RunRoutineResponse {
  readonly fire_id: string;
  readonly run_id: string | null;
}
