// Typed wrappers for the Phase 12 Memory destination
// (sub-PRD `team-memory-cmdk-prd.md` §4.2).
//
// Endpoints:
//   * `fetchMemory(identity, opts)`              — GET    /v1/memory
//   * `fetchMemoryItem(identity, id)`            — GET    /v1/memory/{id}
//   * `createMemory(identity, body)`             — POST   /v1/memory
//   * `patchMemory(identity, id, body)`          — PATCH  /v1/memory/{id}
//   * `deleteMemory(identity, id)`               — DELETE /v1/memory/{id}
//   * `touchMemory(identity, id)`                — POST   /v1/memory/{id}/touch (internal)
//   * `fetchMemoryProposals(identity, opts)`     — GET    /v1/memory/proposals
//   * `acceptMemoryProposal(identity, id, body)` — POST   /v1/memory/proposals/{id}/accept
//   * `rejectMemoryProposal(identity, id)`       — POST   /v1/memory/proposals/{id}/reject
//   * `searchMemory(identity, q)`                — GET    /v1/memory/search?q=…
//   * `streamMemoryEvents({...})`                — SSE    /v1/memory/stream
//
// Mirrors the routinesApi / toolsApi shape — pure adapter functions,
// presentation lives elsewhere. SSE rides the canonical
// `getAppTransport().subscribeServerSentEvents` seam.

import type {
  AcceptMemoryProposalRequest,
  CreateMemoryRequest,
  MemoryItem,
  MemoryItemId,
  MemoryKind,
  MemoryListResponse,
  MemoryListSort,
  MemoryProposal,
  MemoryProposalListResponse,
  MemoryScope,
  MemorySearchResponse,
  MemoryStreamEnvelope,
  ProjectId,
  UpdateMemoryRequest,
} from "@0x-copilot/api-types";

import type { RequestIdentity } from "./config";
import { httpDelete, httpGet, httpPatchQuery, httpPostQuery } from "./http";
import { getAppTransport } from "./transport";

const SSE_EVENT_NAME = "memory_event";

// ===========================================================================
// LIST
// ===========================================================================

export interface FetchMemoryOptions {
  readonly scope?: MemoryScope;
  readonly kind?: MemoryKind;
  readonly tag?: string;
  readonly project_id?: ProjectId;
  readonly q?: string;
  readonly sort?: MemoryListSort;
  readonly after?: string;
  /** 1..200; server default 50. */
  readonly limit?: number;
}

export function fetchMemory(
  identity: RequestIdentity,
  options: FetchMemoryOptions = {},
): Promise<MemoryListResponse> {
  return httpGet<MemoryListResponse>(
    "/v1/memory",
    identity,
    encodeListParams(options),
  );
}

// ===========================================================================
// DETAIL
// ===========================================================================

export function fetchMemoryItem(
  identity: RequestIdentity,
  id: MemoryItemId,
): Promise<MemoryItem> {
  return httpGet<MemoryItem>(`/v1/memory/${encodeURIComponent(id)}`, identity);
}

// ===========================================================================
// MUTATIONS
// ===========================================================================

export function createMemory(
  identity: RequestIdentity,
  body: CreateMemoryRequest,
): Promise<MemoryItem> {
  return httpPostQuery<MemoryItem>("/v1/memory", body, identity);
}

export function patchMemory(
  identity: RequestIdentity,
  id: MemoryItemId,
  body: UpdateMemoryRequest,
): Promise<MemoryItem> {
  return httpPatchQuery<MemoryItem>(
    `/v1/memory/${encodeURIComponent(id)}`,
    body,
    identity,
  );
}

export function deleteMemory(
  identity: RequestIdentity,
  id: MemoryItemId,
): Promise<void> {
  return httpDelete(`/v1/memory/${encodeURIComponent(id)}`, identity);
}

/**
 * POST /v1/memory/{id}/touch — internal endpoint the runtime calls
 * after the retrieval path picks this row (sub-PRD §4.2). Re-exported
 * for completeness; UI callers should rarely fire this directly.
 */
export function touchMemory(
  identity: RequestIdentity,
  id: MemoryItemId,
): Promise<MemoryItem> {
  return httpPostQuery<MemoryItem>(
    `/v1/memory/${encodeURIComponent(id)}/touch`,
    {},
    identity,
  );
}

// ===========================================================================
// PROPOSALS
// ===========================================================================

