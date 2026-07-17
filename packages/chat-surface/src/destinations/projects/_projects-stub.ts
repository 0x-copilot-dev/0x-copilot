// chat-surface Projects adapter shape (transitional; orchestrator rewires
// at merge to `@0x-copilot/api-types/projects`).
//
// Phase 6 has parallel wave-agents working off the same shape conventions
// as the canonical projects-prd.md §3 + §4.1:
//   - P6-A1 (api-types + backend wire) will own canonical
//     `packages/api-types/src/projects.ts`.
//   - P6-A2 (ACL + activity projector) owns the per-tenant projector.
//   - P6-B1 (this shell), P6-B2 (detail + members), P6-B3 (activity tab)
//     ship UI in chat-surface.
//
// Until P6-A1 lands, this stub is the local view-model contract every
// UI sub-agent consumes. Naming + discriminators match the canonical
// site so merge-time rewire is a pure import swap.
//
// Every import of this stub should be marked
// `TODO(merge): rewire to "@0x-copilot/api-types"` so the
// orchestrator's rewrite script can find them.

import type { ProjectId, TenantId, UserId } from "@0x-copilot/api-types";

// ---- §4.1 enums -----------------------------------------------------------

/** Lifecycle status. Source: projects-prd §4.1. Note: no `paused` / `draft`
 *  for projects — they don't have a draft state (projects-prd §3.2). */
export type ProjectStatus = "active" | "archived";

/** Membership role. Source: projects-prd §4.1. */
export type ProjectRole = "owner" | "editor" | "viewer";

/** Color hue (HSL 0–359). Lightness + saturation are design-system fixed. */
export type ProjectColorHue = number;

/** Validated server-side as a single emoji glyph. */
export type ProjectIconEmoji = string;

// ---- §4.1 Denormalized activity counts ------------------------------------

/**
 * Denormalized counts for list-view perf. Source: projects-prd §4.1.
 * Refreshed by the activity projector (§3.7); the shell trusts these
 * values without re-aggregating.
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

// ---- §4.1 Project summary -------------------------------------------------

/**
 * Lightweight projection used by list endpoints and `<ItemLink>` resolvers.
 * Mirrors projects-prd §4.1 `ProjectSummary` exactly. The shell renders a
 * flat list; section bucketing on the destination is filter-driven (no
 * server-side grouping — cross-audit §1.5 multi-value OR semantics).
 *
 * `viewer_role` is `null` iff the caller is not a member (some destinations
 * surface non-member-visible projects for cross-tenant admins; the shell
 * renders the visibility chip regardless and disables hover actions).
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
  /** Denormalized owner display name (server-projected for list perf). */
  readonly owner_display_name?: string;
  readonly viewer_role: ProjectRole | null;
  readonly viewer_starred: boolean;
  readonly counts: ProjectActivityCounts;
  readonly last_activity_at: string | null;
  readonly updated_at: string;
}
