// selfcheck.cjs — prove a compiled workspace_fs.node actually confines reads.
//
// Run by build.mjs against every binary it emits, and safe to run by hand on a
// user's machine to answer "is the atomic confined open working here?".
//
//   node selfcheck.cjs [path/to/workspace_fs.node]
//
// Why this exists as a gate rather than a test: index.cjs treats ANY loadable
// module exporting `openBeneath` as a working atomic primitive and stops warning.
// A binary that loads but does not refuse an escape — or whose kernel primitive
// answers ENOSYS — would therefore be indistinguishable from a good one at load
// time while providing none of the guarantee. So the build proves the guarantee
// against a real temp directory, a real symlink and a real read, and deletes the
// artifact if the proof fails.
//
// CommonJS on purpose: this is the exact `require()` the Electron main process
// performs, so a module-format or ABI problem surfaces here rather than at
// first use in the app.

"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const INSIDE = "workspace-fs selfcheck: real bytes from inside the root\n";
const OUTSIDE = "workspace-fs selfcheck: MUST NOT BE READABLE\n";

function fail(message) {
  process.stderr.write(`[selfcheck] FAIL: ${message}\n`);
  process.exit(1);
}

function resolveBinary() {
  const supplied = process.argv[2];
  if (supplied !== undefined) return path.resolve(supplied);
  const target = `${process.platform}-${process.arch}`;
  return path.join(__dirname, "prebuilds", target, "workspace_fs.node");
}

const binary = resolveBinary();
if (!fs.existsSync(binary)) fail(`no such binary: ${binary}`);

let addon;
try {
  addon = require(binary);
} catch (error) {
  fail(
    `require(${binary}) threw: ${error && error.message}. An addon that cannot ` +
      `be loaded by this runtime must not ship as if it could.`,
  );
}
if (!addon || typeof addon.openBeneath !== "function") {
  fail(`${binary} loaded but exports no openBeneath function`);
}

// --- fixture ---------------------------------------------------------------
// Absolute paths throughout: a Windows junction target must be absolute.

const root = fs.mkdtempSync(path.join(os.tmpdir(), "wfs-selfcheck-"));
const outside = fs.mkdtempSync(path.join(os.tmpdir(), "wfs-selfcheck-out-"));
let linked = true;
try {
  fs.mkdirSync(path.join(root, "sub"));
  fs.writeFileSync(path.join(root, "sub", "inside.txt"), INSIDE);
  fs.writeFileSync(path.join(outside, "secret.txt"), OUTSIDE);
  // "junction" is the only reparse point an unprivileged Windows account can
  // create; on POSIX the type argument is ignored and this is a plain symlink.
  // Either way it is the escape vector the addon must refuse.
  try {
    fs.symlinkSync(outside, path.join(root, "escape"), "junction");
  } catch (error) {
    linked = false;
    process.stdout.write(
      `[selfcheck] note: could not create a reparse point (${error && error.code}) — ` +
        `skipping the symlink-component case on this host\n`,
    );
  }

  // 1. A real file beneath the root reads back byte-exact. This is the case the
  //    orphaned addon never got to serve: an empty or wrong answer here is the
  //    defect class the whole exercise is about.
  let fd;
  try {
    fd = addon.openBeneath(root, "sub/inside.txt", false, false);
  } catch (error) {
    fail(
      `openBeneath refused a legitimate file beneath the root: ${error && error.code}`,
    );
  }
  let text;
  try {
    const buffer = Buffer.alloc(INSIDE.length * 2);
    const read = fs.readSync(fd, buffer, 0, buffer.length, 0);
    text = buffer.subarray(0, read).toString("utf8");
  } finally {
    fs.closeSync(fd);
  }
  if (text !== INSIDE) {
    fail(
      `read back ${JSON.stringify(text)}, expected ${JSON.stringify(INSIDE)}`,
    );
  }

  // 2. Every escape is refused, and refused with a code host-fs maps to a
  //    denial. A refusal that leaked the outside bytes is reported with the
  //    leak so the failure is not merely "wrong code".
  const cases = [
    ...(linked
      ? [
          {
            rel: "escape/secret.txt",
            // Reparse/symlink component: ELOOP on every platform's primitive
            // (RESOLVE_NO_SYMLINKS, O_NOFOLLOW_ANY, the NtCreateFile walk's
            // FILE_ATTRIBUTE_REPARSE_POINT refusal). EXDEV is RESOLVE_BENEATH
            // answering first on Linux.
            codes: ["ELOOP", "EXDEV"],
          },
        ]
      : []),
    // Refused by wfs_relative_path_is_beneath before any syscall, so the code is
    // the same on every platform.
    { rel: `../${path.basename(outside)}/secret.txt`, codes: ["EPERM"] },
    { rel: path.join(outside, "secret.txt"), codes: ["EPERM"] },
  ];
  for (const { rel, codes } of cases) {
    let escaped;
    try {
      escaped = addon.openBeneath(root, rel, false, false);
    } catch (error) {
      const code = error && error.code;
      if (!codes.includes(code)) {
        fail(
          `openBeneath(${JSON.stringify(rel)}) was refused with ${code}, ` +
            `expected one of ${codes.join("/")}`,
        );
      }
      continue;
    }
    const buffer = Buffer.alloc(OUTSIDE.length);
    const read = fs.readSync(escaped, buffer, 0, buffer.length, 0);
    fs.closeSync(escaped);
    fail(
      `openBeneath(${JSON.stringify(rel)}) SUCCEEDED and read ` +
        `${JSON.stringify(buffer.subarray(0, read).toString("utf8"))} — the ` +
        `confinement does not hold on this platform`,
    );
  }

  // 3. A directory handle, which the read pipeline needs for listings.
  const dirFd = addon.openBeneath(root, "sub", true, false);
  if (typeof dirFd !== "number" || dirFd < 0) {
    fail(`directory open returned ${String(dirFd)} instead of an fd`);
  }
  fs.closeSync(dirFd);

  process.stdout.write(
    `[selfcheck] ok: ${path.basename(binary)} on ${process.platform}-${process.arch} ` +
      `(node ${process.versions.node}, napi ${process.versions.napi}) — real read, ` +
      `${cases.length} escape${cases.length === 1 ? "" : "s"} refused\n`,
  );
} finally {
  fs.rmSync(root, { recursive: true, force: true });
  fs.rmSync(outside, { recursive: true, force: true });
}
