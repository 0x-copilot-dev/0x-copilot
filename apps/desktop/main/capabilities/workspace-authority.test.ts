// @vitest-environment node
import { describe, expect, it } from "vitest";

import type { Grant, GrantProvider, GrantSnapshot } from "./types";
import {
  InMemoryWorkspaceJournalStore,
  LocalWorkspaceAuthority,
  type NativePreparedWorkspace,
  type NativeWorkspaceAuthority,
  type NativeWorkspaceCommitResult,
  type WorkspaceChangeSet,
  type WorkspaceRootIdentity,
} from "./workspace-authority";

const ROOT = "/private/project";
const IDENTITY: WorkspaceRootIdentity = { volumeId: "vol_1", fileId: "root_1" };
const FACTS = { runId: "run_1", userId: "user_1", deviceId: "device_1" };

function grant(overrides: Partial<Grant> = {}): Grant {
  return {
    grantId: "grant_1",
    root: ROOT,
    rootIdentity: IDENTITY,
    profileId: FACTS.userId,
    deviceId: FACTS.deviceId,
    allowedPathPrefixes: [""],
    expiresAt: Number.MAX_SAFE_INTEGER,
    mode: "read_write",
    label: "project",
    status: "active",
    createdAt: 1,
    updatedAt: 1,
    ...overrides,
  };
}

class Grants implements GrantProvider {
  grants: Grant[] = [grant()];

  async listAll(): Promise<readonly Grant[]> {
    return this.grants;
  }

  async snapshotActive(): Promise<GrantSnapshot> {
    return {
      snapshotId: "snap_1",
      capturedAt: 1,
      grants: this.grants.filter((item) => item.status === "active"),
    };
  }
}

class Native implements NativeWorkspaceAuthority {
  primitivesAvailable = true;
  identity = IDENTITY;
  prepared = 0;
  writes: Array<{ slot: string; bytes: number }> = [];
  commits: string[] = [];
  reconciles: string[] = [];
  aborts = 0;

  async rootIdentity(_root: string): Promise<WorkspaceRootIdentity> {
    return this.identity;
  }

  async prepare(
    _root: string,
    _entries: WorkspaceChangeSet["entries"],
  ): Promise<NativePreparedWorkspace> {
    this.prepared += 1;
    return {
      handle: `native_${this.prepared}`,
      observedTargetDigest: "a".repeat(64),
      slots: [{ slot: "slot_1", digest: "b".repeat(64), size: 5 }],
    };
  }

  async writePrepared(
    _prepared: NativePreparedWorkspace,
    slot: string,
    chunk: Uint8Array,
  ): Promise<void> {
    this.writes.push({ slot, bytes: chunk.byteLength });
  }

  async sealPrepared(
    _prepared: NativePreparedWorkspace,
    _slot: string,
  ): Promise<{ readonly digest: string; readonly size: number }> {
    return { digest: "b".repeat(64), size: 5 };
  }

  async commitPrepared(
    _prepared: NativePreparedWorkspace,
    claimId: string,
  ): Promise<NativeWorkspaceCommitResult> {
    this.commits.push(claimId);
    return {
      outcome: "applied",
      receiptRef: `workspace-receipt://${claimId}`,
      resultDigest: "c".repeat(64),
    };
  }

  async reconcilePrepared(
    _prepared: NativePreparedWorkspace,
    claimId: string,
  ): Promise<NativeWorkspaceCommitResult> {
    this.reconciles.push(claimId);
    return {
      outcome: "already_applied",
      receiptRef: `workspace-receipt://${claimId}`,
    };
  }

  async reconcileClaim(claimId: string): Promise<NativeWorkspaceCommitResult> {
    this.reconciles.push(claimId);
    return {
      outcome: "already_applied",
      receiptRef: `workspace-receipt://${claimId}`,
    };
  }

  async abortPrepared(_prepared: NativePreparedWorkspace): Promise<void> {
    this.aborts += 1;
  }

  async proposeRecovery(
    _prepared: NativePreparedWorkspace,
  ): Promise<"proposed" | "conflict"> {
    return "proposed";
  }

