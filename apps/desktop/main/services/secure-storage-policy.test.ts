// @vitest-environment node
import { describe, expect, it } from "vitest";

import type { SafeStorageLike } from "../auth/secret-storage";
import {
  DEFAULT_SECURE_STORAGE_MODE,
  gatedSafeStorage,
  loadSecureStorageMode,
  saveSecureStorageMode,
  secureStoragePolicyPath,
  type SecureStoragePolicyFsSync,
} from "./secure-storage-policy";

const USER_DATA = "/user-data";

function makeFakeFs(): {
  fs: SecureStoragePolicyFsSync;
  files: Map<string, string>;
  modes: Map<string, number>;
} {
  const files = new Map<string, string>();
  const modes = new Map<string, number>();
  return {
    files,
    modes,
    fs: {
      readFileSync: (path) => {
        const data = files.get(path);
        if (data === undefined) {
          const err = new Error("ENOENT") as NodeJS.ErrnoException;
          err.code = "ENOENT";
          throw err;
        }
        return Buffer.from(data, "utf-8");
      },
      writeFileSync: (path, data, options) => {
        files.set(path, data);
        if (options?.mode !== undefined) modes.set(path, options.mode);
      },
      mkdirSync: () => undefined,
      chmodSync: (path, mode) => modes.set(path, mode),
    },
  };
}

function makeSafeStorage(available: boolean): SafeStorageLike {
  return {
    isEncryptionAvailable: () => available,
    encryptString: (plaintext) => Buffer.from(`enc:${plaintext}`, "utf-8"),
    decryptString: (cipher) => cipher.toString("utf-8").replace(/^enc:/u, ""),
  };
}

describe("secure-storage policy", () => {
  it("defaults to file mode when no policy exists", () => {
    const { fs } = makeFakeFs();
    expect(loadSecureStorageMode(USER_DATA, fs)).toBe("file");
    expect(DEFAULT_SECURE_STORAGE_MODE).toBe("file");
  });

  it("round-trips a saved mode with 0600 perms", () => {
    const { fs, modes } = makeFakeFs();
    saveSecureStorageMode(USER_DATA, "keychain", fs);
    expect(loadSecureStorageMode(USER_DATA, fs)).toBe("keychain");
    expect(modes.get(secureStoragePolicyPath(USER_DATA))).toBe(0o600);
    saveSecureStorageMode(USER_DATA, "file", fs);
    expect(loadSecureStorageMode(USER_DATA, fs)).toBe("file");
  });

  it("treats an unreadable or garbage policy file as the default", () => {
    const { fs, files } = makeFakeFs();
    files.set(secureStoragePolicyPath(USER_DATA), "not-json{{");
    expect(loadSecureStorageMode(USER_DATA, fs)).toBe("file");
    files.set(secureStoragePolicyPath(USER_DATA), '{"mode":"banana"}');
    expect(loadSecureStorageMode(USER_DATA, fs)).toBe("file");
  });
});

describe("gatedSafeStorage", () => {
  it("reports unavailable in file mode so stores take their plaintext path", () => {
    const gated = gatedSafeStorage(makeSafeStorage(true), () => "file");
    expect(gated.isEncryptionAvailable()).toBe(false);
  });

  it("passes through availability in keychain mode", () => {
    const gated = gatedSafeStorage(makeSafeStorage(true), () => "keychain");
    expect(gated.isEncryptionAvailable()).toBe(true);
  });

  it("follows the LIVE mode getter — a toggle applies without reconstruction", () => {
    let mode: "file" | "keychain" = "file";
    const gated = gatedSafeStorage(makeSafeStorage(true), () => mode);
    expect(gated.isEncryptionAvailable()).toBe(false);
    mode = "keychain";
    expect(gated.isEncryptionAvailable()).toBe(true);
  });

  it("always delegates decrypt so legacy cipher blobs stay readable in file mode", () => {
    const real = makeSafeStorage(true);
    const cipher = real.encryptString("legacy-secret");
    const gated = gatedSafeStorage(real, () => "file");
    expect(gated.decryptString(cipher)).toBe("legacy-secret");
  });
});
