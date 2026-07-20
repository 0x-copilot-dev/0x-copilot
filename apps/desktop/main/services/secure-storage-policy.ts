// Secure-storage policy — the user-facing "Protect secrets with macOS
// Keychain" setting (Settings → Key storage & app lock).
//
// Default is `"file"`: boot secrets, auth sessions, and capability grants are
// persisted chmod-600 on disk with NO OS-keychain involvement, so a default
// install never shows a macOS keychain prompt. Opting in (`"keychain"`) routes
// the same stores through Electron `safeStorage` — the OS prompt then fires at
// TOGGLE time (a user-initiated, explained moment) and again only after an
// upgrade re-signs the binaries. The trade-off is stated in the Settings copy:
// file mode means any process running as this user can read the secrets;
// keychain mode makes the OS gate that access.
//
// The mode is read ONCE at boot (sync — the file is <100 bytes and the
// whenReady path is synchronous) and thereafter through the live getter passed
// to `gatedSafeStorage`, so a toggle applies to future store writes without a
// restart.

import { mkdirSync, readFileSync, writeFileSync, chmodSync } from "node:fs";
import { dirname, join } from "node:path";

import type { SafeStorageLike } from "../auth/secret-storage";

export type SecureStorageMode = "file" | "keychain";

export const DEFAULT_SECURE_STORAGE_MODE: SecureStorageMode = "file";

const POLICY_RELATIVE_PATH = ["settings", "secure-storage.json"] as const;

export interface SecureStoragePolicyFsSync {
  readFileSync(path: string): Buffer;
  writeFileSync(path: string, data: string, options?: { mode?: number }): void;
  mkdirSync(path: string, options: { recursive: boolean }): unknown;
  chmodSync(path: string, mode: number): void;
}

const NODE_FS_SYNC: SecureStoragePolicyFsSync = {
  readFileSync: (path) => readFileSync(path),
  writeFileSync: (path, data, options) => writeFileSync(path, data, options),
  mkdirSync: (path, options) => mkdirSync(path, options),
  chmodSync: (path, mode) => chmodSync(path, mode),
};

export function secureStoragePolicyPath(userDataDir: string): string {
  return join(userDataDir, ...POLICY_RELATIVE_PATH);
}

export function loadSecureStorageMode(
  userDataDir: string,
  fs: SecureStoragePolicyFsSync = NODE_FS_SYNC,
): SecureStorageMode {
  try {
    const raw = fs.readFileSync(secureStoragePolicyPath(userDataDir));
    const parsed: unknown = JSON.parse(raw.toString("utf-8"));
    if (
      typeof parsed === "object" &&
      parsed !== null &&
      (parsed as Record<string, unknown>).mode === "keychain"
    ) {
      return "keychain";
    }
  } catch {
    // Missing or unreadable policy → the safe default below.
  }
  return DEFAULT_SECURE_STORAGE_MODE;
}

export function saveSecureStorageMode(
  userDataDir: string,
  mode: SecureStorageMode,
  fs: SecureStoragePolicyFsSync = NODE_FS_SYNC,
): void {
  const path = secureStoragePolicyPath(userDataDir);
  fs.mkdirSync(dirname(path), { recursive: true });
  fs.writeFileSync(path, JSON.stringify({ version: 1, mode }) + "\n", {
    mode: 0o600,
  });
  // writeFile mode is ignored when the file pre-exists; enforce anyway.
  fs.chmodSync(path, 0o600);
}

/**
 * Wrap the real `safeStorage` behind the live mode. In `"file"` mode
 * `isEncryptionAvailable()` reports false, which activates every store's
 * existing plaintext (chmod-600) path — no keychain touch on the write side.
 * `decryptString` always delegates to the real implementation so cipher blobs
 * written before a toggle (or by a legacy install) stay readable.
 */
export function gatedSafeStorage(
  real: SafeStorageLike,
  getMode: () => SecureStorageMode,
): SafeStorageLike {
  return {
    isEncryptionAvailable: () =>
      getMode() === "keychain" && real.isEncryptionAvailable(),
    encryptString: (plaintext) => real.encryptString(plaintext),
    decryptString: (ciphertext) => real.decryptString(ciphertext),
  };
}
