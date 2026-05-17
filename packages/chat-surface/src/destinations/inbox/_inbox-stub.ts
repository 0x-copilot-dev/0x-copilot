// chat-surface Inbox adapter shape (transitional; orchestrator rewires
// at merge to `@enterprise-search/api-types/inbox`).
//
// Phase 4 has parallel wave-agents working off slightly different
// shape conventions:
//   - P4-A1 (api-types + backend wire) owns canonical
//     `packages/api-types/src/inbox.ts`.
//   - P4-A2/A3 own producer + SSE plumbing.
//   - P4-B1 (this shell), P4-B2 (detail + reply + snooze), P4-B3
//     (960px responsive split-pane) ship UI in chat-surface.
//
// Until P4-A1 lands, this stub is the local view-model contract every
// UI sub-agent consumes. The shape mirrors inbox-prd.md §3 (architecture)
// + §4.1 (wire types) verbatim — naming and discriminators match the
// canonical site so the merge-time rewire is a pure import swap.
//
// Every import of this stub should be marked
// `TODO(merge): rewire to "@enterprise-search/api-types"` so the
// orchestrator's rewrite script can find them.

import type {
  AgentId,
  ApprovalId,
  ConversationId,
  InboxItemId,
  ProjectId,
  RunId,
  UserId,
} from "@enterprise-search/api-types";

// ---- §4.1 Primitive enums --------------------------------------------------

/** What kind of item this is. Source: inbox-prd §4.1. */
export type InboxItemKind = "mention" | "approval_request" | "error" | "system";

/** Per-item lifecycle status. Source: inbox-prd §4.1.
 *
 *  Maps to the four client-side buckets the shell renders:
 *    - `unread` / `read`   -> "Unread" / "Read (last 7d)"
 *    - `snoozed`           -> "Snoozed"
 *    - `done`              -> "Dismissed" (collapsed bucket)
 *
 *  Soft-deleted items never reach the shell (server filters them out
 *  unless an admin opens a compliance read; see inbox-prd §5.3).
 */
export type InboxItemStatus = "unread" | "read" | "done" | "snoozed";

/** Triage priority. Source: inbox-prd §4.1. The producer routing rule
 *  (cross-audit §9.1) dropped the priority predicate for *creation* but
 *  priority is still rendered on the row + drives notifications. */
export type InboxItemPriority = "low" | "med" | "high";

/** Who/what addressed the item to the user. Source: inbox-prd §4.1. */
export type InboxSenderKind = "user" | "agent" | "system";

/** Sub-discriminator for `kind: "system"`. Source: inbox-prd §4.1. */
export type InboxSystemOrigin =
  | "connector_error"
  | "billing"
  | "retention_warning"
  | "admin_action";

/** Discriminated-union sender shape. Source: inbox-prd §4.1. */
export type InboxSender =
  | { readonly kind: "user"; readonly user_id: UserId }
  | {
      readonly kind: "agent";
      readonly agent_id: AgentId;
      readonly agent_name: string;
    }
  | { readonly kind: "system"; readonly origin: InboxSystemOrigin };

// ---- §3.2 / §4.1 Row shape -----------------------------------------------

/**
 * Single inbox item. Section bucketing happens client-side (per
 * inbox-prd §3.2 + sub-PRD §8 list endpoint shape):
 *   - Unread              : status === "unread"
 *   - Snoozed             : status === "snoozed"
 *   - Read (last 7d)      : status === "read" && updated_at >= now - 7d
 *   - Dismissed           : status === "done" (collapsed by default)
 *
 * `body` is intentionally NOT on the list row — inbox-prd §3.4 + §10
 * (perf): the list endpoint omits body bytes; the detail view fetches
 * `GET /v1/inbox/{id}` lazily on mount. P4-B2 wires that fetch through
 * its `renderDetail` slot.
 */
export interface InboxItem {
  readonly id: InboxItemId;
  readonly sender: InboxSender;
  readonly kind: InboxItemKind;
  readonly subject: string; // <= 200 chars
  readonly preview: string; // <= 200 chars
  readonly status: InboxItemStatus;
  readonly priority: InboxItemPriority;
  readonly labels: ReadonlyArray<string>;

  /** Originating thread for mentions / approval_request / error.
   *  Wire-level uses `ConversationId` brand at the canonical site; the
   *  stub keeps it as `string` to avoid a circular brand import. */
  readonly thread_id?: string;

  /** Originating run for approval_request / error. */
  readonly run_id?: string;

  /** Present iff `kind === "approval_request"`. Drives the inline
   *  `<ApprovalCard>` in P4-B2's detail slot. */
  readonly approval_id?: string;

  readonly project_id?: ProjectId;

  /** ISO-8601 instant; present iff `status === "snoozed"`. */
  readonly snoozed_until?: string;

  readonly created_at: string; // ISO-8601
  readonly updated_at: string;

  /**
   * Outbound cross-destination references for primary navigation +
   * inline chips. The shell renders the FIRST entry as the row's
   * primary `<ItemLink>` per the brief; additional entries surface as
   * inline chips in the row metadata.
   *
   * Convention (matches inbox-prd §13):
   *   - `[0]`: opens the item's detail (`{ kind: "inbox_item", id }`)
   *   - additional: thread / run / approval / agent / project chips.
   *
   * Pre-computed by the host (apps/frontend P4-C) so the shell stays
   * substrate-agnostic — no routing or registry calls on the render
   * path.
   */
  readonly links: ReadonlyArray<InboxItemRef>;
}

/**
 * The shape `<ItemLink>` consumes. This is a structural subset of
 * `ItemRef` from api-types — kept as a local re-export so the shell
 * file imports a single stub module and the discriminator branches
 * still use the canonical branded id types (so a stub `InboxItemRef`
 * is assignment-compatible with the registry's `ItemRef`). Merge-time
 * rewire collapses this to `ItemRef`.
 */
export type InboxItemRef =
  | { readonly kind: "inbox_item"; readonly id: InboxItemId }
  | { readonly kind: "chat"; readonly id: ConversationId }
  | { readonly kind: "run"; readonly id: RunId }
  | { readonly kind: "approval"; readonly id: ApprovalId }
  | { readonly kind: "agent"; readonly id: AgentId }
  | { readonly kind: "project"; readonly id: ProjectId }
  | { readonly kind: "person"; readonly id: UserId };

// ---- §3.2 Section keys ----------------------------------------------------

/**
 * Stable bucket keys used for section ordering, render-state tests, and
 * telemetry. Order here is the render order — Unread first so users
 * can't miss it (mirrors todos-prd "Overdue first" pattern). Dismissed
 * is last and collapsed by default per the brief.
 */
export type InboxSectionKey = "unread" | "snoozed" | "read" | "dismissed";

/**
 * Read-window cutoff. inbox-prd doesn't pin a number for the Read
 * bucket — the brief specifies "Read (last 7d)". Older reads are still
 * reachable via filter chips (Mentions / Approvals / Errors) which the
 * panel surfaces but live outside the bucketed view.
 */
export const READ_LOOKBACK_MS = 7 * 24 * 60 * 60 * 1000;
