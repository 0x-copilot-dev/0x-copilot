// Real-filesystem behaviour of the compiled addon: does openBeneath actually
// read the bytes that are there, and actually refuse every escape?
//
// These cases run against a real temp tree and the real kernel primitive, so
// each platform proves its own branch of src/workspace_fs.c:
//   darwin  openat + O_NOFOLLOW_ANY
//   linux   openat2(RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS | RESOLVE_NO_MAGICLINKS)
//   win32   the per-component NtCreateFile walk with FILE_OPEN_REPARSE_POINT
// The win32 branch has never been executed on a Windows host in this repo; the
// ci-desktop `native-workspace-fs (windows-latest)` job is what turns that from
// a claim into evidence.
//
// When no binary exists the whole file SKIPS WITH A STATED REASON. It must never
// pass vacuously: a silent green here would mean exactly the thing this work
// exists to remove — an empty answer that looks like a successful one.

import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import test from "node:test";

const require = createRequire(import.meta.url);
const target = `${process.platform}-${process.arch}`;
const binary = path.join(
  import.meta.dirname,
  "prebuilds",
  target,
  "workspace_fs.node",
);

let addon;
let unavailable;
if (!fs.existsSync(binary)) {
  unavailable =
    `no compiled addon at ${binary} — run ` +
    `\`npm run build:workspace-fs --workspace @0x-copilot/desktop\``;
} else {
  try {
    addon = require(binary);
  } catch (error) {
    unavailable = `require(${binary}) failed: ${error && error.message}`;
  }
}
// A skip is a result, not silence: name the binary and the remedy.
const options = unavailable === undefined ? {} : { skip: unavailable };

const INSIDE = "real bytes, beneath the root\n";
const OUTSIDE = "MUST NOT BE READABLE\n";

/**
 * A root containing `sub/inside.txt`, plus an out-of-tree directory holding
 * `secret.txt` and whatever links into it this host allows.
 */
function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "wfs-"));
  const outside = fs.mkdtempSync(path.join(os.tmpdir(), "wfs-out-"));
  fs.mkdirSync(path.join(root, "sub"));
  fs.writeFileSync(path.join(root, "sub", "inside.txt"), INSIDE);
  const secret = path.join(outside, "secret.txt");
  fs.writeFileSync(secret, OUTSIDE);

  // A directory reparse point: a Windows junction (creatable without elevation)
  // or a POSIX symlink. `type` is ignored off Windows.
  let dirLink;
  try {
    fs.symlinkSync(outside, path.join(root, "escape-dir"), "junction");
    dirLink = "escape-dir";
  } catch {
    dirLink = undefined;
  }
  // A file reparse point. On Windows this needs SeCreateSymbolicLinkPrivilege or
  // Developer Mode, so it is allowed to be absent.
  let fileLink;
  try {
    fs.symlinkSync(secret, path.join(root, "escape-file"), "file");
    fileLink = "escape-file";
  } catch {
    fileLink = undefined;
  }

  return {
    root,
    outside,
    dirLink,
    fileLink,
    cleanup() {
      fs.rmSync(root, { recursive: true, force: true });
      fs.rmSync(outside, { recursive: true, force: true });
    },
  };
}

function withFixture(body) {
  const f = fixture();
  try {
    body(f);
  } finally {
    f.cleanup();
  }
}

/** Open, read the whole thing, close. */
function readAll(root, rel) {
  const fd = addon.openBeneath(root, rel, false, false);
  try {
    const chunks = [];
    const buffer = Buffer.alloc(4096);
    let position = 0;
    for (;;) {
      const read = fs.readSync(fd, buffer, 0, buffer.length, position);
      if (read === 0) break;
      chunks.push(Buffer.from(buffer.subarray(0, read)));
      position += read;
    }
    return Buffer.concat(chunks).toString("utf8");
  } finally {
    fs.closeSync(fd);
  }
}

