import type {
  WorkspaceApprovalDecisionReceipt,
  WorkspaceApprovalDecisionRequest,
} from "@0x-copilot/api-types";

import {
  WorkspaceApprovalHostDecisionRequestSchema,
  WorkspaceApprovalHostDecisionResultSchema,
  type WorkspaceApprovalHostDecisionRequest,
  type WorkspaceApprovalHostDecisionResult,
  type WorkspaceApprovalStageSnapshot,
} from "./schemas";
import type {
  WorkspaceCommitPermit,
  WorkspaceRunFacts,
} from "./workspace-authority";

const SHA256_HEX = /^[a-f0-9]{64}$/u;
const OPAQUE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/u;
const OPAQUE_LEDGER_ID = /^[A-Za-z0-9][A-Za-z0-9._:·-]{0,255}$/u;
const PREPARED_REF = /^workspace-prepared:\/\/[A-Za-z0-9_-]{1,255}$/u;

/**
 * Main-only facade adapter. The renderer cannot select a URL, attach a
 * bearer, or obtain the raw receipt/error body.
 */
export interface WorkspaceApprovalFacadePort {
  recordDecision(input: {
    readonly snapshot: WorkspaceApprovalStageSnapshot;
    readonly decision: "approve" | "reject";
  }): Promise<unknown>;
}

/** Native confirmation is main-owned and receives no path-like display data. */
export interface WorkspaceApprovalNativeConfirmation {
  confirmApproval(): Promise<boolean>;
}

/**
 * Main-only C2 authority surface. The actual permit stays in the C2 authority
 * until its private apply call consumes it; this interface never crosses IPC.
 */
export interface WorkspaceApprovalPermitAuthorizer {
  authorizeWorkspaceCommit(
    facts: WorkspaceRunFacts,
    preparedRef: string,
    decision: {
      readonly stageId: string;
      readonly revision: number;
      readonly decisionLedgerId: string;
    },
  ): Promise<WorkspaceCommitPermit>;
}

/**
 * The only handoff an A5-private host-session adapter may use to obtain a
 * permit. It is deliberately absent from preload, renderer IPC, and the
 * public loopback broker API.
 */
export interface WorkspaceApprovalPermitHandoff {
  take(input: WorkspaceApprovalPermitTakeRequest): Promise<string | null>;
}

/** Main/A5-private request; `preparedRef` is never renderer-visible. */
export interface WorkspaceApprovalPermitTakeRequest {
  readonly facts: WorkspaceRunFacts;
  readonly preparedRef: string;
  readonly stageId: string;
  readonly revision: number;
  readonly decisionLedgerId: string;
  readonly changeSetDigest: string;
  readonly proposalDigest: string;
  readonly targetDigest: string;
}

interface StoredApproval {
  readonly runId: string;
  readonly receipt: WorkspaceApprovalDecisionReceipt;
}

/**
 * Stores a verified approval receipt until the A5-private prepare/apply path
 * has a `preparedRef`. C2 binds the final permit to that ref, so minting a raw
 * permit at renderer-decision time would either fabricate a ref or leak one.
 *
 * `take` consumes the receipt reservation before asking LocalWorkspaceAuthority
 * to mint its exact one-use permit. That authority then consumes the returned
 * token atomically at C2 commit. A retry can never re-arm a consumed receipt.
 */
export class WorkspaceApprovalPermitSource implements WorkspaceApprovalPermitHandoff {
  readonly #authorizer: WorkspaceApprovalPermitAuthorizer;
  readonly #pending = new Map<string, StoredApproval>();
  readonly #consumed = new Set<string>();

  constructor(authorizer: WorkspaceApprovalPermitAuthorizer) {
    this.#authorizer = authorizer;
  }

  recordVerifiedApproval(
    snapshot: WorkspaceApprovalStageSnapshot,
    receipt: WorkspaceApprovalDecisionReceipt,
  ): void {
    // Keep the reservation boundary defensive as well as the IPC host: a
    // future main-only caller cannot smuggle a fabricated typed object into
    // this source and cause a C2 mint.
    const verified = parseAndVerifyReceipt(receipt, snapshot, "approve");
    const key = approvalKey({
      stageId: verified.stage_id,
      revision: verified.revision,
      decisionLedgerId: verified.decision_ledger_id,
      changeSetDigest: verified.change_set_digest,
      proposalDigest: verified.proposal_digest,
      targetDigest: verified.target_digest,
    });
    if (this.#consumed.has(key) || this.#pending.has(key)) return;
    this.#pending.set(
      key,
      Object.freeze({ runId: snapshot.runId, receipt: verified }),
    );
  }

  async take(
    input: WorkspaceApprovalPermitTakeRequest,
  ): Promise<string | null> {
    if (!isSafePermitTake(input)) return null;
    const key = approvalKey(input);
    const stored = this.#pending.get(key);
    if (stored === undefined || stored.runId !== input.facts.runId) return null;

    // Consume before minting: parallel/replayed A5 deliveries cannot race into
    // a second permit even when the authority has not yet reached commit.
    this.#pending.delete(key);
    this.#consumed.add(key);
    try {
      const permit = await this.#authorizer.authorizeWorkspaceCommit(
        input.facts,
        input.preparedRef,
        {
          stageId: input.stageId,
          revision: input.revision,
          decisionLedgerId: input.decisionLedgerId,
        },
      );
      return mintedPermitMatches(permit, input) ? permit.permit : null;
    } catch {
      // The receipt remains auditable server-side, but an unavailable/revoked
      // C2 authority never becomes retryable through a stale approval.
      return null;
    }
  }
}

