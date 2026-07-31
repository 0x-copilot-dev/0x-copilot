// build.mjs — compile the workspace-fs Node-API addon for THIS host.
//
// Invoked as `npm run build:workspace-fs --workspace @0x-copilot/desktop`, which
// `compile` and `test` both chain, exactly as build:workspace-commit-helper is
// chained. Before this script existed the addon was built by nothing at all, so
// loadNative() never found a binary and every confined read on Windows fell
// through to the non-atomic realpath recheck.
//
// ONE binary per {platform, arch} — not per runtime. src/workspace_fs.c is
// Node-API only, so a plain-Node build loads unchanged in Electron main
// (measured here: a Node 25 / modules=141 build read real bytes under Electron
// 43 / modules=148). There is deliberately no --runtime=electron mode; adding
// one would multiply the artifact matrix for no benefit and would rot the moment
// the pinned Electron moves.
//
// Failure posture, mirroring native/workspace-commit-helper/build.mjs:
//   - default        : a toolchain failure WARNS and exits 0, so `npm test` and
//                      `npm run dev` still work on a box without node-gyp's
//                      prerequisites. prebuilds/UNAVAILABLE.txt records why, and
//                      index.cjs decides at load time whether running without
//                      the addon is acceptable on this platform.
//   - --require      : any failure exits non-zero. Every path that produces a
//                      DISTRIBUTABLE passes it (`package`, `dist:*`, and the
//                      release workflow), so a shipped build cannot be missing
//                      the addon on a platform that needs it.
//
// The build is not trusted on its own: a compiled .node that cannot be loaded,
// or that loads but does not actually refuse an escape, is worse than an absent
// one because index.cjs would treat it as a working atomic primitive. So the
// last step runs selfcheck.cjs against the emitted binary and a real temp
// directory, and a selfcheck failure fails the build like a compile error.

import { spawnSync } from "node:child_process";
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const TARGET = `${process.platform}-${process.arch}`;
const PREBUILDS = join(HERE, "prebuilds");
const OUT_DIR = join(PREBUILDS, TARGET);
const OUT = join(OUT_DIR, "workspace_fs.node");
const SENTINEL = join(PREBUILDS, "UNAVAILABLE.txt");

function log(message) {
  process.stdout.write(`[workspace-fs] ${message}\n`);
}

// --- arguments -------------------------------------------------------------
// Parsed before `unavailable` is defined so the "who required this?" attribution
// it prints is settled by the time anything can call it.

let required = process.env.COPILOT_REQUIRE_NATIVE_WORKSPACE_FS === "1";
let requiredBy = required ? "COPILOT_REQUIRE_NATIVE_WORKSPACE_FS=1" : "";
let requestedTarget;
for (let i = 2; i < process.argv.length; i++) {
  const arg = process.argv[i];
  if (arg === "--require") {
    required = true;
    requiredBy = "--require";
  } else if (arg === "--target") requestedTarget = process.argv[++i];
  else {
    process.stderr.write(
      `[workspace-fs] ERROR: unknown argument ${arg}\n` +
        `usage: node build.mjs [--require] [--target <platform>-<arch>]\n`,
    );
    process.exit(1);
  }
}

/**
 * Record the reason no binary exists, leave `prebuilds/` present, and exit.
 *
 * The directory must exist even on failure: electron-builder's extraResources
 * entry names it as a source, and a missing source is a cryptic packaging error
 * instead of a legible one. A packaged app that reaches a user without the addon
 * therefore carries a human-readable reason next to where the binary should be.
 *
 * @returns {never}
 */
