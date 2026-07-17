// Typed wrappers for the Phase 4 Inbox destination.
//
// Surfaces:
//   1. `fetchInbox(identity, opts)`         — GET /v1/inbox (sub-PRD §4.2).
//   2. `fetchInboxItem(identity, id)`       — GET /v1/inbox/{id}; body lazy-loaded
//                                              on detail mount (sub-PRD §3.4, §10).
//   3. `patchInbox / bulkInbox`             — status / labels mutation + bulk.
//   4. `replyToInboxItem`                   — POST /v1/inbox/{id}/reply.
//   5. `fetchUnreadCount`                   — lightweight badge count
//                                              (sub-PRD §4.2, §3.6).
//   6. `streamInboxEvents({...})`           — SSE durable item channel
//                                              (sub-PRD §3.6).
//
// Network rule (CLAUDE.md / `apps/frontend/CLAUDE.md`): apps call the
// **facade** only (`/v1/*`). Never `backend:8100` or `ai-backend:8000`
// directly. The transport singleton enforces this via the same-origin
// Vite proxy → facade.
//
// Wire types come from `@0x-copilot/api-types/src/inbox.ts`
// once Phase 4 Impl-A lands. Today they live in `./_inbox-stub` so the
// frontend wave can run in parallel.
//
// TODO(merge): swap every `./_inbox-stub` import for
// `@0x-copilot/api-types`.

import type { InboxItemId } from "@0x-copilot/api-types";

import type { RequestIdentity } from "./config";
import { httpDelete, httpGet, httpPatchQuery, httpPostQuery } from "./http";
import { getAppTransport } from "./transport";
import type {
  BulkInboxAction,
  BulkInboxResponse,
  InboxItemWithBody,
  InboxReplyRequest,
  InboxReplyResponse,
  InboxSortKey,
  InboxStreamEnvelope,
  InboxUnreadCount,
  ListInboxFilters,
  ListInboxResponse,
  UpdateInboxRequest,
} from "./_inbox-stub";

const SSE_EVENT_NAME = "inbox_event";

// ===========================================================================
// LIST
// ===========================================================================

export interface FetchInboxOptions {
  readonly filters?: ListInboxFilters;
  readonly q?: string;
  readonly sort?: InboxSortKey;
  /** Opaque cursor from a previous response's `next_cursor`. */
  readonly after?: string;
  /** 1..200; server default 50. */
  readonly limit?: number;
}

/**
 * GET /v1/inbox with allowlisted filters + cursor pagination
 * (sub-PRD §4.2, §4.4, §8). Filter encoding mirrors `todosApi.fetchTodos`
 * — `filter[<axis>]=<value>` keys, single value per axis (the server's
 * allowlist disallows repeated axes per sub-PRD §8).
 */
export function fetchInbox(
  identity: RequestIdentity,
  options: FetchInboxOptions = {},
): Promise<ListInboxResponse> {
  return httpGet<ListInboxResponse>(
    "/v1/inbox",
    identity,
    encodeListParams(options),
  );
}

// ===========================================================================
// DETAIL (body lazy-fetch — sub-PRD §3.4, §10)
// ===========================================================================

/**
 * GET /v1/inbox/{id}. The list endpoint never returns body bytes; this
 * is the only path that joins `inbox_bodies`. Every successful call
 * writes an `inbox.item_body_accessed` audit row server-side
 * (sub-PRD §6.1).
 */
export function fetchInboxItem(
  identity: RequestIdentity,
  id: InboxItemId,
): Promise<InboxItemWithBody> {
  return httpGet<InboxItemWithBody>(
    `/v1/inbox/${encodeURIComponent(id)}`,
    identity,
  );
}

// ===========================================================================
// MUTATIONS
// ===========================================================================

/**
 * PATCH /v1/inbox/{id} — mutate status (read/done/snoozed) + labels.
 * Server validates the `(status === 'snoozed') === (snoozed_until !== null)`
 * invariant (sub-PRD §5.1 CHECK constraint).
 */
export function patchInbox(
  identity: RequestIdentity,
  id: InboxItemId,
  body: UpdateInboxRequest,
): Promise<{ readonly id: InboxItemId }> {
  return httpPatchQuery<{ readonly id: InboxItemId }>(
    `/v1/inbox/${encodeURIComponent(id)}`,
    body,
    identity,
  );
}

/**
 * DELETE /v1/inbox/{id} — soft dismiss. Tombstone retained per
 * sub-PRD §5.3 retention rules (30 days, then hard delete).
 */
export function dismissInbox(
  identity: RequestIdentity,
  id: InboxItemId,
): Promise<void> {
  return httpDelete(`/v1/inbox/${encodeURIComponent(id)}`, identity);
}

/**
 * POST /v1/inbox/bulk-action (sub-PRD §4.2). Single transaction; one
 * audit row per affected item with a shared `correlation_id`.
 */
export function bulkInbox(
  identity: RequestIdentity,
  body: BulkInboxAction,
): Promise<BulkInboxResponse> {
  return httpPostQuery<BulkInboxResponse>(
    "/v1/inbox/bulk-action",
    body,
    identity,
  );
}

/**
 * POST /v1/inbox/{id}/reply (sub-PRD §3.4, §11). Routes to the upstream
 * `thread_id` if set; else creates a new thread between sender and
 * recipient. Returns the resulting `thread_id` either way so the
 * destination can navigate after send.
 */
