// Loader for the workspace-fs native addon.
//
// Resolves the compiled `.node` binary next to this file (node-gyp emits it at
// build/Release/workspace_fs.node) and exposes a small, typed surface. If the
// binary is ABSENT or FAILS to load — which is the norm until the addon is
// prebuilt / electron-rebuilt for the target ABI — `loadNative()` returns
// `undefined` and host-fs transparently keeps its pure-Node fallback. Loading
// therefore never throws; a missing binary is a supported, non-fatal state.
//
// BUILD / PACKAGING (follow-up — see native/workspace-fs/BUILD note in this
// file's header comment history and the desktop README task):
//   - Dev/host ABI:   node-gyp rebuild            (npm run build)
//   - Electron ABI:   node-gyp rebuild --runtime=electron --dist-url=... \
//                       --target=<electron version>   (npm run build:electron)
//     or, preferably, @electron/rebuild driven from apps/desktop postinstall.
//   - Distribution:   ship prebuilt binaries per {platform, arch, ABI} (e.g.
//     prebuildify / prebuild-install) and add native/workspace-fs to
//     electron-builder `files` + `asarUnpack` so the .node is extracted.
// None of this is required for correctness: without a binary the Node fallback
// (final-component O_NOFOLLOW + post-open realpath recheck) still denies every
// escape — just non-atomically on Linux/Windows.

"use strict";

const { join } = require("node:path");

/**
 * @returns {import("./index").NativeWorkspaceFs | undefined}
 */
function loadNative() {
  // Candidate locations node-gyp / prebuild tooling may emit to.
  const candidates = [
    join(__dirname, "build", "Release", "workspace_fs.node"),
    join(__dirname, "build", "Debug", "workspace_fs.node"),
    join(
      __dirname,
      "prebuilds",
      `${process.platform}-${process.arch}`,
      "workspace_fs.node",
    ),
  ];
  for (const candidate of candidates) {
    try {
      const addon = require(candidate);
      if (addon && typeof addon.openBeneath === "function") {
        return {
          platform: process.platform,
          openBeneath: (root, rel, opts) =>
            addon.openBeneath(
              root,
              rel,
              Boolean(opts && opts.directory),
              Boolean(opts && opts.write),
            ),
        };
      }
    } catch {
      // Not built for this candidate path / ABI — try the next one.
    }
  }
  return undefined;
}

module.exports = { loadNative };
