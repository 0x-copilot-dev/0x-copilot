import { createHmac } from "node:crypto";
import {
  chmodSync,
  closeSync,
  constants,
  lstatSync,
  mkdirSync,
  openSync,
} from "node:fs";
import { join } from "node:path";

import type { SafeStorageLike } from "../auth/secret-storage";

import {
  NativeWorkspaceCommitHelper,
  resolveNativeWorkspaceCommitHelperPath,
  type NativeWorkspaceCommitHelperConfig,
} from "./native-workspace-commit-helper";
import type {
  NativeWorkspaceAuthority,
  WorkspaceWriteAttestation,
} from "./workspace-authority";

const DEVICE_ID_LABEL = "copilot.desktop.workspace-v2.device-id";
const JOURNAL_KEY_LABEL = "copilot.desktop.workspace-v2.journal-key";
const PRIVATE_ROOT = ["capabilities", "workspace-v2"] as const;

/**
 * Main-owned proof that the supervised runtime has been launched in a
 * filesystem-confined posture. This is deliberately not an environment flag
 * or a renderer claim: a false, absent, or failed probe leaves C2 unavailable.
 */
export interface WorkspaceConfinementProbe {
  verify(): Promise<"enforced" | "unavailable">;
}

/** The narrow seed accepted by CapabilityService's C2 composition root. */
export interface WorkspaceAuthoritySeed {
  readonly native: NativeWorkspaceAuthority;
  readonly attestation: WorkspaceWriteAttestation;
  readonly production: boolean;
  readonly profileIdResolver: () => Promise<string | null>;
  readonly deviceId: string;
  readonly journalIntegrityKey: Buffer;
}

/** Owns the helper process and its private inherited directory descriptors. */
export interface WorkspaceAuthorityLifecycle {
  readonly seed: WorkspaceAuthoritySeed;
  dispose(): Promise<void>;
}

export interface ProductionWorkspaceAuthorityConfig {
  readonly userDataDir: string;
  readonly safeStorage: SafeStorageLike;
  /** Existing main-owned boot secret; never exported to a child or renderer. */
  readonly installationSecret: string;
  readonly profileIdResolver: () => Promise<string | null>;
  readonly confinement: WorkspaceConfinementProbe | undefined;
  readonly production: boolean;
  readonly packaged: boolean;
  readonly platform?: NodeJS.Platform;
  readonly helperPath?: string;
  /** Test seam. Production always launches the signed native helper. */
  readonly launchHelper?: (
    config: NativeWorkspaceCommitHelperConfig,
  ) => Promise<NativeWorkspaceHelper>;
}

export interface NativeWorkspaceHelper extends NativeWorkspaceAuthority {
  close(): Promise<void>;
}

/**
 * Creates the sole writable desktop authority for the only supported posture:
 * signed packaged macOS, encrypted main storage, an enforced child
 * confinement probe, and a live authenticated native commit helper. There is
 * intentionally no development, Node-filesystem, or partial-native fallback.
 */