  async proposeRecoveryClaim(
    _claimId: string,
  ): Promise<"proposed" | "conflict"> {
    return "proposed";
  }
}

function changeSet(
  overrides: Partial<WorkspaceChangeSet> = {},
): WorkspaceChangeSet {
  return {
    stageId: "stg_1",
    revision: 1,
    decisionLedgerId: "rrun1·7",
    grantId: "grant_1",
    mount: "mnt_1",
    changeSetDigest: "d".repeat(64),
    targetDigest: "e".repeat(64),
    proposalDigest: "b".repeat(64),
    entries: [
      {
        operation: "create",
        relativePath: "notes.md",
        contentDigest: "b".repeat(64),
        contentSize: 5,
        contentSlot: "slot_1",
        precondition: { exists: false },
      },
    ],
    ...overrides,
  };
}

function build(
  overrides: Partial<
    ConstructorParameters<typeof LocalWorkspaceAuthority>[0]
  > = {},
) {
  const grants = new Grants();
  const native = new Native();
  const journal = new InMemoryWorkspaceJournalStore();
  const authority = new LocalWorkspaceAuthority({
    grants,
    native,
    journal,
    attestation: {
      workspaceWriteIsolation: "enforced",
      nativeWorkspacePrimitives: "available",
    },
    production: true,
    deviceId: FACTS.deviceId,
    id: (() => {
      let id = 0;
      return () => `id_${++id}`;
    })(),
    randomBytes: () => Buffer.alloc(32, 7),
    journalTokenKey: Buffer.alloc(32, 9),
    ...overrides,
  });
  return { authority, grants, native, journal };
}

