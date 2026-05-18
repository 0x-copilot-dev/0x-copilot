// Local stub for the Phase 8 Agents wire contract.
//
// The canonical types live in `@enterprise-search/api-types`
// (`packages/api-types/src/agents.ts`), authored by the parallel
// Phase 8 P8-A backend-types agent. This frontend wave (P8-C) runs in
// parallel against the same sub-PRD spec and cannot import a type that
// is not yet on `main`, so this stub mirrors the shapes in
// `docs/atlas-new-design/destinations/agents-prd.md` §3.1.
//
// `AgentId`, `TenantId`, `UserId`, `SkillId`, `ConnectorId`, and
// `MemoryItemId` already live in `@enterprise-search/api-types/brands`
// — re-export from there so the cross-destination `<ItemLink>` registry
// stays a single source of truth even before the rest of the Agents
// contract merges.
//
// TODO(merge): delete this file. Replace every `_agents-stub` import
// with `@enterprise-search/api-types` once P8-A's
// `packages/api-types/src/agents.ts` lands on main.

import type {
  AgentId,
  ConnectorId,
  MemoryItemId,
  SkillId,
  TenantId,
  UserId,
} from "@enterprise-search/api-types";

export type { AgentId, ConnectorId, MemoryItemId, SkillId, TenantId, UserId };

// `AgentVersionId` / `AgentInstallId` / `MemoryRef` are not yet brand-typed
// in `@enterprise-search/api-types/brands`. Local opaque aliases until
// P8-A lands them.
export type AgentVersionId = string & { readonly __brand: "AgentVersionId" };
export type AgentInstallId = string & { readonly __brand: "AgentInstallId" };
/**
 * Forward-compatible memory reference. Phase 8 stores it nullable; the
 * real shape (item-id-or-tag-set) lands in Phase 11 Memory.
 */
export type MemoryRef = MemoryItemId | null;

// ===========================================================================
// Enums (§3.1)
// ===========================================================================

export type AgentOrigin = "system" | "community" | "custom";
export type AgentStatus = "installed" | "available" | "disabled" | "draft";
export type AgentAutonomy = "manual_approval" | "auto_apply";
export type AgentReasoningDepth = "fast" | "balanced" | "deep";
export type UsagePeriod = "day" | "week" | "month";

// ===========================================================================
// Permissions + model defaults (§3.1)
// ===========================================================================

export interface AgentPermissions {
  readonly autonomy: AgentAutonomy;
  /** Max tool calls a single run may make. 0 = no cap. */
  readonly max_tool_calls_per_run: number;
  /** Hard upper bound on output tokens per run. */
  readonly max_output_tokens: number;
  /** Read-only restricts ALL connectors to read scope at fire time. */
  readonly read_only: boolean;
  /** Optional allowlist of skill ids. Empty = inherit from `skills`. */
  readonly allowed_skill_ids?: ReadonlyArray<SkillId>;
  /** Optional blocklist of tool family names ("filesystem", "network"). */
  readonly blocked_tool_families?: ReadonlyArray<string>;
}

export interface AgentModelDefault {
  /** e.g. "anthropic:claude-sonnet-4-7-1m". */
  readonly model_id: string;
  readonly reasoning_depth: AgentReasoningDepth;
}

// ===========================================================================
// Agent (§3.1)
// ===========================================================================

export interface AgentUsageRollup {
  readonly agent_id: AgentId;
  readonly period: UsagePeriod;
  /** Number of distinct runs that referenced this agent. */
  readonly run_count: number;
  readonly token_in: number;
  readonly token_out: number;
  /** Micro-USD; divide by 1_000_000 for USD. */
  readonly cost_usd_micro: number;
}

export interface Agent {
  readonly id: AgentId;
  readonly tenant_id: TenantId;
  readonly name: string;
  readonly slug: string;
  readonly description: string;
  readonly icon_emoji: string;
  /** HSL hue 0–359. */
  readonly color_hue: number;
  /** Monotonic version counter. */
  readonly version: number;
  readonly status: AgentStatus;
  readonly origin: AgentOrigin;
  /** Set when origin = "custom". null on "system" and "community". */
  readonly owner_user_id: UserId | null;
  readonly instructions: string;
  readonly model_default: AgentModelDefault;
  readonly connectors_default: ReadonlyArray<ConnectorId>;
  readonly skills: ReadonlyArray<SkillId>;
  readonly permissions: AgentPermissions;
  /** Forward-compatible for Phase 11 Memory. null in Phase 8. */
  readonly memory_ref: MemoryRef;
  readonly created_at: string;
  readonly updated_at: string;
  /** Denormalized display hint: caller's install state. */
  readonly viewer_install_status: AgentStatus;
  /** Denormalized display hint: 7-day usage rollup. */
  readonly viewer_usage_7d: AgentUsageRollup | null;
}

export interface AgentVersion {
  readonly id: AgentVersionId;
  readonly agent_id: AgentId;
  readonly version: number;
  readonly instructions_snapshot: string;
  readonly model_default_snapshot: AgentModelDefault;
  readonly skills_snapshot: ReadonlyArray<SkillId>;
  readonly connectors_default_snapshot: ReadonlyArray<ConnectorId>;
  readonly permissions_snapshot: AgentPermissions;
  readonly created_at: string;
  readonly created_by: UserId;
  /** Free-text label e.g. "Pre-Q3-release config". Optional. */
  readonly label: string | null;
}

