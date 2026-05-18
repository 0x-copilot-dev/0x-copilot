// Agents destination (Phase 8) — App-store wire contract.
//
// Source: docs/atlas-new-design/destinations/agents-prd.md
//   §3.1 (canonical wire types) — Agent + AgentInstall + AgentPermissions.
//   §4   (endpoints) — list + detail + CRUD + install + uninstall.
//   §6   (audit + ACL).
//
// This file is the agents-only wire shape. Per the P8-A1 split:
//
//   * P8-A1 (this file) — Agent, AgentInstall, AgentPermissions, status/
//     origin enums, list response, request bodies for create/patch.
//   * P8-A2 — AgentVersion (versions endpoint + snapshot semantics).
//   * P8-A3 — install/uninstall operational payloads + overrides.
//   * P8-A5 — index.ts re-exports + cross-destination ItemRef branches.
//
// AgentVersion / AgentVersionId / AgentInstallId / MemoryRef / AgentOverrides
// are referenced by AgentInstall here; the AgentVersion *body* lands in P8-A2.
// We declare the missing brand types LOCALLY in this file so the contract
// type-checks today; P8-A5 hoists them to ./brands.ts during the deltas pass
// and re-exports here. This is the only deviation from the PRD §3.1 import
// list — every other type comes from ./brands and ./refs unchanged.

import type { AgentId, ConnectorId, SkillId, TenantId, UserId } from "./brands";

// ---------------------------------------------------------------------------
// Local brand types (hoisted to ./brands.ts by P8-A5)
// ---------------------------------------------------------------------------

/**
 * Immutable agent-version snapshot id. Created by ``POST /v1/agents/<id>/
 * versions`` (P8-A2); referenced by ``AgentInstall.pinned_version_id`` and
 * by Routines' ``agent_version_pin`` (Routines §9.7 Q11).
 */
export type AgentVersionId = string & { readonly __brand: "AgentVersionId" };

/**
 * Per-user install row id. Created by ``POST /v1/agents/<id>/install``
 * (P8-A3). Forms the natural key ``(tenant_id, agent_id, user_id)``; the
 * surrogate id is on the row so audit-row targets can name it directly.
 */
export type AgentInstallId = string & { readonly __brand: "AgentInstallId" };

/**
 * Forward-compat hook for Phase 11 Memory. Today this is always ``null`` on
 * the wire; Phase 11 will tag it with a structured reference (slot or
 * scoped namespace). Declared inline so Phase 8 ships without depending on
 * Memory's wire shape.
 */
export type MemoryRef = string & { readonly __brand: "MemoryRef" };

// ---------------------------------------------------------------------------
// Enums
// ---------------------------------------------------------------------------

/**
 * Where the agent record originated.
 *
 *   * ``system``    — shipped by the catalog seeder; tenant-readable, never
 *     mutated by users. The PATCH endpoint refuses with 409 (must
 *     duplicate); see §4.4 + §4.10.
 *   * ``community`` — submitted by a 3rd party; same write semantics as
 *     ``system``. The submission flow itself is Wave 8 (master §10 Q1).
 *   * ``custom``    — owner-authored within the tenant. Owner-only writes;
 *     non-owners 404 (cross-audit §1.3 master rule).
 */
export type AgentOrigin = "system" | "community" | "custom";

/**
 * App-store-style install state — explicit four-way enum so the UI can
 * render each card without an inference layer.
 *
 *   * ``installed`` — caller has an active install row.
 *   * ``available`` — public catalog entry the caller hasn't installed.
 *   * ``disabled``  — install row exists with ``disabled=true``.
 *   * ``draft``     — custom agent that hasn't been installed yet (the
 *     owner's first edit-cycle state).
 *
 * Status taxonomy is per agents-prd §1.6. Filtering on this axis is
 * multi-value OR (cross-audit §1.5).
 */
export type AgentStatus = "installed" | "available" | "disabled" | "draft";