/**
 * Assert `rel` is refused, and report a leak as a leak. `codes` is a set because
 * the same refusal is legitimately named differently per kernel; an EMPTY or
 * SUCCESSFUL answer is never legitimate.
 */
function assertRefused(root, rel, codes) {
  let fd;
  try {
    fd = addon.openBeneath(root, rel, false, false);
  } catch (error) {
    assert.ok(
      codes.includes(error.code),
      `${JSON.stringify(rel)} refused with ${error.code}; expected one of ${codes.join("/")}`,
    );
    return;
  }
  const buffer = Buffer.alloc(256);
  const read = fs.readSync(fd, buffer, 0, buffer.length, 0);
  fs.closeSync(fd);
  assert.fail(
    `${JSON.stringify(rel)} was OPENED and yielded ` +
      `${JSON.stringify(buffer.subarray(0, read).toString("utf8"))} — the ` +
      `confinement does not hold on ${target}`,
  );
}

// --- the read that the orphaned addon never got to serve -------------------

test("reads the real bytes of a real file beneath the root", options, () => {
  withFixture(({ root }) => {
    assert.equal(readAll(root, "sub/inside.txt"), INSIDE);
  });
});

test("opens the root itself as a directory", options, () => {
  withFixture(({ root }) => {
    for (const rel of ["", "."]) {
      const fd = addon.openBeneath(root, rel, true, false);
      assert.ok(
        typeof fd === "number" && fd >= 0,
        `rel=${JSON.stringify(rel)}`,
      );
      fs.closeSync(fd);
    }
  });
});

test("opens a subdirectory as a directory", options, () => {
  withFixture(({ root }) => {
    const fd = addon.openBeneath(root, "sub", true, false);
    fs.closeSync(fd);
  });
});

test("a missing path is ENOENT, not an empty success", options, () => {
  withFixture(({ root }) => {
    assertRefused(root, "sub/absent.txt", ["ENOENT"]);
    assertRefused(root, "absent-dir/inside.txt", ["ENOENT", "ENOTDIR"]);
  });
});

test("write=true yields a read-write descriptor", options, () => {
  withFixture(({ root }) => {
    const fd = addon.openBeneath(root, "sub/inside.txt", false, true);
    try {
      fs.writeSync(fd, Buffer.from("Z"), 0, 1, 0);
    } finally {
      fs.closeSync(fd);
    }
    assert.equal(
      fs.readFileSync(path.join(root, "sub", "inside.txt"), "utf8"),
      `Z${INSIDE.slice(1)}`,
    );
  });
});

// --- refusals --------------------------------------------------------------

test("a traversal component is refused before any syscall", options, () => {
  withFixture(({ root, outside }) => {
    const escape = `../${path.basename(outside)}/secret.txt`;
    // wfs_relative_path_is_beneath rejects these in C, so the code is the same
    // on every platform — no kernel involved.
    assertRefused(root, escape, ["EPERM"]);
    assertRefused(root, "sub/../sub/inside.txt", ["EPERM"]);
    assertRefused(root, "..", ["EPERM"]);
  });
});

test("an absolute path is refused", options, () => {
  withFixture(({ root, outside }) => {
    assertRefused(root, path.join(outside, "secret.txt"), ["EPERM"]);
  });
});

test("a backslash separator is refused rather than walked", options, () => {
  withFixture(({ root }) => {
    // Matters most on Windows, where a caller might send a native separator: the
    // guard refuses the whole string rather than letting the tokeniser decide
    // what the components were.
    assertRefused(root, "sub\\inside.txt", ["EPERM"]);
  });
});

test("a single-dot component is refused", options, () => {
  withFixture(({ root }) => {
    assertRefused(root, "./sub/inside.txt", ["EPERM"]);
    assertRefused(root, "sub//inside.txt", ["EPERM"]);
  });
});