export interface AgentInstall {
  readonly id: AgentInstallId;
  readonly tenant_id: TenantId;
  readonly user_id: UserId;
  readonly agent_id: AgentId;
  readonly installed_at: string;
  /** Thin per-user override layer. null = no overrides. */
  readonly overrides: AgentOverrides | null;
}

export interface AgentOverrides {
  readonly instructions?: string;
  readonly model_default?: AgentModelDefault;
  readonly skills?: ReadonlyArray<SkillId>;
  readonly connectors_default?: ReadonlyArray<ConnectorId>;
  readonly permissions?: Partial<AgentPermissions>;
}

// ===========================================================================
// List filters + sort (§4.1, §4.13)
// ===========================================================================

export type AgentSortKey =
  | "updated_at:desc"
  | "updated_at:asc"
  | "name:asc"
  | "usage.cost_usd_micro:desc";

export interface ListAgentsFilters {
  readonly origin?: AgentOrigin;
  readonly status?: AgentStatus;
  readonly skill_id?: SkillId;
  readonly connector_id?: ConnectorId;
  /** Admin-only filter. */
  readonly owner_user_id?: UserId;
}

// ===========================================================================
// List responses (§4.1, §4.8)
// ===========================================================================

export interface AgentListResponse {
  readonly items: ReadonlyArray<Agent>;
  readonly next_cursor: string | null;
}

export interface AgentVersionListResponse {
  readonly items: ReadonlyArray<AgentVersion>;
  readonly next_cursor: string | null;
}

// ===========================================================================
// Usage response (§4.9)
// ===========================================================================

export interface AgentUsageResponse {
  readonly agent_id: AgentId;
  readonly period: UsagePeriod;
  readonly rollups: ReadonlyArray<AgentUsageRollup>;
  /** Sum across all rollups. */
  readonly totals: AgentUsageRollup;
}

// ===========================================================================
// Mutations (§4.3 / §4.4 / §4.5 / §4.7 / §4.10)
// ===========================================================================

/** Body for `POST /v1/agents`. */
export interface CreateAgentRequest {
  readonly name: string;
  readonly slug?: string;
  readonly description: string;
  readonly icon_emoji: string;
  readonly color_hue: number;
  readonly instructions: string;
  readonly model_default: AgentModelDefault;
  readonly connectors_default: ReadonlyArray<ConnectorId>;
  readonly skills: ReadonlyArray<SkillId>;
  readonly permissions: AgentPermissions;
  readonly memory_ref?: MemoryRef;
}

/** Body for `PATCH /v1/agents/{id}`. All fields optional. */
export interface UpdateAgentRequest {
  readonly name?: string;
  readonly description?: string;
  readonly icon_emoji?: string;
  readonly color_hue?: number;
  readonly instructions?: string;
  readonly model_default?: AgentModelDefault;
  readonly connectors_default?: ReadonlyArray<ConnectorId>;
  readonly skills?: ReadonlyArray<SkillId>;
  readonly permissions?: AgentPermissions;
  readonly memory_ref?: MemoryRef;
  readonly status?: AgentStatus;
}

/** Body for `POST /v1/agents/{id}/install`. */
export interface InstallAgentRequest {
  /** Default `"user"`. `"tenant"` is admin-only. */
  readonly scope?: "user" | "tenant";
}

/** Body for `POST /v1/agents/{id}/uninstall`. */
export interface UninstallAgentRequest {
  readonly scope?: "user" | "tenant";
}

/** Body for `POST /v1/agents/{id}/versions`. */
export interface SnapshotAgentVersionRequest {
  readonly label?: string;
}

/** Body for `POST /v1/agents/{id}/duplicate`. */
export interface DuplicateAgentRequest {
  /** Auto-suggested as `"<original> (custom)"` when omitted. */
  readonly name?: string;
}

// ===========================================================================
// SSE envelope (§4.12)
// ===========================================================================

export type AgentStreamEventType =
  | "agent_installed"
  | "agent_uninstalled"
  | "agent_updated"
  | "agent_version_snapshot"
  | "agent_status_changed";

/**
 * Discriminated payload union per sub-PRD §4.12. The list-layer reducer
 * only mutates on `agent_updated` / `agent_status_changed` envelopes that
 * carry enough fields to materialize a row; the rest invalidate cache /
 * refetch on the caller side.
 */
export type AgentStreamPayload =
  | {
      readonly agent_id: AgentId;
      readonly user_id?: UserId;
      readonly scope?: "user" | "tenant";
    }
  | {
      readonly agent_id: AgentId;
      readonly version: number;
    }
  | {
      readonly agent_id: AgentId;
      readonly version_id: AgentVersionId;
      readonly version: number;
    }
  | {
      readonly agent_id: AgentId;
      readonly status: AgentStatus;
      readonly prior_status: AgentStatus;
    };

export interface AgentStreamEnvelope {
  readonly sequence_no: number;
  readonly event_type: AgentStreamEventType;
  readonly agent_id: AgentId;
  readonly payload: AgentStreamPayload;
  readonly emitted_at: string;
}