export async function createProductionWorkspaceAuthority(
  config: ProductionWorkspaceAuthorityConfig,
): Promise<WorkspaceAuthorityLifecycle | null> {
  const platform = config.platform ?? process.platform;
  if (
    platform !== "darwin" ||
    !config.packaged ||
    !config.production ||
    !config.safeStorage.isEncryptionAvailable() ||
    config.confinement === undefined
  ) {
    return null;
  }

  let stagingDirectoryFd: number | null = null;
  let journalDirectoryFd: number | null = null;
  let helper: NativeWorkspaceHelper | null = null;
  let retained = false;
  try {
    if ((await config.confinement.verify()) !== "enforced") return null;

    const material = deriveAuthorityMaterial(config.installationSecret);
    const privateRoot = join(config.userDataDir, ...PRIVATE_ROOT);
    stagingDirectoryFd = openPrivateDirectory(join(privateRoot, "staging"));
    journalDirectoryFd = openPrivateDirectory(
      join(privateRoot, "native-journal"),
    );
    const attestation = Object.freeze({
      workspaceWriteIsolation: "enforced",
      nativeWorkspacePrimitives: "available",
    } as const satisfies WorkspaceWriteAttestation);
    const executablePath =
      config.helperPath ??
      resolveNativeWorkspaceCommitHelperPath({
        packaged: true,
        resourcesPath: process.resourcesPath,
        appPath: process.cwd(),
      });
    helper = await (config.launchHelper ?? NativeWorkspaceCommitHelper.launch)({
      executablePath,
      stagingDirectoryFd,
      journalDirectoryFd,
      journalIntegrityKey: material.journalIntegrityKey,
      attestation,
      // This is intentional: supported C2 must verify the nested packaged
      // executable before it is granted a private authority channel.
      packaged: true,
    });
    if (!helper.primitivesAvailable) return null;

    // Ping/root identity is performed by helper.launch. A second harmless
    // main-owned query ensures a malformed helper cannot be attested merely by
    // opening its process; no user workspace is touched here.
    const identity = await helper.rootIdentity(config.userDataDir);
    if (!isRootIdentity(identity)) return null;

    const seed: WorkspaceAuthoritySeed = Object.freeze({
      native: helper,
      attestation,
      production: true,
      profileIdResolver: config.profileIdResolver,
      deviceId: material.deviceId,
      journalIntegrityKey: Buffer.from(material.journalIntegrityKey),
    });
    let disposed = false;
    retained = true;
    return Object.freeze({
      seed,
      dispose: async () => {
        if (disposed) return;
        disposed = true;
        await helper!.close().catch(() => {});
        closePrivateDirectory(stagingDirectoryFd);
        closePrivateDirectory(journalDirectoryFd);
        stagingDirectoryFd = null;
        journalDirectoryFd = null;
      },
    });
  } catch {
    return null;
  } finally {
    // On success the lifecycle retains both descriptors for the helper's
    // lifetime. Every failed verification tears them down before returning an
    // unavailable authority.
    if (!retained) {
      await helper?.close().catch(() => {});
      closePrivateDirectory(stagingDirectoryFd);
      closePrivateDirectory(journalDirectoryFd);
    }
  }
}

export function deriveAuthorityMaterial(installationSecret: string): {
  readonly deviceId: string;
  readonly journalIntegrityKey: Buffer;
} {
  const secret = Buffer.from(installationSecret, "utf8");
  if (secret.byteLength < 32) {
    throw new Error("workspace authority installation secret is too short");
  }
  const derive = (label: string): Buffer =>
    createHmac("sha256", secret).update(label, "utf8").digest();
  return Object.freeze({
    deviceId: `dwa_${derive(DEVICE_ID_LABEL).toString("hex")}`,
    journalIntegrityKey: Buffer.from(derive(JOURNAL_KEY_LABEL)),
  });
}

function openPrivateDirectory(path: string): number {
  mkdirSync(path, { recursive: true, mode: 0o700 });
  chmodSync(path, 0o700);
  const stat = lstatSync(path);
  if (!stat.isDirectory() || stat.isSymbolicLink()) {
    throw new Error("workspace private directory is not a directory");
  }
  const directoryFlag =
    typeof constants.O_DIRECTORY === "number" ? constants.O_DIRECTORY : 0;
  const noFollow =
    typeof constants.O_NOFOLLOW === "number" ? constants.O_NOFOLLOW : 0;
  return openSync(path, constants.O_RDONLY | directoryFlag | noFollow);
}

function closePrivateDirectory(fd: number | null): void {
  if (fd === null) return;
  try {
    closeSync(fd);
  } catch {
    // Failure here is cleanup-only. The helper is already closed or was never
    // exposed, so it cannot turn into a writable fallback.
  }
}

function isRootIdentity(value: unknown): value is {
  readonly volumeId: string;
  readonly fileId: string;
} {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as { volumeId?: unknown }).volumeId === "string" &&
    (value as { volumeId: string }).volumeId.length > 0 &&
    typeof (value as { fileId?: unknown }).fileId === "string" &&
    (value as { fileId: string }).fileId.length > 0
  );
}
