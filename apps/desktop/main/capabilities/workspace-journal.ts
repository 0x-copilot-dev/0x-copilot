import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import {
  mkdir,
  open as openFile,
  readFile,
  rename,
  unlink,
} from "node:fs/promises";
import { dirname, join } from "node:path";

import type { SafeStorageLike } from "../auth/secret-storage";

import type {
  WorkspaceJournalRecord,
  WorkspaceJournalStore,
} from "./workspace-authority";

const CIPHER_MARKER = "COPILOT_WORKSPACE_JOURNAL_V1:cipher:";
const PLAINTEXT_MARKER = "COPILOT_WORKSPACE_JOURNAL_V1:plaintext:";
const JOURNAL_PATH = ["capabilities", "workspace-journal.bin"] as const;

interface PersistedJournal {
  readonly version: 1;
  readonly records: readonly WorkspaceJournalRecord[];
}

export interface EncryptedWorkspaceJournalConfig {
  readonly userDataDir: string;
  readonly safeStorage: SafeStorageLike;
  /** Per-installation secret, retained only in main-owned secure storage. */
  readonly integrityKey: Buffer;
  /** Explicit dev-only fallback; production must leave this false. */
  readonly allowPlaintextFallback?: boolean;
}

/**
 * Local encrypted journal for prepare/authorize/commit/recovery. The store is
 * not an audit export: its opaque references let Electron main recover native
 * state, while public receipts contain only sanctioned ledger references.
 */
export class EncryptedWorkspaceJournalStore implements WorkspaceJournalStore {
  readonly #path: string;
  readonly #safeStorage: SafeStorageLike;
  readonly #integrityKey: Buffer;
  readonly #allowPlaintext: boolean;
  #records = new Map<string, WorkspaceJournalRecord>();
  #loaded = false;
  #writeTail: Promise<void> = Promise.resolve();

  constructor(config: EncryptedWorkspaceJournalConfig) {
    if (config.integrityKey.byteLength < 32) {
      throw new Error("workspace journal integrity key is too short");
    }
    this.#path = join(config.userDataDir, ...JOURNAL_PATH);
    this.#safeStorage = config.safeStorage;
    this.#integrityKey = Buffer.from(config.integrityKey);
    this.#allowPlaintext = config.allowPlaintextFallback ?? false;
  }

  async get(preparedRef: string): Promise<WorkspaceJournalRecord | null> {
    await this.#ensureLoaded();
    return this.#records.get(preparedRef) ?? null;
  }

