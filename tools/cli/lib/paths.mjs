// Path + environment resolution for the `copilot` CLI.
//
// The CLI runs in one of two layouts and must resolve the SAME runtime tree in
// both:
//   * "payload"  — published npm package. A `payload/` dir (assembled by
//     scripts/assemble-payload.mjs at prepack) mirrors the monorepo subset the
//     staging + launch need: tools/desktop-runtime, services/*, packages/*, and
//     the built desktop app under desktop/.
//   * "dev"      — running from the monorepo checkout (no payload assembled).
//     Reads tools/desktop-runtime + apps/desktop straight from the repo.
//
// User state (the staged runtime, downloads, app data) never lives inside the
// installed package — a global npm dir may be read-only and is wiped on upgrade
// — so everything the CLI creates goes under STATE_DIR + the app's userData.

import { existsSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";

export const HOME = os.homedir();

// The Electron app's internal name (app.setName in apps/desktop/main/index.ts).
// Drives app.getPath("userData"); `doctor`/`uninstall` resolve it to find + clear
// the app's local data. Must stay in sync with app.setName.
export const APP_NAME = "0xCopilot";

// User-writable state root. COPILOT_RUNTIME_DIR points the app's supervisor here;
// it resolves the tree at <STATE_DIR>/runtime/<platform>-<arch>.
export const STATE_DIR =
  process.env.COPILOT_HOME && process.env.COPILOT_HOME !== ""
    ? path.resolve(process.env.COPILOT_HOME)
    : path.join(HOME, ".0xcopilot");
export const RUNTIME_DEST = STATE_DIR;

// stage.mjs downloads (CPython + Postgres archives) land here — a shared,
// re-usable binary cache keyed by sha256. Cleaned by `copilot uninstall`.
export const DOWNLOAD_CACHE = path.join(
  HOME,
  ".cache",
  "enterprise-desktop-runtime",
);

export const PLATFORM = process.platform;
export const ARCH = process.arch;
export const PLATFORM_KEY = `${PLATFORM}-${ARCH}`;

// Only these platform-arch combos have a staged runtime — the exact keys in
// tools/desktop-runtime/manifest.json. Gating on the KEY (not just the OS)
// means win32-arm64 / an odd arch is rejected up front with a clear message
// instead of failing deep inside staging.
export const SUPPORTED_KEYS = new Set([
  "darwin-arm64",
  "darwin-x64",
  "win32-x64",
]);
export function isPlatformSupported() {
  return SUPPORTED_KEYS.has(PLATFORM_KEY);
}

// Written after a successful stage; compared on start so a CLI upgrade (new app
// bundle) forces a re-stage of the matching services instead of silently
// running the new app against the previously-staged runtime.
export const VERSION_MARKER = path.join(STATE_DIR, ".copilot-version");

/** The staged runtime root the Electron supervisor will read. */
export function stagedRuntimeRoot() {
  return path.join(RUNTIME_DEST, "runtime", PLATFORM_KEY);
}

/** app.getPath("userData") for APP_NAME — where secrets/pgdata/logs live. */
export function appUserDataDir() {
  if (PLATFORM === "darwin") {
    return path.join(HOME, "Library", "Application Support", APP_NAME);
  }
  if (PLATFORM === "win32") {
    const appData =
      process.env.APPDATA ?? path.join(HOME, "AppData", "Roaming");
    return path.join(appData, APP_NAME);
  }
  const xdg = process.env.XDG_CONFIG_HOME ?? path.join(HOME, ".config");
  return path.join(xdg, APP_NAME);
}

/**
 * Resolve where the staging script + built app live (payload vs dev), plus the
 * bases to resolve the `electron` binary from.
 */
export function resolveRoots(pkgRoot) {
  const payload = path.join(pkgRoot, "payload");
  const payloadStage = path.join(
    payload,
    "tools",
    "desktop-runtime",
    "stage.mjs",
  );
  if (existsSync(payloadStage)) {
    return {
      mode: "payload",
      repoRoot: payload,
      stageScript: payloadStage,
      appDir: path.join(payload, "desktop"),
      electronBases: [pkgRoot, payload],
    };
  }

  // Dev: walk up for a monorepo carrying both the staging tool and the app.
  let dir = pkgRoot;
  for (let i = 0; i < 8; i++) {
    const stage = path.join(dir, "tools", "desktop-runtime", "stage.mjs");
    const app = path.join(dir, "apps", "desktop", "package.json");
    if (existsSync(stage) && existsSync(app)) {
      return {
        mode: "dev",
        repoRoot: dir,
        stageScript: stage,
        appDir: path.join(dir, "apps", "desktop"),
        electronBases: [dir, pkgRoot],
      };
    }
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }

  throw new Error(
    "could not locate the 0xCopilot payload or a monorepo checkout — reinstall the CLI (`npm i -g @0x-copilot/cli`).",
  );
}

/** The bundled Electron entry the app loads (appDir/package.json "main"). */
export function appMainEntry(appDir) {
  return path.join(appDir, "out", "main", "index.js");
}

/** Absolute path to the platform `electron` executable, or throw. */
export function resolveElectronBinary(bases) {
  for (const base of bases) {
    try {
      const require = createRequire(path.join(base, "index.js"));
      const resolved = require("electron");
      if (typeof resolved === "string" && existsSync(resolved)) {
        return resolved;
      }
    } catch {
      // try the next base
    }
  }
  throw new Error(
    "the Electron runtime is not installed. Reinstall the CLI " +
      "(`npm i -g @0x-copilot/cli`); in a monorepo checkout run `npm install` at the repo root.",
  );
}
