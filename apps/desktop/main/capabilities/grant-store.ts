import { randomBytes, randomUUID } from "node:crypto";
import {
  mkdir,
  open as openFile,
  readFile,
  rename,
  unlink,
} from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, isAbsolute, join } from "node:path";

import type { SafeStorageLike } from "../auth/secret-storage";

import { assertGrantableRoot, normalizeVirtualPath } from "./path-validation";
import type {
  Grant,
  GrantMode,
  GrantProvider,
  GrantRootIdentity,
  GrantSnapshot,
} from "./types";

// Encrypted, main-owned persistence for host-folder grants (AC5 slice 1).
//
// Grants live in a SINGLE file under `<userData>/capabilities/grants.bin`,
// deliberately OUTSIDE the agent-data / session / postgres trees so a
// compromised run cannot rewrite the authority list it runs under. The whole
// collection is encrypted with Electron `safeStorage` (OS keychain), mirroring
// `SecretStorage` / `bootSecrets`: a cipher marker prefixes the blob and we
// refuse to write plaintext unless an explicit dev fallback is enabled.

const CIPHER_MARKER = "ATLASCAPv1:cipher:";
const PLAINTEXT_MARKER = "ATLASCAPv1:plaintext:";
const STORE_RELATIVE_PATH = ["capabilities", "grants.bin"] as const;

export interface GrantStoreAudit {
  warn(message: string, context?: Record<string, unknown>): void;
}

export interface GrantStoreConfig {
  readonly userDataDir: string;
  readonly safeStorage: SafeStorageLike;
  /** Dev-only: permit a plaintext (chmod-600) fallback when the OS keychain
   * is unavailable. Defaults to false — production fails closed. */
  readonly allowPlaintextFallback?: boolean;
  readonly audit?: GrantStoreAudit;
  /** User home dir for the sensitive-root policy. Defaults to os.homedir(). */
  readonly homeDir?: string;
  /** Injectable for tests. */
  readonly uuid?: () => string;
  /** Injectable for tests. */
  readonly clock?: () => number;
  /**
   * Electron-main native identity query. Without it, a newly created grant is
   * read-capable only; the v2 writer refuses a root without this identity.
   */
  readonly rootIdentity?: (root: string) => Promise<GrantRootIdentity>;
  /** Local signed-in profile/device facts recorded on newly issued grants. */
  readonly profileId?: string;
  /**
   * Main-only late resolver for the verified signed-in profile. The picker
   * never receives this result; renderer input cannot bind a writable grant.
   */
  readonly profileIdResolver?: () => Promise<string | null>;
  readonly deviceId?: string;
  /** Default expiry for newly created grants. Defaults to thirty days. */
  readonly grantTtlMs?: number;
}

export interface CreateGrantInput {
  /** Canonical, realpath-resolved absolute directory (from the picker). */
  readonly root: string;
  readonly mode: GrantMode;
  readonly label: string;
  /** Optional root-relative restrictions; all entries are checked by main. */
  readonly allowedPathPrefixes?: readonly string[];
  readonly expiresAt?: number;
}

interface PersistedShape {
  readonly version: 1;
  readonly grants: readonly Grant[];
}

export class GrantStore implements GrantProvider {
  readonly #path: string;
  readonly #userDataDir: string;
  readonly #homeDir: string;
  readonly #safeStorage: SafeStorageLike;
  readonly #allowPlaintext: boolean;
  readonly #audit: GrantStoreAudit;
  readonly #uuid: () => string;
  readonly #clock: () => number;
  readonly #rootIdentity:
    | ((root: string) => Promise<GrantRootIdentity>)
    | undefined;
  readonly #profileId: string | undefined;
  readonly #profileIdResolver: (() => Promise<string | null>) | undefined;
  readonly #deviceId: string | undefined;
  readonly #grantTtlMs: number;

  #grants: Map<string, Grant> = new Map();
  #loaded = false;
  /** The single in-flight cold load; see `#ensureLoaded`. */
  #loading: Promise<void> | null = null;
  #plaintextWarned = false;

