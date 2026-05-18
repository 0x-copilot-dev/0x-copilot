// Memory destination (Phase 12) — wire contract.
//
// Source: docs/atlas-new-design/destinations/team-memory-cmdk-prd.md
//   §3.2 (Memory wire shapes), §4.2 (endpoints), §6.2 (ACL),
//   §7.2 (frontend surface — MemoryEditor + MemoryProposalToast),
//   §9 (auto-extraction pipeline → proposals).
//
// Memory items are what Atlas knows about the user / workspace — skills,
// facts, preferences. Persisted in `memory_items` (sub-PRD §5.2). The
// embedding pipeline reuses `library_embeddings` with `target_kind =
// "memory"` — no parallel vector table (sub-PRD §5.1).
//
// Single declaration site for: MemoryScope, MemoryKind, MemoryItem,
// MemoryListResponse, MemoryProposal, MemoryProposalDecisionStatus,
// the create / update / decision bodies, the SSE envelope, and the
// filter / sort axis tokens.
//
// Brand types live in ./brands.ts (canonical site); cross-destination
// refs live in ./refs.ts. Zero new `__brand:` declarations (cross-audit
// §2.1).

import type { MemoryItemId, ProjectId, TenantId, UserId } from "./brands";
import type { ItemRef } from "./refs";

// ---------------------------------------------------------------------------
// Enums
// ---------------------------------------------------------------------------

/**
 * Scoping of a memory row.
 *
 *   * `user`      — visible only to its owner.
 *   * `workspace` — visible to every tenant member.
 *
 * Scope can be flipped via `PATCH /v1/memory/{id}` (audit `scope_changed`).
 * Project scoping is layered on TOP via the optional `project_id` field
 * on `MemoryItem` (cross-audit §1.3 `is_project_member` rule).
 */
export type MemoryScope = "user" | "workspace";

/**
 * What the memory describes.
 *
 *   * `skill`      — capability the user/agent has ("speaks Python", "owns
 *     the Acme account").
 *   * `fact`       — durable factual context ("CTO is X", "Q1 launch is
 *     2026-03-15").
 *   * `preference` — stylistic / workflow preference ("signs off 'Best,
 *     Parth'", "always wants a TL;DR at the top of summaries").
 *
 * Used as the filter axis in `/memory?kind=…` and as the editor's
 * top-level tab strip (sub-PRD §7.2).
 */
export type MemoryKind = "skill" | "fact" | "preference";

/**
 * Decision lifecycle of a `MemoryProposal`.
 *
 *   * `pending`  — awaiting user decision; appears in `/memory/proposals`
 *     and as a toast (sub-PRD §9.2).
 *   * `accepted` — user clicked accept; a `MemoryItem` was created and
 *     this row is terminal.
 *   * `rejected` — user clicked reject; terminal.
 *   * `snoozed`  — user dismissed without deciding; re-appears at next
 *     proposal sweep (server-driven cadence).
 *
 * Terminal rows are hard-deleted 30 days past `decided_at` (sub-PRD §5.3).
 */
export type MemoryProposalDecisionStatus =
  | "pending"
  | "accepted"
  | "rejected"
  | "snoozed";

/**
 * Who created a memory row. Either a human user (manual editor) or an
 * agent run (auto-extraction accept). The `id` is the corresponding
 * branded id but at the wire we keep it a plain string to avoid a
 * second discriminated union here — consumers cast at trust boundary.
 *
 *   * `kind: "user"`  — `id` is `UserId`.
 *   * `kind: "agent"` — `id` is the agent slug (`AgentId`) that surfaced
 *     the proposal.
 */
export type MemoryCreatorKind = "user" | "agent";

export interface MemoryCreator {
  readonly kind: MemoryCreatorKind;
  /** Branded id (`UserId` or `AgentId`) — cast at trust boundary. */
  readonly id: string;
}

// ---------------------------------------------------------------------------
// Composite types
// ---------------------------------------------------------------------------

