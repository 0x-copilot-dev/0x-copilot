// @vitest-environment node
import {
  mkdtempSync,
  mkdirSync,
  realpathSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { NativeWorkspaceFs } from "../../native/workspace-fs";
import type { SafeStorageLike } from "../auth/secret-storage";
import { createCapabilityService } from "./index";
import type { NativeWorkspaceV2Bindings } from "./native-workspace-authority";
import type { WorkspaceJournalRecord } from "./workspace-authority";
import {
  createProductionWorkspaceAuthorityBootstrap,
  deriveWorkspaceAuthorityMaterial,
  type WorkspaceAuthorityBootstrap,
} from "./workspace-authority-bootstrap";

const INSTALLATION_SECRET = "desktop-workspace-bootstrap-secret-".repeat(2);

function encryptedSafeStorage(): SafeStorageLike {
  return {
    isEncryptionAvailable: () => true,
    encryptString: (value) => Buffer.from(value, "utf8"),
    decryptString: (value) => value.toString("utf8"),
  };
}

function unavailableSafeStorage(): SafeStorageLike {
  return {
    isEncryptionAvailable: () => false,
    encryptString: (value) => Buffer.from(value, "utf8"),
    decryptString: (value) => value.toString("utf8"),
  };
}

function bindings(): NativeWorkspaceV2Bindings {
  return {
    platform: process.platform,
    openBeneath: vi.fn(() => 1),
    workspaceRootIdentity: vi.fn(() => ({
      volumeId: "volume_bootstrap",
      fileId: "root_bootstrap",
    })),
    workspacePrepare: vi.fn(() => ({
      handle: "native_prepared_1",
      observedTargetDigest: "a".repeat(64),
      slots: [],
    })),
    workspaceWrite: vi.fn(),
    workspaceSeal: vi.fn(() => ({ digest: "a".repeat(64), size: 0 })),
    workspaceCommit: vi.fn(() => ({
      outcome: "applied" as const,
      receiptRef: "workspace-receipt://claim_1",
    })),
    workspaceReconcile: vi.fn(() => ({
      outcome: "already_applied" as const,
      receiptRef: "workspace-receipt://claim_1",
    })),
    workspaceReconcileClaim: vi.fn(() => ({
      outcome: "already_applied" as const,
      receiptRef: "workspace-receipt://claim_1",
    })),
    workspaceAbort: vi.fn(),
    workspaceProposeRecovery: vi.fn(() => "proposed" as const),
    workspaceProposeRecoveryClaim: vi.fn(() => "conflict" as const),
  };
}

function journalRecord(deviceId: string): WorkspaceJournalRecord {
  return {
    preparedRef: "workspace-prepared://bootstrap_recovery",
    state: "prepared",
    runId: "run_bootstrap",
    userId: "usr_bootstrap",
    deviceId,
    stageId: "stage_bootstrap",
    revision: 1,
    decisionLedgerId: "r1·1",
    pathTokens: ["path_token_1"],
    changeSetDigest: "a".repeat(64),
    targetDigest: "b".repeat(64),
    proposalDigest: "c".repeat(64),
    createdAt: 1_700_000_000_000,
    updatedAt: 1_700_000_000_000,
  };
}

describe("production workspace authority bootstrap", () => {
  let userDataDir: string;
  let pickedRoot: string;

  beforeEach(() => {
    userDataDir = mkdtempSync(join(tmpdir(), "workspace-bootstrap-data-"));
    pickedRoot = mkdtempSync(join(tmpdir(), "workspace-bootstrap-root-"));
  });

  afterEach(() => {
    rmSync(userDataDir, { recursive: true, force: true });
    rmSync(pickedRoot, { recursive: true, force: true });
  });

  it("initializes native root identity, encrypted persistence, and the main-only permit authority before attesting available", async () => {
    const addon = bindings();
    const bootstrap = await createProductionWorkspaceAuthorityBootstrap({
      userDataDir,
      safeStorage: encryptedSafeStorage(),
      installationSecret: INSTALLATION_SECRET,
      production: true,
      confinementProbe: { verify: async () => "enforced" },
      profileIdResolver: async () => "usr_verified_main",
      nativeLoader: () => addon,
    });
    if (bootstrap === null) throw new Error("expected production bootstrap");

    expect(addon.workspaceRootIdentity).toHaveBeenCalledWith(userDataDir);
    expect(bootstrap.attestation).toEqual({
      workspaceWriteIsolation: "enforced",
      nativeWorkspacePrimitives: "available",
    });
    expect(bootstrap.authority.writableAvailable()).toBe(true);

    const service = createCapabilityService({
      userDataDir,
      safeStorage: encryptedSafeStorage(),
      showOpenDialog: async () => ({
        canceled: false,
        filePaths: [pickedRoot],
      }),
      workspace: bootstrap,
    });
    expect(service.workspaceWritesAvailable()).toBe(true);
    expect(service.workspaceWriteAttestation()).toEqual(bootstrap.attestation);

    await service.requestFolderGrant({
      mode: "read_write",
      label: "Renderer-supplied label only",
    });
    const canonicalPickedRoot = realpathSync(pickedRoot);
    const [grant] = await bootstrap.grants.listAll();
    expect(grant).toMatchObject({
      root: canonicalPickedRoot,
      profileId: "usr_verified_main",
      deviceId: bootstrap.deviceId,
    });
    expect(addon.workspaceRootIdentity).toHaveBeenLastCalledWith(
      canonicalPickedRoot,
    );

    // A raw request cannot mint a permit. The source first needs a verified
    // facade receipt recorded by Electron main's approval host.
    await expect(
      bootstrap.permitSource.take({
        facts: {
          runId: "run_bootstrap",
          userId: "usr_verified_main",
          deviceId: bootstrap.deviceId,
        },
        preparedRef: "workspace-prepared://forged",
        stageId: "stage_bootstrap",
        revision: 1,
        decisionLedgerId: "r1·1",
        changeSetDigest: "a".repeat(64),
        proposalDigest: "b".repeat(64),
        targetDigest: "c".repeat(64),
      }),
    ).resolves.toBeNull();
  });

  it("fails closed when the native lifecycle, encrypted storage, or recovered journal is unavailable", async () => {
    const legacy: NativeWorkspaceFs = {
      platform: process.platform,
      openBeneath: () => 1,
    };
    await expect(
      createProductionWorkspaceAuthorityBootstrap({
        userDataDir,
        safeStorage: encryptedSafeStorage(),
        installationSecret: INSTALLATION_SECRET,
        production: true,
        confinementProbe: { verify: async () => "enforced" },
        nativeLoader: () => legacy,
      }),
    ).resolves.toBeNull();

    await expect(
      createProductionWorkspaceAuthorityBootstrap({
        userDataDir,
        safeStorage: encryptedSafeStorage(),
        installationSecret: INSTALLATION_SECRET,
        production: true,
        nativeLoader: bindings,
      }),
    ).resolves.toBeNull();

    await expect(
      createProductionWorkspaceAuthorityBootstrap({
        userDataDir,
        safeStorage: unavailableSafeStorage(),
        installationSecret: INSTALLATION_SECRET,
        production: true,
        confinementProbe: { verify: async () => "enforced" },
        nativeLoader: bindings,
      }),
    ).resolves.toBeNull();

    mkdirSync(join(userDataDir, "capabilities"), { recursive: true });
    writeFileSync(
      join(userDataDir, "capabilities", "workspace-journal.bin"),
      "corrupt journal",
    );
    await expect(
      createProductionWorkspaceAuthorityBootstrap({
        userDataDir,
        safeStorage: encryptedSafeStorage(),
        installationSecret: INSTALLATION_SECRET,
        production: true,
        confinementProbe: { verify: async () => "enforced" },
        nativeLoader: bindings,
      }),
    ).resolves.toBeNull();
  });

  it("recovers the same encrypted journal and device binding after restart", async () => {
    const first = await createProductionWorkspaceAuthorityBootstrap({
      userDataDir,
      safeStorage: encryptedSafeStorage(),
      installationSecret: INSTALLATION_SECRET,
      production: true,
      confinementProbe: { verify: async () => "enforced" },
      nativeLoader: bindings,
    });
    if (first === null) throw new Error("expected first bootstrap");
    const record = journalRecord(first.deviceId);
    await first.journal.put(record);

    const restarted = await createProductionWorkspaceAuthorityBootstrap({
      userDataDir,
      safeStorage: encryptedSafeStorage(),
      installationSecret: INSTALLATION_SECRET,
      production: true,
      confinementProbe: { verify: async () => "enforced" },
      nativeLoader: bindings,
    });
    if (restarted === null) throw new Error("expected restarted bootstrap");

    expect(restarted.deviceId).toBe(first.deviceId);
    expect(restarted.journalIntegrityKey).toEqual(first.journalIntegrityKey);
    expect(await restarted.journal.listNonterminal()).toEqual([record]);
    expect(deriveWorkspaceAuthorityMaterial(INSTALLATION_SECRET).deviceId).toBe(
      first.deviceId,
    );
  });

  it("rejects a structural bootstrap forgery, so renderer data cannot supply a root or permit authority", async () => {
    const genuine = await createProductionWorkspaceAuthorityBootstrap({
      userDataDir,
      safeStorage: encryptedSafeStorage(),
      installationSecret: INSTALLATION_SECRET,
      production: true,
      confinementProbe: { verify: async () => "enforced" },
      nativeLoader: bindings,
    });
    if (genuine === null) throw new Error("expected genuine bootstrap");

    // Deliberately omit the factory's private runtime brand. This resembles a
    // renderer-originated JSON lookalike even though it has privileged values
    // copied into it by this test; the composition root must still reject it.
    const forged = {
      native: genuine.native,
      grants: genuine.grants,
      journal: genuine.journal,
      authority: genuine.authority,
      permitSource: genuine.permitSource,
      attestation: genuine.attestation,
      production: genuine.production,
      deviceId: genuine.deviceId,
      journalIntegrityKey: genuine.journalIntegrityKey,
    } as unknown as WorkspaceAuthorityBootstrap;
    const service = createCapabilityService({
      userDataDir,
      safeStorage: encryptedSafeStorage(),
      showOpenDialog: async () => ({
        canceled: false,
        filePaths: ["/renderer-forged-root"],
      }),
      workspace: forged,
    });

    expect(service.workspaceWritesAvailable()).toBe(false);
    expect(service.workspaceWriteAttestation()).toEqual({
      workspaceWriteIsolation: "unavailable",
      nativeWorkspacePrimitives: "unavailable",
    });
  });
});
