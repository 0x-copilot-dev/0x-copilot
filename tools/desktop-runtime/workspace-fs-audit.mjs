// Does the tree we are about to ship contain the atomic confined-open primitive?
//
// `native/workspace-fs` supplies the kernel's own root-confined,
// symlink/reparse-refusing open (openat2(RESOLVE_BENEATH) on Linux, a
// reparse-refusing NtCreateFile walk on Windows). It was ORPHANED: built by no
// script, carried by no packaging step, named in no workflow. Nothing ever asked
// whether it was there, so for every packaged Windows build the answer was "no"
// and the confined read quietly used a post-open realpath recheck instead —
// the same denials, non-atomically, i.e. a TOCTOU window per read.
//
// This module makes staging ASK. It audits and reports; it deliberately does not
// copy. The loader resolves its binary from its own module directory or from
// `<resourcesPath>/workspace-fs` (electron-builder extraResources), so a third
// staged copy would be read by nobody and would rot as dead weight. What staging
// owes the artifact is a recorded answer, which lands in staging-manifest.json.

import fs from "node:fs";
import path from "node:path";

/**
 * @typedef {object} WorkspaceFsAudit
 * @property {string} target `<platform>-<arch>`
 * @property {boolean} present
 * @property {string} [path] repo-relative path to the binary, when present
 * @property {string} [reason] why it is absent, when absent
 * @property {boolean} required whether this target's confined read depends on it
 */

/**
 * Platforms whose confined read has NO atomic primitive without the addon.
 *
 * darwin is the exception: `O_NOFOLLOW_ANY` refuses a symlink in ANY component
 * during the kernel path-walk, so its pure-Node open is already atomic. Kept in
 * step with NATIVE_REQUIREMENT in apps/desktop/native/workspace-fs/index.cjs,
 * which is the runtime authority; this copy exists because staging must not
 * import from the app it is staging.
 */
export function addonRequiredFor(platform) {
  return platform !== "darwin";
}

/**
 * @param {{
 *   repoRoot: string,
 *   platform: string,
 *   arch: string,
 *   hostPlatform?: string,
 *   hostArch?: string,
 *   log?: (message: string) => void,
 * }} options
 * @returns {WorkspaceFsAudit}
 */
export function auditWorkspaceFsAddon({
  repoRoot,
  platform,
  arch,
  hostPlatform = process.platform,
  hostArch = process.arch,
  log = () => {},
}) {
  const target = `${platform}-${arch}`;
  const required = addonRequiredFor(platform);

  // Dev checkout layout first, then the published CLI payload layout (there
  // repoRoot IS the payload directory and the app lives at desktop/).
  const roots = [
    path.join(repoRoot, "apps", "desktop", "native", "workspace-fs"),
    path.join(repoRoot, "desktop", "native", "workspace-fs"),
  ];
  for (const root of roots) {
    const binary = path.join(root, "prebuilds", target, "workspace_fs.node");
    if (fs.existsSync(binary)) {
      log(`workspace-fs: addon present for ${target} (${binary})`);
      return {
        target,
        present: true,
        path: path.relative(repoRoot, binary),
        required,
      };
    }
  }

  // node-gyp compiles with the host toolchain only, so a cross-target stage can
  // never have found the binary. Distinguishing the two cases matters: one is a
  // missing build step, the other is a wrong runner.
  const hostMatch = platform === hostPlatform && arch === hostArch;
  const reason = hostMatch
    ? "not built on this host — run `npm run build:workspace-fs --workspace @0x-copilot/desktop`"
    : `cannot be built here: node-gyp does not cross-compile from ${hostPlatform}-${hostArch}`;
  log(
    required
      ? `workspace-fs: WARNING no addon for ${target} — ${reason}. Without it the ` +
          `confined read uses a NON-ATOMIC realpath recheck; a production install ` +
          `refuses the read instead of serving it through that race (see ` +
          `apps/desktop/native/workspace-fs/README.md)`
      : `workspace-fs: no addon for ${target} — ${reason} (optional on ${platform}: ` +
          `O_NOFOLLOW_ANY already makes the pure-Node open atomic)`,
  );
  return { target, present: false, reason, required };
}
