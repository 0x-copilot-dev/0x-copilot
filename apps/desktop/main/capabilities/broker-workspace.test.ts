// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CapabilityBroker, CAPABILITY_BROKER_PROTOCOL } from "./broker";
import type { HostFs } from "./host-fs";
import type { Grant, GrantProvider, GrantSnapshot } from "./types";
import {
  InMemoryWorkspaceJournalStore,
  LocalWorkspaceAuthority,
  type NativePreparedWorkspace,
  type NativeWorkspaceAuthority,
  type NativeWorkspaceCommitResult,
  type WorkspaceRootIdentity,
} from "./workspace-authority";

const ROOT = "/private/project";
const FACTS = { runId: "run_1", userId: "user_1", deviceId: "device_1" };
const IDENTITY: WorkspaceRootIdentity = {
  volumeId: "volume_1",
  fileId: "file_1",
};
const DIGEST = "a".repeat(64);

class Grants implements GrantProvider {
  readonly grant: Grant = {
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
  };

  async listAll(): Promise<readonly Grant[]> {
    return [this.grant];
  }

  async snapshotActive(): Promise<GrantSnapshot> {
    return { snapshotId: "snapshot_1", capturedAt: 1, grants: [this.grant] };
  }
}

class Native implements NativeWorkspaceAuthority {
  primitivesAvailable = true;
  commits: string[] = [];
  writes: Uint8Array[] = [];
  aborts = 0;
  failWrites = false;

  async rootIdentity(): Promise<WorkspaceRootIdentity> {
    return IDENTITY;
  }

  async prepare(): Promise<NativePreparedWorkspace> {
    return {
      handle: "native_1",
      observedTargetDigest: DIGEST,
      slots: [{ slot: "slot_1", digest: "b".repeat(64), size: 5 }],
    };
  }

  async writePrepared(
    _prepared: NativePreparedWorkspace,
    _slot: string,
    chunk: Uint8Array,
  ): Promise<void> {
    if (this.failWrites) throw new Error("staging write failed");
    this.writes.push(chunk);
  }

  async sealPrepared(): Promise<{
    readonly digest: string;
    readonly size: number;
  }> {
    return { digest: "b".repeat(64), size: 5 };
  }

  async commitPrepared(
    _prepared: NativePreparedWorkspace,
    claimId: string,
  ): Promise<NativeWorkspaceCommitResult> {
    this.commits.push(claimId);
    return { outcome: "applied", receiptRef: `workspace-receipt://${claimId}` };
  }

  async reconcilePrepared(
    _prepared: NativePreparedWorkspace,
    claimId: string,
  ): Promise<NativeWorkspaceCommitResult> {
    return {
      outcome: "already_applied",
      receiptRef: `workspace-receipt://${claimId}`,
    };
  }

  async reconcileClaim(claimId: string): Promise<NativeWorkspaceCommitResult> {
    return {
      outcome: "already_applied",
      receiptRef: `workspace-receipt://${claimId}`,
    };
  }

  async abortPrepared(): Promise<void> {
    this.aborts += 1;
  }

  async proposeRecovery(): Promise<"proposed" | "conflict"> {
    return "proposed";
  }

  async proposeRecoveryClaim(): Promise<"proposed" | "conflict"> {
    return "proposed";
  }
}

