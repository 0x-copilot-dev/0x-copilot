// @vitest-environment node
import { describe, expect, it, vi } from "vitest";

import type { WorkspaceApprovalDecisionReceipt } from "@0x-copilot/api-types";

import type { WorkspaceApprovalStageSnapshot } from "./schemas";
import {
  FacadeWorkspaceApprovalClient,
  WorkspaceApprovalHost,
  WorkspaceApprovalPermitSource,
  type WorkspaceApprovalPermitAuthorizer,
} from "./workspace-approval";
import type {
  WorkspaceCommitPermit,
  WorkspaceRunFacts,
} from "./workspace-authority";

const SNAPSHOT: WorkspaceApprovalStageSnapshot = Object.freeze({
  runId: "run_c3_001",
  stageId: "stage_c3_001",
  revision: 7,
  proposalDigest: "a".repeat(64),
  targetDigest: "b".repeat(64),
});

const FACTS: WorkspaceRunFacts = Object.freeze({
  runId: SNAPSHOT.runId,
  userId: "user_c3_001",
  deviceId: "device_c3_001",
});

const TAKE_INPUT = Object.freeze({
  facts: FACTS,
  preparedRef: "workspace-prepared://prepared-c3-001",
  stageId: SNAPSHOT.stageId,
  revision: SNAPSHOT.revision,
  decisionLedgerId: "ledger_c3_001",
  proposalDigest: SNAPSHOT.proposalDigest,
  targetDigest: SNAPSHOT.targetDigest,
});

function receipt(
  overrides: Partial<WorkspaceApprovalDecisionReceipt> = {},
): WorkspaceApprovalDecisionReceipt {
  return {
    stage_id: SNAPSHOT.stageId,
    revision: SNAPSHOT.revision,
    decision_ledger_id: TAKE_INPUT.decisionLedgerId,
    proposal_digest: SNAPSHOT.proposalDigest,
    target_digest: SNAPSHOT.targetDigest,
    decision: "approve",
    status: "approved",
    ...overrides,
  };
}

function permit(): WorkspaceCommitPermit {
  return {
    permit: "wcp_main_private_one_use",
    commitId: "wcc_c3_001",
    preparedRef: TAKE_INPUT.preparedRef,
    stageId: SNAPSHOT.stageId,
    revision: SNAPSHOT.revision,
    decisionLedgerId: TAKE_INPUT.decisionLedgerId,
    changeSetDigest: "c".repeat(64),
    targetDigest: SNAPSHOT.targetDigest,
    proposalDigest: SNAPSHOT.proposalDigest,
    runId: FACTS.runId,
    userId: FACTS.userId,
    deviceId: FACTS.deviceId,
    expiresAt: 1_900_000_000_000,
    allowedOperations: 1,
    allowedBytes: 128,
  };
}

function hostHarness(
  options: {
    readonly facadeResult?: unknown;
    readonly facadeError?: Error;
    readonly confirmed?: boolean;
    readonly authorize?: WorkspaceApprovalPermitAuthorizer["authorizeWorkspaceCommit"];
  } = {},
) {
  const calls: string[] = [];
  const recordDecision = vi.fn(async () => {
    calls.push("facade");
    if (options.facadeError !== undefined) throw options.facadeError;
    return options.facadeResult ?? receipt();
  });
  const confirmApproval = vi.fn(async () => {
    calls.push("confirm");
    return options.confirmed ?? true;
  });
  const authorizeWorkspaceCommit = vi.fn(
    options.authorize ?? (async () => permit()),
  );
  const authorizer: WorkspaceApprovalPermitAuthorizer = {
    authorizeWorkspaceCommit,
  };
  const permits = new WorkspaceApprovalPermitSource(authorizer);
  const host = new WorkspaceApprovalHost({
    facade: { recordDecision },
    confirmation: { confirmApproval },
    permits,
  });
  return {
    host,
    permits,
    calls,
    recordDecision,
    confirmApproval,
    authorizeWorkspaceCommit,
  };
}

