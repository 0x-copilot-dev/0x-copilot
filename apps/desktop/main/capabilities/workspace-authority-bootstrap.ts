import { createHmac } from "node:crypto";
import { createRequire } from "node:module";

import type { NativeWorkspaceFs } from "../../native/workspace-fs";
import type { SafeStorageLike } from "../auth/secret-storage";

import { GrantStore } from "./grant-store";
import {
  AddonNativeWorkspaceAuthority,
  hasNativeWorkspaceV2Bindings,
  type NativeWorkspaceV2Bindings,
} from "./native-workspace-authority";
import { WorkspaceApprovalPermitSource } from "./workspace-approval";
import {
  LocalWorkspaceAuthority,
  type NativeWorkspaceAuthority,
  type WorkspaceRootIdentity,
  type WorkspaceWriteAttestation,
} from "./workspace-authority";
import { EncryptedWorkspaceJournalStore } from "./workspace-journal";

const BOOTSTRAP_BRAND: unique symbol = Symbol("workspace-authority-bootstrap");
const DEVICE_ID_LABEL = "copilot.desktop.workspace-v2.device-id";
const JOURNAL_KEY_LABEL = "copilot.desktop.workspace-v2.journal-key";

/**
 * A main-process-only, fully initialized C2 authority graph. This is issued
 * only by createProductionWorkspaceAuthorityBootstrap; structural lookalikes
 * (including renderer-originated IPC payloads) are rejected by the composition
 * root at runtime.
 */
export interface WorkspaceAuthorityBootstrap {
  readonly native: NativeWorkspaceAuthority;
  readonly grants: GrantStore;
  readonly journal: EncryptedWorkspaceJournalStore;
  readonly authority: LocalWorkspaceAuthority;
  readonly permitSource: WorkspaceApprovalPermitSource;
  readonly attestation: WorkspaceWriteAttestation;
  readonly production: boolean;
  readonly profileId?: string;
  readonly deviceId: string;
  /** Stable per-installation key; never exposed through IPC or child env. */
  readonly journalIntegrityKey: Buffer;
  readonly [BOOTSTRAP_BRAND]: true;
}

export interface ProductionWorkspaceAuthorityBootstrapConfig {
  readonly userDataDir: string;
  readonly safeStorage: SafeStorageLike;
  /** Main-owned persistent installation secret (BootSecrets.vaultSecret). */
  readonly installationSecret: string;
  /** The desktop's real runtime posture, not a renderer-supplied value. */
  readonly production: boolean;
  /**
   * Main-owned proof that the supervised Python services cannot directly touch
   * an ungranted host file. This must come from the platform launcher after it
   * applies its OS confinement profile; it is never an environment flag or
   * renderer claim. Omit it to keep writes unavailable.
   */
  readonly confinementProbe?: WorkspaceConfinementProbe;
  /** Trusted fixed profile, if the host has one at boot. */
  readonly profileId?: string;
  /**
   * Main-only late resolver for the verified profile. It is invoked only when
   * a native picker has selected a folder, never from a renderer payload.
   */
  readonly profileIdResolver?: () => Promise<string | null>;
  /** Injectable only for node tests; production uses the packaged loader. */
  readonly nativeLoader?: () => NativeWorkspaceFs | undefined;
}

export interface WorkspaceConfinementProbe {
  verify(): Promise<"enforced" | "unavailable">;
}

export interface WorkspaceAuthorityMaterial {
  readonly deviceId: string;
  readonly journalIntegrityKey: Buffer;
}

/**
 * Derive independent, stable authority material from the persisted boot
 * secret. The raw boot secret itself is never stored in grants, journals,
 * renderer state, child environment, or attestation claims.
 */
export function deriveWorkspaceAuthorityMaterial(
  installationSecret: string,
): WorkspaceAuthorityMaterial {
  const key = Buffer.from(installationSecret, "utf8");
  if (key.byteLength < 32) {
    throw new Error("workspace authority installation secret is too short");
  }
  const derive = (label: string): Buffer =>
    createHmac("sha256", key).update(label, "utf8").digest();
  const deviceDigest = derive(DEVICE_ID_LABEL);
  return Object.freeze({
    // Opaque, stable, and accepted by WorkspaceRunFacts' strict ID grammar.
    deviceId: `dwa_${deviceDigest.toString("hex")}`,
    journalIntegrityKey: Buffer.from(derive(JOURNAL_KEY_LABEL)),
  });
}

/**
 * Build the real desktop C2 authority graph. It does not trust an environment
 * toggle, renderer input, or a partial addon. Availability is issued only
 * after the complete native lifecycle loads, an OS-confinement probe proves
 * the supervised child cannot reach ambient host files, native root identity
 * executes, encrypted grant/journal recovery succeeds, and the permit source
 * is bound to that same LocalWorkspaceAuthority.
 */
