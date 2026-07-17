// chat-surface UI approval shape (transitional adapter, kept post-Phase-1).
//
// Background: P1-A's audit found the canonical backend approval system uses
// `AssignedApproval` + `ApprovalStatus` ("pending"|"approved"|"rejected"|
// "forwarded"|"suggest_edit") in `@0x-copilot/api-types`. P1-B's
// chat-surface UI was designed against a narrower 4-state enum ("pending"|
// "accepted"|"rejected"|"edited") that maps more cleanly to the rail's
// pending-vs-resolved presentation.
//
// Rather than rename every chat-surface call site (~7 files, dozens of
// touches), we keep this **adapter shape** and bridge at the host boundary.
// `apps/frontend` maps a real `AssignedApproval` → this local `Approval`
// shape on the way into chat-surface; outbound approval decisions go
// through the existing `/v1/agent/approvals/{id}/decision` endpoint as
// the canonical `ApprovalDecision` ("approved"|"rejected"|"forwarded"|
// "suggest_edit") — see `services/ai-backend/src/runtime_api/schemas/
// approvals.py` and the wire types in `packages/api-types/src/index.ts`.
//
// Field mapping host-side (apps/frontend) when adapting AssignedApproval:
//   approval_id          → id
//   status="pending"     → state="pending"
//   status="approved"    → state="accepted"
//   status="rejected"    → state="rejected"
//   status="forwarded"   → state="rejected"  (collapse; rail UI treats both as closed)
//   status="suggest_edit" → state="edited"
//   approval_kind        → kind
//   forwarded_by_user_id → requester
//
// Wave 3+ may refactor chat-surface consumers to consume `AssignedApproval`
// directly and delete this adapter; tracking issue: TODO(Wave3).
//
// Brands (`ApprovalId`, `RunId`, etc.) come from api-types — single source
// of truth per cross-audit §2.1.

import type {
  ApprovalId,
  ConversationId,
  RunId,
  TenantId,
  UserId,
} from "@0x-copilot/api-types";

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
