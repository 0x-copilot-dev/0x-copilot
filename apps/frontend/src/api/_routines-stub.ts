// Local stub for the Phase 5 Routines wire contract.
//
// The canonical types live in `@0x-copilot/api-types`
// (`packages/api-types/src/routines.ts`), authored by the parallel
// Phase 5 P5-A backend-types agent. This frontend wave (P5-C) runs in
// parallel against the same sub-PRD spec and cannot import a type that
// is not yet on `main`, so this stub mirrors the shapes in
// `docs/atlas-new-design/destinations/routines-prd.md` §4 (wire contracts).
//
// TODO(merge): delete this file. Replace every `_routines-stub` import
// with `@0x-copilot/api-types` once P5-A's
// `packages/api-types/src/routines.ts` lands on main.

import type { ReasoningDepth } from "@0x-copilot/api-types";

// ===========================================================================
// Branded ids (§4.1)
// ===========================================================================

export type RoutineId = string & { readonly __brand: "RoutineId" };
export type TriggerId = string & { readonly __brand: "TriggerId" };
export type RoutineFireId = string & { readonly __brand: "RoutineFireId" };
export type ProjectId = string & { readonly __brand: "ProjectId" };
export type UserId = string & { readonly __brand: "UserId" };
export type AgentId = string & { readonly __brand: "AgentId" };
export type ToolId = string & { readonly __brand: "ToolId" };
export type SkillId = string & { readonly __brand: "SkillId" };
export type ConnectorId = string & { readonly __brand: "ConnectorId" };
export type TenantId = string & { readonly __brand: "TenantId" };
export type RunId = string & { readonly __brand: "RunId" };

/**
 * Cross-destination reference handle. Routines emit `run_ref`,
 * `output_target` etc as `ItemRef`s so the cross-destination
 * `<ItemLink>` registry can resolve them uniformly (sub-PRD §13 +
 * cross-audit §1.1).
 *
 * Stubbed minimally here — the full union ships with P4 / P5
 * api-types and replaces this on merge.
 */
export interface ItemRef {
  readonly kind:
    | "routine"
    | "run"
    | "library_page"
    | "existing_chat"
    | "project_log"
    | "inbox_item"
    | "todo"
    | "agent";
  readonly id: string;
}

// ===========================================================================
// Enums (§4.1)
// ===========================================================================

export type RoutineStatus = "draft" | "active" | "paused" | "errored";

export type RoutineMissedFirePolicy = "fire_once" | "fire_all" | "skip_all";

export type RoutineAutonomy = "manual_approval" | "auto_apply" | "full_auto";

export type RoutineScope = "read_only" | "read_write";

export type RoutineDataResidency =
  | "inherit"
  | "us_only"
  | "eu_only"
  | "apac_only";

export type RoutineTriggerKind = "schedule" | "webhook" | "event" | "manual";

export type RoutineFireStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "skipped";

// ===========================================================================
// Trigger union (§4.1)
// ===========================================================================

export type RoutineOutputTargetKind =
  | { readonly kind: "inbox" }
  | {
      readonly kind: "library_page";
      readonly ref: ItemRef;
      readonly mode: "new_per_fire" | "update_same";
    }
  | { readonly kind: "existing_chat"; readonly ref: ItemRef }
  | { readonly kind: "project_log"; readonly ref: ItemRef };

export type RoutineTriggerFilterOp =
  | "eq"
  | "ne"
  | "gt"
  | "gte"
  | "lt"
  | "lte"
  | "in"
  | "matches";

export interface RoutineEventFilter {
  readonly field: string;
  readonly op: RoutineTriggerFilterOp;
  readonly value: string | number | boolean | ReadonlyArray<string>;
}

export type RoutineTrigger =
  | {
      readonly kind: "schedule";
      readonly trigger_id: TriggerId;
      readonly cron: string;
      readonly tz: string;
    }
  | {
      readonly kind: "webhook";
      readonly trigger_id: TriggerId;
      readonly secret_masked: string;
      readonly secret_rotated_at: string | null;
      readonly secret_grace_until: string | null;
      readonly ip_allowlist: ReadonlyArray<string>;
    }
  | {
      readonly kind: "event";
      readonly trigger_id: TriggerId;
      readonly event_source: string;
      readonly filter: ReadonlyArray<RoutineEventFilter>;
    }
  | { readonly kind: "manual"; readonly trigger_id: TriggerId };

// ===========================================================================
// Connector / behavior / permissions blocks (§4.1)
// ===========================================================================

export interface RoutineConnectorConfig {
  readonly connector_id: ConnectorId;
  readonly mode: "inherit" | "read_only" | "custom";
  readonly custom_scope?: ReadonlyArray<string>;
}

export interface RoutineBehavior {
  readonly autonomy: RoutineAutonomy;
  readonly max_retries: number; // 0..10
  readonly backoff: "exponential" | "linear" | "none";
  readonly backoff_base_seconds: number;
  readonly max_duration_seconds: number; // 60..7200
  readonly output_target: RoutineOutputTargetKind;
  readonly notify_on_success: ReadonlyArray<
    "owner" | "project_members" | "tenant_admin"
  >;
  readonly notify_on_failure: ReadonlyArray<
    "owner" | "project_members" | "tenant_admin"
  >;
}

