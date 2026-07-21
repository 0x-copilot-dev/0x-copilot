// @vitest-environment node
import { describe, expect, it } from "vitest";

import {
  firstRunStorePath,
  loadFirstRunComplete,
  saveFirstRunComplete,
  type FirstRunFsSync,
} from "./first-run-store";

const USER_DATA = "/user-data";
const WS_A = "org_acme";
const WS_B = "org_beta";

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
    expect(loadFirstRunComplete(USER_DATA, WS_A, fs)).toBe(false);
  });

  it("round-trips completion with 0600 perms", () => {
    const { fs, modes } = makeFakeFs();
    saveFirstRunComplete(USER_DATA, WS_A, true, fs);
    expect(loadFirstRunComplete(USER_DATA, WS_A, fs)).toBe(true);
    expect(modes.get(firstRunStorePath(USER_DATA))).toBe(0o600);
  });

  it("keys completion per workspace", () => {
    const { fs } = makeFakeFs();
    saveFirstRunComplete(USER_DATA, WS_A, true, fs);
    expect(loadFirstRunComplete(USER_DATA, WS_A, fs)).toBe(true);
    // A second identity on the same install still sees its own first run.
    expect(loadFirstRunComplete(USER_DATA, WS_B, fs)).toBe(false);
    saveFirstRunComplete(USER_DATA, WS_B, true, fs);
    expect(loadFirstRunComplete(USER_DATA, WS_B, fs)).toBe(true);
    // A does not regress when B is written.
    expect(loadFirstRunComplete(USER_DATA, WS_A, fs)).toBe(true);
  });

  it("can reset a workspace back to not-completed", () => {
    const { fs } = makeFakeFs();
    saveFirstRunComplete(USER_DATA, WS_A, true, fs);
    saveFirstRunComplete(USER_DATA, WS_A, false, fs);
    expect(loadFirstRunComplete(USER_DATA, WS_A, fs)).toBe(false);
  });

  it("treats a garbage or non-string entry as not-completed", () => {
    const { fs, files } = makeFakeFs();
    files.set(firstRunStorePath(USER_DATA), "not-json{{");
    expect(loadFirstRunComplete(USER_DATA, WS_A, fs)).toBe(false);
    files.set(
      firstRunStorePath(USER_DATA),
      JSON.stringify({ version: 1, completed: { [WS_A]: true } }),
    );
    // A non-string value (true) is not a valid completion stamp → shows.
    expect(loadFirstRunComplete(USER_DATA, WS_A, fs)).toBe(false);
  });
});
