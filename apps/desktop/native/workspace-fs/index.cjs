// Loader for the workspace-fs native addon, and the single source of truth for
// WHICH platforms the confined read actually depends on it.
//
// The addon supplies the kernel's own root-confined, symlink/reparse-refusing
// open (openat2(RESOLVE_BENEATH) on Linux, a reparse-refusing NtCreateFile walk
// on Windows). host-fs.ts prefers it on every non-darwin platform and otherwise
// falls back to `O_NOFOLLOW` + a post-open realpath recheck — which denies the
// same escapes but NON-ATOMICALLY, i.e. with a TOCTOU window on every confined
// read. darwin needs nothing: its pure-Node open already carries O_NOFOLLOW_ANY.
//
// So the addon is OPTIONAL on darwin and LOAD-BEARING on win32/linux, and this
// module makes that difference explicit instead of leaving it to a comment:
//
//   - Development / unpackaged: a missing binary degrades to the Node fallback
//     and logs ONE loud warning. Fast iteration keeps working.
//   - Production posture (packaged install, or the `copilot` CLI's
//     COPILOT_PRODUCTION=1, or a supervised COPILOT_RUNTIME_DIR stack) on a
//     platform that REQUIRES the addon: `loadNative()` returns a FAIL-CLOSED
//     stand-in whose `openBeneath` throws EPERM. host-fs's native branch
//     rethrows anything that is not ENOSYS/ENOTSUP, so every confined read and
//     write is DENIED rather than quietly served through the non-atomic path.
//     Silently degrading to a TOCTOU open is the defect class this exists to
//     remove, so the degradation is either impossible or extremely loud.
//
// `loadNative()` NEVER throws: host-fs.ts wraps the require in a try/catch that
// returns `undefined`, so a throw here would be swallowed and would land right
// back on the silent fallback. The fail-closed signal therefore has to be a
// RETURNED value, not an exception.
//
// BUILD + PACKAGING (see ./README.md for the full contract):
//   - build:   node build.mjs            (apps/desktop `npm run build:workspace-fs`,
//              chained into `compile` and `test` so it cannot be forgotten)
//   - artifact: prebuilds/<platform>-<arch>/workspace_fs.node
//   - packaged: electron-builder extraResources maps prebuilds/ to
//     <resourcesPath>/workspace-fs/, and the CLI payload carries this file plus
//     prebuilds/ next to the app.

"use strict";

const { join } = require("node:path");

/**
 * Per-platform dependency on the addon. `required: true` means the confined
 * read has NO atomic primitive without it.
 *
 * Keyed by `process.platform`. An unlisted platform is treated as required —
 * an unknown kernel has not been shown to close the race, and guessing in the
 * permissive direction is how a TOCTOU window ships.
 */
const NATIVE_REQUIREMENT = Object.freeze({
  darwin: Object.freeze({
    required: false,
    reason:
      "O_NOFOLLOW_ANY refuses a symlink in ANY component during the kernel " +
      "path-walk, so the pure-Node open is already atomic",
  }),
  win32: Object.freeze({
    required: true,
    reason:
      "O_NOFOLLOW guards only the FINAL component on Windows; without the " +
      "reparse-refusing NtCreateFile walk the confined read falls back to a " +
      "non-atomic post-open realpath recheck (TOCTOU window per read)",
  }),
  linux: Object.freeze({
    required: true,
    reason:
      "O_NOFOLLOW guards only the FINAL component on Linux; without " +
      "openat2(RESOLVE_BENEATH|RESOLVE_NO_SYMLINKS) the confined read falls " +
      "back to a non-atomic post-open realpath recheck (TOCTOU window per read)",
  }),
});

const UNKNOWN_PLATFORM_REQUIREMENT = Object.freeze({
  required: true,
  reason:
    "unknown platform: no atomic root-confined open primitive has been " +
    "verified here, so the addon is treated as load-bearing",
});

/**
 * @param {string} platform a `process.platform` value
 * @returns {{ required: boolean, reason: string }}
 */
function nativeRequirement(platform) {
  return Object.prototype.hasOwnProperty.call(NATIVE_REQUIREMENT, platform)
    ? NATIVE_REQUIREMENT[platform]
    : UNKNOWN_PLATFORM_REQUIREMENT;
}