export function replyToInboxItem(
  identity: RequestIdentity,
  id: InboxItemId,
  body: InboxReplyRequest,
): Promise<InboxReplyResponse> {
  return httpPostQuery<InboxReplyResponse>(
    `/v1/inbox/${encodeURIComponent(id)}/reply`,
    body,
    identity,
  );
}

// ===========================================================================
// UNREAD COUNT (rail badge — sub-PRD §3.6)
// ===========================================================================

/**
 * GET /v1/inbox/unread_count. Edge-cached 5s (sub-PRD §10). Used for
 * the initial badge load and the polling fallback when SSE is
 * unavailable. SSE deltas invalidate the local count without waiting
 * for the 5s edge TTL.
 */
export function fetchUnreadCount(
  identity: RequestIdentity,
): Promise<InboxUnreadCount> {
  return httpGet<InboxUnreadCount>("/v1/inbox/unread_count", identity);
}

// ===========================================================================
// SSE (durable item channel — sub-PRD §3.6)
// ===========================================================================

/** Closeable handle for a running inbox-events SSE subscription. */
export interface InboxEventsStream {
  close(): void;
}

/**
 * Open the durable inbox-events SSE stream (sub-PRD §3.6). Each frame
 * carries one `InboxStreamEnvelope`; the client tracks the highest
 * `sequence_no` and reconnects with `?after_sequence=N` to resume
 * without dropping events.
 *
 * Reconnect policy is owned caller-side (mirrors `streamHomeActivity` in
 * homeApi.ts) — the wrapper exposes one connection attempt + a stable
 * error hook so tests can drive the timing deterministically.
 */
export function streamInboxEvents({
  identity,
  afterSequence,
  onEvent,
  onError,
  onOpen,
}: {
  readonly identity: RequestIdentity;
  /** Highest `sequence_no` already applied; backend replays everything strictly greater. */
  readonly afterSequence?: number;
  readonly onEvent: (envelope: InboxStreamEnvelope) => void;
  readonly onError: (err: Event) => void;
  readonly onOpen?: () => void;
}): InboxEventsStream {
  return getAppTransport().subscribeServerSentEvents({
    path: "/v1/inbox/stream",
    query: inboxSseQueryFor(identity, afterSequence),
    eventName: SSE_EVENT_NAME,
    onOpen,
    onError: (_err) => onError(streamErrorEvent()),
    onMessage: (data) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(data) as unknown;
      } catch {
        // Malformed JSON — drop the frame. Mirrors homeApi.ts behavior:
        // a single bad frame must not tear down the connection; the
        // caller has `onError` for the broader "stream broken" signal.
        return;
      }
      if (isInboxStreamEnvelope(parsed)) {
        onEvent(parsed);
      }
    },
  });
}

// ===========================================================================
// Helpers
// ===========================================================================

function encodeListParams(
  options: FetchInboxOptions,
): Record<string, string | undefined> {
  const params: Record<string, string | undefined> = {};
  const { filters, q, sort, after, limit } = options;

  if (filters?.status !== undefined) {
    params["filter[status]"] = filters.status;
  }
  if (filters?.kind !== undefined) {
    params["filter[kind]"] = filters.kind;
  }
  if (filters?.sender_kind !== undefined) {
    params["filter[sender_kind]"] = filters.sender_kind;
  }
  if (filters?.sender_id !== undefined) {
    params["filter[sender_id]"] = filters.sender_id;
  }
  if (filters?.project_id !== undefined) {
    params["filter[project_id]"] = filters.project_id;
  }
  if (q !== undefined && q.length > 0) {
    params.q = q;
  }
  if (sort !== undefined) {
    params.sort = sort;
  }
  if (after !== undefined) {
    params.after = after;
  }
  if (limit !== undefined) {
    params.limit = String(limit);
  }
  return params;
}

function inboxSseQueryFor(
  identity: RequestIdentity,
  afterSequence: number | undefined,
): Record<string, string> {
  const out: Record<string, string> = {
    org_id: identity.orgId,
    user_id: identity.userId,
  };
  if (afterSequence !== undefined) {
    out.after_sequence = String(afterSequence);
  }
  return out;
}

/**
 * Loose structural check on the SSE envelope. Matches the discriminator
 * fields from sub-PRD §4.1 — `sequence_no` (number), `event_type`
 * (string), `item` (object), `emitted_at` (string). Same pattern as
 * `isAgentActivityEntry` in homeApi.ts.
 */
function isInboxStreamEnvelope(value: unknown): value is InboxStreamEnvelope {
  if (value === null || typeof value !== "object") {
    return false;
  }
  const v = value as Record<string, unknown>;
  return (
    typeof v.sequence_no === "number" &&
    typeof v.event_type === "string" &&
    typeof v.emitted_at === "string" &&
    typeof v.item === "object" &&
    v.item !== null
  );
}

// The legacy onError signature was modelled after EventSource's bare
// Event — callers only react to "stream broken" and reconnect. Mirrors
// `streamHomeActivity` / `streamRunEvents`.
function streamErrorEvent(): Event {
  if (typeof Event === "function") {
    return new Event("error");
  }
  return { type: "error" } as unknown as Event;
}
