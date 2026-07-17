// @vitest-environment node
import { describe, expect, it } from "vitest";

import { RotatingLogWriter, type RotatingLogFs } from "./rotating-log";

interface FakeLogFsState {
  files: Map<string, string>;
  renames: Array<[string, string]>;
}

function makeFakeLogFs(): { fs: RotatingLogFs; state: FakeLogFsState } {
  const state: FakeLogFsState = { files: new Map(), renames: [] };
  const fs: RotatingLogFs = {
    appendFile: (path, data) => {
      state.files.set(path, (state.files.get(path) ?? "") + data);
      return Promise.resolve();
    },
    stat: (path) => {
      const content = state.files.get(path);
      if (content === undefined) {
        const err = new Error("ENOENT") as NodeJS.ErrnoException;
        err.code = "ENOENT";
        return Promise.reject(err);
      }
      return Promise.resolve({ size: Buffer.byteLength(content, "utf-8") });
    },
    rename: (oldPath, newPath) => {
      const content = state.files.get(oldPath);
      if (content === undefined) {
        const err = new Error("ENOENT") as NodeJS.ErrnoException;
        err.code = "ENOENT";
        return Promise.reject(err);
      }
      state.files.delete(oldPath);
      state.files.set(newPath, content);
      state.renames.push([oldPath, newPath]);
      return Promise.resolve();
    },
    rm: (path) => {
      state.files.delete(path);
      return Promise.resolve();
    },
    mkdir: () => Promise.resolve(undefined),
  };
  return { fs, state };
}

const LOG = "/user-data/logs/backend.log";

describe("RotatingLogWriter", () => {
  it("appends sequential writes in order", async () => {
    const { fs, state } = makeFakeLogFs();
    const writer = new RotatingLogWriter({ path: LOG, fs, maxBytes: 1000 });
    writer.write("one\n");
    writer.write("two\n");
    await writer.flush();
    expect(state.files.get(LOG)).toBe("one\ntwo\n");
  });

  it("rotates when the cap would be exceeded, keeping maxFiles generations", async () => {
    const { fs, state } = makeFakeLogFs();
    const writer = new RotatingLogWriter({
      path: LOG,
      fs,
      maxBytes: 10,
      maxFiles: 3,
    });
    writer.write("aaaaaaaa\n"); // 9 bytes -> active
    writer.write("bbbbbbbb\n"); // would exceed 10 -> rotate first
    writer.write("cccccccc\n"); // rotate again
    await writer.flush();
    expect(state.files.get(LOG)).toBe("cccccccc\n");
    expect(state.files.get(`${LOG}.1`)).toBe("bbbbbbbb\n");
    expect(state.files.get(`${LOG}.2`)).toBe("aaaaaaaa\n");
  });

  it("drops the oldest generation beyond maxFiles", async () => {
    const { fs, state } = makeFakeLogFs();
    const writer = new RotatingLogWriter({
      path: LOG,
      fs,
      maxBytes: 10,
      maxFiles: 3,
    });
    for (const label of ["a", "b", "c", "d"]) {
      writer.write(`${label.repeat(8)}\n`);
    }
    await writer.flush();
    expect(state.files.get(LOG)).toBe("dddddddd\n");
    expect(state.files.get(`${LOG}.1`)).toBe("cccccccc\n");
    expect(state.files.get(`${LOG}.2`)).toBe("bbbbbbbb\n");
    // "aaaaaaaa" fell off the end.
    expect(state.files.has(`${LOG}.3`)).toBe(false);
  });

  it("picks up the existing file size on first write after restart", async () => {
    const { fs, state } = makeFakeLogFs();
    state.files.set(LOG, "x".repeat(9)); // pre-existing 9 bytes
    const writer = new RotatingLogWriter({
      path: LOG,
      fs,
      maxBytes: 10,
      maxFiles: 3,
    });
    writer.write("yy"); // 9 + 2 > 10 -> rotate
    await writer.flush();
    expect(state.files.get(`${LOG}.1`)).toBe("x".repeat(9));
    expect(state.files.get(LOG)).toBe("yy");
  });

  it("survives fs errors without rejecting the queue", async () => {
    const { fs } = makeFakeLogFs();
    const failing: RotatingLogFs = {
      ...fs,
      appendFile: () => Promise.reject(new Error("disk full")),
    };
    const writer = new RotatingLogWriter({ path: LOG, fs: failing });
    writer.write("data");
    await expect(writer.flush()).resolves.toBeUndefined();
  });
});
