// @vitest-environment node
import {
  constants,
  existsSync,
  lstatSync,
  mkdirSync,
  mkdtempSync,
  openSync,
  readdirSync,
  readFileSync,
  realpathSync,
  rmSync,
  statSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { rm, symlink } from "node:fs/promises";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { isAbsolute, join, relative } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  defaultHostFsDeps,
  HostFs,
  type HostFsDeps,
  type NativeWorkspaceFs,
} from "./host-fs";
import { FS_LIMITS, FsError } from "./path-validation";

const bufOf = (s: string): Buffer => Buffer.from(s, "utf-8");
const readRoot = (rel: string): string =>
  readFileSync(join(root, rel), "utf-8");
const tempFilesIn = (dir: string): string[] =>
  readdirSync(dir).filter((n) => n.startsWith(".captmp-"));

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

describe("HostFs — happy path writes", () => {
  it("write creates a new file (created:true) with exact bytes", async () => {
    const r = await fs.write(root, "created.txt", bufOf("brand new"));
    expect(r.created).toBe(true);
    expect(r.bytesWritten).toBe(9);
    expect(r.path).toBe("created.txt");
    expect(readRoot("created.txt")).toBe("brand new");
  });

  it("write overwrites an existing file (same-file replace, created:false)", async () => {
    const r = await fs.write(root, "file.txt", bufOf("replaced content"));
    expect(r.created).toBe(false);
    expect(readRoot("file.txt")).toBe("replaced content");
  });

  it("write lands a file in a nested existing directory", async () => {
    const r = await fs.write(root, "sub/new.txt", bufOf("x"));
    expect(r.created).toBe(true);
    expect(readRoot("sub/new.txt")).toBe("x");
  });

  it("write refuses when the target is an existing directory", async () => {
    expect(await codeOf(fs.write(root, "sub", bufOf("x")))).toBe("not_a_file");
  });

  it("write fails not_found when the parent directory is missing", async () => {
    expect(await codeOf(fs.write(root, "nope/leaf.txt", bufOf("x")))).toBe(
      "not_found",
    );
  });

  it("write cannot target the grant root itself", async () => {
    expect(await codeOf(fs.write(root, "", bufOf("x")))).toBe(
      "invalid_request",
    );
  });

  it("edit replaces an EXISTING file's full contents", async () => {
    const r = await fs.edit(root, "file.txt", bufOf("edited"));
    expect(r.bytesWritten).toBe(6);
    expect(readRoot("file.txt")).toBe("edited");
  });

  it("edit fails not_found on a missing file and never creates it", async () => {
    expect(await codeOf(fs.edit(root, "ghost.txt", bufOf("x")))).toBe(
      "not_found",
    );
    expect(existsSync(join(root, "ghost.txt"))).toBe(false);
  });

  it("edit refuses a directory", async () => {
    expect(await codeOf(fs.edit(root, "sub", bufOf("x")))).toBe("not_a_file");
  });

  it("mkdir creates a new directory (created:true)", async () => {
    const r = await fs.mkdir(root, "fresh");
    expect(r.created).toBe(true);
    expect(statSync(join(root, "fresh")).isDirectory()).toBe(true);
  });

  it("mkdir is idempotent on an existing directory (created:false)", async () => {
    expect((await fs.mkdir(root, "sub")).created).toBe(false);
  });

  it("mkdir collides with an existing file (not_a_directory)", async () => {
    expect(await codeOf(fs.mkdir(root, "file.txt"))).toBe("not_a_directory");
  });

  it("delete removes a regular file", async () => {
    expect((await fs.delete(root, "file.txt")).type).toBe("file");
    expect(existsSync(join(root, "file.txt"))).toBe(false);
  });

  it("delete removes an empty directory", async () => {
    await fs.mkdir(root, "empty");
    expect((await fs.delete(root, "empty")).type).toBe("dir");
    expect(existsSync(join(root, "empty"))).toBe(false);
  });

  it("delete refuses a non-empty directory (invalid_request), leaving it intact", async () => {
    expect(await codeOf(fs.delete(root, "sub"))).toBe("invalid_request");
    expect(existsSync(join(root, "sub"))).toBe(true);
  });

  it("delete fails not_found on a missing path", async () => {
    expect(await codeOf(fs.delete(root, "ghost"))).toBe("not_found");
  });

  it("move renames a file within the root", async () => {
    const r = await fs.move(root, "file.txt", "moved.txt");
    expect(r).toMatchObject({
      from: "file.txt",
      to: "moved.txt",
      type: "file",
    });
    expect(existsSync(join(root, "file.txt"))).toBe(false);
    expect(readRoot("moved.txt")).toContain("hello world");
  });

  it("move relocates a file into a nested existing directory", async () => {
    expect((await fs.move(root, "file.txt", "sub/relocated.txt")).type).toBe(
      "file",
    );
    expect(existsSync(join(root, "sub", "relocated.txt"))).toBe(true);
  });

  it("move renames a directory", async () => {
    expect((await fs.move(root, "sub", "sub-renamed")).type).toBe("dir");
    expect(existsSync(join(root, "sub-renamed", "nested.txt"))).toBe(true);
  });

  it("move fails not_found on a missing source", async () => {
    expect(await codeOf(fs.move(root, "ghost.txt", "dest.txt"))).toBe(
      "not_found",
    );
  });
});