  constructor(config: GrantStoreConfig) {
    this.#userDataDir = config.userDataDir;
    this.#path = join(config.userDataDir, ...STORE_RELATIVE_PATH);
    this.#homeDir = config.homeDir ?? homedir();
    this.#safeStorage = config.safeStorage;
    this.#allowPlaintext = config.allowPlaintextFallback ?? false;
    this.#audit = config.audit ?? defaultAudit();
    this.#uuid = config.uuid ?? randomUUID;
    this.#clock = config.clock ?? Date.now;
    this.#rootIdentity = config.rootIdentity;
    this.#profileId = config.profileId;
    this.#profileIdResolver = config.profileIdResolver;
    this.#deviceId = config.deviceId;
    this.#grantTtlMs = config.grantTtlMs ?? 30 * 24 * 60 * 60 * 1000;
  }

  /**
   * Apply the grant policy to a candidate root, creating nothing.
   *
   * The policy is `assertGrantableRoot`; this method exists only so a caller
   * can ask the question EARLY without also having to own the home/userData
   * context. The named-folder lane uses it to refuse a class before touching
   * the disk: probing `/etc` first makes the refusal a story about whether the
   * folder exists rather than about whether it may ever be shared, and it
   * performs a filesystem lookup on behalf of a path we had already decided to
   * refuse.
   *
   * `create` calls it again on the resolved root. That is not duplication of
   * the DECISION — it is the same pure function at the choke point, so a future
   * caller that skips this pre-check is still blocked.
   */
  assertGrantable(root: string): void {
    assertGrantableRoot(root, {
      homeDir: this.#homeDir,
      userDataDir: this.#userDataDir,
    });
  }

  async create(input: CreateGrantInput): Promise<Grant> {
    if (!isAbsolute(input.root)) {
      // The picker always hands us a realpath; guard against a caller that
      // bypasses it. Never echo the offending value (could be a host path).
      throw new Error("grant root must be an absolute path");
    }
    // G2: refuse a grant over a location no folder grant may cover — see
    // `classifyForbiddenRoot`. This is the authoritative choke point; a caller
    // bypassing the native picker is still blocked. Throws
    // FsError('permission_denied') without echoing the path.
    this.assertGrantable(input.root);
    await this.#ensureLoaded();
    const now = this.#clock();
    const rootIdentity =
      this.#rootIdentity === undefined
        ? undefined
        : await this.#rootIdentity(input.root);
    const profileId = await this.#resolveProfileId();
    const grant: Grant = {
      grantId: this.#uuid(),
      root: input.root,
      mode: input.mode,
      label: input.label,
      status: "active",
      createdAt: now,
      updatedAt: now,
      rootIdentity,
      profileId,
      deviceId: this.#deviceId,
      allowedPathPrefixes: normalizePrefixes(input.allowedPathPrefixes),
      expiresAt: input.expiresAt ?? now + this.#grantTtlMs,
    };
    this.#supersedeSameRoot(input.root, now);
    this.#grants.set(grant.grantId, grant);
    await this.#persist();
    return grant;
  }

