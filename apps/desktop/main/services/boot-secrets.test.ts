// @vitest-environment node
import { describe, expect, it, vi } from "vitest";

import type { SafeStorageLike } from "../auth/secret-storage";
import {
  BootSecretsUnreadable,
  bootSecretsPath,
  loadOrCreateBootSecrets,
  type BootSecretsFs,
} from "./boot-secrets";

function makeFakeSafeStorage(available: boolean): SafeStorageLike {
  return {
    isEncryptionAvailable: () => available,
    encryptString: (plaintext: string) =>
      Buffer.concat([
        Buffer.from("ENCv1:", "utf-8"),
        Buffer.from(plaintext, "utf-8").map((b) => b ^ 0x42),
      ]),
    decryptString: (cipher: Buffer) => {
      const marker = Buffer.from("ENCv1:", "utf-8");
      if (!cipher.subarray(0, marker.length).equals(marker)) {
        throw new Error("bad ciphertext");
      }
      return Buffer.from(
        cipher.subarray(marker.length).map((b) => b ^ 0x42),
      ).toString("utf-8");
    },
  };
}

interface FakeFsState {
  files: Map<string, Buffer>;
  modes: Map<string, number>;
  mkdirs: string[];
}

function makeFakeFs(): { fs: BootSecretsFs; state: FakeFsState } {
  const state: FakeFsState = {
    files: new Map(),
    modes: new Map(),
    mkdirs: [],
  };
  const fs: BootSecretsFs = {
    readFile: (path) => {
      const buf = state.files.get(path);
      if (buf === undefined) {
        const err = new Error("ENOENT") as NodeJS.ErrnoException;
        err.code = "ENOENT";
        return Promise.reject(err);
      }
      return Promise.resolve(buf);
    },
    writeFile: (path, data, options) => {
      state.files.set(path, Buffer.from(data));
      if (options?.mode !== undefined) state.modes.set(path, options.mode);
      return Promise.resolve();
    },
    mkdir: (path) => {
      state.mkdirs.push(path);
      return Promise.resolve(undefined);
    },
    chmod: (path, mode) => {
      state.modes.set(path, mode);
      return Promise.resolve();
    },
  };
  return { fs, state };
}

const USER_DATA = "/user-data";

