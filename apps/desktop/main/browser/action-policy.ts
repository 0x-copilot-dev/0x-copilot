// AC8 agentic browser — side-effect approval port (worker-side gate).
//
// The ACTION layer adds side-effecting tools (click / type / select / submit /
// download). Every side-effecting action MUST clear a per-action approval
// BEFORE the worker dispatches it (PRD §Action policy and approvals). Reads
// (navigate / snapshot / wait / screenshot / close) never touch this port.
//
// This mirrors the AC5 capability model: rather than hand-rolling a second
// broker or an in-worker approval UI, the worker depends on an INJECTED
// authority (`BrowserApprovalPort`) exactly as the AC5 filesystem broker
// authorizes an op against an injected `GrantProvider`. Electron main owns the
// concrete port and backs it with the existing HITL approval interrupt; the
// worker only asks "is this side effect approved?" and fails CLOSED when the
// answer is no or when no authority is wired.

import { actionRequiresApproval, type BrowserActionClass } from "./protocol";

export const BrowserApprovalDecision = {
  Approved: "approved",
  Denied: "denied",
} as const;
export type BrowserApprovalDecision =
  (typeof BrowserApprovalDecision)[keyof typeof BrowserApprovalDecision];

/**
 * A side-effecting action awaiting approval. Carries ONLY safe, redacted fields
 * — never typed secret text, raw selectors, cookies, or a full URL. The
 * `summary` is a human-readable description of the visible control + effect.
 */
export interface BrowserApprovalRequest {
  readonly requestId: string;
  readonly runId: string;
  readonly workspaceId: string;
  readonly approvalId: string;
  readonly toolName: string;
  readonly actionClass: BrowserActionClass;
  /** Origin the page is currently on (canonical), when known. */
  readonly currentOrigin?: string;
  /** Redacted control label / element role the action targets. */
  readonly targetLabel: string;
  /** Safe one-line description shown to the approver. */
  readonly summary: string;
}

/**
 * The authority the worker consults before dispatching a side-effecting action.
 * Owned by Electron main (backed by the existing capability-broker HITL flow);
 * injected into the session so it is unit-testable with a fake and so the
 * worker never contains approval UI or a second broker.
 */
export interface BrowserApprovalPort {
  requestApproval(
    request: BrowserApprovalRequest,
  ): Promise<BrowserApprovalDecision>;
}

/** True when a tool's classified action requires clearing an approval. */
export function toolRequiresApproval(actionClass: BrowserActionClass): boolean {
  return actionRequiresApproval(actionClass);
}
