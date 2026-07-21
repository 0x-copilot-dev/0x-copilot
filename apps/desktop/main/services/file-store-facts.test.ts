// @vitest-environment node
import { describe, expect, it } from "vitest";

import { fileStoreHasConversations, type ReaddirFs } from "./file-store-facts";

const ROOT = "/user-data/agent-data/v1";

function enoent(): NodeJS.ErrnoException {
  const err = new Error("ENOENT") as NodeJS.ErrnoException;
  err.code = "ENOENT";
  return err;
}

/** Fake fs backed by a { path: entries } map; absent paths reject with ENOENT. */
function fakeFs(tree: Record<string, readonly string[]>): ReaddirFs {
  return {
    readdir: (path: string) => {
      if (path in tree) return Promise.resolve(tree[path]);
      return Promise.reject(enoent());
    },
  };
}

describe("fileStoreHasConversations", () => {
  it("is false when the store root has never been created (workspaces absent)", async () => {
    await expect(fileStoreHasConversations(ROOT, fakeFs({}))).resolves.toBe(
      false,
    );
  });

  it("is false when workspaces exists but no workspace has sessions", async () => {
    const fs = fakeFs({
      [`${ROOT}/workspaces`]: ["ws1"],
      [`${ROOT}/workspaces/ws1/sessions`]: [],
    });
    await expect(fileStoreHasConversations(ROOT, fs)).resolves.toBe(false);
  });

  it("is true when any workspace has at least one session", async () => {
    const fs = fakeFs({
      [`${ROOT}/workspaces`]: ["ws1", "ws2"],
      [`${ROOT}/workspaces/ws1/sessions`]: [],
      [`${ROOT}/workspaces/ws2/sessions`]: ["conv-abc"],
    });
    await expect(fileStoreHasConversations(ROOT, fs)).resolves.toBe(true);
  });

  it("propagates a non-ENOENT error (caller falls back to Postgres)", async () => {
    const fs: ReaddirFs = {
      readdir: () => {
        const err = new Error("EACCES") as NodeJS.ErrnoException;
        err.code = "EACCES";
        return Promise.reject(err);
      },
    };
    await expect(fileStoreHasConversations(ROOT, fs)).rejects.toThrow(
      /EACCES/u,
    );
  });
});
