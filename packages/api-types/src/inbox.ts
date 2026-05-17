// Inbox destination (Phase 4) — CRUD + state machine + multi-link
// wire contract.
//
// Source: docs/atlas-new-design/destinations/inbox-prd.md §3 (item
// shape), §4 (endpoints), §7 (ACL), and docs/atlas-new-design/cross-audit.md
// §1.1 (links collapse to ItemRef[]), §1.3 (project-scoped ACL — recipient
// writes, project-member reads, tenant admin compliance reads, 404-not-403),
// §1.5 (multi-value OR filter axes), §2.1 (branded IDs), §9.1 (Inbox Q6
// revised — inline by default; durable inbox item only when the user has
// NOT viewed the originating thread within a tenant-configurable window,
// independent of priority), and §9.3 (reply-to-error → agent owner +
// connectors repair flow).
//
// Wire-only file: no business logic, no HTTP client, no view models.
// Servers own routes; this package mirrors public payloads exactly as
// the facade serves them. Internal `/internal/v1/*` producer payloads
// (P4-A2) are NOT mirrored here — the producer contract lives behind
// the service boundary.

import type { InboxItemId, ProjectId, TenantId, UserId } from "./brands";
import type { ItemRef } from "./refs";

// ---------------------------------------------------------------------------
// Primitive enums
// ---------------------------------------------------------------------------

/**
 * Discrete inbox-item kinds. Drift between this union and the server
 * CHECK constraint is a bug; the server validates against the same
 * allowlist on every producer write.
 *
 * - `approval_request` — Atlas drafted an edit the user must approve.
 *   The inline approval block in the originating thread is the source
 *   of truth; this row is the out-of-band fallback when the user is
 *   elsewhere (cross-audit §9.1 / inbox-prd §1.3).
 * - `mention` — a teammate or teammate's agent `@user`d the recipient.
 * - `error` — connector token expired, tool error, run failed; only
 *   the user can fix it (re-auth / edit credentials). cross-audit §9.3.
 * - `agent_question` — Atlas needs a clarifying answer mid-run (not a
 *   sign-off; a content question).
 * - `share_invite` — another user shared a chat / project / library
 *   item with the recipient.
 * - `system_announcement` — billing / plan / admin actions on the
 *   user's account; retention warnings.
 */
export type InboxItemKind =
  | "approval_request"
  | "mention"
  | "error"
  | "agent_question"
  | "share_invite"
  | "system_announcement";

/**
 * Lifecycle state machine:
 *
 *   unread ──read──▶ read
 *      │              │
 *      ├──snooze──▶ snoozed (with snoozed_until; cron wakes → unread)
 *      │
 *      └──dismiss──▶ dismissed (terminal; soft-delete marker)
 *
 *  Each transition writes one audit row. Bulk mutations stamp a shared
 *  `correlation_id` on every row so SIEM reconstructs the bulk as a unit.
 */
export type InboxItemState = "unread" | "read" | "snoozed" | "dismissed";

// ---------------------------------------------------------------------------
// Sender shape (denormalized for list rendering; the canonical resolve
// goes through the ItemRef registry on row open).
// ---------------------------------------------------------------------------

/**
 * Who produced the item. `ref` is the canonical cross-destination link
 * (agent / user / system). `agent_name` / `origin` are display
 * denormalizations refreshed on every producer write — never the source
 * of truth for the linked entity (cross-audit §1.1 binding).
 *
 * `ref.kind`:
 *  - `"agent"` — Atlas or a teammate's agent. `agent_name` populated.
 *  - `"person"` — a human teammate. `agent_name` absent.
 *  - The producer (P4-A2) MAY also emit `ref.kind = "connector"` for
 *    system origins (`connector_error`); `origin` carries the system
 *    bucket (`connector_error` / `billing` / `retention_warning` /
 *    `admin_action`) for routing.
 */
export interface InboxItemSender {
  readonly ref: ItemRef;
  /** Display label when `ref.kind === "agent"`. */
  readonly agent_name?: string;
  /** Free-form bucket for system-origin items. Allowlisted server-side. */
  readonly origin?: string;
}

// ---------------------------------------------------------------------------
// Canonical InboxItem shape
// ---------------------------------------------------------------------------

/**
 * One inbox row. `links` is the unified cross-destination pointer
 * field per cross-audit §1.1 — every `thread_id` / `run_id` /
 * `approval_id` / `project_id` from the original sub-PRD collapses into
 * `ReadonlyArray<ItemRef>`. Consumers `switch (ref.kind)` for the type
 * narrowing they need.
 *
 * `body_ref` is an opaque pointer to the inbox-body store; the body is
 * lazy-loaded on detail mount via `GET /v1/inbox/{id}` (inbox-prd §3.4
 * + §10 list-vs-body split). Absent for items whose `title` is the
 * complete payload (e.g. some system announcements).
 *
 * `received_at` is server-stamped; clients render relative time.
 * `read_at` / `snoozed_until` / `dismissed_at` are present iff `state`
 * is in the corresponding bucket.
 */