/**
 * Production posture, mirroring main/posture.ts + main/services/boot-mode.ts
 * (duplicated rather than imported: this is a CJS module loaded through a
 * runtime require from the bundled main process, and it must also load under
 * the plain-Node test runner).
 *
 * `app.isPackaged` alone is not enough — the `copilot` CLI launches Electron
 * pointed at a directory, so a genuine end-user install reports FALSE. The CLI
 * sets COPILOT_PRODUCTION=1, and a supervised stack is named by
 * COPILOT_RUNTIME_DIR; both are real installs.
 *
 * @param {Readonly<Record<string, string | undefined>>} env
 * @param {boolean} isPackaged
 * @returns {boolean}
 */
function isProductionPosture(env, isPackaged) {
  if (env.COPILOT_DEV === "1") return false;
  if (isPackaged) return true;
  if (env.COPILOT_PRODUCTION === "1") return true;
  const runtimeDir = env.COPILOT_RUNTIME_DIR;
  return runtimeDir !== undefined && runtimeDir !== "";
}

/**
 * Explicit, loudly-logged opt-out. Without it a Windows install whose addon
 * failed to build would have no path forward at all; with it, running on the
 * non-atomic fallback is a deliberate, named choice rather than an accident.
 *
 * @param {Readonly<Record<string, string | undefined>>} env
 * @returns {boolean}
 */
function nonAtomicFallbackAllowed(env) {
  return env.COPILOT_ALLOW_NONATOMIC_WORKSPACE_FS === "1";
}

/** `app.isPackaged` when the electron module is reachable, else false. */
function detectPackaged() {
  try {
    // Guarded: absent under the plain-Node test runner and in any non-Electron
    // consumer, where "not packaged" is the correct answer anyway.
    const electron = require("electron");
    return Boolean(electron && electron.app && electron.app.isPackaged);
  } catch {
    return false;
  }
}

/**
 * Every location a compiled binary may live, most-specific first.
 *
 * @param {string} dir this module's directory
 * @param {string} platform
 * @param {string} arch
 * @param {string | undefined} resourcesPath `process.resourcesPath`
 * @returns {string[]}
 */
function candidatePaths(dir, platform, arch, resourcesPath) {
  const target = `${platform}-${arch}`;
  const candidates = [
    // Canonical build output (build.mjs), and what the CLI payload carries.
    join(dir, "prebuilds", target, "workspace_fs.node"),
    // Raw node-gyp output, so a bare `node-gyp rebuild` is still picked up.
    join(dir, "build", "Release", "workspace_fs.node"),
    join(dir, "build", "Debug", "workspace_fs.node"),
  ];
  if (typeof resourcesPath === "string" && resourcesPath !== "") {
    // electron-builder extraResources: prebuilds/ -> <resourcesPath>/workspace-fs
    candidates.push(
      join(resourcesPath, "workspace-fs", target, "workspace_fs.node"),
      join(resourcesPath, "workspace-fs", "workspace_fs.node"),
    );
  }
  return candidates;
}

/**
 * Wrap a raw addon in the documented JS surface. Returns `undefined` when the
 * module loaded but does not expose the primitive.
 *
 * @param {Record<string, unknown>} addon
 * @param {string} platform
 * @returns {import("./index").NativeWorkspaceFs | undefined}
 */
function wrapAddon(addon, platform) {
  if (!addon || typeof addon.openBeneath !== "function") return undefined;
  const openBeneath = addon.openBeneath;
  /** @type {Record<string, unknown>} */
  const wrapped = {
    platform,
    available: true,
    openBeneath: (root, rel, opts) =>
      openBeneath(
        root,
        rel,
        Boolean(opts && opts.directory),
        Boolean(opts && opts.write),
      ),
  };
  // C2 is intentionally all-or-nothing for writes. Preserve the richer
  // native methods only when the compiled addon exports the complete v2
  // handle-relative lifecycle; Electron main otherwise reports writable
  // capability unavailable and never falls back to node:fs mutations.
  const v2 = [
    "workspaceRootIdentity",
    "workspacePrepare",
    "workspaceWrite",
    "workspaceSeal",
    "workspaceCommit",
    "workspaceReconcile",
    "workspaceReconcileClaim",
    "workspaceAbort",
    "workspaceProposeRecovery",
    "workspaceProposeRecoveryClaim",
  ];
  if (v2.every((name) => typeof addon[name] === "function")) {
    for (const name of v2) {
      wrapped[name] = (...args) => addon[name](...args);
    }
  }
  return /** @type {import("./index").NativeWorkspaceFs} */ (wrapped);
}

