import { randomUUID } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { isAbsolute, join } from "node:path";

import type { SafeStorageLike } from "../auth/secret-storage";

import { assertGrantableRoot } from "./path-validation";
import type { Grant, GrantMode, GrantProvider, GrantSnapshot } from "./types";

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
}

export interface CreateGrantInput {
  /** Canonical, realpath-resolved absolute directory (from the picker). */
  readonly root: string;
  readonly mode: GrantMode;
  readonly label: string;
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

  #grants: Map<string, Grant> = new Map();
  #loaded = false;
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
  }

  async create(input: CreateGrantInput): Promise<Grant> {
    if (!isAbsolute(input.root)) {
      // The picker always hands us a realpath; guard against a caller that
      // bypasses it. Never echo the offending value (could be a host path).
      throw new Error("grant root must be an absolute path");
    }
    // G2: refuse a grant over the filesystem root, the home dir, the app's own
    // userData tree, or any well-known credential directory. This is the
    // authoritative choke point — a caller bypassing the native picker is still
    // blocked. Throws FsError('permission_denied') without echoing the path.
    assertGrantableRoot(input.root, {
      homeDir: this.#homeDir,
      userDataDir: this.#userDataDir,
    });
    await this.#ensureLoaded();
    const now = this.#clock();
    const grant: Grant = {
      grantId: this.#uuid(),
      root: input.root,
      mode: input.mode,
      label: input.label,
      status: "active",
      createdAt: now,
      updatedAt: now,
    };
    this.#grants.set(grant.grantId, grant);
    await this.#persist();
    return grant;
  }

  async list(): Promise<readonly Grant[]> {
    await this.#ensureLoaded();
    return [...this.#grants.values()];
  }

  async listActive(): Promise<readonly Grant[]> {
    await this.#ensureLoaded();
    return [...this.#grants.values()].filter((g) => g.status === "active");
  }

  async get(grantId: string): Promise<Grant | null> {
    await this.#ensureLoaded();
    return this.#grants.get(grantId) ?? null;
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

  // --- persistence ---

  async #ensureLoaded(): Promise<void> {
    if (this.#loaded) return;
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
    await mkdir(join(this.#path, ".."), { recursive: true });
    await writeFile(this.#path, blob, { mode: 0o600 });
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
  if (
    typeof grantId !== "string" ||
    typeof root !== "string" ||
    (mode !== "read_only" &&
      mode !== "read_write_no_delete" &&
      mode !== "read_write") ||
    typeof label !== "string" ||
    (status !== "active" && status !== "revoked") ||
    typeof createdAt !== "number" ||
    typeof updatedAt !== "number"
  ) {
    throw new Error("grant row has invalid fields");
  }
  return { grantId, root, mode, label, status, createdAt, updatedAt };
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