describe("WorkspaceApprovalHost", () => {
  it("confirms natively before recording the exact approval and exposes no private data", async () => {
    const harness = hostHarness();

    const result = await harness.host.decide({
      snapshot: SNAPSHOT,
      decision: "approve",
    });

    expect(harness.calls).toEqual(["confirm", "facade"]);
    expect(result).toEqual({
      stageId: SNAPSHOT.stageId,
      revision: SNAPSHOT.revision,
      decision: "approve",
      status: "approved",
    });
    expect(Object.keys(result).sort()).toEqual([
      "decision",
      "revision",
      "stageId",
      "status",
    ]);
    expect(JSON.stringify(result)).not.toMatch(
      /wcp_|workspace-prepared|\/Users|preparedRef|permit/iu,
    );

    expect(await harness.permits.take(TAKE_INPUT)).toBe(
      "wcp_main_private_one_use",
    );
    // A duplicate/retry cannot reissue a permit after the reservation was
    // consumed, even though LocalWorkspaceAuthority has not exposed it.
    expect(await harness.permits.take(TAKE_INPUT)).toBeNull();
    expect(harness.authorizeWorkspaceCommit).toHaveBeenCalledTimes(1);
    expect(harness.authorizeWorkspaceCommit).toHaveBeenCalledWith(
      FACTS,
      TAKE_INPUT.preparedRef,
      {
        stageId: SNAPSHOT.stageId,
        revision: SNAPSHOT.revision,
        decisionLedgerId: TAKE_INPUT.decisionLedgerId,
      },
    );
  });

  it("does not mint a second permit when the facade returns an idempotent duplicate receipt", async () => {
    const harness = hostHarness();
    await harness.host.decide({ snapshot: SNAPSHOT, decision: "approve" });
    await harness.host.decide({ snapshot: SNAPSHOT, decision: "approve" });

    expect(await harness.permits.take(TAKE_INPUT)).toBe(
      "wcp_main_private_one_use",
    );
    expect(await harness.permits.take(TAKE_INPUT)).toBeNull();
    expect(harness.authorizeWorkspaceCommit).toHaveBeenCalledTimes(1);
  });

  it("returns cancelled before the facade call or permit reservation when native confirmation declines", async () => {
    const harness = hostHarness({ confirmed: false });

    await expect(
      harness.host.decide({ snapshot: SNAPSHOT, decision: "approve" }),
    ).resolves.toEqual({
      stageId: SNAPSHOT.stageId,
      revision: SNAPSHOT.revision,
      decision: "approve",
      status: "cancelled",
    });
    expect(harness.calls).toEqual(["confirm"]);
    expect(await harness.permits.take(TAKE_INPUT)).toBeNull();
    expect(harness.authorizeWorkspaceCommit).not.toHaveBeenCalled();
  });

  it("does not confirm or reserve a rejected decision", async () => {
    const harness = hostHarness({
      facadeResult: receipt({ decision: "reject", status: "rejected" }),
    });

    await expect(
      harness.host.decide({ snapshot: SNAPSHOT, decision: "reject" }),
    ).resolves.toEqual({
      stageId: SNAPSHOT.stageId,
      revision: SNAPSHOT.revision,
      decision: "reject",
      status: "rejected",
    });
    expect(harness.calls).toEqual(["facade"]);
    expect(await harness.permits.take(TAKE_INPUT)).toBeNull();
    expect(harness.authorizeWorkspaceCommit).not.toHaveBeenCalled();
  });

  it("rejects every tampered, missing, mismatched, or leaky receipt before a permit can exist", async () => {
    const badReceipts: readonly unknown[] = [
      receipt({ revision: SNAPSHOT.revision + 1 }),
      receipt({ proposal_digest: "c".repeat(64) }),
      receipt({ target_digest: "d".repeat(64) }),
      receipt({ decision_ledger_id: "" }),
      {
        ...receipt(),
        prepared_ref: "workspace-prepared://must-not-be-public",
      },
      {
        stage_id: SNAPSHOT.stageId,
        revision: SNAPSHOT.revision,
        decision_ledger_id: TAKE_INPUT.decisionLedgerId,
        proposal_digest: SNAPSHOT.proposalDigest,
        decision: "approve",
        status: "approved",
      },
    ];

    for (const badReceipt of badReceipts) {
      const harness = hostHarness({ facadeResult: badReceipt });
      await expect(
        harness.host.decide({ snapshot: SNAPSHOT, decision: "approve" }),
      ).rejects.toMatchObject({
        code: "workspace_approval_receipt_invalid",
      });
      expect(await harness.permits.take(TAKE_INPUT)).toBeNull();
      expect(harness.authorizeWorkspaceCommit).not.toHaveBeenCalled();
    }
  });

  it("fails closed on a foreign/unknown facade response without creating a permit", async () => {
    const harness = hostHarness({
      facadeError: new Error("404 /effect-stages/foreign-stage"),
    });

    await expect(
      harness.host.decide({ snapshot: SNAPSHOT, decision: "approve" }),
    ).rejects.toMatchObject({
      code: "workspace_approval_facade_unavailable",
    });
    expect(await harness.permits.take(TAKE_INPUT)).toBeNull();
    expect(harness.authorizeWorkspaceCommit).not.toHaveBeenCalled();
  });

  it("consumes a verified reservation when C2 denies a missing or revoked grant", async () => {
    const harness = hostHarness({
      authorize: async () => {
        throw new Error("workspace_capability_denied");
      },
    });
    await harness.host.decide({ snapshot: SNAPSHOT, decision: "approve" });

    expect(await harness.permits.take(TAKE_INPUT)).toBeNull();
    expect(await harness.permits.take(TAKE_INPUT)).toBeNull();
    expect(harness.authorizeWorkspaceCommit).toHaveBeenCalledTimes(1);
  });

  it("does not hand a malformed C2 mint to A5", async () => {
    const harness = hostHarness({
      authorize: async () => ({ ...permit(), targetDigest: "c".repeat(64) }),
    });
    await harness.host.decide({ snapshot: SNAPSHOT, decision: "approve" });

    expect(await harness.permits.take(TAKE_INPUT)).toBeNull();
    expect(await harness.permits.take(TAKE_INPUT)).toBeNull();
    expect(harness.authorizeWorkspaceCommit).toHaveBeenCalledTimes(1);
  });

  it("rejects a renderer snapshot that tries to include a physical root before confirmation", async () => {
    const harness = hostHarness();
    await expect(
      harness.host.decide({
        snapshot: { ...SNAPSHOT, root: "/Users/alice/private" },
        decision: "approve",
      } as unknown as Parameters<typeof harness.host.decide>[0]),
    ).rejects.toThrow();
    expect(harness.confirmApproval).not.toHaveBeenCalled();
    expect(harness.recordDecision).not.toHaveBeenCalled();
    expect(harness.authorizeWorkspaceCommit).not.toHaveBeenCalled();
  });
});