/**
 * How the agent's runs interact with the approval flow.
 *
 *   * ``manual_approval`` — every write tool call surfaces an Inbox approval
 *     before firing. Default for custom + community.
 *   * ``auto_apply``      — agent fires write tool calls without an
 *     approval gate. Reserved for trusted system agents and per-user
 *     opt-in overrides (P8-A3 thin-layer overrides).
 */
export type AgentAutonomy = "manual_approval" | "auto_apply";

/**
 * Reasoning depth knob exposed on the agent's model default. Mirrors the
 * runtime's ``reasoning_depth`` parameter; the canonical mapping from
 * depth → token/time budget lives in ``ai-backend``'s model registry.
 */
export type AgentReasoningDepth = "fast" | "balanced" | "deep";

// ---------------------------------------------------------------------------
// Composite types
// ---------------------------------------------------------------------------

/**
 * Permissions envelope. Applied at run construction time in ``ai-backend``
 * via the internal ``GET /internal/v1/agents/<id>?as_user_id=<u>`` route.
 *
 * Field-wise overrides (§3.3): when a user PATCHes their install with
 * ``overrides.permissions = { autonomy: "manual_approval" }`` the rest of
 * the canonical permissions stay; the override layer is thin by design.
 */
export interface AgentPermissions {
  readonly autonomy: AgentAutonomy;
  /** Max tool calls a single run may make. 0 = no cap. */
  readonly max_tool_calls_per_run: number;
  /** Hard upper bound on output tokens per run. */
  readonly max_output_tokens: number;
  /** Read-only restricts ALL connectors to read scope at fire time. */
  readonly read_only: boolean;
  /** Optional allowlist of skill ids. Empty/undefined = inherit from ``skills``. */
  readonly allowed_skill_ids?: ReadonlyArray<SkillId>;
  /** Optional blocklist of tool family names ("filesystem", "network"). */
  readonly blocked_tool_families?: ReadonlyArray<string>;
}

/** The model + reasoning-depth pair the agent defaults to at run time. */
export interface AgentModelDefault {
  readonly model_id: string; // e.g. "anthropic:claude-sonnet-4-7-1m"
  readonly reasoning_depth: AgentReasoningDepth;
}

/**
 * Canonical Agent record. Returned by:
 *
 *   * ``GET  /v1/agents`` — gallery list.
 *   * ``GET  /v1/agents/<id>`` — detail with merged-overrides view.
 *   * ``POST /v1/agents`` — create-custom response.
 *   * ``PATCH /v1/agents/<id>`` — owner-edit response.
 *   * ``POST /v1/agents/<id>/install`` / ``uninstall`` — install state flip.
 *
 * ``viewer_install_status`` is the **caller-relative** install state — the
 * gallery card uses this for the install/uninstall pill regardless of the
 * underlying ``status`` (which is the agent's own catalog state). The
 * usage rollup is denormalized at read time for the per-card chip.
 */
export interface Agent {
  readonly id: AgentId;
  readonly tenant_id: TenantId;
  readonly name: string;
  readonly slug: string;
  readonly description: string;
  readonly icon_emoji: string;
  readonly color_hue: number; // HSL 0–359
  readonly version: number; // monotonic counter (v3 = 3)
  readonly status: AgentStatus;
  readonly origin: AgentOrigin;
  /** Set when ``origin = "custom"``. ``null`` on ``system`` / ``community``. */
  readonly owner_user_id: UserId | null;
  readonly instructions: string;
  readonly model_default: AgentModelDefault;
  readonly connectors_default: ReadonlyArray<ConnectorId>;
  readonly skills: ReadonlyArray<SkillId>;
  readonly permissions: AgentPermissions;
  /** Provenance: the source agent id when this row came from ``POST /duplicate``. */
  readonly forked_from_agent_id: AgentId | null;
  /** Forward-compatible for Phase 11 Memory. ``null`` in Phase 8. */
  readonly memory_ref: MemoryRef | null;
  readonly created_at: string; // ISO8601
  readonly updated_at: string;
  /** Caller-relative display hint — install state for *this* viewer. */
  readonly viewer_install_status: AgentStatus;
}