/**
 * Canonical Memory record. Returned by `GET /v1/memory` (list rows),
 * `GET /v1/memory/{id}` (detail), and the create / update endpoints.
 *
 * `body` is markdown (sub-PRD §7.2 — MemoryEditor). The embedding
 * derived from `title + body` is computed in the background after every
 * write; reads do NOT block on it.
 *
 * `last_used_at` is updated by the runtime via `POST /v1/memory/{id}/touch`
 * whenever the retrieval path picks the row (sub-PRD §4.2). Used for the
 * "last used X ago" UI hint and as the default sort axis.
 *
 * Soft-delete: `deleted_at` is server-side only (sub-PRD §5.3 — 90d
 * hard-delete grace); not exposed on the wire.
 */
export interface MemoryItem {
  readonly id: MemoryItemId;
  readonly tenant_id: TenantId;
  readonly scope: MemoryScope;
  readonly kind: MemoryKind;
  readonly title: string;
  /** Markdown body. */
  readonly body: string;
  readonly tags: ReadonlyArray<string>;
  readonly created_by: MemoryCreator;
  /** ISO8601; null when the row has never been retrieved by the runtime. */
  readonly last_used_at: string | null;
  readonly created_at: string;
  readonly updated_at: string;
  /**
   * Optional project scope (cross-audit §1.3 — `is_project_member` rule).
   * Null = workspace/user scoped only.
   */
  readonly project_id?: ProjectId | null;
}

/** Cursor-paginated memory listing. */
export interface MemoryListResponse {
  readonly items: ReadonlyArray<MemoryItem>;
  readonly next_cursor: string | null;
}

/**
 * Server-generated proposal from runtime auto-extraction (sub-PRD §9.1).
 *
 * The proposal is the input row of the accept/reject decision flow.
 * `source` is the chat / run that produced this proposal; the FE renders
 * a "from chat X" backlink via the canonical `<ItemLink>` resolver.
 *
 * On accept: `POST /v1/memory/proposals/{id}/accept` creates a
 * corresponding `MemoryItem` and transitions `status → "accepted"`.
 * On reject: `POST /v1/memory/proposals/{id}/reject` transitions to
 * `"rejected"` without creating a memory row.
 */
export interface MemoryProposal {
  /** Server-generated id; not a branded type (proposals are pre-memory). */
  readonly id: string;
  readonly tenant_id: TenantId;
  /** Owner of the proposal — the user the chat/run belonged to. */
  readonly user_id: UserId;
  readonly proposed_at: string;
  readonly proposed_kind: MemoryKind;
  readonly proposed_title: string;
  readonly proposed_body: string;
  /** The chat / run that produced this proposal. */
  readonly source: ItemRef;
  readonly status: MemoryProposalDecisionStatus;
  /** ISO8601; non-null on terminal statuses. */
  readonly decided_at: string | null;
}

/**
 * `GET /v1/memory/proposals` — pending auto-extraction queue. The server
 * pre-filters by status (default `pending`) and by the caller's user_id.
 */
export interface MemoryProposalListResponse {
  readonly proposals: ReadonlyArray<MemoryProposal>;
  readonly next_cursor: string | null;
}

// ---------------------------------------------------------------------------
// Request bodies
// ---------------------------------------------------------------------------

/** Body for `POST /v1/memory` — create memory row (§4.2). */
export interface CreateMemoryRequest {
  readonly scope: MemoryScope;
  readonly kind: MemoryKind;
  readonly title: string;
  readonly body: string;
  readonly tags?: ReadonlyArray<string>;
  readonly project_id?: ProjectId | null;
}

/**
 * Body for `PATCH /v1/memory/{id}` — partial update.
 *
 * Any field flip triggers a background re-embed. The `scope` flip writes
 * an audit `scope_changed` event (§6.2). Writes 404-not-403 when the
 * caller is not the owner / admin (cross-audit §1.3).
 */
