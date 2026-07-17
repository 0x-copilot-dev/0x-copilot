// `copilot doctor` — inspect the install and report what would stop a launch.

import { existsSync, statSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import path from "node:path";

import {
  appMainEntry,
  appUserDataDir,
  DOWNLOAD_CACHE,
  isPlatformSupported,
  PLATFORM_KEY,
  resolveElectronBinary,
  resolveRoots,
  stagedRuntimeRoot,
} from "./paths.mjs";
import { readStagingManifest } from "./stage.mjs";
import * as ui from "./ui.mjs";

function electronVersion(binary) {
  // The version lives in electron's package.json next to its dist dir.
  let dir = path.dirname(binary);
  for (let i = 0; i < 8; i++) {
    const pkg = path.join(dir, "package.json");
    if (existsSync(pkg)) {
      try {
        const require = createRequire(path.join(dir, "index.js"));
        const v = require(pkg).version;
        if (typeof v === "string") return v;
      } catch {
        /* keep walking */
      }
    }
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return "unknown";
}

function line(label, value) {
  ui.plain(`  ${ui.c.dim(label.padEnd(16))} ${value}`);
}

export function doctor(pkgRoot) {
  ui.banner();
  const problems = [];

  line("platform", PLATFORM_KEY);
  if (!isPlatformSupported()) {
    problems.push(
      "this platform has no staged runtime (macOS + Windows only).",
    );
  }

  // Where the app + staging script come from.
  let roots = null;
  try {
    roots = resolveRoots(pkgRoot);
    line("source", `${roots.mode} (${roots.repoRoot})`);
  } catch (e) {
    problems.push(e.message);
  }

  // Electron.
  if (roots) {
    try {
      const bin = resolveElectronBinary(roots.electronBases);
      line("electron", `${electronVersion(bin)}  ${ui.c.dim(bin)}`);
    } catch (e) {
      problems.push(e.message);
    }
    // App bundle.
    if (existsSync(appMainEntry(roots.appDir))) {
      line("app bundle", ui.c.green("present"));
    } else {
      line("app bundle", ui.c.yellow("not built"));
      if (roots.mode !== "dev") {
        problems.push("app bundle missing — reinstall the CLI.");
      }
    }
  }

  // Staged runtime.
  const manifest = readStagingManifest();
  if (manifest === null) {
    line("runtime", ui.c.yellow("not staged"));
    ui.plain(
      `  ${ui.c.dim("".padEnd(16))} run ${ui.c.bold("copilot install")}`,
    );
  } else if (manifest.host_exec !== true) {
    line("runtime", ui.c.red("download-only (not runnable)"));
    problems.push(
      "runtime was staged download-only — re-run `copilot install`.",
    );
  } else {
    line(
      "runtime",
      `${ui.c.green("staged")}  ${ui.c.dim(`${stagedRuntimeRoot()} · ${manifest.staged_at ?? "?"}`)}`,
    );
    if (process.platform === "darwin") {
      line(
        "signing",
        manifest.adhoc_signed
          ? ui.c.green("ad-hoc signed")
          : ui.c.yellow("unsigned (may not run on Apple Silicon)"),
      );
      verifySignatures(problems);
    }
  }

  line("app data", appUserDataDir());
  line(
    "downloads",
    existsSync(DOWNLOAD_CACHE)
      ? DOWNLOAD_CACHE
      : `${DOWNLOAD_CACHE} ${ui.c.dim("(empty)")}`,
  );
  ui.plain();

  if (problems.length === 0) {
    ui.ok("all good — run `copilot` to start");
    return true;
  }
  for (const p of problems) ui.err(p);
  return false;
}

/** Spot-check a couple of critical binaries actually carry a valid signature. */
function verifySignatures(problems) {
  const root = stagedRuntimeRoot();
  const targets = [
    path.join(root, "python", "bin", "python3.13"),
    path.join(root, "postgres", "bin", "postgres"),
  ].filter((p) => existsSync(p));
  const bad = [];
  for (const t of targets) {
    const res = spawnSync("codesign", ["-v", t], { stdio: "ignore" });
    if (res.error || res.status !== 0) bad.push(path.basename(t));
  }
  if (bad.length) {
    problems.push(
      `invalid code signature on: ${bad.join(", ")} — re-run \`copilot install --force\`.`,
    );
  }
}