test(
  "a directory reparse point in the middle of the path is refused",
  options,
  (t) => {
    withFixture(({ root, dirLink }) => {
      if (dirLink === undefined) {
        t.skip("this host would not let the test create a junction/symlink");
        return;
      }
      // ELOOP from O_NOFOLLOW_ANY / RESOLVE_NO_SYMLINKS / the walk's
      // FILE_ATTRIBUTE_REPARSE_POINT refusal; EXDEV when RESOLVE_BENEATH answers
      // first on Linux.
      assertRefused(root, `${dirLink}/secret.txt`, ["ELOOP", "EXDEV"]);
    });
  },
);

test("a symlinked final component is refused", options, (t) => {
  withFixture(({ root, fileLink }) => {
    if (fileLink === undefined) {
      t.skip(
        "this host would not let the test create a file symlink (Windows needs " +
          "Developer Mode or elevation)",
      );
      return;
    }
    assertRefused(root, fileLink, ["ELOOP", "EXDEV"]);
  });
});

test(
  "a reparse point cannot be laundered through a traversal",
  options,
  (t) => {
    withFixture(({ root, dirLink }) => {
      if (dirLink === undefined) {
        t.skip("this host would not let the test create a junction/symlink");
        return;
      }
      assertRefused(root, `sub/../${dirLink}/secret.txt`, ["EPERM"]);
    });
  },
);

// --- the packaged layout ---------------------------------------------------

test("the packaged extraResources layout resolves and reads", options, () => {
  // The electron-builder contract end to end, without running electron-builder:
  //   files:          native/workspace-fs/index.cjs -> <appRoot>/native/workspace-fs/
  //   extraResources: native/workspace-fs/prebuilds -> <resourcesPath>/workspace-fs/
  // The loader must find the binary under resourcesPath even though nothing sits
  // beside index.cjs — which is exactly the packaged case, since the .node is
  // deliberately kept out of the asar.
  const stage = fs.mkdtempSync(path.join(os.tmpdir(), "wfs-pkg-"));
  try {
    const appRoot = path.join(stage, "app.asar");
    const loaderDir = path.join(appRoot, "native", "workspace-fs");
    const resources = path.join(stage, "Resources");
    fs.mkdirSync(loaderDir, { recursive: true });
    fs.copyFileSync(
      path.join(import.meta.dirname, "index.cjs"),
      path.join(loaderDir, "index.cjs"),
    );
    const resourceTarget = path.join(resources, "workspace-fs", target);
    fs.mkdirSync(resourceTarget, { recursive: true });
    fs.copyFileSync(binary, path.join(resourceTarget, "workspace_fs.node"));

    const { loadNative } = require(path.join(loaderDir, "index.cjs"));
    const native = loadNative({
      dir: loaderDir,
      resourcesPath: resources,
      isPackaged: true,
      env: {},
    });
    assert.equal(native.available, true, "the packaged binary must be found");

    withFixture(({ root }) => {
      const fd = native.openBeneath(root, "sub/inside.txt", {
        directory: false,
      });
      try {
        const buffer = Buffer.alloc(INSIDE.length);
        fs.readSync(fd, buffer, 0, buffer.length, 0);
        assert.equal(buffer.toString("utf8"), INSIDE);
      } finally {
        fs.closeSync(fd);
      }
    });
  } finally {
    // This test require()s the staged .node, and Windows will not let a loaded
    // native module be deleted — the loader holds the image open and Node has
    // no way to unload an addon. That lock is inherent to what the test proves,
    // not a defect, and every assertion above has already run by this point.
    // The stage lives in the OS temp directory, so leaving it is harmless.
    // Narrow on purpose: any other cleanup failure, and every failure on every
    // other platform, still fails the test.
    try {
      fs.rmSync(stage, { recursive: true, force: true });
    } catch (error) {
      if (process.platform !== "win32" || error.code !== "EPERM") throw error;
    }
  }
});