  /**
   * ONE FOLDER, ONE AUTHORITY. Re-picking a folder that is already attached
   * retires the previous grant instead of adding a second one for the same
   * tree.
   *
   * Accumulating them broke the only promise this subsystem exists to keep.
   * Two live grants over one root are indistinguishable to every surface that
   * shows them (same label, and the renderer projection deliberately carries no
   * path), so the user sees two identical pills — and dismissing either one
   * revokes a grant while the folder stays fully readable through the other. A
   * dismissed pill that does not remove access is exactly the class of lie the
   * grant model is here to eliminate. It also made the effective mode of a tree
   * "whichever grant the caller happens to name" rather than the user's latest
   * choice.
   *
   * Superseding rather than mutating in place keeps the trail auditable (the
   * old grant is retired at a known time, the new one is issued with the new
   * mode) and leaves any run that PINNED the old grant untouched — run contexts
   * hold their own frozen copies.
   */
  #supersedeSameRoot(root: string, now: number): void {
    for (const existing of this.#grants.values()) {
      if (existing.root !== root || !isLive(existing, now)) continue;
      this.#grants.set(existing.grantId, {
        ...existing,
        status: "revoked",
        updatedAt: now,
      });
    }
  }

  /**
   * Every grant on record, projected AS OF NOW: a grant past its expiry is
   * reported `revoked`, because that is what it is for every authority check
   * (see `Grant.expiresAt`). Reporting the stored literal instead left an
   * expired grant looking active in the renderer's folder list and on
   * `/v1/grants/list` while `snapshotActive` — the projection every read is
   * actually authorized against — had already dropped it. The user saw a pill
   * for a folder that answered `grant_required`, with nothing to explain it.
   * `expiresAt` still rides along, so an auditor can tell expiry from revoke.
   */
  async list(): Promise<readonly Grant[]> {
    await this.#ensureLoaded();
    const now = this.#clock();
    return [...this.#grants.values()].map((g) => asOf(g, now));
  }

  async listActive(): Promise<readonly Grant[]> {
    await this.#ensureLoaded();
    const now = this.#clock();
    return [...this.#grants.values()].filter((g) => isLive(g, now));
  }

  async get(grantId: string): Promise<Grant | null> {
    await this.#ensureLoaded();
    const grant = this.#grants.get(grantId);
    return grant === undefined ? null : asOf(grant, this.#clock());
  }

  /**
   * Revoke a grant. Idempotent: revoking a missing grant returns null;
   * revoking an already-revoked grant returns it unchanged. Revocation removes
   * future authority immediately — the next snapshot will not include it.
   */
  async revoke(grantId: string): Promise<Grant | null> {
    await this.#ensureLoaded();
    const existing = this.#grants.get(grantId);
    if (existing === undefined) return null;
    if (existing.status === "revoked") return existing;
    const revoked: Grant = {
      ...existing,
      status: "revoked",
      updatedAt: this.#clock(),
    };
    this.#grants.set(grantId, revoked);
    await this.#persist();
    return revoked;
  }

  // --- GrantProvider (broker read-side) ---

  async listAll(): Promise<readonly Grant[]> {
    return this.list();
  }

  async snapshotActive(): Promise<GrantSnapshot> {
    const active = await this.listActive();
    return Object.freeze({
      snapshotId: this.#uuid(),
      capturedAt: this.#clock(),
      grants: Object.freeze(active.map((g) => Object.freeze({ ...g }))),
    });
  }

  async #resolveProfileId(): Promise<string | undefined> {
    if (this.#profileIdResolver === undefined) return this.#profileId;
    try {
      const profileId = await this.#profileIdResolver();
      return typeof profileId === "string" && profileId.trim() !== ""
        ? profileId
        : undefined;
    } catch {
      // A transient facade/auth failure must never let a renderer supply a
      // replacement identity. The resulting unbound grant is read-only to C2.
      return undefined;
    }
  }

  // --- persistence ---

  /**
   * Load the grant file at most once, and at most once CONCURRENTLY.
   *
   * The read is awaited, so without memoization two callers that both arrive
   * cold each start their own read and each assign `#grants` when it settles.
   * If the slower read settles after the faster caller has already created and
   * persisted a grant, the stale decode REPLACES the map and the next
   * `#persist()` writes the clobbered set to disk: the folder the user just
   * attached vanishes, after the UI has already been handed the grant. The same
   * interleaving can resurrect a revoked grant in memory.
   *
   * Sharing one in-flight promise makes the assignment happen exactly once,
   * before any caller proceeds, so no caller can mutate the map ahead of it. A
   * failed read is not cached — the next call retries rather than inheriting a
   * rejection forever.
   */
  async #ensureLoaded(): Promise<void> {
    if (this.#loaded) return;
    this.#loading ??= this.#load().catch((error: unknown) => {
      this.#loading = null;
      throw error;
    });
    await this.#loading;
  }

  async #load(): Promise<void> {
    let raw: Buffer;
    try {
      raw = await readFile(this.#path);
    } catch (err) {
      if (isEnoent(err)) {
        this.#loaded = true;
        return;
      }
      throw err;
    }
    const decoded = this.#decode(raw);
    this.#grants = new Map(decoded.map((g) => [g.grantId, g]));
    this.#loaded = true;
  }

  async #persist(): Promise<void> {
    const payload: PersistedShape = {
      version: 1,
      grants: [...this.#grants.values()],
    };
    const blob = this.#encode(payload);
    const directory = dirname(this.#path);
    await mkdir(directory, { recursive: true, mode: 0o700 });
    // A grant file is an authority list.  A plain write can leave an empty or
    // partial list after power loss, so persist through temp -> file fsync ->
    // atomic rename -> directory fsync.  The temporary name is unguessable and
    // created with O_EXCL; no plaintext ever touches disk in production.
    const temporary = join(
      directory,
      `.grants-${randomBytes(16).toString("hex")}.tmp`,
    );
    let handle: Awaited<ReturnType<typeof openFile>> | undefined;
    try {
      handle = await openFile(temporary, "wx", 0o600);
      await handle.writeFile(blob);
      await handle.sync();
      await handle.close();
      handle = undefined;
      await rename(temporary, this.#path);
      const directoryHandle = await openFile(directory, "r");
      try {
        await directoryHandle.sync();
      } finally {
        await directoryHandle.close();
      }
    } catch (error) {
      await handle?.close().catch(() => {});
      await unlink(temporary).catch(() => {});
      throw error;
    }
  }

  #encode(payload: PersistedShape): Buffer {
    const plaintext = JSON.stringify(payload);
    if (this.#safeStorage.isEncryptionAvailable()) {
      return Buffer.concat([
        Buffer.from(CIPHER_MARKER, "utf-8"),
        this.#safeStorage.encryptString(plaintext),
      ]);
    }
    if (!this.#allowPlaintext) {
      throw new Error(
        "safeStorage unavailable; refusing to write plaintext grant store",
      );
    }
    if (!this.#plaintextWarned) {
      this.#plaintextWarned = true;
      this.#audit.warn(
        "grant-store: safeStorage unavailable; falling back to plaintext (dev only)",
      );
    }
    return Buffer.from(PLAINTEXT_MARKER + plaintext, "utf-8");
  }

  #decode(raw: Buffer): readonly Grant[] {
    let plaintext: string;
    if (startsWith(raw, CIPHER_MARKER)) {
      const cipher = raw.subarray(Buffer.byteLength(CIPHER_MARKER));
      plaintext = this.#safeStorage.decryptString(cipher);
    } else if (startsWith(raw, PLAINTEXT_MARKER)) {
      if (!this.#allowPlaintext) {
        throw new Error(
          "plaintext grant store on disk but plaintext fallback is disabled",
        );
      }
      plaintext = raw
        .subarray(Buffer.byteLength(PLAINTEXT_MARKER))
        .toString("utf-8");
    } else {
      throw new Error("unknown grant-store format");
    }
    const parsed = JSON.parse(plaintext) as unknown;
    return normalizeGrants(parsed);
  }
}