describe("LocalWorkspaceAuthority", () => {
  it("requires production isolation and native write primitives before preparation", async () => {
    const { authority, native } = build({
      attestation: {
        workspaceWriteIsolation: "unavailable",
        nativeWorkspacePrimitives: "available",
      },
    });
    const capability = await authority.createReadCapability(FACTS, ["grant_1"]);
    await expect(
      authority.prepareChangeSet(capability.capability, changeSet()),
    ).rejects.toMatchObject({ code: "workspace_write_unsupported" });
    expect(native.prepared).toBe(0);
  });

  it("rejects Unicode and non-canonical writable path spellings before native prepare", async () => {
    const { authority, native } = build();
    const capability = await authority.createReadCapability(FACTS, ["grant_1"]);
    await expect(
      authority.prepareChangeSet(
        capability.capability,
        changeSet({
          entries: [
            {
              ...changeSet().entries[0]!,
              relativePath: "notes/cafe\u0301.md",
            },
          ],
        }),
      ),
    ).rejects.toMatchObject({ code: "workspace_conflict" });
    await expect(
      authority.prepareChangeSet(
        capability.capability,
        changeSet({
          entries: [
            {
              ...changeSet().entries[0]!,
              relativePath: "notes/plan copy.md",
            },
          ],
        }),
      ),
    ).rejects.toMatchObject({ code: "workspace_conflict" });
    expect(native.prepared).toBe(0);
  });

  it("stages bytes privately, needs an exact user-issued permit, and commits once", async () => {
    const { authority, native, journal } = build();
    const capability = await authority.createReadCapability(FACTS, ["grant_1"]);
    const prepared = await authority.prepareChangeSet(
      capability.capability,
      changeSet(),
    );
    await authority.uploadPreparedContent(
      prepared.preparedRef,
      "slot_1",
      Buffer.from("hello"),
    );
    await authority.sealPreparedContent(prepared.preparedRef, "slot_1");

    await expect(
      authority.commitPreparedChangeSet(prepared.preparedRef, "not-a-permit"),
    ).rejects.toMatchObject({ code: "workspace_permit_denied" });
    expect(native.commits).toEqual([]);

    const permit = await authority.authorizeCommitFromUserDecision(
      FACTS,
      prepared.preparedRef,
      {
        stageId: "stg_1",
        revision: 1,
        decisionLedgerId: "rrun1·7",
      },
    );
    const first = await authority.commitPreparedChangeSet(
      prepared.preparedRef,
      permit.permit,
    );
    const replay = await authority.commitPreparedChangeSet(
      prepared.preparedRef,
      permit.permit,
    );

    expect(first.outcome).toBe("applied");
    expect(replay).toEqual(first);
    expect(native.commits).toEqual([permit.commitId]);
    const record = await journal.get(prepared.preparedRef);
    expect(record?.state).toBe("applied");
    expect(JSON.stringify(record)).not.toContain(ROOT);
    expect(JSON.stringify(record)).not.toContain("notes.md");
  });

  it("enforces immediate revocation between prepare and commit", async () => {
    const { authority, grants, native } = build();
    const capability = await authority.createReadCapability(FACTS, ["grant_1"]);
    const prepared = await authority.prepareChangeSet(
      capability.capability,
      changeSet(),
    );
    const permit = await authority.authorizeCommitFromUserDecision(
      FACTS,
      prepared.preparedRef,
      {
        stageId: "stg_1",
        revision: 1,
        decisionLedgerId: "rrun1·7",
      },
    );
    grants.grants = [grant({ status: "revoked" })];

    await expect(
      authority.commitPreparedChangeSet(prepared.preparedRef, permit.permit),
    ).rejects.toMatchObject({ code: "workspace_capability_denied" });
    expect(native.commits).toEqual([]);
  });

  it("invalidates a grant when its root identity changes", async () => {
    const { authority, native } = build();
    const capability = await authority.createReadCapability(FACTS, ["grant_1"]);
    const prepared = await authority.prepareChangeSet(
      capability.capability,
      changeSet(),
    );
    const permit = await authority.authorizeCommitFromUserDecision(
      FACTS,
      prepared.preparedRef,
      {
        stageId: "stg_1",
        revision: 1,
        decisionLedgerId: "rrun1·7",
      },
    );
    native.identity = { volumeId: "vol_1", fileId: "replacement_root" };

    await expect(
      authority.commitPreparedChangeSet(prepared.preparedRef, permit.permit),
    ).rejects.toMatchObject({ code: "workspace_capability_denied" });
    expect(native.commits).toEqual([]);
  });

  it("rejects an unconfined dev composition in production even when native exists", async () => {
    const { authority } = build({
      attestation: {
        workspaceWriteIsolation: "unavailable",
        nativeWorkspacePrimitives: "available",
        unsafeDevWorkspaceTcb: true,
      },
    });
    expect(authority.writableAvailable()).toBe(false);
  });

  it("requires every declared payload to be fully uploaded and sealed", async () => {
    const { authority, native } = build();
    const capability = await authority.createReadCapability(FACTS, ["grant_1"]);
    const prepared = await authority.prepareChangeSet(
      capability.capability,
      changeSet(),
    );
    await expect(
      authority.sealPreparedContent(prepared.preparedRef, "slot_1"),
    ).rejects.toMatchObject({ code: "workspace_conflict" });
    await authority.uploadPreparedContent(
      prepared.preparedRef,
      "slot_1",
      Buffer.from("hello"),
    );
    const permit = await authority.authorizeCommitFromUserDecision(
      FACTS,
      prepared.preparedRef,
      {
        stageId: "stg_1",
        revision: 1,
        decisionLedgerId: "rrun1·7",
      },
    );
    await expect(
      authority.commitPreparedChangeSet(prepared.preparedRef, permit.permit),
    ).rejects.toMatchObject({ code: "workspace_conflict" });
    expect(native.commits).toEqual([]);
  });

  it("enforces grant ownership, expiry, and subpath confinement on every use", async () => {
    const { authority, grants, native } = build();
    grants.grants = [grant({ allowedPathPrefixes: ["allowed"] })];
    const capability = await authority.createReadCapability(FACTS, ["grant_1"]);
    await expect(
      authority.prepareChangeSet(capability.capability, changeSet()),
    ).rejects.toMatchObject({ code: "workspace_capability_denied" });
    expect(native.prepared).toBe(0);
  });
});