describe("HostFs — adversarial write rejection", () => {
  it("write rejects .. traversal before touching disk", async () => {
    expect(await codeOf(fs.write(root, "../outside/x.txt", bufOf("x")))).toBe(
      "invalid_path",
    );
    expect(existsSync(join(outside, "x.txt"))).toBe(false);
  });

  it("write rejects an absolute path", async () => {
    expect(await codeOf(fs.write(root, "/tmp/evil.txt", bufOf("x")))).toBe(
      "invalid_path",
    );
  });

  it("write rejects a reserved device name and an ADS segment", async () => {
    expect(await codeOf(fs.write(root, "NUL", bufOf("x")))).toBe(
      "invalid_path",
    );
    expect(await codeOf(fs.write(root, "a.txt:stream", bufOf("x")))).toBe(
      "invalid_path",
    );
  });

  it("write THROUGH a parent symlink that escapes the root is denied", async () => {
    symlinkSync(outside, join(root, "escape"));
    expect(await codeOf(fs.write(root, "escape/pwn.txt", bufOf("x")))).toBe(
      "permission_denied",
    );
    // The escape target directory got no new file.
    expect(existsSync(join(outside, "pwn.txt"))).toBe(false);
  });

  it("write REFUSES to overwrite a symlink leaf (never follows it out)", async () => {
    symlinkSync(join(outside, "secret.txt"), join(root, "link.txt"));
    expect(await codeOf(fs.write(root, "link.txt", bufOf("x")))).toBe(
      "permission_denied",
    );
    // The out-of-root secret behind the link is untouched.
    expect(readFileSync(join(outside, "secret.txt"), "utf-8")).toBe(
      "TOP SECRET DATA",
    );
  });

  it("delete THROUGH an escaping symlink parent is denied (secret survives)", async () => {
    symlinkSync(outside, join(root, "escape"));
    expect(await codeOf(fs.delete(root, "escape/secret.txt"))).toBe(
      "permission_denied",
    );
    expect(existsSync(join(outside, "secret.txt"))).toBe(true);
  });

  it("delete REFUSES a symlink leaf (removes neither the link's target nor escapes)", async () => {
    symlinkSync(join(outside, "secret.txt"), join(root, "link.txt"));
    expect(await codeOf(fs.delete(root, "link.txt"))).toBe("permission_denied");
    expect(existsSync(join(outside, "secret.txt"))).toBe(true);
  });

  it("move cannot relocate a file OUT of the root via a symlink dest parent", async () => {
    symlinkSync(outside, join(root, "escape"));
    expect(await codeOf(fs.move(root, "file.txt", "escape/stolen.txt"))).toBe(
      "permission_denied",
    );
    expect(existsSync(join(root, "file.txt"))).toBe(true);
    expect(existsSync(join(outside, "stolen.txt"))).toBe(false);
  });

  it("write refuses content over the write byte ceiling (too_large)", async () => {
    const tooBig = Buffer.alloc(FS_LIMITS.maxWriteBytes + 1);
    expect(await codeOf(fs.write(root, "big.bin", tooBig))).toBe("too_large");
    expect(existsSync(join(root, "big.bin"))).toBe(false);
  });
});