/**
 * The fail-closed stand-in. Every call throws an Error carrying a POSIX-style
 * `.code` that host-fs maps to a hard denial (`EPERM` -> permission_denied).
 * `ENOSYS`/`ENOTSUP` are deliberately NOT used: those two mean "fall back to
 * the Node path", which is exactly the outcome this object exists to prevent.
 *
 * @param {string} platform
 * @param {string} detail
 * @returns {import("./index").NativeWorkspaceFs}
 */
function failClosedStandIn(platform, detail) {
  const deny = () => {
    const err = new Error(
      `workspace-fs native addon unavailable: ${detail}. Refusing the ` +
        `confined open rather than serving it through the non-atomic ` +
        `(TOCTOU-exposed) fallback. Rebuild the addon ` +
        `(npm run build:workspace-fs --workspace @0x-copilot/desktop) or, to ` +
        `accept the race deliberately, set ` +
        `COPILOT_ALLOW_NONATOMIC_WORKSPACE_FS=1.`,
    );
    // EPERM, not ENOSYS: host-fs treats ENOSYS/ENOTSUP as "kernel lacks the
    // primitive, use the Node path" and would fall straight back.
    err.code = "EPERM";
    throw err;
  };
  return /** @type {import("./index").NativeWorkspaceFs} */ ({
    platform,
    available: false,
    openBeneath: deny,
  });
}

/**
 * Load the compiled addon.
 *
 * @param {{
 *   platform?: string,
 *   arch?: string,
 *   env?: Readonly<Record<string, string | undefined>>,
 *   isPackaged?: boolean,
 *   resourcesPath?: string,
 *   dir?: string,
 *   require?: (id: string) => unknown,
 *   log?: (message: string) => void,
 * }} [overrides] test seams; production passes nothing
 * @returns {import("./index").NativeWorkspaceFs | undefined}
 */
function loadNative(overrides) {
  const o = overrides || {};
  const platform = o.platform !== undefined ? o.platform : process.platform;
  const arch = o.arch !== undefined ? o.arch : process.arch;
  const env = o.env !== undefined ? o.env : process.env;
  const dir = o.dir !== undefined ? o.dir : __dirname;
  const req = o.require !== undefined ? o.require : require;
  const log =
    o.log !== undefined
      ? o.log
      : (message) => process.stderr.write(`${message}\n`);
  const resourcesPath =
    o.resourcesPath !== undefined ? o.resourcesPath : process.resourcesPath;
  const isPackaged =
    o.isPackaged !== undefined ? o.isPackaged : detectPackaged();

  for (const candidate of candidatePaths(dir, platform, arch, resourcesPath)) {
    let addon;
    try {
      addon = req(candidate);
    } catch {
      // Not built / not present at this candidate — try the next one.
      continue;
    }
    const wrapped = wrapAddon(addon, platform);
    if (wrapped !== undefined) return wrapped;
    // Loaded but wrong shape: that is a build/packaging defect, not a
    // "not built yet". Say so instead of moving on in silence.
    log(
      `[workspace-fs] loaded ${candidate} but it does not export ` +
        `openBeneath — treating the addon as unavailable`,
    );
  }

  const requirement = nativeRequirement(platform);
  if (!requirement.required) return undefined;

  const production = isProductionPosture(env, isPackaged);
  const target = `${platform}-${arch}`;
  if (production && !nonAtomicFallbackAllowed(env)) {
    log(
      `[workspace-fs] FAIL-CLOSED: no compiled addon for ${target} in a ` +
        `production install. ${requirement.reason}. Confined reads and ` +
        `writes will be DENIED until the addon ships.`,
    );
    return failClosedStandIn(
      platform,
      `no compiled binary for ${target} in a production install`,
    );
  }
  log(
    `[workspace-fs] WARNING: no compiled addon for ${target}; falling back to ` +
      `the NON-ATOMIC confined open. ${requirement.reason}.` +
      (production
        ? " Accepted because COPILOT_ALLOW_NONATOMIC_WORKSPACE_FS=1."
        : " Development posture — run npm run build:workspace-fs to close the race."),
  );
  return undefined;
}

module.exports = {
  NATIVE_REQUIREMENT,
  candidatePaths,
  isProductionPosture,
  loadNative,
  nativeRequirement,
  nonAtomicFallbackAllowed,
};