export interface RoutinePermissions {
  readonly scope: RoutineScope;
  readonly allowed_tools: ReadonlyArray<ToolId>;
  readonly allowed_skills: ReadonlyArray<SkillId>;
  readonly max_tool_calls_per_fire: number;
  readonly max_output_tokens_per_fire: number;
  readonly data_residency: RoutineDataResidency;
  readonly manual_fire: "owner" | "project_members" | "tenant"; // §3.11
}

// ===========================================================================
// Routine row (§4.1)
// ===========================================================================

export interface Routine {
  readonly id: RoutineId;
  readonly tenant_id: TenantId;
  readonly owner_user_id: UserId;
  readonly project_id: ProjectId | null;
  readonly name: string; // ≤ 80 chars
  readonly description: string; // ≤ 200 chars
  readonly instructions: string; // ≤ 16 KB
  readonly model: string;
  readonly depth: ReasoningDepth | null;
  readonly base_agent_id: AgentId | null;
  readonly repository?: { readonly url: string; readonly ref: string };
  readonly environment?: Readonly<Record<string, string>>;
  readonly status: RoutineStatus;
  readonly pause_reason: string | null;
  readonly triggers: ReadonlyArray<RoutineTrigger>;
  readonly connectors: ReadonlyArray<RoutineConnectorConfig>;
  readonly behavior: RoutineBehavior;
  readonly permissions: RoutinePermissions;
  readonly missed_fire_policy: RoutineMissedFirePolicy;
  readonly next_fire_at: string | null;
  readonly last_fire_at: string | null;
  readonly last_fire_status: "succeeded" | "failed" | "skipped" | null;
  readonly created_at: string;
  readonly updated_at: string;
}

// ===========================================================================
// List + filter (§4.2, §4.5)
// ===========================================================================

export type RoutineSortKey =
  | "name:asc"
  | "name:desc"
  | "next_fire_at:asc"
  | "created_at:desc"
  | "last_fire_at:desc";

export interface ListRoutinesFilters {
  readonly status?: RoutineStatus;
  readonly owner_user_id?: UserId;
  readonly project_id?: ProjectId;
  readonly trigger_kind?: RoutineTriggerKind;
}

export interface ListRoutinesResponse {
  readonly items: ReadonlyArray<Routine>;
  readonly next_cursor: string | null;
}

// ===========================================================================
// Mutations (§4.2)
// ===========================================================================

/**
 * Body for `POST /v1/routines`. Server assigns `id`, `tenant_id`,
 * `created_at`, `updated_at`, `next_fire_at`. Status starts at `draft`
 * unless the caller activates explicitly.
 */
export type CreateRoutineRequest = Omit<
  Routine,
  | "id"
  | "tenant_id"
  | "created_at"
  | "updated_at"
  | "next_fire_at"
  | "last_fire_at"
  | "last_fire_status"
  | "pause_reason"
>;

/** PATCH /v1/routines/{id} — partial update of mutable fields. */
export interface UpdateRoutineRequest {
  readonly name?: string;
  readonly description?: string;
  readonly instructions?: string;
  readonly model?: string;
  readonly depth?: ReasoningDepth | null;
  readonly base_agent_id?: AgentId | null;
  readonly connectors?: ReadonlyArray<RoutineConnectorConfig>;
  readonly behavior?: RoutineBehavior;
  readonly permissions?: RoutinePermissions;
  readonly missed_fire_policy?: RoutineMissedFirePolicy;
}

/** POST /v1/routines/{id}/pause — body. */
export interface PauseRoutineRequest {
  readonly pause_reason?: string;
}

/**
 * Manual fire response — sub-PRD §3.11 / §4.2.
 *
 * Server returns the new run reference so the client can navigate to
 * the run timeline if the user clicks "View run".
 */
export interface ManualFireResponse {
  readonly run_ref: ItemRef;
}

// ===========================================================================
// Fire history (§4.1)
// ===========================================================================

export interface RoutineFire {
  readonly id: RoutineFireId;
  readonly tenant_id: TenantId;
  readonly routine_id: RoutineId;
  readonly trigger_kind: RoutineTriggerKind;
  readonly trigger_id: TriggerId;
  readonly run_ref: ItemRef;
  readonly status: RoutineFireStatus;
  readonly skip_reason: string | null;
  readonly payload_snapshot?: unknown;
  readonly created_at: string;
  readonly completed_at: string | null;
}

// ===========================================================================
// SSE envelope (§4.2 — durable stream)
// ===========================================================================

export type RoutineStreamEventType =
  | "routine_created"
  | "routine_updated"
  | "routine_deleted"
  | "routine_fired"
  | "routine_paused";

export interface RoutineStreamEnvelope {
  readonly sequence_no: number;
  readonly event_type: RoutineStreamEventType;
  readonly routine: Routine;
  readonly emitted_at: string;
}