  async put(record: WorkspaceJournalRecord): Promise<void> {
    await this.#ensureLoaded();
    this.#writeTail = this.#writeTail.then(async () => {
      this.#records.set(record.preparedRef, Object.freeze({ ...record }));
      await this.#persist();
    });
    return this.#writeTail;
  }

  async listNonterminal(): Promise<readonly WorkspaceJournalRecord[]> {
    await this.#ensureLoaded();
    return [...this.#records.values()].filter(
      (record) =>
        record.state !== "applied" &&
        record.state !== "failed_before_effect" &&
        record.state !== "rolled_back" &&
        record.state !== "recovery_conflict",
    );
  }

  async #ensureLoaded(): Promise<void> {
    if (this.#loaded) return;
    let raw: Buffer;
    try {
      raw = await readFile(this.#path);
    } catch (error) {
      if (isEnoent(error)) {
        this.#loaded = true;
        return;
      }
      throw error;
    }
    const decoded = this.#decode(raw);
    this.#records = new Map(
      decoded.records.map((record) => [record.preparedRef, record]),
    );
    this.#loaded = true;
  }

  async #persist(): Promise<void> {
    const payload: PersistedJournal = {
      version: 1,
      records: [...this.#records.values()],
    };
    const blob = this.#encode(payload);
    const directory = dirname(this.#path);
    await mkdir(directory, { recursive: true, mode: 0o700 });
    const temporary = join(
      directory,
      `.workspace-journal-${randomBytes(16).toString("hex")}.tmp`,
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

  #encode(payload: PersistedJournal): Buffer {
    const clear = Buffer.from(JSON.stringify(payload), "utf8");
    const encryptionAvailable = this.#safeStorage.isEncryptionAvailable();
    const encrypted = encryptionAvailable
      ? this.#safeStorage.encryptString(clear.toString("utf8"))
      : this.#plaintextFallback(clear);
    const marker = encryptionAvailable ? CIPHER_MARKER : PLAINTEXT_MARKER;
    const mac = this.#mac(encrypted);
    return Buffer.from(
      `${marker}${mac}:${encrypted.toString("base64")}`,
      "utf8",
    );
  }

  #decode(raw: Buffer): PersistedJournal {
    const text = raw.toString("utf8");
    const marker = text.startsWith(CIPHER_MARKER)
      ? CIPHER_MARKER
      : text.startsWith(PLAINTEXT_MARKER)
        ? PLAINTEXT_MARKER
        : null;
    if (marker === null) throw new Error("workspace journal format is invalid");
    if (marker === PLAINTEXT_MARKER && !this.#allowPlaintext) {
      throw new Error("workspace journal plaintext fallback is disabled");
    }
    const [mac, encoded] = text.slice(marker.length).split(":", 2);
    if (mac === undefined || encoded === undefined) {
      throw new Error("workspace journal integrity record is invalid");
    }
    const payload = Buffer.from(encoded, "base64");
    if (!this.#matchesMac(payload, mac)) {
      throw new Error("workspace journal integrity verification failed");
    }
    const clear =
      marker === CIPHER_MARKER
        ? this.#safeStorage.decryptString(payload)
        : payload.toString("utf8");
    return validatePersistedJournal(JSON.parse(clear) as unknown);
  }

  #plaintextFallback(clear: Buffer): Buffer {
    if (!this.#allowPlaintext) {
      throw new Error(
        "safeStorage unavailable; refusing plaintext workspace journal",
      );
    }
    return clear;
  }

  #mac(value: Buffer): string {
    return createHmac("sha256", this.#integrityKey).update(value).digest("hex");
  }

  #matchesMac(value: Buffer, expected: string): boolean {
    const actual = Buffer.from(this.#mac(value), "hex");
    const candidate = Buffer.from(expected, "hex");
    return (
      actual.byteLength === candidate.byteLength &&
      timingSafeEqual(actual, candidate)
    );
  }
}

function validatePersistedJournal(value: unknown): PersistedJournal {
  if (
    typeof value !== "object" ||
    value === null ||
    (value as { version?: unknown }).version !== 1 ||
    !Array.isArray((value as { records?: unknown }).records)
  ) {
    throw new Error("workspace journal payload is invalid");
  }
  const records = (value as { records: unknown[] }).records.map((record) => {
    if (!isJournalRecord(record))
      throw new Error("workspace journal record is invalid");
    return Object.freeze({ ...record });
  });
  return { version: 1, records: Object.freeze(records) };
}

function isJournalRecord(value: unknown): value is WorkspaceJournalRecord {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  return (
    typeof record.preparedRef === "string" &&
    typeof record.state === "string" &&
    typeof record.runId === "string" &&
    typeof record.userId === "string" &&
    typeof record.deviceId === "string" &&
    typeof record.stageId === "string" &&
    typeof record.revision === "number" &&
    typeof record.decisionLedgerId === "string" &&
    Array.isArray(record.pathTokens) &&
    record.pathTokens.every((token) => typeof token === "string") &&
    typeof record.changeSetDigest === "string" &&
    typeof record.targetDigest === "string" &&
    typeof record.proposalDigest === "string" &&
    typeof record.createdAt === "number" &&
    typeof record.updatedAt === "number"
  );
}

function isEnoent(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    (error as { code?: unknown }).code === "ENOENT"
  );
}
