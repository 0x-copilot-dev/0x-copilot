// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CapabilityBroker, CAPABILITY_BROKER_PROTOCOL } from "./broker";
import type { HostFs } from "./host-fs";
import { WorkspaceApprovalPermitSource } from "./workspace-approval";
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
  revoked = false;

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
    return [this.#current()];
  }

  async snapshotActive(): Promise<GrantSnapshot> {
    return {
      snapshotId: "snapshot_1",
      capturedAt: 1,
      grants: this.revoked ? [] : [this.#current()],
    };
  }

  #current(): Grant {
    return { ...this.grant, status: this.revoked ? "revoked" : "active" };
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
  let grants: Grants;
  let permits: WorkspaceApprovalPermitSource;
  let authorizationCalls: number;

  const headers = (extra: Record<string, string> = {}) => ({
    authorization: `Bearer ${token}`,
    "x-capability-protocol": CAPABILITY_BROKER_PROTOCOL,
    ...extra,
  });

  beforeEach(async () => {
    native = new Native();
    grants = new Grants();
    authorizationCalls = 0;
    authority = new LocalWorkspaceAuthority({
      grants,
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
    permits = new WorkspaceApprovalPermitSource({
      authorizeWorkspaceCommit: (facts, preparedRef, decision) => {
        authorizationCalls += 1;
        return authority.authorizeCommitFromUserDecision(
          facts,
          preparedRef,
          decision,
        );
      },
    });
    broker = new CapabilityBroker({
      grants,
      workspaceAuthority: authority,
    });
    broker.installWorkspaceApprovalPermitHandoff(permits);
    baseUrl = (await broker.start()).baseUrl;
    token = broker.authToken();
  });

  afterEach(async () => {
    await broker.stop();
  });

  it("does not turn the boot bearer into a private workspace host session", async () => {
    const response = await fetch(`${baseUrl}/internal/workspace/v2/prepare`, {
      method: "POST",
      headers: headers({ "content-type": "application/json" }),
      body: JSON.stringify(
        changeBody({ host_session_ref: `whs_${"x".repeat(43)}` }),
      ),
    });
    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({
      error: "workspace_capability_denied",
    });
  });

  it("consumes a verified receipt privately after prepare, commits once, and disables legacy writes", async () => {
    const write = vi.fn();
    await broker.stop();
    broker = new CapabilityBroker({
      grants,
      workspaceAuthority: authority,
      hostFs: { write } as unknown as HostFs,
    });
    broker.installWorkspaceApprovalPermitHandoff(permits);
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

    recordVerifiedApproval(permits);
    const session = await bootstrapHostSession(baseUrl, headers);
    const preparedResponse = await fetch(
      `${baseUrl}/internal/workspace/v2/prepare`,
      {
        method: "POST",
        headers: headers({ "content-type": "application/json" }),
        body: JSON.stringify(changeBody({ host_session_ref: session })),
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

    const commitUrl = `${baseUrl}/internal/workspace/v2/prepared/prepared_1/commit`;
    const rejected = await fetch(commitUrl, {
      method: "POST",
      headers: headers({ "content-type": "application/json" }),
      body: JSON.stringify({ permit: "child-invented" }),
    });
    expect(rejected.status).toBe(400);
    expect(native.commits).toEqual([]);

    const first = await fetch(commitUrl, {
      method: "POST",
      headers: headers({ "content-type": "application/json" }),
      body: JSON.stringify({ host_session_ref: session }),
    });
    const replay = await fetch(commitUrl, {
      method: "POST",
      headers: headers({ "content-type": "application/json" }),
      body: JSON.stringify({ host_session_ref: session }),
    });
    expect(first.status).toBe(200);
    expect(await replay.json()).toEqual(await first.clone().json());
    expect(native.commits).toEqual(["wcc_prepared_1"]);
    expect(await first.text()).not.toMatch(
      /wcp_|permit|preparedRef|\/private/u,
    );
  });

  it("aborts a partial staging object when the upload stream fails", async () => {
    recordVerifiedApproval(permits);
    const session = await bootstrapHostSession(baseUrl, headers);
    const preparedResponse = await fetch(
      `${baseUrl}/internal/workspace/v2/prepare`,
      {
        method: "POST",
        headers: headers({ "content-type": "application/json" }),
        body: JSON.stringify(changeBody({ host_session_ref: session })),
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
    const retry = await fetch(
      `${baseUrl}/internal/workspace/v2/prepared/prepared_1/commit`,
      {
        method: "POST",
        headers: headers({ "content-type": "application/json" }),
        body: JSON.stringify({ host_session_ref: session }),
      },
    );
    expect(retry.status).toBe(409);
    expect(await retry.json()).toEqual({ error: "workspace_conflict" });
    expect(authorizationCalls).toBe(0);
  });

  it("rejects a mismatched session without consuming the exact approval reservation", async () => {
    recordVerifiedApproval(permits);
    const firstSession = await bootstrapHostSession(baseUrl, headers);
    const secondSession = await bootstrapHostSession(baseUrl, headers);
    const prepared = await prepare(baseUrl, headers, firstSession);
    await upload(baseUrl, headers, prepared.prepared_ref);
    const commitUrl = `${baseUrl}/internal/workspace/v2/prepared/prepared_1/commit`;

    const mismatched = await fetch(commitUrl, {
      method: "POST",
      headers: headers({ "content-type": "application/json" }),
      body: JSON.stringify({ host_session_ref: secondSession }),
    });
    expect(mismatched.status).toBe(403);
    expect(native.commits).toEqual([]);

    const exact = await fetch(commitUrl, {
      method: "POST",
      headers: headers({ "content-type": "application/json" }),
      body: JSON.stringify({ host_session_ref: firstSession }),
    });
    expect(exact.status).toBe(200);
    expect(native.commits).toEqual(["wcc_prepared_1"]);
  });

  it("fails closed after grant revocation and when no host session exists", async () => {
    const absent = await fetch(`${baseUrl}/internal/workspace/v2/prepare`, {
      method: "POST",
      headers: headers({ "content-type": "application/json" }),
      body: JSON.stringify(
        changeBody({ host_session_ref: `whs_${"z".repeat(43)}` }),
      ),
    });
    expect(absent.status).toBe(403);

    recordVerifiedApproval(permits);
    const session = await bootstrapHostSession(baseUrl, headers);
    const prepared = await prepare(baseUrl, headers, session);
    await upload(baseUrl, headers, prepared.prepared_ref);
    grants.revoked = true;
    const committed = await fetch(
      `${baseUrl}/internal/workspace/v2/prepared/prepared_1/commit`,
      {
        method: "POST",
        headers: headers({ "content-type": "application/json" }),
        body: JSON.stringify({ host_session_ref: session }),
      },
    );
    expect(committed.status).toBe(403);
    expect(native.commits).toEqual([]);
  });
});

function changeBody(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
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

const APPROVAL_SNAPSHOT = {
  runId: FACTS.runId,
  stageId: "stg_1",
  revision: 1,
  proposalDigest: "d".repeat(64),
  targetDigest: "c".repeat(64),
} as const;

function recordVerifiedApproval(source: WorkspaceApprovalPermitSource): void {
  source.recordVerifiedApproval(APPROVAL_SNAPSHOT, {
    stage_id: APPROVAL_SNAPSHOT.stageId,
    revision: APPROVAL_SNAPSHOT.revision,
    decision_ledger_id: "rrun1·7",
    change_set_digest: DIGEST,
    proposal_digest: APPROVAL_SNAPSHOT.proposalDigest,
    target_digest: APPROVAL_SNAPSHOT.targetDigest,
    decision: "approve",
    status: "approved",
  });
}

async function bootstrapHostSession(
  baseUrl: string,
  headersFor: (extra?: Record<string, string>) => Record<string, string>,
): Promise<string> {
  const response = await fetch(
    `${baseUrl}/internal/workspace/v2/host-sessions`,
    {
      method: "POST",
      headers: headersFor({ "content-type": "application/json" }),
      body: JSON.stringify({ run_id: FACTS.runId, user_id: FACTS.userId }),
    },
  );
  expect(response.status).toBe(201);
  const body = (await response.json()) as Record<string, unknown>;
  expect(Object.keys(body).sort()).toEqual([
    "expires_at",
    "grants",
    "host_session_ref",
  ]);
  expect(JSON.stringify(body)).not.toMatch(/root|path|wcp_|permit|prepared/u);
  expect(typeof body.host_session_ref).toBe("string");
  return body.host_session_ref as string;
}

async function prepare(
  baseUrl: string,
  headersFor: (extra?: Record<string, string>) => Record<string, string>,
  hostSessionRef: string,
): Promise<{ prepared_ref: string }> {
  const response = await fetch(`${baseUrl}/internal/workspace/v2/prepare`, {
    method: "POST",
    headers: headersFor({ "content-type": "application/json" }),
    body: JSON.stringify(changeBody({ host_session_ref: hostSessionRef })),
  });
  expect(response.status).toBe(201);
  return (await response.json()) as { prepared_ref: string };
}

async function upload(
  baseUrl: string,
  headersFor: (extra?: Record<string, string>) => Record<string, string>,
  preparedRef: string,
): Promise<void> {
  const preparedId = preparedRef.replace(/^workspace-prepared:\/\//u, "");
  const response = await fetch(
    `${baseUrl}/internal/workspace/v2/prepared/${preparedId}/content/slot_1`,
    {
      method: "PUT",
      headers: headersFor({ "x-workspace-upload-final": "true" }),
      body: Buffer.from("hello"),
    },
  );
  expect(response.status).toBe(200);
}
