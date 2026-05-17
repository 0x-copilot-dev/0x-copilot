import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { createHash } from "node:crypto";

export type ServerKind = "backend" | "mcp" | "saas";

export interface SafeStorageLike {
  isEncryptionAvailable(): boolean;
  encryptString(plaintext: string): Buffer;
  decryptString(ciphertext: Buffer): string;
}

export interface SecretAuditLog {
  warn(message: string, context?: Record<string, unknown>): void;
}

export interface SecretStorageConfig {
  readonly userDataDir: string;
  readonly safeStorage: SafeStorageLike;
  readonly allowPlaintextFallback?: boolean;
  readonly audit?: SecretAuditLog;
}

const SERVER_KINDS: ReadonlySet<ServerKind> = new Set([
  "backend",
  "mcp",
  "saas",
]);
const PLAINTEXT_MARKER = "ATLASv1:plaintext:";
const CIPHER_MARKER = "ATLASv1:cipher:";

// Per-(workspace_id, server_kind, server_id) ciphertext storage. The
// active-workspace gate (PRD §6.7) is enforced on every read so that a
// compromised renderer cannot request workspace B's secrets while the
// session is bound to workspace A. The gate fires BEFORE any decrypt
// attempt; never touch the file when the gate rejects.
export class SecretStorage {
  readonly #root: string;
  readonly #safeStorage: SafeStorageLike;
  readonly #allowPlaintext: boolean;
  readonly #audit: SecretAuditLog;
  #activeWorkspace: string | null = null;
  #plaintextWarned = false;

  constructor(config: SecretStorageConfig) {
    this.#root = join(config.userDataDir, "secrets");
    this.#safeStorage = config.safeStorage;
    this.#allowPlaintext = config.allowPlaintextFallback ?? false;
    this.#audit = config.audit ?? defaultAudit();
  }

  setActiveWorkspace(workspaceId: string | null): void {
    this.#activeWorkspace = workspaceId;
  }

  getActiveWorkspace(): string | null {
    return this.#activeWorkspace;
  }

  async get(
    workspaceId: string,
    serverKind: ServerKind,
    serverId: string,
  ): Promise<unknown | null> {
    assertWorkspaceId(workspaceId);
    assertServerKind(serverKind);
    assertServerId(serverId);
    if (!this.#gateAllows(workspaceId)) {
      this.#audit.warn("secret-storage: active-workspace gate rejected read", {
        requested: workspaceId,
        active: this.#activeWorkspace,
        serverKind,
      });
      return null;
    }
    const path = this.#path(workspaceId, serverKind, serverId);
    let raw: Buffer;
    try {
      raw = await readFile(path);
    } catch (err) {
      if (isEnoent(err)) return null;
      throw err;
    }
    return this.#decode(raw);
  }

  async set(
    workspaceId: string,
    serverKind: ServerKind,
    serverId: string,
    payload: unknown,
  ): Promise<void> {
    assertWorkspaceId(workspaceId);
    assertServerKind(serverKind);
    assertServerId(serverId);
    if (!this.#gateAllows(workspaceId)) {
      this.#audit.warn("secret-storage: active-workspace gate rejected write", {
        requested: workspaceId,
        active: this.#activeWorkspace,
        serverKind,
      });
      throw new Error("active-workspace gate rejected write");
    }
    const dir = join(this.#root, workspaceId, serverKind);
    await mkdir(dir, { recursive: true });
    const path = this.#path(workspaceId, serverKind, serverId);
    const encoded = this.#encode(payload);
    await writeFile(path, encoded);
  }

  async delete(
    workspaceId: string,
    serverKind: ServerKind,
    serverId: string,
  ): Promise<void> {
    assertWorkspaceId(workspaceId);
    assertServerKind(serverKind);
    assertServerId(serverId);
    if (!this.#gateAllows(workspaceId)) {
      this.#audit.warn(
        "secret-storage: active-workspace gate rejected delete",
        {
          requested: workspaceId,
          active: this.#activeWorkspace,
        },
      );
      throw new Error("active-workspace gate rejected delete");
    }
    const path = this.#path(workspaceId, serverKind, serverId);
    await rm(path, { force: true });
  }

  async deleteWorkspaceSecrets(workspaceId: string): Promise<void> {
    assertWorkspaceId(workspaceId);
    const dir = join(this.#root, workspaceId);
    await rm(dir, { recursive: true, force: true });
  }

  #gateAllows(workspaceId: string): boolean {
    if (this.#activeWorkspace === null) return false;
    return this.#activeWorkspace === workspaceId;
  }

  #path(workspaceId: string, serverKind: ServerKind, serverId: string): string {
    const safeId = createHash("sha256")
      .update(serverId)
      .digest("hex")
      .slice(0, 32);
    return join(this.#root, workspaceId, serverKind, `${safeId}.bin`);
  }

  #encode(payload: unknown): Buffer {
    const plaintext = JSON.stringify(payload);
    if (this.#safeStorage.isEncryptionAvailable()) {
      const cipher = this.#safeStorage.encryptString(plaintext);
      return Buffer.concat([Buffer.from(CIPHER_MARKER, "utf-8"), cipher]);
    }
    if (!this.#allowPlaintext) {
      throw new Error(
        "safeStorage not available; refusing to write plaintext token (PRD §6.7)",
      );
    }
    if (!this.#plaintextWarned) {
      this.#plaintextWarned = true;
      this.#audit.warn(
        "secret-storage: safeStorage unavailable; falling back to plaintext (dev only)",
      );
    }
    return Buffer.from(PLAINTEXT_MARKER + plaintext, "utf-8");
  }

  #decode(raw: Buffer): unknown {
    if (startsWith(raw, CIPHER_MARKER)) {
      const cipher = raw.subarray(Buffer.byteLength(CIPHER_MARKER));
      const plaintext = this.#safeStorage.decryptString(cipher);
      return JSON.parse(plaintext);
    }
    if (startsWith(raw, PLAINTEXT_MARKER)) {
      if (!this.#allowPlaintext) {
        throw new Error(
          "plaintext secret on disk but plaintext fallback is disabled",
        );
      }
      const plaintext = raw
        .subarray(Buffer.byteLength(PLAINTEXT_MARKER))
        .toString("utf-8");
      return JSON.parse(plaintext);
    }
    throw new Error("unknown secret format");
  }
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

function assertWorkspaceId(id: string): void {
  if (id.length === 0 || /[/\\.\0]/u.test(id)) {
    throw new Error("invalid workspaceId");
  }
}

function assertServerKind(kind: string): asserts kind is ServerKind {
  if (!SERVER_KINDS.has(kind as ServerKind)) {
    throw new Error(`invalid serverKind: ${kind}`);
  }
}

function assertServerId(id: string): void {
  if (id.length === 0) {
    throw new Error("invalid serverId");
  }
}

function defaultAudit(): SecretAuditLog {
  return {
    warn: (msg, ctx) => {
      console.warn(`[secret-storage] ${msg}`, ctx ?? "");
    },
  };
}