// ---------------------------------------------------------------------------
// Per-user overrides + install row (P8-A3 owns the operational endpoints).
// The shape is declared here because ``AgentInstall`` is part of the wire
// contract returned from ``GET /v1/agents/<id>`` (the merged-overrides view).
// ---------------------------------------------------------------------------

/**
 * Thin-layer per-user override. Each field is OPTIONAL; an absent field
 * leaves the canonical agent field untouched at resolution time. The
 * ``permissions`` override merges field-wise (§3.3) — a user can pin
 * ``autonomy="manual_approval"`` without restating the other knobs.
 *
 * Deep edits are intentionally not supported here. If the user wants
 * deeper customization the path is ``POST /v1/agents/<id>/duplicate`` to
 * fork into a fully-owned ``origin="custom"`` agent (§4.10).
 */
export interface AgentOverrides {
  readonly instructions?: string;
  readonly model_default?: AgentModelDefault;
  readonly skills?: ReadonlyArray<SkillId>;
  readonly connectors_default?: ReadonlyArray<ConnectorId>;
  readonly permissions?: Partial<AgentPermissions>;
}

/**
 * Per-user install row. Created by ``POST /v1/agents/<id>/install``
 * (P8-A3 owns the route + service). The ``overrides`` payload is the
 * thin layer applied at ``resolve_agent_view`` time (§3.3).
 */
export interface AgentInstall {
  readonly id: AgentInstallId;
  readonly tenant_id: TenantId;
  readonly user_id: UserId;
  readonly agent_id: AgentId;
  readonly installed_at: string;
  /** ``true`` when the user explicitly disabled the agent (Composer hides). */
  readonly disabled: boolean;
  /** Thin per-user override layer. ``null`` = no overrides. */
  readonly overrides: AgentOverrides | null;
  /**
   * Optional version pin. ``null`` = live (auto-upgrade on snapshots);
   * non-null = the user pinned a specific ``AgentVersion`` (§3.2). The
   * version body itself is fetched separately via P8-A2.
   */
  readonly pinned_version_id: AgentVersionId | null;
}

// ---------------------------------------------------------------------------
// List response + request bodies
// ---------------------------------------------------------------------------

/** Cursor-paginated catalog listing. */
export interface AgentListResponse {
  readonly items: ReadonlyArray<Agent>;
  readonly next_cursor: string | null;
}

/** Body for ``POST /v1/agents`` — create custom agent (§4.3). */
export interface CreateAgentRequest {
  readonly name: string;
  /** Optional; auto-generated from name when absent. */
  readonly slug?: string;
  readonly description?: string;
  readonly icon_emoji?: string;
  readonly color_hue?: number;
  readonly instructions?: string;
  readonly model_default?: AgentModelDefault;
  readonly connectors_default?: ReadonlyArray<ConnectorId>;
  readonly skills?: ReadonlyArray<SkillId>;
  readonly permissions?: AgentPermissions;
  readonly memory_ref?: MemoryRef | null;
}

/** Body for ``PATCH /v1/agents/<id>`` — owner-only edit on live record (§4.4). */
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
  readonly memory_ref?: MemoryRef | null;
  /**
   * Status flip — owner may move ``draft`` → ``installed`` on first install,
   * or toggle ``disabled``. Origin-immutable ``system``/``community`` agents
   * refuse the PATCH with 409 (must duplicate first).
   */
  readonly status?: AgentStatus;
}

// ---------------------------------------------------------------------------
// Filter axis allowlist (cross-audit §1.5 reproduction so the FE can
// statically narrow ``filter[<axis>]`` keys).
// ---------------------------------------------------------------------------

export type AgentListFilterAxis =
  | "origin"
  | "status"
  | "skill_id"
  | "connector_id"
  | "owner_user_id";

/** Allowed sort tokens — ``filter[sort]=…``. */
export type AgentListSort =
  | "updated_at:desc"
  | "updated_at:asc"
  | "name:asc"
  | "usage.cost_usd_micro:desc";