/** Whether `grant` is authority at `now` — the ONE definition of "live". */
function isLive(grant: Grant, now: number): boolean {
  return (
    grant.status === "active" &&
    (grant.expiresAt === undefined || grant.expiresAt > now)
  );
}

/** `grant` as it stands at `now`: past its expiry it reports as revoked. */
function asOf(grant: Grant, now: number): Grant {
  if (grant.status !== "active" || isLive(grant, now)) return grant;
  return { ...grant, status: "revoked" };
}

function normalizeGrants(parsed: unknown): readonly Grant[] {
  if (
    typeof parsed !== "object" ||
    parsed === null ||
    !Array.isArray((parsed as { grants?: unknown }).grants)
  ) {
    throw new Error("grant store JSON is malformed");
  }
  const rows = (parsed as { grants: unknown[] }).grants;
  return rows.map((row) => coerceGrant(row));
}

function coerceGrant(row: unknown): Grant {
  if (typeof row !== "object" || row === null) {
    throw new Error("grant row is not an object");
  }
  const r = row as Record<string, unknown>;
  const grantId = r.grantId;
  const root = r.root;
  const mode = r.mode;
  const label = r.label;
  const status = r.status;
  const createdAt = r.createdAt;
  const updatedAt = r.updatedAt;
  const rootIdentity = r.rootIdentity;
  const profileId = r.profileId;
  const deviceId = r.deviceId;
  const allowedPathPrefixes = r.allowedPathPrefixes;
  const expiresAt = r.expiresAt;
  if (
    typeof grantId !== "string" ||
    typeof root !== "string" ||
    (mode !== "read_only" &&
      mode !== "read_write_no_delete" &&
      mode !== "read_write") ||
    typeof label !== "string" ||
    (status !== "active" && status !== "revoked") ||
    typeof createdAt !== "number" ||
    typeof updatedAt !== "number" ||
    (rootIdentity !== undefined && !isRootIdentity(rootIdentity)) ||
    (profileId !== undefined && typeof profileId !== "string") ||
    (deviceId !== undefined && typeof deviceId !== "string") ||
    (allowedPathPrefixes !== undefined &&
      (!Array.isArray(allowedPathPrefixes) ||
        allowedPathPrefixes.some((value) => typeof value !== "string"))) ||
    (expiresAt !== undefined && typeof expiresAt !== "number")
  ) {
    throw new Error("grant row has invalid fields");
  }
  return {
    grantId,
    root,
    mode,
    label,
    status,
    createdAt,
    updatedAt,
    rootIdentity: rootIdentity as GrantRootIdentity | undefined,
    profileId: profileId as string | undefined,
    deviceId: deviceId as string | undefined,
    allowedPathPrefixes:
      allowedPathPrefixes === undefined
        ? undefined
        : Object.freeze([...allowedPathPrefixes] as string[]),
    expiresAt: expiresAt as number | undefined,
  };
}

