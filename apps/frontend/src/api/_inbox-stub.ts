// Local stub for the Phase 4 Inbox wire contract.
//
// The canonical types live in `@0x-copilot/api-types`
// (`packages/api-types/src/inbox.ts`), authored by the parallel
// Phase 4 Impl-A backend-types agent. This frontend wave (P4-C) runs
// in parallel against the same sub-PRD spec and cannot import a type
// that is not yet on `main`, so this stub mirrors the shapes in
// `docs/atlas-new-design/destinations/inbox-prd.md` §4 (wire contracts).
//
// `InboxItemId` already lives in `@0x-copilot/api-types/brands.ts`
// — re-export from there so the cross-destination `<ItemLink>` registry
// stays a single source of truth even before the rest of the Inbox
// contract merges.
//
// TODO(merge): delete this file. Replace every `_inbox-stub` import
// with `@0x-copilot/api-types` once Impl-A's
// `packages/api-types/src/inbox.ts` lands on main.

import type { InboxItemId } from "@0x-copilot/api-types";

export type { InboxItemId };

/**
 * Opaque body-ref handle. Sub-PRD §4.1 splits the body into a separate
 * row (`inbox_bodies`) so list queries don't pay for body bytes; the
 * client dereferences via `GET /v1/inbox/<id>` when the detail view
 * opens.
 */
export type InboxBodyRef = string & { readonly __brand: "InboxBodyRef" };

export type InboxItemKind = "mention" | "approval_request" | "error" | "system";

export type InboxItemStatus = "unread" | "read" | "done" | "snoozed";

export type InboxItemPriority = "low" | "med" | "high";

export type InboxSenderKind = "user" | "agent" | "system";

export type InboxSystemOrigin =
  | "connector_error"
  | "billing"
  | "retention_warning"
  | "admin_action";

export type InboxSender =
  | { readonly kind: "user"; readonly user_id: string }
  | {
      readonly kind: "agent";
      readonly agent_id: string;
      readonly agent_name: string;
    }
  | { readonly kind: "system"; readonly origin: InboxSystemOrigin };

/** Sub-PRD §4.1 — canonical row shape returned by `GET /v1/inbox`. */
export interface InboxItem {
  readonly id: InboxItemId;
  readonly tenant_id: string;
  readonly recipient_user_id: string;
  readonly sender: InboxSender;
  readonly kind: InboxItemKind;
  readonly subject: string;
  readonly preview: string;
  readonly body_ref: InboxBodyRef;
  readonly thread_id?: string;
  readonly run_id?: string;
  readonly approval_id?: string;
  readonly project_id?: string;
  readonly status: InboxItemStatus;
  readonly snoozed_until?: string;
  readonly priority: InboxItemPriority;
  readonly labels: ReadonlyArray<string>;
  readonly created_at: string;
  readonly updated_at: string;
}

/**
 * Body returned by `GET /v1/inbox/{id}`. Lazy-fetched only on detail
 * mount — sub-PRD §3.4 + §10.
 */
export interface InboxItemBody {
  readonly id: InboxItemId;
  readonly body: string;
}

/**
 * `GET /v1/inbox/{id}` returns the item + body. The list endpoint
 * returns only the row (without body bytes) — body fetches happen
 * exactly once per detail mount.
 */
export interface InboxItemWithBody extends InboxItem {
  readonly body: string;
}

// ===========================================================================
// List + filter (sub-PRD §4.2, §4.4)
// ===========================================================================

export type InboxSortKey =
  | "created_at:desc"
  | "created_at:asc"
  | "priority:desc"
  | "snoozed_until:asc";

export interface ListInboxFilters {
  readonly status?: InboxItemStatus;
  readonly kind?: InboxItemKind;
  readonly sender_kind?: InboxSenderKind;
  readonly sender_id?: string;
  readonly project_id?: string;
}

export interface ListInboxResponse {
  readonly items: ReadonlyArray<InboxItem>;
  readonly next_cursor: string | null;
}

// ===========================================================================
// Mutations (sub-PRD §4.2)
// ===========================================================================

/**
 * PATCH /v1/inbox/{id} — mutate status + labels.
 *
 * `snoozed_until` is required when `status === "snoozed"` (sub-PRD §5.1
 * CHECK constraint); the server enforces, and the wrapper passes
 * through verbatim.
 */
export interface UpdateInboxRequest {
  readonly status?: InboxItemStatus;
  readonly snoozed_until?: string | null;
  readonly labels?: ReadonlyArray<string>;
}

/**
 * POST /v1/inbox/bulk-action — sub-PRD §4.2. Currently supports
 * mark-all-read and bulk-dismiss within a filter (sub-PRD §16 Q2 still
 * open; the wrapper passes whatever the server allowlists).
 */
export type BulkInboxAction =
  | { readonly action: "mark_read"; readonly filter_payload: ListInboxFilters }
  | { readonly action: "mark_done"; readonly filter_payload: ListInboxFilters }
  | { readonly action: "dismiss"; readonly filter_payload: ListInboxFilters };

export interface BulkInboxResponse {
  readonly affected: number;
  readonly correlation_id: string;
}

// ===========================================================================
// Unread count + reply (sub-PRD §4.2)
// ===========================================================================

export interface InboxUnreadCount {
  readonly unread: number;
  readonly high_priority_unread: number;
  readonly as_of: string;
}

export interface InboxReplyRequest {
  readonly text: string;
}

export interface InboxReplyResponse {
  /** Thread the reply landed in (existing if `thread_id` was set, new otherwise). */
  readonly thread_id: string;
  /** True iff a new thread was created (no upstream `thread_id`). */
  readonly created_new_thread: boolean;
}

// ===========================================================================
// SSE envelope (sub-PRD §3.6, §4.1)
// ===========================================================================

export type InboxStreamEventType =
  | "item_created"
  | "item_updated"
  | "item_deleted";

export interface InboxStreamEnvelope {
  readonly sequence_no: number;
  readonly event_type: InboxStreamEventType;
  readonly item: InboxItem;
  readonly emitted_at: string;
}
