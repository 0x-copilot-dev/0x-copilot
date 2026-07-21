// @vitest-environment node
import { describe, expect, it } from "vitest";

import {
  firstRunStorePath,
  loadFirstRunComplete,
  saveFirstRunComplete,
  type FirstRunFsSync,
} from "./first-run-store";

const USER_DATA = "/user-data";
// Opaque per-account keys (hashed claims.sub in production), NOT workspaceIds.
const KEY_A = "acct_hash_a";
const KEY_B = "acct_hash_b";

function makeFakeFs(): {
  fs: FirstRunFsSync;
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

describe("first-run store", () => {
  it("reports not-completed when no file exists (onboarding shows)", () => {
    const { fs } = makeFakeFs();
    expect(loadFirstRunComplete(USER_DATA, KEY_A, fs)).toBe(false);
  });

  it("round-trips completion with 0600 perms", () => {
    const { fs, modes } = makeFakeFs();
    saveFirstRunComplete(USER_DATA, KEY_A, true, fs);
    expect(loadFirstRunComplete(USER_DATA, KEY_A, fs)).toBe(true);
    expect(modes.get(firstRunStorePath(USER_DATA))).toBe(0o600);
  });

  it("keys completion per account", () => {
    const { fs } = makeFakeFs();
    saveFirstRunComplete(USER_DATA, KEY_A, true, fs);
    expect(loadFirstRunComplete(USER_DATA, KEY_A, fs)).toBe(true);
    // A second account on the same install still sees its own first run.
    expect(loadFirstRunComplete(USER_DATA, KEY_B, fs)).toBe(false);
    saveFirstRunComplete(USER_DATA, KEY_B, true, fs);
    expect(loadFirstRunComplete(USER_DATA, KEY_B, fs)).toBe(true);
    // A does not regress when B is written.
    expect(loadFirstRunComplete(USER_DATA, KEY_A, fs)).toBe(true);
  });

  it("can reset an account back to not-completed", () => {
    const { fs } = makeFakeFs();
    saveFirstRunComplete(USER_DATA, KEY_A, true, fs);
    saveFirstRunComplete(USER_DATA, KEY_A, false, fs);
    expect(loadFirstRunComplete(USER_DATA, KEY_A, fs)).toBe(false);
  });

  it("treats a garbage or non-string entry as not-completed", () => {
    const { fs, files } = makeFakeFs();
    files.set(firstRunStorePath(USER_DATA), "not-json{{");
    expect(loadFirstRunComplete(USER_DATA, KEY_A, fs)).toBe(false);
    files.set(
      firstRunStorePath(USER_DATA),
      JSON.stringify({ version: 1, completed: { [KEY_A]: true } }),
    );
    // A non-string value (true) is not a valid completion stamp → shows.
    expect(loadFirstRunComplete(USER_DATA, KEY_A, fs)).toBe(false);
  });
});
