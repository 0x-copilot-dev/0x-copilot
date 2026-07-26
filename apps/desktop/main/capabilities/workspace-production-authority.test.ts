// @vitest-environment node
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

import type { SafeStorageLike } from "../auth/secret-storage";
import { createCapabilityService } from ".";
import type { NativeWorkspaceHelper } from "./workspace-production-authority";
import { createProductionWorkspaceAuthority } from "./workspace-production-authority";
import type { NativeWorkspaceCommitHelperConfig } from "./native-workspace-commit-helper";

const INSTALLATION_SECRET = "workspace-production-authority-secret-".repeat(2);
const roots: string[] = [];

function root(prefix: string): string {
  const value = mkdtempSync(join(tmpdir(), prefix));
  roots.push(value);
  return value;
}

function encryptedStorage(): SafeStorageLike {
  return {
    isEncryptionAvailable: () => true,
    encryptString: (value) => Buffer.from(value, "utf8"),
    decryptString: (value) => value.toString("utf8"),
  };
}

function helper(): NativeWorkspaceHelper {
  return {
    primitivesAvailable: true,
    rootIdentity: vi.fn(async () => ({
      volumeId: "vol_main",
      fileId: "id_main",
    })),
    prepare: vi.fn(),
    writePrepared: vi.fn(),
    sealPrepared: vi.fn(),
    commitPrepared: vi.fn(),
    reconcilePrepared: vi.fn(),
    reconcileClaim: vi.fn(),
    abortPrepared: vi.fn(),
    proposeRecovery: vi.fn(),
    proposeRecoveryClaim: vi.fn(),
    close: vi.fn(async () => {}),
  } as unknown as NativeWorkspaceHelper;
}

afterEach(() => {
  while (roots.length > 0)
    rmSync(roots.pop()!, { force: true, recursive: true });
});

describe("production workspace authority composition", () => {
  it("selects the real authority only after a recording confinement gate and native-helper launch", async () => {
    const native = helper();
    const launch = vi.fn(
      async (
        _config: NativeWorkspaceCommitHelperConfig,
      ): Promise<NativeWorkspaceHelper> => native,
    );
    const lifecycle = await createProductionWorkspaceAuthority({
      userDataDir: root("workspace-authority-data-"),
      safeStorage: encryptedStorage(),
      installationSecret: INSTALLATION_SECRET,
      profileIdResolver: async () => "usr_verified_main",
      confinement: { verify: async () => "enforced" },
      production: true,
      packaged: true,
      platform: "darwin",
      helperPath: "/main-owned/workspace-commit-helper",
      launchHelper: launch,
    });
    expect(lifecycle).not.toBeNull();
    if (lifecycle === null) throw new Error("expected writable lifecycle");

    expect(launch).toHaveBeenCalledOnce();
    const config = launch.mock.calls[0]![0];
    expect(config).toMatchObject({
      executablePath: "/main-owned/workspace-commit-helper",
      packaged: true,
      attestation: {
        workspaceWriteIsolation: "enforced",
        nativeWorkspacePrimitives: "available",
      },
    });
    expect(config.stagingDirectoryFd).toEqual(expect.any(Number));
    expect(config.journalDirectoryFd).toEqual(expect.any(Number));
    expect(config.journalIntegrityKey).toHaveLength(32);
    expect(lifecycle.seed.deviceId).toMatch(/^dwa_[a-f0-9]{64}$/u);

    const service = createCapabilityService({
      userDataDir: root("workspace-authority-service-"),
      safeStorage: encryptedStorage(),
      showOpenDialog: async () => ({ canceled: true, filePaths: [] }),
      workspace: lifecycle.seed,
    });
    expect(service.workspaceWritesAvailable()).toBe(true);
    expect(service.workspaceWriteAttestation()).toEqual({
      workspaceWriteIsolation: "enforced",
      nativeWorkspacePrimitives: "available",
    });

    await lifecycle.dispose();
    expect(native.close).toHaveBeenCalledOnce();
  });

  it.each([
    [
      "unsupported platform",
      "linux" as NodeJS.Platform,
      true,
      true,
      "enforced" as const,
    ],
    [
      "development posture",
      "darwin" as NodeJS.Platform,
      false,
      true,
      "enforced" as const,
    ],
    [
      "unpackaged build",
      "darwin" as NodeJS.Platform,
      true,
      false,
      "enforced" as const,
    ],
    [
      "missing confinement proof",
      "darwin" as NodeJS.Platform,
      true,
      true,
      "unavailable" as const,
    ],
  ])(
    "fails closed for %s",
    async (_name, platform, production, packaged, proof) => {
      const launch = vi.fn(async () => helper());
      const lifecycle = await createProductionWorkspaceAuthority({
        userDataDir: root("workspace-authority-reject-"),
        safeStorage: encryptedStorage(),
        installationSecret: INSTALLATION_SECRET,
        profileIdResolver: async () => "usr_verified_main",
        confinement: { verify: async () => proof },
        production,
        packaged,
        platform,
        launchHelper: launch,
      });
      expect(lifecycle).toBeNull();
      expect(launch).not.toHaveBeenCalled();
    },
  );

  it("fails closed when the signed helper cannot be launched", async () => {
    const launch = vi.fn(async () => {
      throw new Error("signature or launch failure");
    });
    const lifecycle = await createProductionWorkspaceAuthority({
      userDataDir: root("workspace-authority-helper-failure-"),
      safeStorage: encryptedStorage(),
      installationSecret: INSTALLATION_SECRET,
      profileIdResolver: async () => "usr_verified_main",
      confinement: { verify: async () => "enforced" },
      production: true,
      packaged: true,
      platform: "darwin",
      launchHelper: launch,
    });
    expect(lifecycle).toBeNull();
    expect(launch).toHaveBeenCalledOnce();
  });

  it("fails closed when the desktop keychain cannot protect authority material", async () => {
    const launch = vi.fn(async () => helper());
    const lifecycle = await createProductionWorkspaceAuthority({
      userDataDir: root("workspace-authority-no-keychain-"),
      safeStorage: {
        ...encryptedStorage(),
        isEncryptionAvailable: () => false,
      },
      installationSecret: INSTALLATION_SECRET,
      profileIdResolver: async () => "usr_verified_main",
      confinement: { verify: async () => "enforced" },
      production: true,
      packaged: true,
      platform: "darwin",
      launchHelper: launch,
    });
    expect(lifecycle).toBeNull();
    expect(launch).not.toHaveBeenCalled();
  });
});
