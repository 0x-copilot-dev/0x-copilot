// chat-surface Routines adapter shape (transitional; orchestrator rewires
// at merge to `@enterprise-search/api-types/routines`).
//
// Phase 5 has parallel wave-agents working off the same shape conventions
// as the canonical routines-prd.md §3 + §4.1:
//   - P5-A1 (api-types + backend wire) will own canonical
//     `packages/api-types/src/routines.ts`.
//   - P5-A2 (scheduler + dispatch) owns the in-process loop.
//   - P5-B1 (this shell), P5-B2 (editor), P5-B3 (detail + webhook UI)
//     ship UI in chat-surface.
//
// Until P5-A1 lands, this stub is the local view-model contract every
// UI sub-agent consumes. Naming + discriminators match the canonical
// site so merge-time rewire is a pure import swap.
//
// Every import of this stub should be marked
// `TODO(merge): rewire to "@enterprise-search/api-types"` so the
// orchestrator's rewrite script can find them.

import type {
  AgentId,
  ConnectorId,
  ProjectId,
  RoutineId,
  SkillId,
  TenantId,
  ToolId,
  UserId,
} from "@enterprise-search/api-types";
import type { ItemRef } from "@enterprise-search/api-types";

// ---- §4.1 brand-shaped trigger ids
//
// `TriggerId` is owned canonically by `@enterprise-search/api-types`
// (`packages/api-types/src/brands.ts`). Re-exported here so existing
// imports `from "../_routines-stub"` keep working without a churn pass.

/** Stable identifier for a single trigger row inside a routine. */
import type { TriggerId } from "@enterprise-search/api-types";
export type { TriggerId };

// ---- §3 / §4.1 enums ------------------------------------------------------

/** Lifecycle status. Source: routines-prd §4.1. */
export type RoutineStatus = "draft" | "active" | "paused" | "errored";

/** Missed-fire policy on activation after a long pause. routines-prd §3.7. */
export type RoutineMissedFirePolicy = "fire_once" | "fire_all" | "skip_all";

/** Autonomy axis on the Behavior tab. routines-prd §3.9. */
export type RoutineAutonomy = "manual_approval" | "auto_apply" | "full_auto";

/** Permission scope on the Permissions tab. routines-prd §3.10. */
export type RoutineScope = "read_only" | "read_write";

/** Data-residency constraint. routines-prd §3.10. */
export type RoutineDataResidency =
  | "inherit"
  | "us_only"
  | "eu_only"
  | "apac_only";

/** Who may push the "Run now" button. routines-prd §3.11. */
export type RoutineManualFire = "owner" | "project_members" | "tenant";

// ---- §3.9 Output target ---------------------------------------------------
//
// Each non-inbox target carries an `ItemRef` so the UI uses `<ItemLink>`
// for cross-destination navigation (single source of truth — no ad-hoc
// route strings; cross-audit §1.1).

export type RoutineOutputTarget =
  | { readonly kind: "inbox" }
  | {
      readonly kind: "library_page";
      readonly ref: ItemRef;
      readonly mode: "new_per_fire" | "update_same";
    }
  | { readonly kind: "existing_chat"; readonly ref: ItemRef }
  | { readonly kind: "project_log"; readonly ref: ItemRef };

// ---- §3.6 / §4.1 Trigger kinds (the four) ---------------------------------
//
// Schedule: cron + tz. routines-prd §3.6.1 enforces 1-minute granularity
// server-side; the shell renders the human-readable preview only.
//
// Webhook: secret_masked + IP allowlist. Cleartext secret is shown once
// via `[Reveal secret]` in detail view (P5-B3); the shell never holds it.
//
// Event: server-allowlisted source. The shell renders the source label;
// filter editing lives in the editor (P5-B2).
//
// Manual: just the "Run now" button (always available — see §3.11).

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
      readonly filter: ReadonlyArray<{
        readonly field: string;
        readonly op:
          | "eq"
          | "ne"
          | "gt"
          | "gte"
          | "lt"
          | "lte"
          | "in"
          | "matches";
        readonly value: string | number | boolean | ReadonlyArray<string>;
      }>;
    }
  | { readonly kind: "manual"; readonly trigger_id: TriggerId };

/** Stable discriminator for the trigger filter chip groups (panel §3.3). */
export type RoutineTriggerKind = RoutineTrigger["kind"];

// ---- §3.8 Connector configuration -----------------------------------------

export interface RoutineConnectorConfig {
  readonly connector_id: ConnectorId;
  readonly mode: "inherit" | "read_only" | "custom";
  readonly custom_scope?: ReadonlyArray<string>;
}

// ---- §3.9 Behavior block --------------------------------------------------

export interface RoutineBehavior {
  readonly autonomy: RoutineAutonomy;
  readonly max_retries: number; // 0-10
  readonly backoff: "exponential" | "linear" | "none";
  readonly backoff_base_seconds: number;
  readonly max_duration_seconds: number; // 60 - 7200
  readonly output_target: RoutineOutputTarget;
  readonly notify_on_success: ReadonlyArray<
    "owner" | "project_members" | "tenant_admin"
  >;
  readonly notify_on_failure: ReadonlyArray<
    "owner" | "project_members" | "tenant_admin"
  >;
}

// ---- §3.10 Permissions block ----------------------------------------------

export interface RoutinePermissions {
  readonly scope: RoutineScope;
  readonly allowed_tools: ReadonlyArray<ToolId>;
  readonly allowed_skills: ReadonlyArray<SkillId>;
  readonly max_tool_calls_per_fire: number;
  readonly max_output_tokens_per_fire: number;
  readonly data_residency: RoutineDataResidency;
  readonly manual_fire: RoutineManualFire;
}

// ---- §3 / §4.1 Routine row ------------------------------------------------

/**
 * Single routine. Mirrors routines-prd §4.1 `Routine`. The shell renders
 * a flat list; section bucketing on the destination is filter-driven (no
 * server-side grouping — cross-audit §1.5 multi-value OR semantics).
 *
 * `next_fire_at` is `null` for webhook/event/manual-only routines (no
 * schedule); the shell renders the next-event-source label instead.
 */
export interface Routine {
  readonly id: RoutineId;
  readonly tenant_id: TenantId;
  readonly owner_user_id: UserId;
  readonly owner_display_name?: string;
  readonly project_id: ProjectId | null;
  readonly project_name?: string;
  readonly name: string; // <= 80 chars
  readonly description: string; // <= 200 chars
  readonly model: string;
  readonly base_agent_id: AgentId | null;
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
  /**
   * Outbound cross-destination references — agent, project, base-agent,
   * output target. The shell renders these as `<ItemLink>` chips per
   * cross-audit §1.1 (no router.navigate from rows). Host pre-computes
   * (apps/frontend P5-C) so the shell stays substrate-agnostic.
   */
  readonly links: ReadonlyArray<ItemRef>;
}