describe("HostFs — sensitive-file write policy (G2)", () => {
  it("write denies creating a .env file", async () => {
    expect(await codeOf(fs.write(root, ".env", bufOf("SECRET=1")))).toBe(
      "permission_denied",
    );
    expect(existsSync(join(root, ".env"))).toBe(false);
  });

  it("write denies a *.pem key file and id_rsa (at any depth)", async () => {
    expect(await codeOf(fs.write(root, "server.pem", bufOf("k")))).toBe(
      "permission_denied",
    );
    expect(await codeOf(fs.write(root, "sub/id_rsa", bufOf("k")))).toBe(
      "permission_denied",
    );
  });

  it("edit denies overwriting an existing credentials file (contents preserved)", async () => {
    writeFileSync(join(root, "credentials"), "old");
    expect(await codeOf(fs.edit(root, "credentials", bufOf("new")))).toBe(
      "permission_denied",
    );
    expect(readRoot("credentials")).toBe("old");
  });

  it("delete denies removing a secret (.env survives)", async () => {
    writeFileSync(join(root, ".env"), "SECRET=1");
    expect(await codeOf(fs.delete(root, ".env"))).toBe("permission_denied");
    expect(existsSync(join(root, ".env"))).toBe(true);
  });

  it("move denies relocating a secret file AND landing on a secret name", async () => {
    writeFileSync(join(root, "id_rsa"), "KEY");
    expect(await codeOf(fs.move(root, "id_rsa", "moved_key"))).toBe(
      "permission_denied",
    );
    expect(existsSync(join(root, "id_rsa"))).toBe(true);
    expect(await codeOf(fs.move(root, "file.txt", ".env"))).toBe(
      "permission_denied",
    );
    expect(existsSync(join(root, "file.txt"))).toBe(true);
    expect(existsSync(join(root, ".env"))).toBe(false);
  });

  it("still writes an ordinary file (policy does not over-block)", async () => {
    const r = await fs.write(root, "notes.md", bufOf("plain"));
    expect(r.created).toBe(true);
  });
});

describe("HostFs — atomic write (all-or-nothing, no partial file)", () => {
  it("a failure before the commit rename leaves the target unchanged + no temp", async () => {
    const before = readRoot("file.txt");
    const deps: HostFsDeps = {
      ...defaultHostFsDeps(),
      beforeCommit: async () => {
        throw new Error("simulated crash before rename");
      },
    };
    await expect(
      new HostFs(deps).write(root, "file.txt", bufOf("SHOULD NOT LAND")),
    ).rejects.toThrow(/simulated crash/u);
    // Original bytes intact — the rename never ran.
    expect(readRoot("file.txt")).toBe(before);
    // No orphaned temp file left behind.
    expect(tempFilesIn(root)).toEqual([]);
  });

  it("a failed CREATE leaves no target file and no temp", async () => {
    const deps: HostFsDeps = {
      ...defaultHostFsDeps(),
      beforeCommit: async () => {
        throw new Error("boom");
      },
    };
    await expect(
      new HostFs(deps).write(root, "newfile.txt", bufOf("x")),
    ).rejects.toThrow(/boom/u);
    expect(existsSync(join(root, "newfile.txt"))).toBe(false);
    expect(tempFilesIn(root)).toEqual([]);
  });

  it("a successful write commits via a single rename (temp cleaned up)", async () => {
    await fs.write(root, "committed.txt", bufOf("done"));
    expect(readRoot("committed.txt")).toBe("done");
    expect(tempFilesIn(root)).toEqual([]);
  });
});