/** Path-free renderer entry point for the shared C3 stage UI. */
export interface WorkspaceApprovalHostPort {
  decide(
    input: WorkspaceApprovalHostDecisionRequest,
  ): Promise<WorkspaceApprovalHostDecisionResult>;
}

export class WorkspaceApprovalHostError extends Error {
  readonly code:
    | "workspace_approval_confirmation_unavailable"
    | "workspace_approval_facade_unavailable"
    | "workspace_approval_receipt_invalid";

  constructor(code: WorkspaceApprovalHostError["code"]) {
    super(code);
    this.name = "WorkspaceApprovalHostError";
    this.code = code;
  }
}

/**
 * Electron-main implementation of C3 D6. Approval is deliberately confirmed
 * natively for every approve action: the receipt contract contains no trusted
 * destructive-operation bit, and trusting a renderer-provided classification
 * would let a destructive stage bypass the stronger treatment.
 */
export class WorkspaceApprovalHost implements WorkspaceApprovalHostPort {
  readonly #facade: WorkspaceApprovalFacadePort;
  readonly #confirmation: WorkspaceApprovalNativeConfirmation;
  readonly #permits: WorkspaceApprovalPermitSource;

  constructor(deps: {
    readonly facade: WorkspaceApprovalFacadePort;
    readonly confirmation: WorkspaceApprovalNativeConfirmation;
    readonly permits: WorkspaceApprovalPermitSource;
  }) {
    this.#facade = deps.facade;
    this.#confirmation = deps.confirmation;
    this.#permits = deps.permits;
  }

  async decide(
    input: WorkspaceApprovalHostDecisionRequest,
  ): Promise<WorkspaceApprovalHostDecisionResult> {
    const request = WorkspaceApprovalHostDecisionRequestSchema.parse(input);
    if (request.decision === "approve") {
      let confirmed: boolean;
      try {
        confirmed = await this.#confirmation.confirmApproval();
      } catch {
        throw new WorkspaceApprovalHostError(
          "workspace_approval_confirmation_unavailable",
        );
      }
      if (!confirmed) {
        return WorkspaceApprovalHostDecisionResultSchema.parse({
          stageId: request.snapshot.stageId,
          revision: request.snapshot.revision,
          decision: request.decision,
          status: "cancelled",
        });
      }
    }

    let rawReceipt: unknown;
    try {
      rawReceipt = await this.#facade.recordDecision({
        snapshot: request.snapshot,
        decision: request.decision,
      });
    } catch {
      throw new WorkspaceApprovalHostError(
        "workspace_approval_facade_unavailable",
      );
    }
    const receipt = parseAndVerifyReceipt(
      rawReceipt,
      request.snapshot,
      request.decision,
    );
    if (receipt.decision === "approve") {
      this.#permits.recordVerifiedApproval(request.snapshot, receipt);
    }
    return WorkspaceApprovalHostDecisionResultSchema.parse({
      stageId: receipt.stage_id,
      revision: receipt.revision,
      decision: receipt.decision,
      status: receipt.status,
    });
  }
}

export class FacadeWorkspaceApprovalClient implements WorkspaceApprovalFacadePort {
  readonly #facadeBaseUrl: string;
  readonly #getBearer: () => Promise<string | null>;
  readonly #fetch: typeof fetch;

  constructor(deps: {
    readonly facadeBaseUrl: string;
    readonly getBearer: () => Promise<string | null>;
    readonly fetch?: typeof fetch;
  }) {
    this.#facadeBaseUrl = deps.facadeBaseUrl.replace(/\/+$/u, "");
    this.#getBearer = deps.getBearer;
    this.#fetch = deps.fetch ?? globalThis.fetch.bind(globalThis);
  }

  async recordDecision(input: {
    readonly snapshot: WorkspaceApprovalStageSnapshot;
    readonly decision: "approve" | "reject";
  }): Promise<unknown> {
    const bearer = await this.#getBearer();
    if (bearer === null) {
      throw new WorkspaceApprovalHostError(
        "workspace_approval_facade_unavailable",
      );
    }
    const body: WorkspaceApprovalDecisionRequest = {
      revision: input.snapshot.revision,
      decision: input.decision,
      proposal_digest: input.snapshot.proposalDigest,
      target_digest: input.snapshot.targetDigest,
    };
    const response = await this.#fetch(
      `${this.#facadeBaseUrl}/v1/agent/effect-stages/${encodeURIComponent(
        input.snapshot.stageId,
      )}/decisions?run_id=${encodeURIComponent(input.snapshot.runId)}`,
      {
        method: "POST",
        redirect: "error",
        headers: {
          accept: "application/json",
          authorization: `Bearer ${bearer}`,
          "content-type": "application/json",
        },
        body: JSON.stringify(body),
      },
    );
    if (!response.ok) {
      throw new WorkspaceApprovalHostError(
        "workspace_approval_facade_unavailable",
      );
    }
    try {
      return (await response.json()) as unknown;
    } catch {
      throw new WorkspaceApprovalHostError(
        "workspace_approval_receipt_invalid",
      );
    }
  }
}