describe("loadOrCreateBootSecrets", () => {
  it("generates every secret with the mandated shapes on first boot", async () => {
    const { fs } = makeFakeFs();
    const secrets = await loadOrCreateBootSecrets({
      userDataDir: USER_DATA,
      safeStorage: makeFakeSafeStorage(true),
      fs,
    });
    // 64 bytes hex = 128 hex chars.
    expect(secrets.authSecret).toMatch(/^[0-9a-f]{128}$/u);
    // 48 bytes base64url = 64 chars, no padding.
    expect(secrets.serviceToken).toMatch(/^[A-Za-z0-9_-]{64}$/u);
    expect(secrets.vaultSecret).toMatch(/^[A-Za-z0-9_-]{64}$/u);
    // 32 bytes base64url = 43 chars.
    expect(secrets.pgPassword).toMatch(/^[A-Za-z0-9_-]{43}$/u);
    // AUDIT_HMAC_KEY: 32 bytes hex = 64 hex chars (>= 32 bytes, hex-encoded).
    expect(secrets.auditHmacKey).toMatch(/^[0-9a-f]{64}$/u);
  });

  it("persists once and returns identical secrets on the next load", async () => {
    const { fs, state } = makeFakeFs();
    const config = {
      userDataDir: USER_DATA,
      safeStorage: makeFakeSafeStorage(true),
      fs,
    };
    const first = await loadOrCreateBootSecrets(config);
    expect(state.files.size).toBe(1);
    const blobAfterFirst = state.files.get(bootSecretsPath(USER_DATA));

    const second = await loadOrCreateBootSecrets(config);
    expect(second).toEqual(first);
    // No rewrite happened — the blob is byte-identical.
    expect(state.files.get(bootSecretsPath(USER_DATA))).toEqual(blobAfterFirst);
  });

  it("encrypts at rest when safeStorage is available (no plaintext in blob)", async () => {
    const { fs, state } = makeFakeFs();
    const secrets = await loadOrCreateBootSecrets({
      userDataDir: USER_DATA,
      safeStorage: makeFakeSafeStorage(true),
      fs,
    });
    const blob = state.files.get(bootSecretsPath(USER_DATA))!;
    const asText = blob.toString("utf-8");
    expect(asText).toContain("ATLASBOOTv1:cipher:");
    expect(asText).not.toContain(secrets.authSecret);
    expect(asText).not.toContain(secrets.pgPassword);
  });

  it("falls back to a chmod-600 JSON blob when safeStorage is unavailable", async () => {
    const { fs, state } = makeFakeFs();
    const path = bootSecretsPath(USER_DATA);
    const secrets = await loadOrCreateBootSecrets({
      userDataDir: USER_DATA,
      safeStorage: makeFakeSafeStorage(false),
      fs,
    });
    const blob = state.files.get(path)!;
    expect(blob.toString("utf-8")).toContain("ATLASBOOTv1:plaintext:");
    expect(blob.toString("utf-8")).toContain(secrets.pgPassword);
    expect(state.modes.get(path)).toBe(0o600);
  });

  it("round-trips the plaintext fallback on reload", async () => {
    const { fs } = makeFakeFs();
    const config = {
      userDataDir: USER_DATA,
      safeStorage: makeFakeSafeStorage(false),
      fs,
    };
    const first = await loadOrCreateBootSecrets(config);
    const second = await loadOrCreateBootSecrets(config);
    expect(second).toEqual(first);
  });

  it("throws BootSecretsUnreadable when decryption fails — never regenerates", async () => {
    const { fs, state } = makeFakeFs();
    const path = bootSecretsPath(USER_DATA);
    state.files.set(
      path,
      Buffer.concat([
        Buffer.from("ATLASBOOTv1:cipher:", "utf-8"),
        Buffer.from("corrupted-not-real-ciphertext", "utf-8"),
      ]),
    );
    const before = state.files.get(path);
    await expect(
      loadOrCreateBootSecrets({
        userDataDir: USER_DATA,
        safeStorage: makeFakeSafeStorage(true),
        fs,
      }),
    ).rejects.toThrow(BootSecretsUnreadable);
    // The blob was NOT overwritten with fresh secrets.
    expect(state.files.get(path)).toEqual(before);
    expect(state.files.size).toBe(1);
  });

  it("throws BootSecretsUnreadable when the blob is encrypted but safeStorage is now unavailable", async () => {
    const { fs, state } = makeFakeFs();
    // Write with encryption available...
    await loadOrCreateBootSecrets({
      userDataDir: USER_DATA,
      safeStorage: makeFakeSafeStorage(true),
      fs,
    });
    // ...then reload with the keychain gone.
    await expect(
      loadOrCreateBootSecrets({
        userDataDir: USER_DATA,
        safeStorage: makeFakeSafeStorage(false),
        fs,
      }),
    ).rejects.toThrow(BootSecretsUnreadable);
    expect(state.files.size).toBe(1);
  });

  it("throws BootSecretsUnreadable on an unknown blob format", async () => {
    const { fs, state } = makeFakeFs();
    state.files.set(
      bootSecretsPath(USER_DATA),
      Buffer.from("garbage-format", "utf-8"),
    );
    await expect(
      loadOrCreateBootSecrets({
        userDataDir: USER_DATA,
        safeStorage: makeFakeSafeStorage(true),
        fs,
      }),
    ).rejects.toThrow(BootSecretsUnreadable);
  });

  it("throws BootSecretsUnreadable when a required field is missing", async () => {
    const { fs, state } = makeFakeFs();
    state.files.set(
      bootSecretsPath(USER_DATA),
      Buffer.from(
        "ATLASBOOTv1:plaintext:" +
          JSON.stringify({ version: 1, authSecret: "a" }),
        "utf-8",
      ),
    );
    await expect(
      loadOrCreateBootSecrets({
        userDataDir: USER_DATA,
        safeStorage: makeFakeSafeStorage(false),
        fs,
      }),
    ).rejects.toThrow(/serviceToken/u);
  });

  it("uses the injected randomBytes source", async () => {
    const { fs } = makeFakeFs();
    const randomBytes = vi.fn((size: number) => Buffer.alloc(size, 7));
    await loadOrCreateBootSecrets({
      userDataDir: USER_DATA,
      safeStorage: makeFakeSafeStorage(true),
      fs,
      randomBytes,
    });
    expect(randomBytes).toHaveBeenCalledWith(64);
    expect(randomBytes).toHaveBeenCalledWith(48);
    expect(randomBytes).toHaveBeenCalledWith(32);
  });
});
