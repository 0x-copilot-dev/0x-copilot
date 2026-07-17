import { randomBytes as nodeRandomBytes } from "node:crypto";
import { dirname, join } from "node:path";

import type { SafeStorageLike } from "../auth/secret-storage";

// The four secrets every supervised boot needs. Generated ONCE on first
// boot and persisted; every later boot reuses the same values (the postgres
// password in particular cannot change without losing access to the data
// directory, and rotating ENTERPRISE_AUTH_SECRET invalidates sessions).
export interface BootSecrets {
  /** ENTERPRISE_AUTH_SECRET — 64 random bytes, hex encoded (128 chars). */
  readonly authSecret: string;
  /** ENTERPRISE_SERVICE_TOKEN — 48 random bytes, base64url. */
  readonly serviceToken: string;
  /** MCP_TOKEN_VAULT_SECRET — 48 random bytes, base64url. */
  readonly vaultSecret: string;
  /** Postgres superuser (atlas) password — 32 random bytes, base64url. */
  readonly pgPassword: string;
}

// Thrown when the persisted blob exists but cannot be decrypted/parsed.
// The supervisor surfaces this on the fatal boot screen. We must NEVER
// regenerate silently: a fresh pgPassword would lock us out of the
// existing database and a fresh authSecret invalidates every session.
export class BootSecretsUnreadable extends Error {
  constructor(reason: string) {
    super(
      `Boot secrets exist on disk but could not be read (${reason}). ` +
        `Refusing to regenerate: doing so would orphan the local database. ` +
        `If the OS keychain changed, restore it; as a last resort delete ` +
        `the app's data directory to start fresh.`,
    );
    this.name = "BootSecretsUnreadable";
  }
}

const CIPHER_MARKER = "ATLASBOOTv1:cipher:";
const PLAINTEXT_MARKER = "ATLASBOOTv1:plaintext:";
const BLOB_RELATIVE_PATH = ["secrets", "boot-env.bin"] as const;

export interface BootSecretsFs {
  readFile(path: string): Promise<Buffer>;
  writeFile(
    path: string,
    data: Buffer,
    options?: { mode?: number },
  ): Promise<void>;
  mkdir(path: string, options: { recursive: boolean }): Promise<unknown>;
  chmod(path: string, mode: number): Promise<void>;
}

export interface BootSecretsConfig {
  readonly userDataDir: string;
  readonly safeStorage: SafeStorageLike;
  readonly fs: BootSecretsFs;
  readonly randomBytes?: (size: number) => Buffer;
}

export function bootSecretsPath(userDataDir: string): string {
  return join(userDataDir, ...BLOB_RELATIVE_PATH);
}

export async function loadOrCreateBootSecrets(
  config: BootSecretsConfig,
): Promise<BootSecrets> {
  const path = bootSecretsPath(config.userDataDir);
  let raw: Buffer | null = null;
  try {
    raw = await config.fs.readFile(path);
  } catch (err) {
    if (!isEnoent(err)) throw err;
  }
  if (raw !== null) {
    return decode(raw, config.safeStorage);
  }

  const random = config.randomBytes ?? nodeRandomBytes;
  const secrets: BootSecrets = {
    authSecret: random(64).toString("hex"),
    serviceToken: random(48).toString("base64url"),
    vaultSecret: random(48).toString("base64url"),
    pgPassword: random(32).toString("base64url"),
  };
  await persist(path, secrets, config);
  return secrets;
}

async function persist(
  path: string,
  secrets: BootSecrets,
  config: BootSecretsConfig,
): Promise<void> {
  const plaintext = JSON.stringify({ version: 1, ...secrets });
  let blob: Buffer;
  if (config.safeStorage.isEncryptionAvailable()) {
    blob = Buffer.concat([
      Buffer.from(CIPHER_MARKER, "utf-8"),
      config.safeStorage.encryptString(plaintext),
    ]);
  } else {
    // chmod-600 JSON fallback (headless linux / keychain unavailable).
    blob = Buffer.from(PLAINTEXT_MARKER + plaintext, "utf-8");
  }
  await config.fs.mkdir(dirname(path), { recursive: true });
  await config.fs.writeFile(path, blob, { mode: 0o600 });
  // writeFile mode is ignored when the file pre-exists; enforce anyway.
  await config.fs.chmod(path, 0o600);
}

function decode(raw: Buffer, safeStorage: SafeStorageLike): BootSecrets {
  let plaintext: string;
  if (startsWith(raw, CIPHER_MARKER)) {
    if (!safeStorage.isEncryptionAvailable()) {
      throw new BootSecretsUnreadable(
        "blob is encrypted but OS safeStorage is unavailable",
      );
    }
    const cipher = raw.subarray(Buffer.byteLength(CIPHER_MARKER));
    try {
      plaintext = safeStorage.decryptString(cipher);
    } catch (err) {
      throw new BootSecretsUnreadable(
        `safeStorage decryption failed: ${
          err instanceof Error ? err.message : String(err)
        }`,
      );
    }
  } else if (startsWith(raw, PLAINTEXT_MARKER)) {
    plaintext = raw
      .subarray(Buffer.byteLength(PLAINTEXT_MARKER))
      .toString("utf-8");
  } else {
    throw new BootSecretsUnreadable("unknown blob format");
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(plaintext);
  } catch {
    throw new BootSecretsUnreadable("blob decrypted to invalid JSON");
  }
  if (typeof parsed !== "object" || parsed === null) {
    throw new BootSecretsUnreadable("blob JSON is not an object");
  }
  const rec = parsed as Record<string, unknown>;
  const fields = [
    "authSecret",
    "serviceToken",
    "vaultSecret",
    "pgPassword",
  ] as const;
  for (const field of fields) {
    const value = rec[field];
    if (typeof value !== "string" || value.length === 0) {
      throw new BootSecretsUnreadable(`missing or empty field "${field}"`);
    }
  }
  return {
    authSecret: rec.authSecret as string,
    serviceToken: rec.serviceToken as string,
    vaultSecret: rec.vaultSecret as string,
    pgPassword: rec.pgPassword as string,
  };
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
