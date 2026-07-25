// Workspace stage host boundary (PRD-C3 D4/D6/D8).
//
// The shared cockpit can review a canonical workspace effect, but it must not
// receive a filesystem capability.  In particular this contract intentionally
// contains no physical path, grant, permit, prepared reference, content
// reference, or generic "execute" operation.  Desktop delegates the exact
// approval snapshot to Electron main; web records the same canonical decision
// through the facade and can offer an artifact download fallback.

/**
 * The complete identity of the exact bytes + target a reviewer may decide on.
 * All values are opaque to the shared UI except the positive revision number.
 */
export interface WorkspaceApprovalSnapshot {
  readonly runId: string;
  readonly stageId: string;
  readonly revision: number;
  readonly proposalDigest: string;
  readonly targetDigest: string;
}

export type WorkspaceApprovalDecision = "approve" | "reject";

/** Renderer-safe result from the Electron-main approval bridge. */
export interface WorkspaceApprovalHostDecisionResult {
  readonly stageId: string;
  readonly revision: number;
  readonly decision: WorkspaceApprovalDecision;
  /** `cancelled` means native confirmation was dismissed; no decision landed. */
  readonly status: "approved" | "rejected" | "cancelled";
}

/**
 * Narrow desktop bridge. The renderer adapter invokes the existing allowlisted
 * main IPC channel; Electron main owns native confirmation, facade receipt
 * verification, and its private permit handoff.
 */
export interface WorkspaceApprovalHostPort {
  decide(input: {
    readonly snapshot: WorkspaceApprovalSnapshot;
    readonly decision: WorkspaceApprovalDecision;
  }): Promise<WorkspaceApprovalHostDecisionResult>;
}

/**
 * Host discriminator consumed by `RunDestination`.
 *
 * The web arm deliberately has no approval capability: it uses the normal
 * facade decision route in the cockpit and cannot claim a local workspace
 * write. The desktop arm is the only one allowed to use the native bridge.
 */
export type WorkspaceStageHost =
  | {
      readonly kind: "desktop";
      readonly approvalPort: WorkspaceApprovalHostPort;
    }
  | {
      readonly kind: "web";
    };
