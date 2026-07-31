// @vitest-environment node
//
// The cold-load race, driven deterministically.
//
// `#ensureLoaded` awaits a file read. Without a shared in-flight promise, two
// callers that both arrive cold each start their own read and each assign the
// decoded map when it settles. Order the reads so the SLOW one settles after
// the fast caller has already created and persisted a grant, and the stale
// decode replaces the map — the folder the user just attached disappears, after
// `requestFolderGrant` has already returned it to the renderer.
//
// It lives in its own file because reproducing it needs `node:fs/promises`
// mocked, and the rest of the suite deliberately runs against a real temp dir.
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SafeStorageLike } from "../auth/secret-storage";

/** Milliseconds to stall each successive `readFile`, consumed in order. */
const readStalls: number[] = [];

vi.mock("node:fs/promises", async (importOriginal) => {
  const actual = await importOriginal<typeof import("node:fs/promises")>();
  return {
    ...actual,
    readFile: async (...args: Parameters<typeof actual.readFile>) => {
      const stall = readStalls.shift() ?? 0;
      const result = await actual.readFile(...args);
      if (stall > 0) {
        await new Promise((resolve) => setTimeout(resolve, stall));
      }
      return result;
    },
  };
});

const { GrantStore } = await import("./grant-store");

function makeFakeSafeStorage(): SafeStorageLike {
  return {
    isEncryptionAvailable: () => true,
    encryptString: (plaintext: string) =>
      Buffer.concat([
        Buffer.from("ENC:", "utf-8"),
        Buffer.from(plaintext, "utf-8").map((b) => b ^ 0x42),
      ]),
    decryptString: (cipher: Buffer) => {
      const rest = cipher.subarray(Buffer.from("ENC:", "utf-8").length);
      return Buffer.from(rest.map((b) => b ^ 0x42)).toString("utf-8");
    },
  };
}

describe("GrantStore — concurrent cold load", () => {
  let tmp: string;
  let ids = 0;

  beforeEach(() => {
    ids = 0;
    readStalls.length = 0;
    tmp = mkdtempSync(join(tmpdir(), "cap-grants-race-"));
  });

  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  function makeStore() {
    ids += 1;
    let seq = ids * 100;
    return new GrantStore({
      userDataDir: tmp,
      safeStorage: makeFakeSafeStorage(),
      uuid: () => {
        seq += 1;
        return `00000000-0000-4000-8000-${String(seq).padStart(12, "0")}`;
      },
      clock: () => 1000,
    });
  }

  it("does not lose a grant created while a slower load is still in flight", async () => {
    const seeded = makeStore();
    await seeded.create({
      root: "/data/existing",
      mode: "read_only",
      label: "existing",
    });

    // A fresh store: both calls below take the cold path. The first read
    // returns immediately, the second stalls past the create + persist.
    const store = makeStore();
    readStalls.push(0, 25);

    const [created] = await Promise.all([
      store.create({ root: "/data/new", mode: "read_only", label: "new" }),
      store.list(),
    ]);

    // The grant handed to the caller must still be in the store...
    const roots = (await store.list()).map((g) => g.root).sort();
    expect(roots).toEqual(["/data/existing", "/data/new"]);
    expect((await store.get(created.grantId))?.root).toBe("/data/new");

    // ...and must survive the NEXT persist, which writes whatever the map holds.
    await store.create({
      root: "/data/later",
      mode: "read_only",
      label: "later",
    });
    const reopened = makeStore();
    expect((await reopened.list()).map((g) => g.root).sort()).toEqual([
      "/data/existing",
      "/data/later",
      "/data/new",
    ]);
  });

  it("reads the grant file once no matter how many callers arrive cold", async () => {
    const seeded = makeStore();
    await seeded.create({ root: "/data/a", mode: "read_only", label: "a" });

    const store = makeStore();
    readStalls.push(0, 25, 25, 25);

    await Promise.all([
      store.list(),
      store.listActive(),
      store.snapshotActive(),
      store.get("nope"),
    ]);

    // Three stalls unconsumed => exactly one read happened.
    expect(readStalls).toHaveLength(3);
  });
});