describe("HostFs — write TOCTOU (ancestor swap between resolve and use)", () => {
  // Swap an INTERMEDIATE ancestor (`sub`) of the resolved parent for a symlink
  // pointing OUTSIDE the root, AFTER resolve/authorize but BEFORE the atomic
  // parent-pin open. A decoy `outside/deep` exists so the escaped path is
  // openable — forcing the post-open recheck (not just an ENOENT) to be what
  // denies, exactly as in the read-side TOCTOU tests. The write target is
  // `sub/deep/planted.txt`, so `sub` is the parent's parent.
  function swapSubToOutsideSymlink(): () => Promise<void> {
    let fired = false;
    return async () => {
      if (fired) return;
      fired = true;
      await rm(join(root, "sub"), { recursive: true, force: true });
      await symlink(outside, join(root, "sub"));
    };
  }

  beforeEach(() => {
    // Decoy so the swapped path root/sub/deep resolves to a real outside dir.
    mkdirSync(join(outside, "deep"), { recursive: true });
  });

  it("darwin O_NOFOLLOW_ANY: a mid-flight ancestor swap is denied; nothing lands outside", async () => {
    if (process.platform !== "darwin") return;
    const deps: HostFsDeps = {
      ...defaultHostFsDeps(),
      afterResolve: swapSubToOutsideSymlink(),
    };
    expect(
      await codeOf(
        new HostFs(deps).write(root, "sub/deep/planted.txt", bufOf("x")),
      ),
    ).toBe("permission_denied");
    expect(existsSync(join(outside, "deep", "planted.txt"))).toBe(false);
  });

  it("non-darwin fallback: the post-open realpath recheck denies the swapped ancestor", async () => {
    // Force the O_NOFOLLOW-only (non-atomic) path even on macOS, proving the
    // recheck — not the darwin flag — catches an intermediate-ancestor swap.
    const deps: HostFsDeps = {
      ...defaultHostFsDeps(),
      platform: "linux",
      afterResolve: swapSubToOutsideSymlink(),
    };
    expect(
      await codeOf(
        new HostFs(deps).write(root, "sub/deep/planted.txt", bufOf("x")),
      ),
    ).toBe("permission_denied");
    expect(existsSync(join(outside, "deep", "planted.txt"))).toBe(false);
  });

  it("a legitimate write still succeeds when the seam does NOT swap", async () => {
    const deps: HostFsDeps = {
      ...defaultHostFsDeps(),
      afterResolve: async () => {
        /* no swap */
      },
    };
    const r = await new HostFs(deps).write(root, "sub/ok.txt", bufOf("fine"));
    expect(r.created).toBe(true);
    expect(readRoot("sub/ok.txt")).toBe("fine");
    // `lstatSync` proves it is a real regular file, not a symlink we followed.
    expect(lstatSync(join(root, "sub", "ok.txt")).isFile()).toBe(true);
  });
});