function isRootIdentity(value: unknown): value is GrantRootIdentity {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as { volumeId?: unknown }).volumeId === "string" &&
    typeof (value as { fileId?: unknown }).fileId === "string"
  );
}

function normalizePrefixes(
  value: readonly string[] | undefined,
): readonly string[] {
  if (value === undefined) return Object.freeze([""]);
  const unique = new Set<string>();
  for (const prefix of value) {
    if (typeof prefix !== "string" || prefix.includes("\u0000")) {
      throw new Error("grant path prefix is invalid");
    }
    const trimmed = prefix.replace(/^[/\\]+/u, "").replace(/[/\\]+$/u, "");
    if (trimmed === "") {
      unique.add("");
      continue;
    }
    try {
      // Prefixes become an authorization boundary below the root, so they use
      // the exact same virtual-path grammar as an untrusted workspace entry.
      unique.add(normalizeVirtualPath(trimmed).join("/"));
    } catch (error) {
      if (error instanceof Error) {
        throw new Error("grant path prefix is invalid");
      }
      throw error;
    }
  }
  return Object.freeze([...unique].sort());
}

function startsWith(raw: Buffer, marker: string): boolean {
  const markerBuf = Buffer.from(marker, "utf-8");
  if (raw.length < markerBuf.length) return false;
  return raw.subarray(0, markerBuf.length).equals(markerBuf);
}

function isEnoent(err: unknown): boolean {
  return (
    typeof err === "object" &&
    err !== null &&
    "code" in err &&
    (err as { code: string }).code === "ENOENT"
  );
}

function defaultAudit(): GrantStoreAudit {
  return {
    warn: (msg, ctx) => {
      console.warn(`[grant-store] ${msg}`, ctx ?? "");
    },
  };
}