export interface UpdateMemoryRequest {
  readonly scope?: MemoryScope;
  readonly kind?: MemoryKind;
  readonly title?: string;
  readonly body?: string;
  readonly tags?: ReadonlyArray<string>;
  readonly project_id?: ProjectId | null;
}

/**
 * Body for `POST /v1/memory/proposals/{id}/accept` — accept the proposal
 * and create the resulting `MemoryItem`.
 *
 * Optional overrides: the user may tweak the proposed title/body/tags
 * before accepting. When absent, the server uses the proposal fields
 * verbatim.
 */
export interface AcceptMemoryProposalRequest {
  readonly title_override?: string;
  readonly body_override?: string;
  readonly scope_override?: MemoryScope;
  readonly tags?: ReadonlyArray<string>;
  readonly project_id?: ProjectId | null;
}

// ---------------------------------------------------------------------------
// Memory search response (§4.2 — `GET /v1/memory/search`)
// ---------------------------------------------------------------------------

/**
 * One row of the hybrid (BM25 + embedding) memory search response.
 * Mirrors the Library hybrid-search engine (sub-PRD §4.2 — "reuses
 * Library's hybrid search engine; new index target_kind=memory").
 */
export interface MemorySearchHit {
  readonly item: MemoryItem;
  /** Server score; 0–1. Used for tiebreak / display. */
  readonly score: number;
  /** Highlighted body snippet (server-rendered; safe HTML allowed). */
  readonly snippet?: string;
}

export interface MemorySearchResponse {
  readonly hits: ReadonlyArray<MemorySearchHit>;
  readonly took_ms: number;
}

// ---------------------------------------------------------------------------
// Filter axis + sort tokens (cross-audit §1.5).
// ---------------------------------------------------------------------------

/** Allowed `filter[<axis>]` keys on `GET /v1/memory`. */
export type MemoryListFilterAxis =
  | "scope"
  | "kind"
  | "tag"
  | "project_id"
  | "q";

/** Allowed sort tokens — `filter[sort]=…`. */
export type MemoryListSort =
  | "last_used:desc"
  | "created_at:desc"
  | "created_at:asc"
  | "updated_at:desc";

// ---------------------------------------------------------------------------
// SSE — `GET /v1/memory/stream` (sub-PRD §4.2)
// ---------------------------------------------------------------------------

/**
 * Memory SSE event types.
 *
 *   * `memory.created`           — new row inserted.
 *   * `memory.updated`           — fields changed (also fires on scope
 *                                  flip; the FE inspects `item.scope`).
 *   * `memory.deleted`           — soft-delete; UI removes from list.
 *   * `memory.proposal_appended` — auto-extraction queued a new proposal;
 *                                  the FE lifts a toast (sub-PRD §9.2).
 *   * `memory.proposal_decided`  — proposal moved to a terminal status;
 *                                  used to keep `/memory/proposals` live.
 *   * `heartbeat`                — SSE keepalive comment frame.
 */
export type MemoryStreamEventType =
  | "memory.created"
  | "memory.updated"
  | "memory.deleted"
  | "memory.proposal_appended"
  | "memory.proposal_decided"
  | "heartbeat";

/**
 * SSE envelope mirroring the inbox / home / connectors streams. Monotonic
 * `sequence_no` per `(tenant_id, user_id)` channel; reconnect via
 * `Last-Event-ID`.
 *
 * Exactly one of `item` / `proposal` / `deleted_id` is present on a
 * non-heartbeat frame, keyed by `event_type`.
 */
export interface MemoryStreamEnvelope {
  readonly event_id: string;
  readonly sequence_no: number;
  readonly event_type: MemoryStreamEventType;
  /** Present on `memory.created` / `memory.updated`. */
  readonly item?: MemoryItem;
  /** Present on `memory.proposal_appended` / `memory.proposal_decided`. */
  readonly proposal?: MemoryProposal;
  /** Present on `memory.deleted` (item already removed). */
  readonly deleted_id?: MemoryItemId;
  readonly created_at: string;
}