describe("FacadeWorkspaceApprovalClient", () => {
  it("uses only the facade's digest-pinned decision route and request wire", async () => {
    const fetchImpl = vi.fn(
      async () =>
        new Response(JSON.stringify(receipt()), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
    );
    const client = new FacadeWorkspaceApprovalClient({
      facadeBaseUrl: "https://facade.example/",
      getBearer: async () => "desktop-main-bearer",
      fetch: fetchImpl as unknown as typeof fetch,
    });

    await expect(
      client.recordDecision({ snapshot: SNAPSHOT, decision: "approve" }),
    ).resolves.toEqual(receipt());
    expect(fetchImpl).toHaveBeenCalledWith(
      "https://facade.example/v1/agent/effect-stages/stage_c3_001/decisions?run_id=run_c3_001",
      expect.objectContaining({
        method: "POST",
        redirect: "error",
        headers: expect.objectContaining({
          authorization: "Bearer desktop-main-bearer",
        }),
        body: JSON.stringify({
          revision: SNAPSHOT.revision,
          decision: "approve",
          proposal_digest: SNAPSHOT.proposalDigest,
          target_digest: SNAPSHOT.targetDigest,
        }),
      }),
    );
    expect(JSON.stringify(fetchImpl.mock.calls)).not.toMatch(
      /workspace-prepared|wcp_|\/Users|permit/iu,
    );
  });
});