export interface InboxItem {
  readonly id: InboxItemId;
  readonly tenant_id: TenantId;
  /** Recipient — the user this item is addressed to. */
  readonly owner_user_id: UserId;
  /** Project context, if the item was filed under a project. */
  readonly project_id: ProjectId | null;
  readonly kind: InboxItemKind;
  readonly title: string;
  /** Opaque pointer to the inbox-body row; deref via GET /v1/inbox/{id}. */
  readonly body_ref?: string;
  /** Cross-destination links (thread, run, approval, project, etc.). */
  readonly links: ReadonlyArray<ItemRef>;
  readonly sender: InboxItemSender;
  readonly state: InboxItemState;
  /** ISO-8601 UTC; server-stamped at insert time. */
  readonly received_at: string;
  /** ISO-8601 UTC; set when state transitions to `read`. */
  readonly read_at?: string;
  /** ISO-8601 UTC; set when state transitions to `snoozed`. */
  readonly snoozed_until?: string;
  /** ISO-8601 UTC; set when state transitions to `dismissed`. */
  readonly dismissed_at?: string;
}

// ---------------------------------------------------------------------------
// List / mutation payloads
// ---------------------------------------------------------------------------

/**
 * Cursor-paginated list response. `next_cursor` is opaque
 * (base64 of `(received_at, id)`); the client passes it back verbatim.
 * Absent means "no more pages".
 *
 * `unread_count` is the tenant-scoped recipient-scoped unread total —
 * the rail badge reads from this on first paint, then folds SSE deltas
 * (P4-A3) and falls back to `GET /v1/inbox/unread_count` polling when
 * SSE is degraded (inbox-prd §5.2 SSE-fallback convention).
 */
export interface InboxListResponse {
  readonly items: ReadonlyArray<InboxItem>;
  readonly next_cursor?: string;
  readonly unread_count: number;
}

/**
 * PATCH body. Every field is optional; transitions to `snoozed` MUST
 * carry a future `snoozed_until` (ISO-8601 UTC); other transitions MAY
 * omit it. `state` validates against the state machine:
 *
 *   - any → `read` clears `snoozed_until`, stamps `read_at`.
 *   - any → `snoozed` requires `snoozed_until > now`.
 *   - any → `dismissed` stamps `dismissed_at` (terminal).
 *   - re-opening a dismissed row is not supported (use a producer
 *     resubmit with the same `external_ref` to revive).
 */
export interface UpdateInboxItemRequest {
  readonly state?: InboxItemState;
  readonly snoozed_until?: string;
}

/** Permitted bulk-action verbs. Each bulk write produces one audit row
 * per affected item, all sharing the request's `correlation_id`
 * (inbox-prd §6 + cross-audit §1.4). */
export type BulkInboxAction =
  | "mark_read"
  | "mark_unread"
  | "dismiss"
  | "snooze";

/**
 * Bulk-mutation body. The optional `payload` shape depends on `action`:
 *   - `snooze` → `{ snoozed_until: string }` (ISO-8601 UTC; > now).
 *   - others   → omit.
 *
 * `correlation_id` is client-minted (uuid v4 typical); the server
 * stamps it on every audit row written by the bulk so SIEM can
 * reconstruct the rows as a unit (cross-audit §1.4).
 */
export interface BulkUpdateInboxItemsRequest {
  readonly action: BulkInboxAction;
  readonly ids: ReadonlyArray<InboxItemId>;
  readonly correlation_id: string;
  readonly payload?: {
    readonly snoozed_until?: string;
  };
}

/** Bulk response. `affected` counts rows the server actually mutated;
 * ids the caller has no write access to are silently dropped (cross-audit
 * §1.3 — non-writers get a best-effort skip on bulk). */
export interface BulkUpdateInboxItemsResponse {
  readonly affected: number;
  readonly correlation_id: string;
}

/**
 * Lightweight count for the rail badge. SSE fallback when the durable
 * `/v1/inbox/stream` (P4-A3) is degraded — clients poll this every 60s.
 *
 * `as_of` is server-stamped so the rail can show a "stale" badge when
 * the last successful fetch slips beyond the fresh window.
 */
export interface InboxUnreadCountResponse {
  readonly unread_count: number;
  readonly as_of: string;
}