export interface FetchMemoryProposalsOptions {
  readonly status?: "pending" | "accepted" | "rejected" | "snoozed";
  readonly after?: string;
  readonly limit?: number;
}

export function fetchMemoryProposals(
  identity: RequestIdentity,
  options: FetchMemoryProposalsOptions = {},
): Promise<MemoryProposalListResponse> {
  const params: Record<string, string | undefined> = {};
  if (options.status !== undefined) {
    params["filter[status]"] = options.status;
  }
  if (options.after !== undefined) params.after = options.after;
  if (options.limit !== undefined) params.limit = String(options.limit);
  return httpGet<MemoryProposalListResponse>(
    "/v1/memory/proposals",
    identity,
    params,
  );
}

export function acceptMemoryProposal(
  identity: RequestIdentity,
  id: string,
  body: AcceptMemoryProposalRequest = {},
): Promise<MemoryItem> {
  return httpPostQuery<MemoryItem>(
    `/v1/memory/proposals/${encodeURIComponent(id)}/accept`,
    body,
    identity,
  );
}

export function rejectMemoryProposal(
  identity: RequestIdentity,
  id: string,
): Promise<MemoryProposal> {
  return httpPostQuery<MemoryProposal>(
    `/v1/memory/proposals/${encodeURIComponent(id)}/reject`,
    {},
    identity,
  );
}

// ===========================================================================
// SEARCH
// ===========================================================================

export interface SearchMemoryOptions {
  readonly q: string;
  readonly limit?: number;
}

/**
 * GET /v1/memory/search — reuses Library's hybrid (BM25 + embedding)
 * search engine with `target_kind=memory` (sub-PRD §4.2 / §5.1 — no
 * parallel index table).
 */
export function searchMemory(
  identity: RequestIdentity,
  options: SearchMemoryOptions,
): Promise<MemorySearchResponse> {
  const params: Record<string, string | undefined> = { q: options.q };
  if (options.limit !== undefined) params.limit = String(options.limit);
  return httpGet<MemorySearchResponse>("/v1/memory/search", identity, params);
}

// ===========================================================================
// SSE — `GET /v1/memory/stream`
// ===========================================================================

export interface MemoryEventsStream {
  close(): void;
}

export function streamMemoryEvents({
  identity,
  afterSequence,
  onEvent,
  onError,
  onOpen,
}: {
  readonly identity: RequestIdentity;
  readonly afterSequence?: number;
  readonly onEvent: (envelope: MemoryStreamEnvelope) => void;
  readonly onError: (err: Event) => void;
  readonly onOpen?: () => void;
}): MemoryEventsStream {
  return getAppTransport().subscribeServerSentEvents({
    path: "/v1/memory/stream",
    query: streamQueryFor(identity, afterSequence),
    eventName: SSE_EVENT_NAME,
    onOpen,
    onError: (_err) => onError(streamErrorEvent()),
    onMessage: (data) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(data) as unknown;
      } catch {
        return;
      }
      if (isMemoryStreamEnvelope(parsed)) {
        onEvent(parsed);
      }
    },
  });
}

// ===========================================================================
// Helpers
// ===========================================================================

function encodeListParams(
  options: FetchMemoryOptions,
): Record<string, string | undefined> {
  const params: Record<string, string | undefined> = {};
  const { scope, kind, tag, project_id, q, sort, after, limit } = options;
  if (scope !== undefined) params["filter[scope]"] = scope;
  if (kind !== undefined) params["filter[kind]"] = kind;
  if (tag !== undefined) params["filter[tag]"] = tag;
  if (project_id !== undefined) params["filter[project_id]"] = project_id;
  if (q !== undefined && q.length > 0) params.q = q;
  if (sort !== undefined) params.sort = sort;
  if (after !== undefined) params.after = after;
  if (limit !== undefined) params.limit = String(limit);
  return params;
}

function streamQueryFor(
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

function isMemoryStreamEnvelope(value: unknown): value is MemoryStreamEnvelope {
  if (value === null || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.event_id === "string" &&
    typeof v.sequence_no === "number" &&
    typeof v.event_type === "string" &&
    typeof v.created_at === "string"
  );
}

function streamErrorEvent(): Event {
  if (typeof Event === "function") {
    return new Event("error");
  }
  return { type: "error" } as unknown as Event;
}