describe("HostFs — native atomic open (workspace-fs helper)", () => {
  // A faithful in-process stand-in for the native `openBeneath` primitive: it
  // resolves `rel` beneath `rootReal` reading the CURRENT filesystem state (so
  // it observes a mid-flight swap, exactly as openat2(RESOLVE_BENEATH) /
  // NtCreateFile would), refuses ANY symlink component (RESOLVE_NO_SYMLINKS)
  // with ELOOP and any out-of-root escape (RESOLVE_BENEATH) with EXDEV, and
  // otherwise hands back a real OS fd the Node side can fstat/read/close. This
  // lets us prove host-fs's native WIRING atomically here; the compiled C addon
  // is validated per-platform at build/packaging time (follow-up).
  interface FakeNative extends NativeWorkspaceFs {
    readonly calls: string[];
  }
  function makeFakeNative(): FakeNative {
    const calls: string[] = [];
    return {
      calls,
      platform: "linux",
      openBeneath(rootReal, rel, opts) {
        calls.push(rel);
        const parts = rel === "" ? [] : rel.split("/");
        let cur = rootReal;
        for (const part of parts) {
          cur = join(cur, part);
          let st: ReturnType<typeof lstatSync>;
          try {
            st = lstatSync(cur);
          } catch {
            throw Object.assign(new Error("no entry"), { code: "ENOENT" });
          }
          // RESOLVE_NO_SYMLINKS: a symlink in ANY component is refused.
          if (st.isSymbolicLink()) {
            throw Object.assign(new Error("symlink component"), {
              code: "ELOOP",
            });
          }
        }
        // RESOLVE_BENEATH: the resolved target must stay under the root.
        const real = parts.length === 0 ? rootReal : realpathSync(cur);
        const rel2 = relative(rootReal, real);
        if (rel2 !== "" && (rel2.startsWith("..") || isAbsolute(rel2))) {
          throw Object.assign(new Error("escapes root"), { code: "EXDEV" });
        }
        const flags = opts.directory
          ? constants.O_RDONLY | constants.O_DIRECTORY
          : constants.O_RDONLY;
        return openSync(parts.length === 0 ? rootReal : cur, flags);
      },
    };
  }

  function swapSubToOutsideSymlink(): () => Promise<void> {
    let fired = false;
    return async () => {
      if (fired) return;
      fired = true;
      await rm(join(root, "sub"), { recursive: true, force: true });
      await symlink(outside, join(root, "sub"));
    };
  }

  // Deps whose pure-Node post-open recheck is DELIBERATELY DEFEATED: `realpath`
  // reports no drift and `lstat` follows symlinks so the fstat-vs-lstat identity
  // check always matches. Under these deps the Node fallback would let an
  // intermediate-component swap escape — so any denial can ONLY come from the
  // native primitive, making the "atomic, not the recheck" claim airtight.
  function depsWithDefeatedRecheck(extra: Partial<HostFsDeps>): HostFsDeps {
    return {
      ...defaultHostFsDeps(),
      platform: "linux",
      realpath: async (p) => p,
      lstat: async (p) => statSync(p),
      ...extra,
    };
  }

  it("native path denies an intermediate-component swap ATOMICALLY (not the recheck)", async () => {
    const native = makeFakeNative();
    const hostFs = new HostFs(
      depsWithDefeatedRecheck({
        native,
        afterResolve: swapSubToOutsideSymlink(),
      }),
    );
    // root/sub is swapped to a symlink→outside AFTER resolve; the native
    // openBeneath observes the symlink component and refuses (ELOOP). The Node
    // recheck is defeated, so this denial is attributable solely to native.
    expect(await codeOf(hostFs.read(root, "sub/nested.txt"))).toBe(
      "permission_denied",
    );
    expect(native.calls).toContain("sub/nested.txt");
  });

  it("control: with the recheck defeated AND no native, the SAME swap escapes (so native is what closes it)", async () => {
    // No `native` → the Node O_NOFOLLOW-only path runs; O_NOFOLLOW guards only
    // the FINAL component, so the swapped intermediate `sub` symlink is
    // followed and (recheck defeated) the outside file leaks. This is the
    // residual the native helper exists to close — proven by contrast.
    const hostFs = new HostFs(
      depsWithDefeatedRecheck({ afterResolve: swapSubToOutsideSymlink() }),
    );
    const r = await hostFs.read(root, "sub/nested.txt");
    expect(Buffer.from(r.base64, "base64").toString("utf-8")).toBe(
      "OUTSIDE NESTED SECRET",
    );
  });

  it("native path allows a legitimate read (no swap) and is the code path taken", async () => {
    const native = makeFakeNative();
    const hostFs = new HostFs({
      ...defaultHostFsDeps(),
      platform: "linux",
      native,
    });
    const r = await hostFs.read(root, "sub/nested.txt");
    expect(Buffer.from(r.base64, "base64").toString("utf-8")).toContain(
      "needle",
    );
    expect(native.calls).toContain("sub/nested.txt");
  });

  it("native ENOSYS falls back to the Node recheck, which still denies the swap", async () => {
    const nativeEnosys: NativeWorkspaceFs = {
      platform: "linux",
      openBeneath() {
        throw Object.assign(new Error("no openat2"), { code: "ENOSYS" });
      },
    };
    const hostFs = new HostFs({
      ...defaultHostFsDeps(),
      platform: "linux",
      native: nativeEnosys,
      afterResolve: swapSubToOutsideSymlink(),
    });
    // ENOSYS → fall through to the (real, not defeated) Node recheck → denied.
    expect(await codeOf(hostFs.read(root, "sub/nested.txt"))).toBe(
      "permission_denied",
    );
  });

  it("darwin ignores the native helper (keeps its already-atomic Node path)", async () => {
    const native = makeFakeNative();
    const hostFs = new HostFs({
      ...defaultHostFsDeps(),
      platform: "darwin",
      native,
      afterResolve: swapSubToOutsideSymlink(),
    });
    // darwin's O_NOFOLLOW_ANY open denies the swap atomically; the native
    // helper must NOT be consulted (darwin path is unchanged).
    expect(await codeOf(hostFs.read(root, "sub/nested.txt"))).toBe(
      "permission_denied",
    );
    expect(native.calls).toEqual([]);
  });

  it("native parent-pin denies a write TOCTOU ancestor swap; nothing lands outside", async () => {
    mkdirSync(join(outside, "deep"), { recursive: true }); // decoy target dir
    const native = makeFakeNative();
    const hostFs = new HostFs(
      depsWithDefeatedRecheck({
        native,
        afterResolve: swapSubToOutsideSymlink(),
      }),
    );
    // Write target sub/deep/planted.txt → parent `sub/deep` is pinned via the
    // native open; the swapped `sub` symlink component is refused (ELOOP).
    expect(
      await codeOf(hostFs.write(root, "sub/deep/planted.txt", bufOf("x"))),
    ).toBe("permission_denied");
    expect(existsSync(join(outside, "deep", "planted.txt"))).toBe(false);
    expect(native.calls).toContain("sub/deep");
  });

  it("loader is graceful: loadNative() never throws and returns undefined or a valid helper", () => {
    // Until the addon is prebuilt / electron-rebuilt for the target ABI, no
    // `.node` binary exists and the loader must degrade to `undefined` (Node
    // fallback) rather than throw. If a binary IS present it must expose a
    // callable `openBeneath`. Both are acceptable — loading is always safe.
    const req = createRequire(import.meta.url);
    const mod = req("../../native/workspace-fs/index.cjs") as {
      loadNative: () => NativeWorkspaceFs | undefined;
    };
    expect(typeof mod.loadNative).toBe("function");
    const helper = mod.loadNative();
    expect(
      helper === undefined || typeof helper.openBeneath === "function",
    ).toBe(true);
  });

  // --- REAL compiled-addon integration (runs only when a binary is present) ---
  // These load the ACTUAL native `.node` (built via `npm run build` in
  // native/workspace-fs) and drive host-fs through it. When no binary exists in
  // the environment they skip — the fake-native tests above already cover the
  // wiring, and the compiled binary is validated per-platform at packaging time.
  function loadRealNative(): NativeWorkspaceFs | undefined {
    const req = createRequire(import.meta.url);
    const mod = req("../../native/workspace-fs/index.cjs") as {
      loadNative: () => NativeWorkspaceFs | undefined;
    };
    return mod.loadNative();
  }

  it("REAL addon denies a genuine intermediate-component symlink swap (skips if unbuilt)", async () => {
    const realNative = loadRealNative();
    if (realNative === undefined) return;
    const hostFs = new HostFs({
      ...defaultHostFsDeps(),
      platform: "linux", // force host-fs down the native branch
      native: realNative,
      afterResolve: swapSubToOutsideSymlink(),
    });
    expect(await codeOf(hostFs.read(root, "sub/nested.txt"))).toBe(
      "permission_denied",
    );
  });

  it("REAL addon opens a legitimate in-root file (skips if unbuilt)", async () => {
    const realNative = loadRealNative();
    if (realNative === undefined) return;
    const hostFs = new HostFs({
      ...defaultHostFsDeps(),
      platform: "linux",
      native: realNative,
    });
    const r = await hostFs.read(root, "sub/nested.txt");
    expect(Buffer.from(r.base64, "base64").toString("utf-8")).toContain(
      "needle",
    );
  });
});
