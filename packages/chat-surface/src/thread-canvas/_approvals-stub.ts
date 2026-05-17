// LOCAL STUB — replaced at merge by P1-A's `@enterprise-search/api-types/approvals`.
//
// Phase 1 P1-B runs in parallel with P1-A (backend approvals) and P1-C
// (frontend migration). P1-A owns `packages/api-types/src/approvals.ts`
// where the canonical `Approval` interface lives. While P1-A is in flight
// this stub matches the sub-PRD (`docs/atlas-new-design/destinations/
// chats-canvas-prd.md` §3.6 + §6 audit shape) field-for-field. When P1-A
// lands the orchestrator deletes this file and rewires every import site
// to `import { Approval, ApprovalState } from "@enterprise-search/api-types"`.
//
// Brands (`ApprovalId`, `RunId`, …) come from api-types already — only
// the `Approval` interface is stubbed. The shape is intentionally a
// superset of the sub-PRD: any extra optional field P1-A introduces is
// trivially compatible.

import type {
  ApprovalId,
  ConversationId,
  RunId,
  TenantId,
  UserId,
} from "@enterprise-search/api-types";

/**
 * Approval lifecycle state.
 *
 * - `pending`  — approval_requested emitted; awaiting human decision.
 * - `accepted` — human accepted; runtime resumed.
 * - `rejected` — human rejected; runtime resumed with a "no" answer.
 * - `edited`   — human suggested an edit; runtime resumed with the
 *   edited payload (sub-PRD §3.6 — future-tense for Phase 1.5; included
 *   here so consumers exhaustively switch).
 */
export type ApprovalState = "pending" | "accepted" | "rejected" | "edited";

/**
 * The canonical Approval shape. Field-for-field with chats-canvas-prd
 * §3.6 (`TcInlineDiffProps`) + §6 (audit row). The Approvals right-rail
 * tab renders one row per `Approval` whose `state === "pending"`; resolved
 * approvals stay in the projector but are hidden from the live tab.
 */
export interface Approval {
  /** Branded server-issued id. */
  readonly id: ApprovalId;
  /** Run the approval was requested from. */
  readonly run_id: RunId;
  /** Conversation the run belongs to. */
  readonly conversation_id: ConversationId;
  /** Tenant scope — enforced server-side, mirrored here for client filtering. */
  readonly tenant_id: TenantId;
  /** Whoever (subagent / human) requested the approval. */
  readonly requester: UserId;
  /**
   * Designated approver. `null` falls back to the thread owner per
   * sub-PRD §7 (owner-only writes in Phase 1).
   */
  readonly target_user_id: UserId | null;
  /**
   * Approval kind — e.g. `"surface_diff"`, `"mcp_auth"`, `"tool_call"`.
   * String-typed so the projector can route to the right inline-card
   * renderer without compile-time coupling to every kind.
   */
  readonly kind: string;
  /** Approval payload — opaque to chat-surface; the inline diff card unpacks it. */
  readonly payload: unknown;
  /** Optional diff for surface_diff-shaped approvals. */
  readonly diff?: unknown;
  /** Current lifecycle state. */
  readonly state: ApprovalState;
  /** ISO timestamp of approval_requested. */
  readonly created_at: string;
  /** ISO timestamp of approval_resolved. Absent while pending. */
  readonly resolved_at?: string;
  /** Resolution details — present only when state !== "pending". */
  readonly resolution?: {
    readonly resolver: UserId;
    readonly action: "accept" | "reject" | "suggest_edit";
    readonly note?: string;
    readonly edited_payload?: unknown;
  };
  /**
   * Stream context — `(conversation_id, run_id, sequence_no?)` for jumping
   * the surface viewport to the moment the approval was requested.
   */
  readonly context?: {
    readonly conversation_id: ConversationId;
    readonly run_id: RunId;
    readonly sequence_no?: number;
  };
}
