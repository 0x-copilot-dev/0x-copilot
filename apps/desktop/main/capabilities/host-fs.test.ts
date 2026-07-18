// @vitest-environment node
import {
  mkdirSync,
  mkdtempSync,
  realpathSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { rm, symlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { defaultHostFsDeps, HostFs, type HostFsDeps } from "./host-fs";
import { FsError } from "./path-validation";

// These tests run against REAL temporary directories and REAL symlinks so the
// symlink-escape and TOCTOU guards are exercised against the actual kernel,
// not a mock. `root` is the grant root; `outside` is a sibling the agent must
// never reach.

let base: string;
let root: string;
let outside: string;
let fs: HostFs;

async function codeOf(p: Promise<unknown>): Promise<string | "no-throw"> {
  try {
    await p;
    return "no-throw";
  } catch (err) {
    return err instanceof FsError ? err.code : `other:${String(err)}`;
  }
}

beforeEach(() => {
  // realpathSync so `base` is symlink-free (macOS /var → /private/var).
  base = realpathSync(mkdtempSync(join(tmpdir(), "cap-hostfs-")));
  root = join(base, "root");
  outside = join(base, "outside");
  mkdirSync(join(root, "sub", "deep"), { recursive: true });
  mkdirSync(outside, { recursive: true });
  writeFileSync(join(root, "file.txt"), "hello world\nsecond line\n");
  writeFileSync(join(root, "sub", "nested.txt"), "a needle in here\n");
  writeFileSync(join(root, "sub", "deep", "inner.txt"), "deep content\n");
  writeFileSync(join(outside, "secret.txt"), "TOP SECRET DATA");
  // A decoy with the SAME relative name as a real in-root file, so a TOCTOU
  // swap of `sub` → symlink(outside) makes the escaped open SUCCEED — forcing
  // the post-open recheck (not just ENOENT) to be what denies it.
  writeFileSync(join(outside, "nested.txt"), "OUTSIDE NESTED SECRET");
  fs = new HostFs();
});

afterEach(() => {
  rmSync(base, { recursive: true, force: true });
});

describe("HostFs — happy path reads", () => {
  it("stat a regular file", async () => {
    const s = await fs.stat(root, "file.txt");
    expect(s.type).toBe("file");
    expect(s.size).toBe(Buffer.byteLength("hello world\nsecond line\n"));
    expect(s.name).toBe("file.txt");
  });

  it("stat a directory (and the grant root itself)", async () => {
    expect((await fs.stat(root, "sub")).type).toBe("dir");
    expect((await fs.stat(root, "")).type).toBe("dir");
  });

  it("list a directory's immediate children with types", async () => {
    const { entries, truncated } = await fs.list(root, "sub");
    expect(truncated).toBe(false);
    const byName = Object.fromEntries(entries.map((e) => [e.name, e.type]));
    expect(byName["nested.txt"]).toBe("file");
    expect(byName["deep"]).toBe("dir");
  });

  it("read a whole file", async () => {
    const r = await fs.read(root, "file.txt");
    expect(Buffer.from(r.base64, "base64").toString("utf-8")).toBe(
      "hello world\nsecond line\n",
    );
    expect(r.truncated).toBe(false);
    expect(r.bytesRead).toBe(r.size);
  });

  it("glob matches by relative POSIX path", async () => {
    const g = await fs.glob(root, "**/*.txt");
    expect([...g.paths].sort()).toEqual([
      "file.txt",
      "sub/deep/inner.txt",
      "sub/nested.txt",
    ]);
    expect(g.truncated).toBe(false);
  });

  it("glob '*.txt' matches only the top level", async () => {
    const g = await fs.glob(root, "*.txt");
    expect(g.paths).toEqual(["file.txt"]);
  });

  it("grep finds a literal match with line/column/preview", async () => {
    const g = await fs.grep(root, "needle");
    expect(g.hits).toHaveLength(1);
    expect(g.hits[0].path).toBe("sub/nested.txt");
    expect(g.hits[0].line).toBe(1);
    expect(g.hits[0].column).toBe(3); // "a needle" → 'needle' at col 3
    expect(g.hits[0].preview).toContain("needle");
  });

  it("grep supports regex mode", async () => {
    const g = await fs.grep(root, "sec[o0]nd", { isRegex: true });
    expect(g.hits.map((h) => h.path)).toContain("file.txt");
  });
});

describe("HostFs — byte / result / recursion bounds", () => {
  it("read caps at maxBytes and flags truncation", async () => {
    const r = await fs.read(root, "file.txt", { maxBytes: 5 });
    expect(r.bytesRead).toBe(5);
    expect(r.truncated).toBe(true);
    expect(Buffer.from(r.base64, "base64").toString("utf-8")).toBe("hello");
  });

  it("read honours an offset window", async () => {
    const r = await fs.read(root, "file.txt", { offset: 6, maxBytes: 5 });
    expect(Buffer.from(r.base64, "base64").toString("utf-8")).toBe("world");
    expect(r.offset).toBe(6);
  });

  it("glob is bounded by maxResults", async () => {
    for (let i = 0; i < 6; i += 1) {
      writeFileSync(join(root, `f${i}.log`), "x");
    }
    const g = await fs.glob(root, "*.log", { maxResults: 2 });
    expect(g.paths).toHaveLength(2);
    expect(g.truncated).toBe(true);
  });

  it("glob does not descend past the recursion-depth ceiling", async () => {
    // Build a chain deeper than FS_LIMITS.maxWalkDepth (32) with a marker file
    // at the bottom; it must NOT be discovered.
    let cur = join(root, "chain");
    for (let i = 0; i < 40; i += 1) {
      cur = join(cur, `d${i}`);
    }
    mkdirSync(cur, { recursive: true });
    writeFileSync(join(cur, "buried.txt"), "x");
    const g = await fs.glob(root, "**/buried.txt");
    // The buried file sits below the depth ceiling, so the walk never reaches
    // it — no match.
    expect(g.paths).toHaveLength(0);
  });

  it("glob stops at the wall-clock deadline (now() injected past it)", async () => {
    // now() returns 0 for the deadline capture, then a huge value inside the
    // walk so the very first entry is already past the deadline.
    let calls = 0;
    const deps: HostFsDeps = {
      ...defaultHostFsDeps(),
      now: () => (calls++ === 0 ? 0 : 1_000_000_000),
    };
    const g = await new HostFs(deps).glob(root, "**/*");
    expect(g.truncated).toBe(true);
  });
});

describe("HostFs — adversarial path rejection", () => {
  it("rejects .. traversal (invalid_path, before any disk touch)", async () => {
    expect(await codeOf(fs.read(root, "../outside/secret.txt"))).toBe(
      "invalid_path",
    );
  });

  it("rejects an absolute path", async () => {
    expect(await codeOf(fs.read(root, "/etc/passwd"))).toBe("invalid_path");
  });

  it("rejects a reserved device name and an ADS segment", async () => {
    expect(await codeOf(fs.read(root, "NUL"))).toBe("invalid_path");
    expect(await codeOf(fs.read(root, "file.txt:stream"))).toBe("invalid_path");
  });

  it("denies a symlink whose target is OUTSIDE the root (dir symlink)", async () => {
    symlinkSync(outside, join(root, "escape"));
    // resolve-before-authorize: realpath lands outside → permission_denied.
    expect(await codeOf(fs.read(root, "escape/secret.txt"))).toBe(
      "permission_denied",
    );
    expect(await codeOf(fs.list(root, "escape"))).toBe("permission_denied");
    expect(await codeOf(fs.stat(root, "escape"))).toBe("permission_denied");
  });

  it("denies a symlink FILE that points outside the root", async () => {
    symlinkSync(join(outside, "secret.txt"), join(root, "escape.txt"));
    expect(await codeOf(fs.read(root, "escape.txt"))).toBe("permission_denied");
  });

  it("never returns the outside secret contents on any escape attempt", async () => {
    symlinkSync(outside, join(root, "escape"));
    try {
      const r = await fs.read(root, "escape/secret.txt");
      // Must not reach here; if it does, prove no leak.
      expect(r.base64).not.toContain(
        Buffer.from("TOP SECRET").toString("base64").slice(0, 8),
      );
      throw new Error("escape was not denied");
    } catch (err) {
      expect(err).toBeInstanceOf(FsError);
    }
  });
});

describe("HostFs — sensitive-file content policy (G2)", () => {
  it("denies reading .ssh/id_rsa inside a granted folder", async () => {
    mkdirSync(join(root, ".ssh"), { recursive: true });
    writeFileSync(join(root, ".ssh", "id_rsa"), "PRIVATE KEY MATERIAL");
    expect(await codeOf(fs.read(root, ".ssh/id_rsa"))).toBe(
      "permission_denied",
    );
  });

  it("denies reading a .env file", async () => {
    writeFileSync(join(root, ".env"), "SECRET=hunter2\n");
    writeFileSync(join(root, ".env.production"), "TOKEN=abc\n");
    expect(await codeOf(fs.read(root, ".env"))).toBe("permission_denied");
    expect(await codeOf(fs.read(root, ".env.production"))).toBe(
      "permission_denied",
    );
  });

  it("denies reading a *.pem private-key file", async () => {
    writeFileSync(join(root, "server.pem"), "-----BEGIN PRIVATE KEY-----\n");
    writeFileSync(join(root, "tls.key"), "-----BEGIN RSA PRIVATE KEY-----\n");
    expect(await codeOf(fs.read(root, "server.pem"))).toBe("permission_denied");
    expect(await codeOf(fs.read(root, "tls.key"))).toBe("permission_denied");
  });

  it("grep never returns the contents of a secret file", async () => {
    writeFileSync(join(root, ".env"), "API_KEY=needle-secret-value\n");
    const g = await fs.grep(root, "needle-secret-value");
    // The literal lives ONLY in .env, which grep must skip — no hits, no leak.
    expect(g.hits).toHaveLength(0);
  });

  it("still reads an ordinary file (policy does not over-block)", async () => {
    const r = await fs.read(root, "file.txt");
    expect(Buffer.from(r.base64, "base64").toString("utf-8")).toContain(
      "hello world",
    );
  });
});

describe("HostFs — TOCTOU (swap between resolve and use)", () => {
  // The afterResolve seam swaps a real directory component for a symlink
  // pointing OUTSIDE the root AFTER path resolution/authorization but BEFORE
  // the atomic open. A correct implementation must deny, not read the swapped
  // target.
  function swapDirToOutsideSymlink(): () => Promise<void> {
    let fired = false;
    return async () => {
      if (fired) return;
      fired = true;
      await rm(join(root, "sub"), { recursive: true, force: true });
      await symlink(outside, join(root, "sub"));
    };
  }

  it("darwin O_NOFOLLOW_ANY: a mid-flight ancestor swap is denied atomically", async () => {
    // Only meaningful on darwin; skip elsewhere (the guarantee differs).
    if (process.platform !== "darwin") return;
    const deps: HostFsDeps = {
      ...defaultHostFsDeps(),
      afterResolve: swapDirToOutsideSymlink(),
    };
    const hostFs = new HostFs(deps);
    // Target resolves to root/sub/nested.txt; the seam then makes root/sub a
    // symlink to outside. The open of the resolved path must fail closed.
    expect(await codeOf(hostFs.read(root, "sub/nested.txt"))).toBe(
      "permission_denied",
    );
  });

  it("non-darwin fallback: post-open realpath/identity recheck denies the swap", async () => {
    // Force the O_NOFOLLOW-only (non-atomic) code path even on macOS, proving
    // the recheck — not just the darwin flag — catches an intermediate swap.
    const deps: HostFsDeps = {
      ...defaultHostFsDeps(),
      platform: "linux",
      afterResolve: swapDirToOutsideSymlink(),
    };
    const hostFs = new HostFs(deps);
    const result = await codeOf(hostFs.read(root, "sub/nested.txt"));
    expect(result).toBe("permission_denied");
  });

  it("a legitimate read still succeeds when the seam does NOT swap", async () => {
    const deps: HostFsDeps = {
      ...defaultHostFsDeps(),
      afterResolve: async () => {
        /* no swap */
      },
    };
    const r = await new HostFs(deps).read(root, "sub/nested.txt");
    expect(Buffer.from(r.base64, "base64").toString("utf-8")).toContain(
      "needle",
    );
  });
});