describe("CapabilityBroker workspace v2", () => {
  let authority: LocalWorkspaceAuthority;
  let broker: CapabilityBroker;
  let baseUrl: string;
  let token: string;
  let native: Native;

  const headers = (extra: Record<string, string> = {}) => ({
    authorization: `Bearer ${token}`,
    "x-capability-protocol": CAPABILITY_BROKER_PROTOCOL,
    ...extra,
  });

  beforeEach(async () => {
    native = new Native();
    authority = new LocalWorkspaceAuthority({
      grants: new Grants(),
      native,
      journal: new InMemoryWorkspaceJournalStore(),
      attestation: {
        workspaceWriteIsolation: "enforced",
        nativeWorkspacePrimitives: "available",
      },
      production: true,
      deviceId: FACTS.deviceId,
      id: () => "prepared_1",
      randomBytes: () => Buffer.alloc(32, 7),
      journalTokenKey: Buffer.alloc(32, 9),
    });
    broker = new CapabilityBroker({
      grants: new Grants(),
      workspaceAuthority: authority,
    });
    baseUrl = (await broker.start()).baseUrl;
    token = broker.authToken();
  });

  afterEach(async () => {
    await broker.stop();
  });

  it("does not turn the boot bearer into filesystem authority", async () => {
    const response = await fetch(`${baseUrl}/internal/workspace/v2/prepare`, {
      method: "POST",
      headers: headers({ "content-type": "application/json" }),
      body: JSON.stringify(changeBody({ read_capability: "not-main-minted" })),
    });
    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({
      error: "workspace_capability_denied",
    });
  });

  it("streams only staged bytes, commits once with an exact main-issued permit, and disables legacy writes", async () => {
    const write = vi.fn();
    await broker.stop();
    broker = new CapabilityBroker({
      grants: new Grants(),
      workspaceAuthority: authority,
      hostFs: { write } as unknown as HostFs,
    });
    baseUrl = (await broker.start()).baseUrl;
    token = broker.authToken();
    const legacy = await fetch(`${baseUrl}/v1/fs/write`, {
      method: "POST",
      headers: headers({ "content-type": "application/json" }),
      body: JSON.stringify({
        grant_id: "grant_1",
        path: "notes.md",
        content_base64: "aGVsbG8=",
      }),
    });
    expect(legacy.status).toBe(404);
    expect(await legacy.json()).toEqual({ error: "unsupported" });
    expect(write).not.toHaveBeenCalled();

    const capability = await authority.createReadCapability(FACTS, ["grant_1"]);
    const preparedResponse = await fetch(
      `${baseUrl}/internal/workspace/v2/prepare`,
      {
        method: "POST",
        headers: headers({ "content-type": "application/json" }),
        body: JSON.stringify(
          changeBody({ read_capability: capability.capability }),
        ),
      },
    );
    expect(preparedResponse.status).toBe(201);
    const prepared = (await preparedResponse.json()) as {
      prepared_ref: string;
    };
    expect(JSON.stringify(prepared)).not.toContain(ROOT);

    const uploaded = await fetch(
      `${baseUrl}/internal/workspace/v2/prepared/prepared_1/content/slot_1`,
      {
        method: "PUT",
        headers: headers({ "x-workspace-upload-final": "true" }),
        body: Buffer.from("hello"),
      },
    );
    expect(uploaded.status).toBe(200);
    expect(native.writes).toEqual([Buffer.from("hello")]);

    const permit = await authority.authorizeCommitFromUserDecision(
      FACTS,
      prepared.prepared_ref,
      {
        stageId: "stg_1",
        revision: 1,
        decisionLedgerId: "rrun1·7",
      },
    );
    const commitUrl = `${baseUrl}/internal/workspace/v2/prepared/prepared_1/commit`;
    const rejected = await fetch(commitUrl, {
      method: "POST",
      headers: headers({ "content-type": "application/json" }),
      body: JSON.stringify({ permit: "child-invented" }),
    });
    expect(rejected.status).toBe(403);

    const first = await fetch(commitUrl, {
      method: "POST",
      headers: headers({ "content-type": "application/json" }),
      body: JSON.stringify({ permit: permit.permit }),
    });
    const replay = await fetch(commitUrl, {
      method: "POST",
      headers: headers({ "content-type": "application/json" }),
      body: JSON.stringify({ permit: permit.permit }),
    });
    expect(first.status).toBe(200);
    expect(await replay.json()).toEqual(await first.clone().json());
    expect(native.commits).toEqual([permit.commitId]);
  });

  it("aborts a partial staging object when the upload stream fails", async () => {
    const capability = await authority.createReadCapability(FACTS, ["grant_1"]);
    const preparedResponse = await fetch(
      `${baseUrl}/internal/workspace/v2/prepare`,
      {
        method: "POST",
        headers: headers({ "content-type": "application/json" }),
        body: JSON.stringify(
          changeBody({ read_capability: capability.capability }),
        ),
      },
    );
    const prepared = (await preparedResponse.json()) as {
      prepared_ref: string;
    };
    native.failWrites = true;

    const uploaded = await fetch(
      `${baseUrl}/internal/workspace/v2/prepared/prepared_1/content/slot_1`,
      {
        method: "PUT",
        headers: headers({ "x-workspace-upload-final": "true" }),
        body: Buffer.from("hello"),
      },
    );

    expect(uploaded.status).toBe(500);
    expect(native.aborts).toBe(1);
    await expect(
      authority.authorizeCommitFromUserDecision(FACTS, prepared.prepared_ref, {
        stageId: "stg_1",
        revision: 1,
        decisionLedgerId: "rrun1·7",
      }),
    ).rejects.toMatchObject({ code: "workspace_conflict" });
  });
});

function changeBody(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    read_capability: "main-issued-capability",
    stage_id: "stg_1",
    revision: 1,
    decision_ledger_id: "rrun1·7",
    grant_id: "grant_1",
    mount: "mnt_1",
    change_set_digest: DIGEST,
    target_digest: "c".repeat(64),
    proposal_digest: "d".repeat(64),
    entries: [
      {
        operation: "create",
        relative_path: "notes.md",
        content_digest: "b".repeat(64),
        content_size: 5,
        content_slot: "slot_1",
        precondition: { exists: false },
      },
    ],
    ...overrides,
  };
}