export async function createProductionWorkspaceAuthorityBootstrap(
  config: ProductionWorkspaceAuthorityBootstrapConfig,
): Promise<WorkspaceAuthorityBootstrap | null> {
  try {
    // A production C2 journal must be encrypted at rest. We intentionally do
    // not make a plaintext "works on my machine" path attestable in dev either:
    // a desktop that cannot protect its durable authority state stays disabled.
    if (!config.safeStorage.isEncryptionAvailable()) return null;

    const loaded = (config.nativeLoader ?? loadPackagedNativeWorkspaceFs)();
    if (!hasNativeWorkspaceV2Bindings(loaded)) return null;
    const confinement = await config.confinementProbe?.verify();
    if (confinement !== "enforced") return null;

    const material = deriveWorkspaceAuthorityMaterial(
      config.installationSecret,
    );
    const native = new AddonNativeWorkspaceAuthority(loaded);
    const journal = new EncryptedWorkspaceJournalStore({
      userDataDir: config.userDataDir,
      safeStorage: config.safeStorage,
      integrityKey: material.journalIntegrityKey,
    });
    const grants = new GrantStore({
      userDataDir: config.userDataDir,
      safeStorage: config.safeStorage,
      rootIdentity: (root) => native.rootIdentity(root),
      profileId: config.profileId,
      profileIdResolver: config.profileIdResolver,
      deviceId: material.deviceId,
    });

    // These are all main-owned operations. No selected grant root, native
    // handle, approval receipt, or permit is ever sent to or accepted from the
    // renderer while bootstrapping.
    const [rootIdentity] = await Promise.all([
      native.rootIdentity(config.userDataDir),
      journal.initialize(),
      grants.listAll(),
    ]);
    if (!isRootIdentity(rootIdentity)) return null;

    const attestation = Object.freeze({
      workspaceWriteIsolation: "enforced" as const,
      nativeWorkspacePrimitives: "available" as const,
    });
    const authority = new LocalWorkspaceAuthority({
      grants,
      native,
      journal,
      attestation,
      production: config.production,
      deviceId: material.deviceId,
      journalTokenKey: material.journalIntegrityKey,
    });
    if (!authority.writableAvailable()) return null;

    const permitSource = new WorkspaceApprovalPermitSource({
      authorizeWorkspaceCommit: (facts, preparedRef, decision) =>
        authority.authorizeCommitFromUserDecision(facts, preparedRef, decision),
    });
    return Object.freeze({
      native,
      grants,
      journal,
      authority,
      permitSource,
      attestation,
      production: config.production,
      ...(config.profileId === undefined
        ? {}
        : { profileId: config.profileId }),
      deviceId: material.deviceId,
      journalIntegrityKey: Buffer.from(material.journalIntegrityKey),
      [BOOTSTRAP_BRAND]: true as const,
    });
  } catch {
    // Initialization is an authority boundary. A corrupt journal, failed
    // secure-store read, malformed addon result, or filesystem failure never
    // downgrades to a string-path implementation and never upgrades claims.
    return null;
  }
}

/** Runtime guard used by the composition root against structural forgeries. */
export function isWorkspaceAuthorityBootstrap(
  value: unknown,
): value is WorkspaceAuthorityBootstrap {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<WorkspaceAuthorityBootstrap>;
  if (candidate[BOOTSTRAP_BRAND] !== true) return false;
  if (
    candidate.attestation?.workspaceWriteIsolation !== "enforced" ||
    candidate.attestation.nativeWorkspacePrimitives !== "available" ||
    candidate.native?.primitivesAvailable !== true ||
    !Buffer.isBuffer(candidate.journalIntegrityKey) ||
    candidate.journalIntegrityKey.byteLength < 32
  ) {
    return false;
  }
  try {
    return candidate.authority?.writableAvailable() === true;
  } catch {
    return false;
  }
}

function isRootIdentity(value: WorkspaceRootIdentity): boolean {
  return (
    typeof value.volumeId === "string" &&
    value.volumeId.length > 0 &&
    typeof value.fileId === "string" &&
    value.fileId.length > 0
  );
}

/** Load the actual packaged native addon without letting esbuild bundle it. */
function loadPackagedNativeWorkspaceFs(): NativeWorkspaceFs | undefined {
  try {
    const specifier = ["..", "..", "native", "workspace-fs", "index.cjs"].join(
      "/",
    );
    const require = createRequire(import.meta.url);
    const module = require(specifier) as {
      readonly loadNative?: () => NativeWorkspaceFs | undefined;
    };
    return typeof module.loadNative === "function"
      ? module.loadNative()
      : undefined;
  } catch {
    return undefined;
  }
}

export type { NativeWorkspaceV2Bindings };
