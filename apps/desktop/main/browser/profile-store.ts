// AC8 agentic browser — profile store (main-owned, secret-bearing local state).
//
// A profile is an isolated browser identity: its own `userDataDir`, cookies,
// storage, cache. The store enforces the AC8 isolation rules:
//
//   - Ephemeral is the DEFAULT: `newEphemeral` mints an opaque id + a per-run
//     staging-only directory whose bytes are discarded at close.
//   - A persistent profile is created only by an explicit `createPersistent`
//     call. Its directory is derived from a RANDOM opaque id (never the
//     workspace name), created `0700`, and bound to EXACTLY ONE workspace id.
//   - Two profiles NEVER share a directory. A persistent profile only reopens
//     under its original workspace binding (`browser_profile_version_mismatch`
//     / cross-workspace denial handled by `acquireLease`).
//   - At most ONE active automation lease per profile; a second acquire fails
//     with `browser_profile_busy`.
//
// The store performs no browser automation; it owns identity + lifecycle. All
// filesystem + id + clock touchpoints are injected so unit tests drive the full
// lifecycle without touching disk.

import { BrowserErrorCode, BrowserProfileMode } from "./protocol";

export interface ProfileManifest {
  readonly version: 1;
  readonly profileId: string;
  readonly workspaceId: string;
  readonly mode: BrowserProfileMode;
  readonly browserVersion: string;
  readonly policyVersion: number;
  readonly createdAt: number;
  readonly lastUsedAt: number;
  /** Absolute host path to this profile's userDataDir. MAIN-ONLY. */
  readonly userDataDir: string;
}

export interface ProfileFsPort {
  mkdir(
    path: string,
    opts: { recursive: boolean; mode?: number },
  ): Promise<void>;
  writeFile(
    path: string,
    data: string,
    opts?: { mode?: number },
  ): Promise<void>;
  readFile(path: string): Promise<string>;
  rm(path: string, opts: { recursive: boolean; force: boolean }): Promise<void>;
  exists(path: string): Promise<boolean>;
}

export interface ProfileStoreConfig {
  /** Root under which persistent profile dirs live (app-owned userData). */
  readonly profilesRoot: string;
  /** Root under which ephemeral / per-run staging dirs live. */
  readonly ephemeralRoot: string;
  readonly fs: ProfileFsPort;
  /** Pinned Chromium build id — recorded in the manifest for version pinning. */
  readonly browserVersion: string;
  readonly policyVersion?: number;
  readonly randomId?: () => string;
  readonly now?: () => number;
  /** Path join (injectable for cross-platform tests). */
  readonly join?: (...parts: string[]) => string;
}

export class ProfileError extends Error {
  readonly code: BrowserErrorCode;
  constructor(code: BrowserErrorCode, message: string) {
    super(message);
    this.name = "ProfileError";
    this.code = code;
  }
}

const DIR_MODE = 0o700;
const FILE_MODE = 0o600;

export class ProfileStore {
  readonly #cfg: ProfileStoreConfig;
  readonly #randomId: () => string;
  readonly #now: () => number;
  readonly #join: (...parts: string[]) => string;
  /** profileId -> true while an automation lease is held. */
  readonly #leases = new Set<string>();

  constructor(cfg: ProfileStoreConfig) {
    this.#cfg = cfg;
    this.#randomId =
      cfg.randomId ?? (() => `prf_${Math.random().toString(36).slice(2, 14)}`);
    this.#now = cfg.now ?? Date.now;
    this.#join = cfg.join ?? ((...p) => p.join("/"));
  }

  /**
   * Mint a fresh EPHEMERAL profile bound to `workspaceId`. Its directory lives
   * under the ephemeral root and is discarded by `discardEphemeral` at close.
   * No manifest is persisted — ephemeral identities never survive a restart.
   */
  async newEphemeral(workspaceId: string): Promise<ProfileManifest> {
    const profileId = this.#randomId();
    const userDataDir = this.#join(this.#cfg.ephemeralRoot, profileId);
    await this.#cfg.fs.mkdir(userDataDir, { recursive: true, mode: DIR_MODE });
    const ts = this.#now();
    return {
      version: 1,
      profileId,
      workspaceId,
      mode: BrowserProfileMode.Ephemeral,
      browserVersion: this.#cfg.browserVersion,
      policyVersion: this.#cfg.policyVersion ?? 1,
      createdAt: ts,
      lastUsedAt: ts,
      userDataDir,
    };
  }