function unavailable(reason) {
  mkdirSync(PREBUILDS, { recursive: true });
  writeFileSync(
    SENTINEL,
    `No workspace_fs.node for ${TARGET}.\n\n${reason}\n\n` +
      `Rebuild with:\n` +
      `  npm run build:workspace-fs --workspace @0x-copilot/desktop\n\n` +
      `Without this binary the confined read on Windows and Linux uses a\n` +
      `post-open realpath recheck, which denies the same escapes but NOT\n` +
      `atomically. index.cjs refuses the read outright in a production install\n` +
      `rather than serving it through that race.\n`,
  );
  if (required) {
    process.stderr.write(
      `[workspace-fs] ERROR: ${reason}\n` +
        `[workspace-fs] The addon is REQUIRED for this build (${requiredBy}), so\n` +
        `[workspace-fs] this is a failure. A distributable must not ship without\n` +
        `[workspace-fs] it on a platform whose confined read depends on it.\n`,
    );
    process.exit(1);
  }
  log(`WARNING: ${reason}`);
  log(
    `continuing without a native binary (see ${SENTINEL}); pass --require to ` +
      `make this fatal`,
  );
  process.exit(0);
}

// node-gyp compiles with the host toolchain; there is no cross-build. Refusing a
// mismatched --target loudly is the point of accepting the flag at all: the
// release matrix names its target explicitly and must fail if it is ever
// scheduled onto the wrong runner rather than quietly emitting a host binary
// under the requested target's directory name.
if (requestedTarget !== undefined && requestedTarget !== TARGET) {
  process.stderr.write(
    `[workspace-fs] ERROR: asked for ${requestedTarget} on a ${TARGET} host.\n` +
      `[workspace-fs] node-gyp cannot cross-compile — build each target on a\n` +
      `[workspace-fs] matching runner (see the matrix in ci-desktop.yml).\n`,
  );
  process.exit(1);
}

// --- locate node-gyp -------------------------------------------------------

/**
 * node-gyp resolved from the workspace install rather than from PATH.
 *
 * PATH would work under `npm run` but not when this script is invoked directly,
 * and on Windows the PATH entry is a `.cmd` shim that needs a shell. Spawning
 * `node <node-gyp.js>` needs neither.
 */
function resolveNodeGyp(start) {
  let dir = start;
  for (;;) {
    const candidate = join(
      dir,
      "node_modules",
      "node-gyp",
      "bin",
      "node-gyp.js",
    );
    if (existsSync(candidate)) return candidate;
    const parent = dirname(dir);
    if (parent === dir) return undefined;
    dir = parent;
  }
}

const nodeGyp = resolveNodeGyp(HERE);
if (nodeGyp === undefined) {
  unavailable(
    "node-gyp was not found in any ancestor node_modules. Run `npm ci` at the " +
      "repository root.",
  );
}

// --- compile ---------------------------------------------------------------

log(`building ${TARGET} with ${nodeGyp}`);
const build = spawnSync(process.execPath, [nodeGyp, "rebuild"], {
  cwd: HERE,
  stdio: "inherit",
});
if (build.error) {
  unavailable(`node-gyp could not be spawned: ${build.error.message}`);
}
if (build.status !== 0) {
  unavailable(
    `node-gyp rebuild exited ${build.status}. A C toolchain and Python are ` +
      `required (Xcode CLT on macOS, MSVC Build Tools on Windows).`,
  );
}

const compiled = join(HERE, "build", "Release", "workspace_fs.node");
if (!existsSync(compiled)) {
  unavailable(`node-gyp reported success but produced no ${compiled}`);
}

// --- place -----------------------------------------------------------------

// Rename into place so a concurrent loader never observes a partially-copied
// binary: the addon may already be loaded by a running dev app.
mkdirSync(OUT_DIR, { recursive: true });
const staging = `${OUT}.${process.pid}.tmp`;
copyFileSync(compiled, staging);
renameSync(staging, OUT);

// --- prove it actually works ----------------------------------------------

const selfcheck = spawnSync(
  process.execPath,
  [join(HERE, "selfcheck.cjs"), OUT],
  { stdio: "inherit" },
);
if (selfcheck.error || selfcheck.status !== 0) {
  // Do not leave a binary index.cjs would treat as a working atomic primitive.
  rmSync(OUT, { force: true });
  unavailable(
    `the compiled addon failed selfcheck.cjs (exit ${selfcheck.status}) and was ` +
      `deleted. A binary that loads but does not refuse an escape is worse than ` +
      `no binary at all.`,
  );
}

rmSync(SENTINEL, { force: true });
log(`ok: ${OUT}`);
