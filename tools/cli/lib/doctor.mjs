// `copilot doctor` — inspect the install and report what would stop a launch.

import { existsSync, readFileSync, statSync } from "node:fs";
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

  // Orphaned embedded database: a force-quit/crash leftover that would block a
  // fresh start. The app now auto-reclaims it on boot, but surface it here with
  // the one-liner fix. Informational — doesn't fail `doctor`.
  const orphanPid = orphanedDatabasePid();
  if (orphanPid !== null) {
    line(
      "database",
      ui.c.yellow(`orphaned instance running (pid ${orphanPid})`),
    );
    ui.plain(
      `  ${ui.c.dim("".padEnd(16))} if a launch won't start, run ${ui.c.bold("copilot repair")}`,
    );
  }
  ui.plain();

  if (problems.length === 0) {
    ui.ok("all good — run `copilot` to start");
    return true;
  }
  for (const p of problems) ui.err(p);
  return false;
}

/** The pid of a live orphaned embedded postgres holding pgdata, or null. */
function orphanedDatabasePid() {
  const pidPath = path.join(appUserDataDir(), "pgdata", "postmaster.pid");
  if (!existsSync(pidPath)) return null;
  let pid;
  try {
    const first = readFileSync(pidPath, "utf-8").split(/\r?\n/u, 1)[0] ?? "";
    pid = Number.parseInt(first.trim(), 10);
  } catch {
    return null;
  }
  if (Number.isNaN(pid)) return null;
  try {
    process.kill(pid, 0);
    return pid;
  } catch (e) {
    return e && e.code === "EPERM" ? pid : null;
  }
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