  /**
   * Create a PERSISTENT profile bound to exactly one workspace. Directory is
   * `0700`, derived from a random id, and a `manifest.json` records the binding.
   */
  async createPersistent(workspaceId: string): Promise<ProfileManifest> {
    const profileId = this.#randomId();
    const userDataDir = this.#join(this.#cfg.profilesRoot, profileId);
    await this.#cfg.fs.mkdir(userDataDir, { recursive: true, mode: DIR_MODE });
    const ts = this.#now();
    const manifest: ProfileManifest = {
      version: 1,
      profileId,
      workspaceId,
      mode: BrowserProfileMode.Persistent,
      browserVersion: this.#cfg.browserVersion,
      policyVersion: this.#cfg.policyVersion ?? 1,
      createdAt: ts,
      lastUsedAt: ts,
      userDataDir,
    };
    await this.#writeManifest(manifest);
    return manifest;
  }

  /** Load a persistent profile's manifest. */
  async load(profileId: string): Promise<ProfileManifest> {
    const path = this.#manifestPath(profileId);
    if (!(await this.#cfg.fs.exists(path))) {
      throw new ProfileError(
        BrowserErrorCode.Unavailable,
        "profile manifest not found",
      );
    }
    const raw = await this.#cfg.fs.readFile(path);
    const parsed = JSON.parse(raw) as ProfileManifest;
    return parsed;
  }

  /**
   * Acquire the single automation lease for a profile bound to `workspaceId`.
   * Denies a cross-workspace open (`browser_profile_busy` semantics differ from
   * a version mismatch): a persistent profile whose manifest names a different
   * workspace is rejected. A second concurrent acquire is `browser_profile_busy`.
   */
  async acquireLease(
    manifest: ProfileManifest,
    workspaceId: string,
  ): Promise<void> {
    if (manifest.workspaceId !== workspaceId) {
      throw new ProfileError(
        BrowserErrorCode.ProfileVersionMismatch,
        "profile is bound to a different workspace",
      );
    }
    if (manifest.browserVersion !== this.#cfg.browserVersion) {
      throw new ProfileError(
        BrowserErrorCode.ProfileVersionMismatch,
        "profile browser version is incompatible",
      );
    }
    if (this.#leases.has(manifest.profileId)) {
      throw new ProfileError(
        BrowserErrorCode.ProfileBusy,
        "profile already has an active lease",
      );
    }
    this.#leases.add(manifest.profileId);
  }

  releaseLease(profileId: string): void {
    this.#leases.delete(profileId);
  }

  hasLease(profileId: string): boolean {
    return this.#leases.has(profileId);
  }

  /** Remove an ephemeral profile's directory. Best-effort; never throws. */
  async discardEphemeral(manifest: ProfileManifest): Promise<void> {
    this.#leases.delete(manifest.profileId);
    try {
      await this.#cfg.fs.rm(manifest.userDataDir, {
        recursive: true,
        force: true,
      });
    } catch {
      // Best-effort cleanup; a leftover dir is swept on next restart.
    }
  }

  /**
   * Delete a persistent profile: release its lease and remove its directory.
   * Best-effort erase (OS snapshots/backups remain a deployment concern).
   */
  async deletePersistent(profileId: string): Promise<void> {
    this.#leases.delete(profileId);
    const dir = this.#join(this.#cfg.profilesRoot, profileId);
    await this.#cfg.fs.rm(dir, { recursive: true, force: true });
  }

  #manifestPath(profileId: string): string {
    return this.#join(this.#cfg.profilesRoot, profileId, "manifest.json");
  }

  async #writeManifest(manifest: ProfileManifest): Promise<void> {
    await this.#cfg.fs.writeFile(
      this.#manifestPath(manifest.profileId),
      JSON.stringify(manifest, null, 2),
      { mode: FILE_MODE },
    );
  }
}
