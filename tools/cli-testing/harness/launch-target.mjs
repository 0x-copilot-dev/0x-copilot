// Resolve the exact desktop artifact that the live harness launches.
//
// The default is deliberately the source checkout for day-to-day development.
// `COPILOT_DESKTOP_TEST_TARGET=installed-payload` instead resolves the globally
// installed @0x-copilot/cli package and launches its assembled payload with the
// Electron binary installed alongside it. That is the end-user npm artifact,
// not a source app that happens to share the same runtime.

import { existsSync } from "node:fs";
import { spawnSync } from "node:child_process";
import path from "node:path";

export const INSTALLED_PAYLOAD_TARGET = "installed-payload";

function globalNpmModulesRoot(platform = process.platform) {
  const npm = platform === "win32" ? "npm.cmd" : "npm";
  const result = spawnSync(npm, ["root", "-g"], {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  });
  const root = result.stdout?.trim();
  if (result.error || result.status !== 0 || !root) {
    const detail =
      result.error?.message || result.stderr?.trim() || "unknown error";
    throw new Error(
      `could not locate npm's global package directory: ${detail}. ` +
        "Set COPILOT_CLI_PACKAGE_ROOT to the installed @0x-copilot/cli directory.",
    );
  }
  return root;
}

function requirePath(exists, candidate, description) {
  if (exists(candidate)) return;
  throw new Error(
    `${description} is missing at ${candidate}. ` +
      "Run `make desktop-install` (or set COPILOT_CLI_PACKAGE_ROOT to a packed install).",
  );
}

/**
 * Pick the app and Electron binary base for the control driver.
 *
 * Kept pure apart from the injected global-npm lookup so the installed-artifact
 * contract can be tested without launching Electron.
 */
export function resolveLaunchTarget({
  repoRoot,
  env = process.env,
  exists = existsSync,
  getGlobalNpmRoot = globalNpmModulesRoot,
}) {
  const target = env.COPILOT_DESKTOP_TEST_TARGET ?? "source";
  if (target === "source") {
    return {
      kind: "source",
      appDir: env.APP_DIR
        ? path.resolve(env.APP_DIR)
        : path.join(repoRoot, "apps", "desktop"),
      electronBases: [repoRoot],
      cliPackageRoot: null,
    };
  }
  if (target !== INSTALLED_PAYLOAD_TARGET) {
    throw new Error(
      `unknown COPILOT_DESKTOP_TEST_TARGET=${JSON.stringify(target)}; ` +
        `use "source" or "${INSTALLED_PAYLOAD_TARGET}".`,
    );
  }
  if (env.APP_DIR) {
    throw new Error(
      "APP_DIR cannot be combined with COPILOT_DESKTOP_TEST_TARGET=installed-payload; " +
        "the installed-payload target must launch the package's own payload/desktop.",
    );
  }

  const cliPackageRoot = env.COPILOT_CLI_PACKAGE_ROOT
    ? path.resolve(env.COPILOT_CLI_PACKAGE_ROOT)
    : path.join(getGlobalNpmRoot(), "@0x-copilot", "cli");
  const appDir = path.join(cliPackageRoot, "payload", "desktop");
  requirePath(
    exists,
    path.join(cliPackageRoot, "package.json"),
    "installed CLI package",
  );
  requirePath(
    exists,
    path.join(appDir, "package.json"),
    "installed desktop payload",
  );
  requirePath(
    exists,
    path.join(appDir, "out", "main", "index.js"),
    "built desktop payload entry",
  );

  return {
    kind: INSTALLED_PAYLOAD_TARGET,
    appDir,
    // Match tools/cli/lib/paths.mjs: the published CLI resolves Electron from
    // its own package, never from this checkout's node_modules.
    electronBases: [cliPackageRoot],
    cliPackageRoot,
  };
}