function parseAndVerifyReceipt(
  raw: unknown,
  snapshot: WorkspaceApprovalStageSnapshot,
  decision: "approve" | "reject",
): WorkspaceApprovalDecisionReceipt {
  const parsed = ReceiptSchema.safeParse(raw);
  if (!parsed.success) {
    throw new WorkspaceApprovalHostError("workspace_approval_receipt_invalid");
  }
  const receipt = parsed.data;
  if (
    receipt.stage_id !== snapshot.stageId ||
    receipt.revision !== snapshot.revision ||
    receipt.proposal_digest !== snapshot.proposalDigest ||
    receipt.target_digest !== snapshot.targetDigest ||
    receipt.decision !== decision ||
    receipt.status !== (decision === "approve" ? "approved" : "rejected")
  ) {
    throw new WorkspaceApprovalHostError("workspace_approval_receipt_invalid");
  }
  return receipt;
}

const ReceiptSchema = {
  safeParse(value: unknown):
    | {
        readonly success: true;
        readonly data: WorkspaceApprovalDecisionReceipt;
      }
    | { readonly success: false } {
    if (typeof value !== "object" || value === null || Array.isArray(value)) {
      return { success: false };
    }
    const record = value as Record<string, unknown>;
    const expectedKeys = [
      "stage_id",
      "revision",
      "decision_ledger_id",
      "change_set_digest",
      "proposal_digest",
      "target_digest",
      "decision",
      "status",
    ];
    if (
      Object.keys(record).length !== expectedKeys.length ||
      !expectedKeys.every((key) => Object.hasOwn(record, key)) ||
      typeof record.stage_id !== "string" ||
      !OPAQUE_ID.test(record.stage_id) ||
      typeof record.revision !== "number" ||
      !Number.isSafeInteger(record.revision) ||
      record.revision < 1 ||
      typeof record.decision_ledger_id !== "string" ||
      !OPAQUE_LEDGER_ID.test(record.decision_ledger_id) ||
      typeof record.change_set_digest !== "string" ||
      !SHA256_HEX.test(record.change_set_digest) ||
      typeof record.proposal_digest !== "string" ||
      !SHA256_HEX.test(record.proposal_digest) ||
      typeof record.target_digest !== "string" ||
      !SHA256_HEX.test(record.target_digest) ||
      (record.decision !== "approve" && record.decision !== "reject") ||
      (record.status !== "approved" && record.status !== "rejected")
    ) {
      return { success: false };
    }
    return {
      success: true,
      data: {
        stage_id: record.stage_id,
        revision: record.revision,
        decision_ledger_id: record.decision_ledger_id,
        change_set_digest: record.change_set_digest,
        proposal_digest: record.proposal_digest,
        target_digest: record.target_digest,
        decision: record.decision,
        status: record.status,
      },
    };
  },
};

function approvalKey(input: {
  readonly stageId: string;
  readonly revision: number;
  readonly decisionLedgerId: string;
  readonly changeSetDigest: string;
  readonly proposalDigest: string;
  readonly targetDigest: string;
}): string {
  return JSON.stringify([
    input.stageId,
    input.revision,
    input.decisionLedgerId,
    input.changeSetDigest,
    input.proposalDigest,
    input.targetDigest,
  ]);
}

function isSafePermitTake(input: WorkspaceApprovalPermitTakeRequest): boolean {
  return (
    OPAQUE_ID.test(input.facts.runId) &&
    OPAQUE_ID.test(input.facts.userId) &&
    OPAQUE_ID.test(input.facts.deviceId) &&
    PREPARED_REF.test(input.preparedRef) &&
    OPAQUE_ID.test(input.stageId) &&
    Number.isSafeInteger(input.revision) &&
    input.revision > 0 &&
    OPAQUE_LEDGER_ID.test(input.decisionLedgerId) &&
    SHA256_HEX.test(input.proposalDigest) &&
    SHA256_HEX.test(input.targetDigest) &&
    SHA256_HEX.test(input.changeSetDigest)
  );
}

function mintedPermitMatches(
  permit: WorkspaceCommitPermit,
  input: WorkspaceApprovalPermitTakeRequest,
): boolean {
  return (
    permit.permit.startsWith("wcp_") &&
    permit.preparedRef === input.preparedRef &&
    permit.stageId === input.stageId &&
    permit.revision === input.revision &&
    permit.decisionLedgerId === input.decisionLedgerId &&
    permit.changeSetDigest === input.changeSetDigest &&
    permit.proposalDigest === input.proposalDigest &&
    permit.targetDigest === input.targetDigest &&
    permit.runId === input.facts.runId &&
    permit.userId === input.facts.userId &&
    permit.deviceId === input.facts.deviceId
  );
}
